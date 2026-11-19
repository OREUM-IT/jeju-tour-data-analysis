#!/usr/bin/env python3
"""Explicit, two-request AWS queue smoke check. May incur Vertex AI charges.

--execute-model is required before reading credentials or using the network.
Exactly two recommendation POST attempts; subsequent requests only read jobs.
No model resubmission, cancellation, raw result/credential output, or file export.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


HOST = 'agent.oreumit.com'
MAX_RESPONSE = 64 * 1024
MAX_CATALOG = 32 * 1024 * 1024
LIFETIME_SECONDS = 720
ID = re.compile(r'gw-[a-f0-9]{32}')
HASH = re.compile(r'sha256:[a-f0-9]{64}')
SUBJECT = re.compile(r'queue-model-[a-f0-9]{32}-[abp]')
SAFE_CODES = frozenset(('AUTH_FAILED', 'ACCESS_REQUIRED', 'ORIGIN_REJECTED', 'REPLAY_DETECTED',
    'RATE_LIMITED', 'BUSY', 'QUEUE_FULL', 'STATE_UNAVAILABLE', 'NOT_FOUND', 'JOB_NOT_FOUND',
    'JOB_EXPIRED', 'JOB_CANCELLED', 'FLOW_CHANGED', 'MCP_NOT_READY', 'RECOMMENDATION_BLOCKED',
    'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES', 'SCOPE_CLEANUP_FAILED',
    'UPSTREAM_TIMEOUT_NO_RETRY', 'GATEWAY_UNAVAILABLE', 'INVALID_RECOMMENDATION_REQUEST'))
SERVICES = ('agent-gateway', 'langflow', 'jeju-mcp')
COMPOSE_FILES = ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                 'compose.gateway.yaml', 'compose.tunnel.yaml')


class ProbeError(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'QUEUE_MODEL_ARGUMENTS_INVALID: use --help; never supply secrets as arguments.\n')


def require(condition, code='QUEUE_MODEL_CHECK_INVALID'):
    if not condition:
        raise ProbeError(code)


def exact(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def json_value(content):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    def constant(_value):
        raise ProbeError('QUEUE_MODEL_RESPONSE_INVALID')
    return json.loads(content.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def load_configuration(deploy):
    spec = importlib.util.spec_from_file_location('queue_model_tunnel_config', deploy / 'tunnel/configure.py')
    configure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configure)
    require(os.geteuid() == configure.ROOT_UID, 'ROOT_REQUIRED')
    root = configure.directory(deploy / '.local')
    access = configure.read_access(root)
    require(access['GATEWAY_PUBLIC_HOST'] == HOST)
    gateway = configure.check_gateway_file(root, required=True)
    service = configure.read_service(root)
    key = gateway.get('GATEWAY_AUTH_KEY')
    require(isinstance(key, str) and re.fullmatch(r'[a-f0-9]{64}', key))
    folder = configure.directory(root / 'catalog')
    descriptor = os.open(folder / 'agent-catalog-b0602a1.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_CATALOG)
        content = stream.read(MAX_CATALOG + 1)
    require(len(content) <= MAX_CATALOG)
    catalog = json_value(content)
    require(isinstance(catalog, dict) and catalog.get('schemaVersion') == '2.0'
            and catalog.get('dataMode') == 'app-export' and isinstance(catalog.get('places'), list)
            and len(catalog['places']) == 981)
    ids = [place.get('id') if isinstance(place, dict) else None for place in catalog['places']]
    require(all(isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip() for value in ids)
            and len(set(ids)) == 981)
    return root, key, service, frozenset(ids), 'sha256:' + hashlib.sha256(content).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bounded_body(response, deadline, *, monotonic=time.monotonic):
    # urllib's default read timeout is an inactivity timeout. read1 makes at most
    # one buffered/raw read, and every such read gets the remaining total budget.
    stream, connection = response, None
    for _ in range(5):
        connection = getattr(stream, '_sock', None)
        if connection is not None:
            break
        stream = getattr(stream, 'fp', None) or getattr(stream, 'raw', None)
        if stream is None:
            break
    require(connection is not None and callable(getattr(connection, 'settimeout', None))
            and callable(getattr(response, 'read1', None)), 'QUEUE_MODEL_RESPONSE_INVALID')
    chunks, size = [], 0
    while True:
        remaining = deadline - monotonic()
        require(remaining > 0, 'QUEUE_MODEL_RESPONSE_TIMEOUT')
        connection.settimeout(remaining)
        chunk = response.read1(min(16 * 1024, MAX_RESPONSE + 1 - size))
        require(monotonic() < deadline, 'QUEUE_MODEL_RESPONSE_TIMEOUT')
        size += len(chunk)
        require(size <= MAX_RESPONSE, 'QUEUE_MODEL_RESPONSE_TOO_LARGE')
        chunks.append(chunk)
        if not chunk or response.isclosed():
            return b''.join(chunks)


class PublicClient:
    def __init__(self, key, service, *, wall=time.time, monotonic=time.monotonic,
                 nonce=lambda: secrets.token_hex(16), opener=None):
        require(isinstance(key, str) and re.fullmatch(r'[a-f0-9]{64}', key))
        self.key, self.service, self.wall, self.nonce = key, dict(service), wall, nonce
        self.opener = opener
        self.monotonic = monotonic

    def request(self, method, path, subject, payload=None, *, timeout=20):
        require(SUBJECT.fullmatch(subject) is not None)
        require((method == 'POST' and path == '/v1/jobs' and isinstance(payload, dict))
                or (method == 'GET' and payload is None and
                    (path == '/readyz' or re.fullmatch(r'/v1/jobs/gw-[a-f0-9]{32}', path))))
        require(0 < timeout <= 20)
        body = b'' if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        require(len(body) <= 32 * 1024)
        timestamp, nonce = str(int(self.wall())), self.nonce()
        require(re.fullmatch(r'[a-f0-9]{32}', nonce) is not None)
        canonical = '\n'.join((method, path, subject, timestamp, nonce, hashlib.sha256(body).hexdigest()))
        headers = {'Accept': 'application/json', 'User-Agent': 'oreumit-agent-queue-check/1.0',
            'CF-Access-Client-Id': self.service['CF_ACCESS_CLIENT_ID'],
            'CF-Access-Client-Secret': self.service['CF_ACCESS_CLIENT_SECRET'],
            'X-Jeju-Subject': subject, 'X-Jeju-Timestamp': timestamp, 'X-Jeju-Nonce': nonce,
            'X-Jeju-Signature': hmac.new(self.key.encode('ascii'), canonical.encode('ascii'), hashlib.sha256).hexdigest()}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request('https://' + HOST + path, headers=headers, method=method,
                                         data=body if payload is not None else None)
        # A separate opener per call avoids shared thread state; preserve normal
        # TLS/proxy policy. No CookieJar, redirect, Origin, or browser cookies.
        opener = self.opener or urllib.request.build_opener(NoRedirect())
        deadline = self.monotonic() + timeout
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = bounded_body(response, deadline, monotonic=self.monotonic)
            require(re.match(r'^application/json(?:\s*;|$)', response.headers.get('Content-Type', ''), re.I),
                    'QUEUE_MODEL_RESPONSE_INVALID')
            return response.status, json_value(content)


def pending(value, expected_id, now, *, prior_expiry=None):
    require(exact(value, ('schemaVersion', 'status', 'requestId', 'position', 'pollAfterSeconds', 'expiresAt', 'productionReady'))
            and value['schemaVersion'] == '1.0' and value['status'] in ('queued', 'running')
            and isinstance(value['requestId'], str) and ID.fullmatch(value['requestId'])
            and (expected_id is None or value['requestId'] == expected_id)
            and type(value['position']) is int and (value['position'] == 0 if value['status'] == 'running'
                                                    else 1 <= value['position'] <= 8)
            and type(value['pollAfterSeconds']) is int and value['pollAfterSeconds'] == 2
            and value['productionReady'] is False and isinstance(value['expiresAt'], str)
            and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', value['expiresAt']))
    expiry = datetime.fromisoformat(value['expiresAt'].replace('Z', '+00:00')).timestamp()
    require(now - 5 <= expiry <= now + LIFETIME_SECONDS + 5
            and (prior_expiry is None or value['expiresAt'] == prior_expiry))
    return value['requestId'], value['status'], value['expiresAt']


def accepted(value, request_id, catalog_ids, catalog_version):
    require(exact(value, ('schemaVersion', 'status', 'requestId', 'response', 'catalogVersion', 'policyVersion', 'productionReady'))
            and value['schemaVersion'] == '1.0' and value['status'] == 'accepted'
            and value['requestId'] == request_id and value['catalogVersion'] == catalog_version
            and value['policyVersion'] == 'recommendation-validation-v1' and value['productionReady'] is False)
    response = value['response']
    require(exact(response, ('schemaVersion', 'requestId', 'action', 'placeIds', 'requirementClaims', 'confirmationNotice', 'noticeTopics'))
            and response['schemaVersion'] == '1.0' and response['requestId'] == request_id
            and response['action'] in ('recommend', 'cannot_confirm')
            and isinstance(response['placeIds'], list) and 1 <= len(response['placeIds']) <= 5
            and all(isinstance(item, str) and item in catalog_ids for item in response['placeIds'])
            and len(set(response['placeIds'])) == len(response['placeIds'])
            and isinstance(response['requirementClaims'], list)
            and len(response['requirementClaims']) == len(response['placeIds']))
    seen, unknown = set(), False
    for claim in response['requirementClaims']:
        require(exact(claim, ('placeId', 'requirement', 'verdict')) and claim['placeId'] in response['placeIds']
                and claim['placeId'] not in seen and claim['requirement'] == 'indoor_required'
                and claim['verdict'] in ('pass', 'unknown'))
        seen.add(claim['placeId'])
        unknown = unknown or claim['verdict'] == 'unknown'
    require(response['confirmationNotice'] is unknown
            and response['noticeTopics'] == (['indoor_unverified'] if unknown else [])
            and response['action'] == ('cannot_confirm' if unknown else 'recommend'))
    return {'status': 'accepted', 'action': response['action'], 'placeCount': len(response['placeIds']),
            'confirmationNotice': response['confirmationNotice'], 'finalValidationAccepted': True}


def safe_exchange(client, *args, **kwargs):
    try:
        return client.request(*args, **kwargs)
    except Exception:
        # Includes ambiguous POST outcomes. Never retry the submission.
        return None


def memory_snapshot(deploy):
    try:
        command = ['docker', 'compose', '-p', os.environ.get('COMPOSE_PROJECT_NAME', 'oreumit-bootstrap')]
        for name in COMPOSE_FILES:
            command += ['-f', name]
        result = subprocess.run(command + ['ps', '--format', 'json', *SERVICES], cwd=deploy,
                                capture_output=True, timeout=5, check=False)
        require(result.returncode == 0 and len(result.stdout) <= 64 * 1024)
        content = result.stdout.strip()
        rows = json_value(content) if content.startswith(b'[') else [json_value(line) for line in content.splitlines()]
        require(isinstance(rows, list))
        identifiers = {row['ID']: row['Service'] for row in rows if isinstance(row, dict)
                       and row.get('Service') in SERVICES and isinstance(row.get('ID'), str)
                       and re.fullmatch(r'[a-f0-9]{12,64}', row['ID'])}
        require(set(identifiers.values()) == set(SERVICES) and len(identifiers) == 3)
        stats = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *identifiers],
                               capture_output=True, timeout=5, check=False)
        require(stats.returncode == 0 and len(stats.stdout) <= 16 * 1024)
        units = {'B': 1, 'kB': 1000, 'MB': 1000**2, 'GB': 1000**3,
                 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4}
        def mib(value):
            found = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(B|kB|MB|GB|KiB|MiB|GiB|TiB)', value.strip())
            require(found is not None)
            return round(float(found[1]) * units[found[2]] / 1024**2, 2)
        values = {}
        for line in stats.stdout.splitlines():
            row = json_value(line)
            require(isinstance(row, dict) and isinstance(row.get('ID'), str) and len(row['ID']) >= 12)
            matches = [name for identifier, name in identifiers.items() if identifier.startswith(row['ID'])]
            require(len(matches) == 1 and matches[0] not in values and isinstance(row.get('MemUsage'), str))
            parts = row['MemUsage'].split('/')
            require(len(parts) == 2)
            values[matches[0]] = {'usageMiB': mib(parts[0]), 'limitMiB': mib(parts[1])}
        require(set(values) == set(SERVICES))
        return {'available': True, 'services': values}
    except Exception:
        return {'available': False}


def run_probe(client, catalog_ids, catalog_version, *, monotonic=time.monotonic, wall=time.time,
              sleep=time.sleep, sample_memory=lambda: {'available': False}):
    run_id = secrets.token_hex(16)
    subjects = ['queue-model-' + run_id + '-' + suffix for suffix in ('a', 'b')]
    ready = safe_exchange(client, 'GET', '/readyz', 'queue-model-' + run_id + '-p')
    require(ready is not None and ready[0] == 200, 'QUEUE_MODEL_PREFLIGHT_FAILED')
    value = ready[1]
    require(isinstance(value, dict) and value.get('ready') is True and value.get('mode') == 'access'
            and value.get('flowVerified') is True and value.get('catalogPlaces') == 981
            and value.get('productionReady') is False and value.get('asyncJobs') is True
            and type(value.get('maxConcurrent')) is int and value['maxConcurrent'] == 2
            and exact(value.get('jobs'), ('queued', 'running', 'uncertain'))
            and all(type(count) is int and count == 0 for count in value['jobs'].values()),
            'QUEUE_MODEL_REQUIRES_IDLE_TWO_SLOT_GATEWAY')
    memory = {'before': sample_memory()}
    started, results, jobs = monotonic(), [None, None], [None, None]
    poll_requests, two_running = 0, False
    inputs = [{'schemaVersion': '1.0', 'timezone': 'Asia/Seoul',
               'query': f'합성 동시 처리 검사 {label}: 실내 방문 조건이 필요한 제주 장소 후보를 최대 3곳 찾아주세요. 미확인 조건은 확인 필요로 안내해 주세요.',
               'filters': {}, 'excludedIds': [], 'requirements': [{'type': 'indoor_required', 'value': True}]}
              for label in ('A', 'B')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        # No retry/fallback path exists, including an ambiguous network timeout.
        posts = [pool.submit(safe_exchange, client, 'POST', '/v1/jobs', subjects[i], inputs[i]) for i in range(2)]
        for i, future in enumerate(posts):
            response = future.result()
            try:
                require(response is not None and response[0] == 202)
                job_id, status, expiry = pending(response[1], None, wall())
                require(all(job is None or job['id'] != job_id for job in jobs))
                jobs[i] = {'id': job_id, 'status': status, 'expiry': expiry}
            except Exception:
                code = response[1].get('error', {}).get('code') if response and isinstance(response[1], dict) \
                    and isinstance(response[1].get('error'), dict) else None
                known_code = isinstance(code, str) and code in SAFE_CODES
                results[i] = {'status': 'failed' if known_code else 'unknown',
                              'code': code if known_code else 'SUBMISSION_OUTCOME_UNKNOWN'}
        while any(job is not None and results[i] is None for i, job in enumerate(jobs)):
            remaining = LIFETIME_SECONDS - (monotonic() - started)
            if remaining <= 0:
                break
            active = [i for i, job in enumerate(jobs) if job is not None and results[i] is None]
            polls = {i: pool.submit(safe_exchange, client, 'GET', '/v1/jobs/' + jobs[i]['id'], subjects[i],
                                    timeout=min(20, remaining)) for i in active}
            poll_requests += len(polls)
            running_this_round = set()
            for i, future in polls.items():
                response = future.result()
                if response is None:
                    continue  # Only a GET may be repeated after transport failure.
                status, value = response
                try:
                    if status == 202:
                        _, state, _ = pending(value, jobs[i]['id'], wall(), prior_expiry=jobs[i]['expiry'])
                        require(jobs[i]['status'] != 'running' or state == 'running')
                        jobs[i]['status'] = state
                        if state == 'running':
                            running_this_round.add(i)
                    elif status == 200:
                        results[i] = {**accepted(value, jobs[i]['id'], catalog_ids, catalog_version),
                                      'elapsedSeconds': round(monotonic() - started, 2)}
                    else:
                        code = value.get('error', {}).get('code') if isinstance(value, dict) and isinstance(value.get('error'), dict) else None
                        require(isinstance(code, str) and code in SAFE_CODES)
                        results[i] = {'status': 'failed', 'code': code,
                                      'elapsedSeconds': round(monotonic() - started, 2)}
                except Exception:
                    results[i] = {'status': 'unknown', 'code': 'RESULT_RESPONSE_INVALID'}
            if len(running_this_round) == 2:
                two_running = True
                if 'whileBothRunning' not in memory:
                    memory['whileBothRunning'] = sample_memory()
            if any(result is None for result in results):
                sleep(max(0, min(2, LIFETIME_SECONDS - (monotonic() - started))))
    for i, result in enumerate(results):
        if result is None:
            results[i] = {'status': 'unknown', 'code': 'POLL_DEADLINE_REACHED'}
    elapsed = round(monotonic() - started, 2)
    memory['after'] = sample_memory()
    accepted_count = sum(result['status'] == 'accepted' for result in results)
    return {'status': 'passed' if accepted_count == 2 and two_running else 'inconclusive' if accepted_count == 2 else 'failed',
            'scope': 'aws-two-job-model-check', 'recommendationPostAttempts': 2, 'acceptedResults': accepted_count,
            'twoRunningObserved': two_running, 'providerModelSpanOverlapVerified': False,
            'pollRequests': poll_requests, 'elapsedSeconds': elapsed, 'results': results, 'memorySamples': memory,
            'modelExecutionRequested': True, 'automaticResubmission': False, 'productionReady': False}


def main():
    parser = SafeParser(description=__doc__)
    parser.add_argument('--execute-model', action='store_true', help='Authorize exactly two recommendation submissions; provider charges may apply.')
    args = parser.parse_args()
    if not args.execute_model:
        print('{"status":"not_executed","code":"EXPLICIT_MODEL_FLAG_REQUIRED","recommendationPostAttempts":0,"modelExecutionRequested":false}')
        return 2
    phase = 'configuration'
    try:
        deploy = Path(__file__).resolve().parent
        root, key, service, ids, version = load_configuration(deploy)
        descriptor = os.open(root / '.agent-queue-model.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'wb') as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            phase = 'preflight_or_execution'
            result = run_probe(PublicClient(key, service), ids, version, sample_memory=lambda: memory_snapshot(deploy))
        print(json.dumps(result, separators=(',', ':')))
        return 0 if result['status'] == 'passed' else 1
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'failed', 'code': 'QUEUE_MODEL_CHECK_FAILED', 'phase': phase,
              'submissionOutcomeMayBeUnknown': phase == 'preflight_or_execution',
              'automaticResubmission': False, 'productionReady': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
