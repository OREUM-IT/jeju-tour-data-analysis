#!/usr/bin/env python3
"""Explicit, bounded AWS load check of the Agent Gateway. Sends paid model requests.

--execute-model is required before reading credentials or using the network.
At most --requests (default 10, hard cap 20) recommendation POSTs, in waves of
--wave-size (default 6, at most 10 = 2 running + 8 waiting). Every wave starts
only from an idle Gateway. Each request is submitted once; later calls only read.
No resubmission, cancellation, state-file access, key change, raw result or
credential output. One run per hour: the start time is kept in the lock file.
The request counts in the output are counted where requests leave this script.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time


DEPLOY = Path(__file__).resolve().parent
DEFAULT_REQUESTS, MAX_REQUESTS = 10, 20
DEFAULT_WAVE, MAX_WAVE = 6, 10
POLL_SECONDS = 2
IDLE_WAIT_SECONDS = 150
RUN_INTERVAL_SECONDS = 3600
MEMORY_INTERVAL_SECONDS = 15
ROOT_UID = 0  # required owner of the hourly stamp file
SUBJECT = re.compile(r'load-check-[a-f0-9]{32}-(?:p|[0-9]{2})')
ACTIONS = frozenset(('recommend', 'cannot_confirm', 'ask_clarification', 'impossible'))
# The model run finished and validation rejected its answer: an outcome of the
# request, not of the infrastructure. Anything else stops further waves.
MODEL_OUTCOME_CODES = frozenset(('RECOMMENDATION_BLOCKED', 'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES'))


def load_base():
    # Transport, signing, response limits and memory sampling are the reviewed
    # code of the two-request check. This is a private module instance: only its
    # subject pattern is replaced, so its client signs this script's subjects.
    spec = importlib.util.spec_from_file_location('agent_load_base', DEPLOY / 'check-agent-queue-model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SUBJECT = SUBJECT
    return module


base = load_base()
ProbeError, require, exact, safe_exchange = base.ProbeError, base.require, base.exact, base.safe_exchange
LIFETIME_SECONDS, SAFE_CODES, SERVICES = base.LIFETIME_SECONDS, base.SAFE_CODES, base.SERVICES


class Traffic:
    """Counts every request at the point where it is handed to the transport.

    The report takes its submission count, its request counts and its
    resubmission flag from here, so they describe what was sent, not intent.
    """
    def __init__(self, client):
        self.client, self.lock = client, threading.Lock()
        self.sent, self.submitted = {}, []

    def request(self, method, path, subject, payload=None, **kwargs):
        kind = method + ' ' + ('/v1/jobs/<id>' if path.startswith('/v1/jobs/') else path)
        with self.lock:
            self.sent[kind] = self.sent.get(kind, 0) + 1
            if payload is not None:
                self.submitted.append(subject)
        return self.client.request(method, path, subject, payload, **kwargs)

    def submissions(self):
        with self.lock:
            return len(self.submitted)

    def resubmitted(self):
        with self.lock:
            return len(set(self.submitted)) != len(self.submitted)

    def counts(self):
        with self.lock:
            return dict(sorted(self.sent.items()))


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'LOAD_CHECK_ARGUMENTS_INVALID: use --help; never supply secrets as arguments.\n')


def load_configuration(deploy):
    spec = importlib.util.spec_from_file_location('agent_load_tunnel_config', deploy / 'tunnel/configure.py')
    configure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configure)
    require(os.geteuid() == configure.ROOT_UID, 'ROOT_REQUIRED')
    root = configure.directory(deploy / '.local')
    access = configure.read_access(root)
    require(access['GATEWAY_PUBLIC_HOST'] == base.HOST)
    gateway = configure.check_gateway_file(root, required=True)
    service = configure.read_service(root)
    key = gateway.get('GATEWAY_AUTH_KEY')
    require(isinstance(key, str) and re.fullmatch(r'[a-f0-9]{64}', key))
    return root, key, service


def reserve_run(stream, now, *, uid=0):
    """Refuse a second run within an hour; return the callback that records this run's start."""
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 32)
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    stream.seek(0)
    content = stream.read(33)
    require(content == b'' or re.fullmatch(rb'[0-9]{10}\n', content) is not None)
    # A stamp from the future is treated as recent rather than ignored.
    require(content == b'' or now - int(content) >= RUN_INTERVAL_SECONDS, 'LOAD_CHECK_RECENT_RUN')

    def record():
        stream.seek(0)
        stream.truncate()
        stream.write(b'%010d\n' % int(now))
        stream.flush()
        os.fsync(stream.fileno())
    return record


