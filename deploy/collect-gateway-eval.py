#!/usr/bin/env python3
"""Collect the 96 held-out evaluation questions through the deployed Agent Gateway. Sends paid model requests.

Used once per applied flow (Gemini, then GPT) to compare the two on the same questions. The requests file
is made by services/jeju-mcp/scripts/gateway-eval.mjs; scoring happens there too.

--execute-model is required before reading credentials or using the network. One request at a time, at
most --hourly-budget submissions per rolling hour (default 45 of the Gateway's 60, leaving room for web
users). A submission that the Gateway definitively refused before running (RATE_LIMITED, BUSY, QUEUE_FULL)
is waited out and sent again; any other submission is never repeated, an ambiguous one is recorded as
unknown. Results are appended to <label>-results.jsonl (0600, inside .local) so an interrupted run resumes
where it stopped. Place IDs and responses are written only there; stdout shows outcome, action and seconds.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time


DEPLOY = Path(__file__).resolve().parent
EVAL_DIR = DEPLOY / '.local' / 'gateway-eval'
LABEL = re.compile(r'[a-z0-9][a-z0-9-]{0,40}')
SUBJECT = re.compile(r'gateway-eval-[a-f0-9]{32}-(?:p|[0-9]{3})')
RUNTIME_ID = re.compile(r'baseline-[a-z0-9-]{8,100}')
PAYLOAD_KEYS = ('schemaVersion', 'query', 'timezone', 'visitDate', 'filters', 'excludedIds', 'requirements')
RESPONSE_KEYS = ('schemaVersion', 'requestId', 'action', 'placeIds', 'requirementClaims', 'confirmationNotice', 'noticeTopics')
ACTIONS = frozenset(('recommend', 'cannot_confirm', 'ask_clarification', 'impossible'))
# Refused before anything ran: safe to wait and submit the same case again.
REFUSED = frozenset(('RATE_LIMITED', 'BUSY', 'QUEUE_FULL'))
# The Gateway sends its wait in a Retry-After header that the reviewed client does not expose; wait fixed times.
REFUSED_WAIT_SECONDS = {'RATE_LIMITED': 120, 'BUSY': 30, 'QUEUE_FULL': 30}
MAX_CASES = 96
DEFAULT_BUDGET, MAX_BUDGET = 45, 50
POLL_SECONDS = 2
MAX_REFUSED_WAIT_SECONDS = 3600
STOP_AFTER_CONSECUTIVE_INFRA_FAILURES = 3


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reviewed transport, signing and response bounds of the queue checks; only the subject pattern differs.
base = load_module('gateway_eval_base', 'check-agent-queue-model.py')
base.SUBJECT = SUBJECT
load = load_module('gateway_eval_load', 'check-agent-load.py')
ProbeError, require, exact, safe_exchange = base.ProbeError, base.require, base.exact, base.safe_exchange


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'GATEWAY_EVAL_ARGUMENTS_INVALID: use --help; never supply secrets as arguments.\n')


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def read_requests(path):
    entries = json.loads(path.read_text('utf-8'))
    require(isinstance(entries, list) and 1 <= len(entries) <= MAX_CASES, 'GATEWAY_EVAL_REQUESTS_INVALID')
    seen = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get('caseId'), str) and entry['caseId'] not in seen
                and isinstance(entry.get('runtimeRequest'), dict)
                and RUNTIME_ID.fullmatch(entry['runtimeRequest'].get('requestId', '')) is not None
                and exact(entry.get('payload'), PAYLOAD_KEYS), 'GATEWAY_EVAL_REQUESTS_INVALID')
        seen.add(entry['caseId'])
    return entries


def read_done(path, *, retry_unknown=False):
    """Cases that already have a recorded outcome; a refused submission is never written, so it is not done.

    With retry_unknown, cases whose last line is 'unknown' are sent again (one more paid call each).
    """
    done = {}
    if path.exists():
        for line in path.read_text('utf-8').splitlines():
            if line.strip():
                value = json.loads(line)
                done[value['caseId']] = value['final']
    return {case: final for case, final in done.items() if not (retry_unknown and final == 'unknown')}


def accepted_result(value, request_id):
    require(exact(value, ('schemaVersion', 'status', 'requestId', 'response', 'catalogVersion', 'policyVersion', 'productionReady'))
            and value['schemaVersion'] == '1.0' and value['status'] == 'accepted' and value['requestId'] == request_id
            and value['productionReady'] is False and isinstance(value['catalogVersion'], str)
            and base.HASH.fullmatch(value['catalogVersion']) and value['policyVersion'] == 'recommendation-validation-v1',
            'RESULT_RESPONSE_INVALID')
    response = value['response']
    require(exact(response, RESPONSE_KEYS) and response['requestId'] == request_id and response['action'] in ACTIONS
            and isinstance(response['placeIds'], list) and len(response['placeIds']) <= 5
            and all(isinstance(item, str) for item in response['placeIds']), 'RESULT_RESPONSE_INVALID')
    return {'gateway': {'status': 'accepted', 'requestId': request_id, 'catalogVersion': value['catalogVersion'],
                        'policyVersion': value['policyVersion']}, 'response': response}


class Budget:
    """Own submissions per rolling hour, so a long run leaves the rest of the Gateway's hourly limit to web users."""
    def __init__(self, limit, monotonic):
        self.limit, self.monotonic, self.times = limit, monotonic, []

    def wait(self):
        now = self.monotonic()
        self.times = [at for at in self.times if now - at < 3600]
        return 0 if len(self.times) < self.limit else 3600 - (now - self.times[0]) + 1

    def record(self):
        self.times.append(self.monotonic())