def idle_state(client, subject):
    """'idle', 'busy' or 'not_ready' from one authenticated /readyz read."""
    ready = safe_exchange(client, 'GET', '/readyz', subject)
    if ready is None or ready[0] != 200:
        return 'not_ready'
    value = ready[1]
    if not (isinstance(value, dict) and value.get('ready') is True and value.get('mode') == 'access'
            and value.get('flowVerified') is True and value.get('catalogPlaces') == 981
            and value.get('productionReady') is False and value.get('asyncJobs') is True
            and type(value.get('maxConcurrent')) is int and value['maxConcurrent'] == 2
            and exact(value.get('jobs'), ('queued', 'running', 'uncertain'))
            and all(type(count) is int and count >= 0 for count in value['jobs'].values())):
        return 'not_ready'
    return 'idle' if not any(value['jobs'].values()) else 'busy'


def finished(value, request_id):
    require(exact(value, ('schemaVersion', 'status', 'requestId', 'response', 'catalogVersion', 'policyVersion', 'productionReady'))
            and value['schemaVersion'] == '1.0' and value['status'] == 'accepted'
            and value['requestId'] == request_id and value['productionReady'] is False
            and isinstance(value['catalogVersion'], str) and base.HASH.fullmatch(value['catalogVersion'])
            and value['policyVersion'] == 'recommendation-validation-v1')
    response = value['response']
    require(isinstance(response, dict) and response.get('requestId') == request_id
            and response.get('action') in ACTIONS and isinstance(response.get('placeIds'), list)
            and len(response['placeIds']) <= 5 and all(isinstance(item, str) for item in response['placeIds']))
    # Only the action and a count leave this function; place ids and notices do not.
    return {'final': 'accepted', 'action': response['action'], 'placeCount': len(response['placeIds'])}


def error_code(value):
    code = value.get('error', {}).get('code') if isinstance(value, dict) and isinstance(value.get('error'), dict) else None
    return code if isinstance(code, str) and code in SAFE_CODES else None


def distribution(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {'count': 0}
    def at(share):
        position = (len(values) - 1) * share
        low, high = math.floor(position), math.ceil(position)
        return round(values[low] + (values[high] - values[low]) * (position - low), 2)
    return {'count': len(values), 'p50': at(0.5), 'p95': at(0.95), 'max': round(values[-1], 2)}


class MemoryMonitor:
    """Samples container memory on its own thread so that polling keeps its rhythm."""
    def __init__(self, sample, interval=MEMORY_INTERVAL_SECONDS):
        self.sample, self.interval = sample, interval
        self.stopped, self.thread = threading.Event(), None
        self.before, self.count, self.failed, self.peak = None, 0, 0, {}

    def record(self, value):
        if not (isinstance(value, dict) and value.get('available') is True):
            self.failed += 1
            return
        try:
            usage = {name: float(value['services'][name]['usageMiB']) for name in SERVICES}
        except Exception:
            self.failed += 1
            return
        self.count += 1
        for name, amount in usage.items():
            self.peak[name] = max(self.peak.get(name, 0), amount)

    def loop(self):
        while not self.stopped.wait(self.interval):
            self.record(self.sample())

    def start(self):
        self.before = self.sample()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=15)
        return {'before': self.before, 'after': self.sample(), 'intervalSeconds': self.interval,
                'samplesDuring': self.count, 'failedSamplesDuring': self.failed,
                'peakUsageMiBDuring': dict(self.peak)}


def run_wave(client, pool, run_id, wave, first, size, records, counters, *, monotonic, wall, sleep, before_post):
    jobs = {}
    stopped = None
    for index in range(first, first + size):
        subject = 'load-check-%s-%02d' % (run_id, index)
        record = records[index]
        payload = {'schemaVersion': '1.0', 'timezone': 'Asia/Seoul',
                   'query': f'합성 부하 검사 {index + 1:02d}: 실내 방문 조건이 필요한 제주 장소 후보를 최대 3곳 찾아주세요. 미확인 조건은 확인 필요로 안내해 주세요.',
                   'filters': {}, 'excludedIds': [], 'requirements': [{'type': 'indoor_required', 'value': True}]}
        before_post()
        sent = monotonic()
        # One attempt. A refused or ambiguous submission is never repeated and
        # ends all further submissions of this run.
        response = safe_exchange(client, 'POST', '/v1/jobs', subject, payload)
        record.update(wave=wave, submitHttp=response[0] if response else None)
        try:
            require(response is not None and response[0] == 202)
            job_id, status, expiry = base.pending(response[1], None, wall())
            require(all(job['id'] != job_id for job in jobs.values()))
        except Exception:
            code = error_code(response[1]) if response else None
            record.update(final='failed' if code else 'unknown', code=code or 'SUBMISSION_OUTCOME_UNKNOWN')
            stopped = 'SUBMISSION_REJECTED' if code else 'SUBMISSION_OUTCOME_UNKNOWN'
            break
        accepted_at = monotonic()
        jobs[index] = {'id': job_id, 'subject': subject, 'status': status, 'expiry': expiry, 'sent': sent,
                       'running': accepted_at if status == 'running' else None}
        record['final'] = None
    deadline = monotonic() + LIFETIME_SECONDS
    while any(records[index]['final'] is None for index in jobs):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        active = [index for index in jobs if records[index]['final'] is None]
        polls = {index: pool.submit(safe_exchange, client, 'GET', '/v1/jobs/' + jobs[index]['id'],
                                    jobs[index]['subject'], timeout=min(20, remaining)) for index in active}
        counters['polls'] += len(polls)
        running = queued = 0
        for index, future in polls.items():
            response = future.result()
            if response is None:
                continue  # Only a read may be repeated after a transport failure.
            status, value = response
            job, record, now = jobs[index], records[index], monotonic()
            try:
                if status == 202:
                    _, state, _ = base.pending(value, job['id'], wall(), prior_expiry=job['expiry'])
                    require(job['status'] != 'running' or state == 'running')
                    job['status'] = state
                    if state == 'running':
                        running += 1
                        job['running'] = now if job['running'] is None else job['running']
                    else:
                        queued += 1
                    continue
                if status == 200:
                    record.update(finished(value, job['id']))
                else:
                    code = error_code(value)
                    require(code is not None)
                    record.update(final='failed', code=code)
                record['finalHttp'] = status
                record['totalSeconds'] = round(now - job['sent'], 2)
                if job['running'] is not None:
                    record['queueWaitSeconds'] = round(job['running'] - job['sent'], 2)
                    record['runSeconds'] = round(now - job['running'], 2)
            except Exception:
                record.update(final='unknown', code='RESULT_RESPONSE_INVALID')
        counters['maxRunning'] = max(counters['maxRunning'], running)
        counters['maxQueued'] = max(counters['maxQueued'], queued)
        if any(records[index]['final'] is None for index in jobs):
            sleep(max(0, min(POLL_SECONDS, deadline - monotonic())))
    for index in jobs:
        if records[index]['final'] is None:
            records[index].update(final='unknown', code='POLL_DEADLINE_REACHED')
    return stopped