KEY = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,39}')
CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
STATES = frozenset(('queued', 'running', 'accepted', 'cancelled'))


def describe(http, body, now, expiry):
    """Why a Gateway reply did not fit: field names, small integers and fixed vocabularies only.

    No place IDs, query text or free-form values. Written to the .local results line, never to stdout.
    """
    keys = lambda value: sorted(k for k in value if isinstance(k, str) and KEY.fullmatch(k))[:16] if isinstance(value, dict) else None
    detail = {'http': http if type(http) is int else None, 'bodyType': type(body).__name__, 'bodyKeys': keys(body)}
    if isinstance(body, dict):
        if body.get('status') in STATES:
            detail['status'] = body['status']
        error = body.get('error')
        if isinstance(error, dict) and isinstance(error.get('code'), str) and CODE.fullmatch(error['code']):
            detail['errorCode'] = error['code']
        for name in ('position', 'pollAfterSeconds'):
            if type(body.get(name)) is int and -1000 <= body[name] <= 1000:
                detail[name] = body[name]
        if isinstance(body.get('expiresAt'), str):
            detail['expiresAtSameAsSubmit'] = body['expiresAt'] == expiry
            try:
                detail['expiresInSeconds'] = round(datetime.fromisoformat(body['expiresAt'].replace('Z', '+00:00')).timestamp() - now)
            except ValueError:
                detail['expiresInSeconds'] = None
        response = body.get('response')
        if isinstance(response, dict):
            detail['responseKeys'] = keys(response)
            if response.get('action') in ACTIONS:
                detail['action'] = response['action']
            if isinstance(response.get('placeIds'), list):
                detail['placeCount'] = len(response['placeIds'])
    return detail


def collect_case(client, entry, subject, *, monotonic, wall, sleep):
    """One case: returns (record or None if refused, retry_after_seconds)."""
    sent_wall, sent = wall(), monotonic()
    response = safe_exchange(client, 'POST', '/v1/jobs', subject, entry['payload'])
    record = {'caseId': entry['caseId'], 'runtimeRequestId': entry['runtimeRequest']['requestId'], 'submittedAt': iso(sent_wall)}
    if response is None:
        return {**record, 'final': 'unknown', 'code': 'SUBMISSION_OUTCOME_UNKNOWN', 'finishedAt': iso(wall())}, 0
    status, value = response
    if status != 202:
        code = load.error_code(value)
        if code in REFUSED:
            return None, REFUSED_WAIT_SECONDS[code]
        return {**record, 'final': 'failed', 'code': code or 'SUBMISSION_REJECTED', 'finishedAt': iso(wall())}, 0
    try:
        job_id, state, expiry = base.pending(value, None, wall())
    except Exception:
        return {**record, 'final': 'unknown', 'code': 'SUBMISSION_OUTCOME_UNKNOWN', 'finishedAt': iso(wall())}, 0
    running_at = monotonic() if state == 'running' else None
    deadline = sent + base.LIFETIME_SECONDS
    while monotonic() < deadline:
        read = safe_exchange(client, 'GET', '/v1/jobs/' + job_id, subject, timeout=min(20, max(1, deadline - monotonic())))
        now = monotonic()
        if read is not None:
            code_http, body = read
            timing = {}
            try:
                if code_http == 202:
                    _, state, _ = base.pending(body, job_id, wall(), prior_expiry=expiry)
                    if state == 'running' and running_at is None:
                        running_at = now
                    sleep(POLL_SECONDS)
                    continue
                timing = {'totalSeconds': round(now - sent, 2)}
                if running_at is not None:
                    timing.update(queueWaitSeconds=round(running_at - sent, 2), runSeconds=round(now - running_at, 2))
                if code_http == 200:
                    return {**record, 'final': 'accepted', 'gatewayHttp': 200, **timing, 'finishedAt': iso(wall()),
                            **accepted_result(body, job_id)}, 0
                code = load.error_code(body)
                require(code is not None)
                return {**record, 'final': 'failed', 'code': code, 'gatewayHttp': code_http, **timing, 'finishedAt': iso(wall())}, 0
            except Exception as error:
                reason = error.args[0] if isinstance(error, ProbeError) and error.args and isinstance(error.args[0], str) else type(error).__name__
                detail = {**describe(code_http, body, wall(), expiry), 'reason': reason,
                          'requestIdMatches': isinstance(body, dict) and body.get('requestId') == job_id}
                return {**record, 'final': 'unknown', 'code': 'RESULT_RESPONSE_INVALID', 'diagnostic': detail, **timing,
                        'finishedAt': iso(wall())}, 0
        sleep(POLL_SECONDS)
    return {**record, 'final': 'unknown', 'code': 'POLL_DEADLINE_REACHED', 'totalSeconds': round(monotonic() - sent, 2),
            'finishedAt': iso(wall())}, 0