def run_load(client, requests, wave_size, *, monotonic=time.monotonic, wall=time.time, sleep=time.sleep,
             monitor=None, before_first_post=lambda: None):
    require(type(requests) is int and 1 <= requests <= MAX_REQUESTS and type(wave_size) is int
            and 1 <= wave_size <= MAX_WAVE, 'LOAD_CHECK_LIMIT_INVALID')
    client = client if isinstance(client, Traffic) else Traffic(client)
    run_id = secrets.token_hex(16)
    probe = 'load-check-' + run_id + '-p'
    require(idle_state(client, probe) == 'idle', 'LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY')
    records = [{'wave': None, 'submitHttp': None, 'final': 'not_submitted'} for _ in range(requests)]
    counters = {'polls': 0, 'maxRunning': 0, 'maxQueued': 0}
    started, stopped, waves, marked = monotonic(), None, 0, False

    def before_post():
        nonlocal marked
        if not marked:
            marked = True
            before_first_post()
            if monitor is not None:
                monitor.start()

    with ThreadPoolExecutor(max_workers=wave_size) as pool:
        for first in range(0, requests, wave_size):
            if first:
                # The next wave needs an idle Gateway again: no other user's job
                # and no protection lease may be pending.
                waited, state = monotonic(), idle_state(client, probe)
                while state == 'busy' and monotonic() - waited < IDLE_WAIT_SECONDS:
                    sleep(POLL_SECONDS)
                    state = idle_state(client, probe)
                if state != 'idle':
                    stopped = 'GATEWAY_NOT_IDLE_BETWEEN_WAVES' if state == 'busy' else 'GATEWAY_NOT_READY_BETWEEN_WAVES'
                    break
            waves += 1
            size = min(wave_size, requests - first)
            stopped = run_wave(client, pool, run_id, waves, first, size, records, counters,
                               monotonic=monotonic, wall=wall, sleep=sleep, before_post=before_post)
            wave = records[first:first + size]
            if stopped is None and any(item['final'] == 'unknown' or (item['final'] == 'failed'
                                       and item['code'] not in MODEL_OUTCOME_CODES) for item in wave):
                stopped = 'UNEXPECTED_RESULT_IN_WAVE'
            if stopped is None and not any(item['final'] == 'accepted' for item in wave):
                stopped = 'NO_ACCEPTED_RESULT_IN_WAVE'
            if stopped is not None:
                break
    elapsed = round(monotonic() - started, 2)
    memory = monitor.stop() if monitor is not None and marked else {'available': False}
    outcomes = {}
    for item in records:
        name = item['final'] if item['final'] in ('accepted', 'not_submitted') else item['final'] + ':' + item['code']
        outcomes[name] = outcomes.get(name, 0) + 1
    accepted_count = outcomes.get('accepted', 0)
    return {'status': 'completed' if stopped is None else 'stopped', 'scope': 'aws-bounded-load-check',
            'requestedRequests': requests, 'waveSize': wave_size, 'wavesStarted': waves, 'stoppedReason': stopped,
            'recommendationPostAttempts': client.submissions(), 'acceptedResults': accepted_count, 'outcomes': outcomes,
            'timing': {name: distribution(item.get(name) for item in records)
                       for name in ('queueWaitSeconds', 'runSeconds', 'totalSeconds')},
            'pollIntervalSeconds': POLL_SECONDS, 'pollRequests': counters['polls'],
            'maxRunningObserved': counters['maxRunning'], 'maxQueuedObserved': counters['maxQueued'],
            'elapsedSeconds': elapsed, 'requests': records, 'memorySamples': memory,
            'modelExecutionRequested': client.submissions() > 0, 'automaticResubmission': client.resubmitted(),
            'requestCounts': client.counts(), 'productionReady': False}


def main():
    parser = SafeParser(description=__doc__)
    parser.add_argument('--execute-model', action='store_true',
                        help='Authorize the recommendation submissions; provider charges apply.')
    parser.add_argument('--requests', type=int, default=DEFAULT_REQUESTS,
                        help=f'Total submissions, 1 to {MAX_REQUESTS} (default {DEFAULT_REQUESTS}).')
    parser.add_argument('--wave-size', type=int, default=DEFAULT_WAVE,
                        help=f'Submissions per wave, 1 to {MAX_WAVE} (default {DEFAULT_WAVE}).')
    args = parser.parse_args()
    refusal = None if args.execute_model else 'EXPLICIT_MODEL_FLAG_REQUIRED'
    if refusal is None and not (1 <= args.requests <= MAX_REQUESTS and 1 <= args.wave_size <= MAX_WAVE):
        refusal = 'REQUEST_COUNT_OUT_OF_RANGE'
    if refusal is not None:
        print(json.dumps({'status': 'not_executed', 'code': refusal, 'recommendationPostAttempts': 0,
                          'modelExecutionRequested': False}, separators=(',', ':')))
        return 2
    phase, traffic = 'configuration', None
    try:
        root, key, service = load_configuration(DEPLOY)
        descriptor = os.open(root / '.agent-load-check.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'r+b') as lock:
            phase = 'recent_run_check'
            record_start = reserve_run(lock, time.time(), uid=ROOT_UID)
            phase = 'preflight_or_execution'
            traffic = Traffic(base.PublicClient(key, service))
            result = run_load(traffic, args.requests, args.wave_size,
                              monitor=MemoryMonitor(lambda: base.memory_snapshot(DEPLOY)),
                              before_first_post=record_start)
        print(json.dumps(result, separators=(',', ':')))
        return 0 if result['status'] == 'completed' and result['acceptedResults'] == args.requests else 1
    except (Exception, KeyboardInterrupt) as error:
        code = error.args[0] if isinstance(error, ProbeError) and error.args and error.args[0] in (
            'LOAD_CHECK_RECENT_RUN', 'LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY', 'ROOT_REQUIRED') else 'LOAD_CHECK_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'phase': phase,
              'submissionOutcomeMayBeUnknown': phase == 'preflight_or_execution' and code == 'LOAD_CHECK_FAILED',
              'recommendationPostAttempts': traffic.submissions() if traffic else 0,
              'automaticResubmission': traffic.resubmitted() if traffic else False,
              'productionReady': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