def run(client, entries, results_path, *, budget, limit=None, retry_unknown=False, monotonic=time.monotonic, wall=time.time,
        sleep=time.sleep, out=sys.stdout):
    run_id = secrets.token_hex(16)
    probe = 'gateway-eval-%s-p' % run_id
    require(load.idle_state(client, probe) != 'not_ready', 'GATEWAY_NOT_READY')
    done = read_done(results_path, retry_unknown=retry_unknown)
    todo = [(index, entry) for index, entry in enumerate(entries) if entry['caseId'] not in done]
    if limit is not None:
        todo = todo[:limit]
    budget = Budget(budget, monotonic)
    counts, infra, stopped, submissions = {}, 0, None, 0
    descriptor = os.open(results_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as sink:
        for index, entry in todo:
            subject = 'gateway-eval-%s-%03d' % (run_id, index)
            waited = 0
            while True:
                pause = budget.wait()
                if pause:
                    sleep(pause)
                    continue
                budget.record()
                submissions += 1
                record, retry = collect_case(client, entry, subject, monotonic=monotonic, wall=wall, sleep=sleep)
                if record is not None:
                    break
                waited += retry
                require(waited <= MAX_REFUSED_WAIT_SECONDS, 'GATEWAY_REFUSED_FOR_TOO_LONG')
                sleep(retry)
            sink.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
            sink.flush()
            os.fsync(sink.fileno())
            name = record['final'] if record['final'] == 'accepted' else record['final'] + ':' + record['code']
            counts[name] = counts.get(name, 0) + 1
            print(json.dumps({'n': len(done) + sum(counts.values()), 'of': len(entries), 'outcome': name,
                              'action': record.get('response', {}).get('action'), 'seconds': record.get('totalSeconds'),
                              **({'diagnostic': record['diagnostic']} if 'diagnostic' in record else {})},
                             separators=(',', ':')), file=out, flush=True)
            # A blocked answer is a model outcome; anything else unexpected is infrastructure.
            infra = 0 if record['final'] == 'accepted' or record.get('code') in load.MODEL_OUTCOME_CODES else infra + 1
            if infra >= STOP_AFTER_CONSECUTIVE_INFRA_FAILURES:
                stopped = 'CONSECUTIVE_INFRASTRUCTURE_FAILURES'
                break
    remaining = len(entries) - len(read_done(results_path))
    return {'status': 'completed' if remaining == 0 else 'stopped' if stopped else 'partial', 'stoppedReason': stopped,
            'outcomesThisRun': counts, 'submissionsThisRun': submissions, 'casesRemaining': remaining,
            'modelExecutionRequested': submissions > 0, 'productionReady': False}


def main():
    parser = SafeParser(description=__doc__)
    parser.add_argument('--execute-model', action='store_true', help='Authorize paid model submissions.')
    parser.add_argument('--label', required=True, help='Same label as gateway-eval.mjs requests (e.g. gpt, gemini).')
    parser.add_argument('--hourly-budget', type=int, default=DEFAULT_BUDGET,
                        help=f'Own submissions per rolling hour, 1 to {MAX_BUDGET} (default {DEFAULT_BUDGET}).')
    parser.add_argument('--limit', type=int, help='Collect at most this many remaining cases (a short trial).')
    parser.add_argument('--retry-unknown', action='store_true',
                        help='Send cases recorded as unknown again (each may already have run once).')
    args = parser.parse_args()
    refusal = None if args.execute_model else 'EXPLICIT_MODEL_FLAG_REQUIRED'
    if refusal is None and not (LABEL.fullmatch(args.label) and 1 <= args.hourly_budget <= MAX_BUDGET
                                and (args.limit is None or 1 <= args.limit <= MAX_CASES)):
        refusal = 'ARGUMENTS_OUT_OF_RANGE'
    if refusal is not None:
        print(json.dumps({'status': 'not_executed', 'code': refusal, 'modelExecutionRequested': False}, separators=(',', ':')))
        return 2
    try:
        root, key, service = load.load_configuration(DEPLOY)
        entries = read_requests(EVAL_DIR / (args.label + '-requests.json'))
        descriptor = os.open(root / '.gateway-eval.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run(base.PublicClient(key, service), entries, EVAL_DIR / (args.label + '-results.jsonl'),
                         budget=args.hourly_budget, limit=args.limit, retry_unknown=args.retry_unknown)
        print(json.dumps(result, separators=(',', ':')))
        return 0 if result['status'] in ('completed', 'partial') else 1
    except (Exception, KeyboardInterrupt) as error:
        code = error.args[0] if isinstance(error, ProbeError) and error.args and isinstance(error.args[0], str) else 'GATEWAY_EVAL_FAILED'
        if isinstance(error, BlockingIOError):
            code = 'ANOTHER_COLLECTION_IS_RUNNING'
        print(json.dumps({'status': 'failed', 'code': code, 'resumable': True, 'productionReady': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
