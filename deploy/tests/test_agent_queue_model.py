"""Offline two-job live-check contracts; no AWS, Docker, network or model calls."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'check-agent-queue-model.py'
SPEC = importlib.util.spec_from_file_location('queue_model_check', SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
NOW = 1795046400
KEY = 'a' * 64
SERVICE = {'CF_ACCESS_CLIENT_ID': 'b' * 32 + '.access', 'CF_ACCESS_CLIENT_SECRET': 'cfast_' + 'C' * 48}
VERSION = 'sha256:' + 'd' * 64
IDS = frozenset(('fictional-1', 'fictional-2'))
READY = {'ready': True, 'mode': 'access', 'flowVerified': True, 'catalogPlaces': 981,
         'productionReady': False, 'asyncJobs': True, 'maxConcurrent': 2,
         'jobs': {'queued': 0, 'running': 0, 'uncertain': 0}}
CANARY = 'PRIVATE_CANARY_MUST_NEVER_APPEAR'


def job_id(label):
    return 'gw-' + label * 32


def waiting(label, state='running'):
    return {'schemaVersion': '1.0', 'status': state, 'requestId': job_id(label),
            'position': 0 if state == 'running' else 1, 'pollAfterSeconds': 2,
            'expiresAt': datetime.fromtimestamp(NOW + 720, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'productionReady': False}


def accepted(label):
    place_id = 'fictional-1' if label == 'a' else 'fictional-2'
    return {'schemaVersion': '1.0', 'status': 'accepted', 'requestId': job_id(label),
            'catalogVersion': VERSION, 'policyVersion': 'recommendation-validation-v1', 'productionReady': False,
            'response': {'schemaVersion': '1.0', 'requestId': job_id(label), 'action': 'cannot_confirm',
                'placeIds': [place_id], 'requirementClaims': [{'placeId': place_id, 'requirement': 'indoor_required', 'verdict': 'unknown'}],
                'confirmationNotice': True, 'noticeTopics': ['indoor_unverified']}}


class Clock:
    def __init__(self, step=None): self.elapsed, self.step = 0, step
    def monotonic(self): return self.elapsed
    def wall(self): return NOW + self.elapsed
    def sleep(self, seconds): self.elapsed += seconds if self.step is None else self.step


class FakeClient:
    def __init__(self, *, ready=None, post_fail=None, sequences=None):
        self.ready, self.post_fail = READY if ready is None else ready, post_fail
        self.sequences = sequences or {}
        self.calls, self.polls, self.lock = [], {'a': 0, 'b': 0}, threading.Lock()
        self.barrier = threading.Barrier(2, timeout=3)

    def request(self, method, path, subject, payload=None, **kwargs):
        with self.lock:
            self.calls.append((method, path, subject, payload))
        label = subject[-1]
        if path == '/readyz': return 200, self.ready
        if method == 'POST':
            self.barrier.wait()  # Both submissions must start independently.
            if self.post_fail == label: raise TimeoutError(CANARY)
            return 202, waiting(label)
        assert method == 'GET' and path == '/v1/jobs/' + job_id(label)
        with self.lock:
            index = self.polls[label]
            self.polls[label] += 1
        sequence = self.sequences.get(label, [(202, waiting(label)), (200, accepted(label))])
        result = sequence[min(index, len(sequence) - 1)]
        if isinstance(result, Exception): raise result
        return result


def run(client, clock=None):
    clock = clock or Clock()
    return probe.run_probe(client, IDS, VERSION, monotonic=clock.monotonic, wall=clock.wall, sleep=clock.sleep)


class TwoJobTests(unittest.TestCase):
    def assert_post_bound(self, client):
        posts = [call for call in client.calls if call[0] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertEqual({call[1] for call in posts}, {'/v1/jobs'})
        self.assertEqual(len({call[2] for call in posts}), 2)
        self.assertTrue(all(call[0] in ('GET', 'POST') for call in client.calls))
        for method, path, subject, payload in client.calls:
            self.assertTrue(probe.SUBJECT.fullmatch(subject))
            if method == 'GET': self.assertIsNone(payload)
            if path.startswith('/v1/jobs/'): self.assertEqual(path, '/v1/jobs/' + job_id(subject[-1]))

    def test_two_parallel_posts_then_owner_bound_polls_validate_both_results(self):
        client = FakeClient()
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['acceptedResults'], 2)
        self.assertTrue(result['twoRunningObserved'])
        self.assertFalse(result['providerModelSpanOverlapVerified'])
        self.assertFalse(result['automaticResubmission'])
        self.assertEqual(result['pollRequests'], 4)
        self.assertNotIn('fictional-', json.dumps(result))
        self.assertNotIn('gw-', json.dumps(result))

    def test_ambiguous_first_submission_does_not_retry_or_abandon_other_job(self):
        client = FakeClient(post_fail='a')
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['acceptedResults'], 1)
        self.assertEqual(result['results'][0]['code'], 'SUBMISSION_OUTCOME_UNKNOWN')
        self.assertEqual(result['results'][1]['status'], 'accepted')
        self.assertEqual(client.polls, {'a': 0, 'b': 2})
        self.assertNotIn(CANARY, json.dumps(result))

    def test_terminal_failure_still_polls_other_job_to_completion(self):
        client = FakeClient(sequences={'a': [(422, {'error': {'code': 'RECOMMENDATION_BLOCKED', 'secret': CANARY}})]})
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['results'][0]['code'], 'RECOMMENDATION_BLOCKED')
        self.assertEqual(result['results'][1]['status'], 'accepted')
        self.assertEqual(client.polls, {'a': 1, 'b': 2})
        self.assertNotIn(CANARY, json.dumps(result))

    def test_get_transport_error_can_repeat_read_but_never_submission(self):
        client = FakeClient(sequences={'a': [TimeoutError(CANARY), (200, accepted('a'))]})
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['acceptedResults'], 2)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertFalse(result['twoRunningObserved'])

    def test_fast_success_without_two_running_observation_is_inconclusive(self):
        client = FakeClient(sequences={label: [(200, accepted(label))] for label in ('a', 'b')})
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['acceptedResults'], 2)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertFalse(result['twoRunningObserved'])

    def test_running_to_queued_regression_is_rejected_and_other_job_finishes(self):
        client = FakeClient(sequences={'a': [(202, waiting('a', 'queued'))]})
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['results'][0]['code'], 'RESULT_RESPONSE_INVALID')
        self.assertEqual(result['results'][1]['status'], 'accepted')
        self.assertFalse(result['twoRunningObserved'])

    def test_poll_deadline_bounds_reads_and_never_resubmits(self):
        client = FakeClient(sequences={label: [TimeoutError(CANARY)] for label in ('a', 'b')})
        result = run(client, Clock(step=180))
        self.assert_post_bound(client)
        self.assertEqual(result['elapsedSeconds'], 720)
        self.assertEqual(result['pollRequests'], 8)
        self.assertTrue(all(item['code'] == 'POLL_DEADLINE_REACHED' for item in result['results']))

    def test_busy_old_or_wrong_capacity_gateway_never_receives_post(self):
        for overrides in ({'asyncJobs': False}, {'maxConcurrent': 1}, {'maxConcurrent': True},
                          {'jobs': {'queued': 1, 'running': 0, 'uncertain': 0}},
                          {'jobs': {'queued': 0, 'running': 0, 'uncertain': 1}}, {'productionReady': True}):
            client = FakeClient(ready={**READY, **overrides})
            with self.subTest(overrides=overrides), self.assertRaises(probe.ProbeError): run(client)
            self.assertFalse(any(call[0] == 'POST' for call in client.calls))

    def test_changed_id_catalog_unknown_place_and_missing_notice_are_rejected(self):
        corruptions = [lambda value: value.update(requestId=job_id('b')),
                       lambda value: value.update(catalogVersion='sha256:' + '0' * 64),
                       lambda value: value['response'].update(placeIds=['not-in-catalog']),
                       lambda value: value['response'].update(confirmationNotice=False),
                       lambda value: value['response'].update(privateProse=CANARY)]
        for corrupt in corruptions:
            value = accepted('a'); corrupt(value)
            with self.assertRaises(probe.ProbeError): probe.accepted(value, job_id('a'), IDS, VERSION)

    def test_malformed_error_code_cannot_escape_or_abandon_second_job(self):
        client = FakeClient(sequences={'a': [(503, {'error': {'code': [CANARY]}})]})
        result = run(client)
        self.assert_post_bound(client)
        self.assertEqual(result['results'][0]['code'], 'RESULT_RESPONSE_INVALID')
        self.assertEqual(result['results'][1]['status'], 'accepted')
        self.assertNotIn(CANARY, json.dumps(result))


class TransportTests(unittest.TestCase):
    def test_fixed_https_endpoint_and_fresh_signed_owner_bound_gets(self):
        calls = []
        class Response:
            status, headers = 202, {'Content-Type': 'application/json'}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda _value: None)))
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read1(self, _limit): return json.dumps(waiting('a')).encode()
            def isclosed(self): return True
        class Opener:
            def open(self, request, timeout): calls.append(request); return Response()
        nonces = iter(('1' * 32, '2' * 32, '3' * 32))
        client = probe.PublicClient(KEY, SERVICE, wall=lambda: NOW, nonce=lambda: next(nonces), opener=Opener())
        subject = 'queue-model-' + '0' * 32 + '-a'
        payload = {'test': '합성'}
        client.request('POST', '/v1/jobs', subject, payload)
        client.request('GET', '/v1/jobs/' + job_id('a'), subject)
        client.request('GET', '/v1/jobs/' + job_id('a'), subject)
        for request in calls:
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(request.full_url, 'https://agent.oreumit.com' + request.selector)
            self.assertEqual(headers['cf-access-client-id'], SERVICE['CF_ACCESS_CLIENT_ID'])
            self.assertEqual(headers['cf-access-client-secret'], SERVICE['CF_ACCESS_CLIENT_SECRET'])
            self.assertNotIn('cookie', headers); self.assertNotIn('origin', headers)
            canonical = '\n'.join((request.method, request.selector, subject, str(NOW),
                headers['x-jeju-nonce'], hashlib.sha256(request.data or b'').hexdigest()))
            self.assertEqual(headers['x-jeju-signature'], hmac.new(KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest())
        self.assertEqual(len({dict((key.lower(), value) for key, value in request.header_items())['x-jeju-nonce'] for request in calls}), 3)
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid'))
        with self.assertRaises(probe.ProbeError): client.request('DELETE', '/v1/jobs/' + job_id('a'), subject)
        with self.assertRaises(probe.ProbeError): client.request('POST', '/v1/recommend', subject, payload)

    def test_duplicate_json_and_extra_pending_fields_are_rejected(self):
        with self.assertRaises(probe.ProbeError): probe.json_value(b'{"status":1,"status":2}')
        with self.assertRaises(probe.ProbeError): probe.pending({**waiting('a'), 'secret': CANARY}, None, NOW)
        with self.assertRaises(probe.ProbeError): probe.pending(waiting('a'), job_id('b'), NOW)

    def test_trickling_response_has_total_deadline_and_each_read_uses_remaining_budget(self):
        clock, timeouts = Clock(), []
        class Trickle:
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))
            def read1(self, _limit): clock.elapsed += 1; return b' '
            def isclosed(self): return False
        with self.assertRaisesRegex(probe.ProbeError, 'QUEUE_MODEL_RESPONSE_TIMEOUT'):
            probe.bounded_body(Trickle(), 3, monotonic=clock.monotonic)
        self.assertEqual(clock.elapsed, 3)
        self.assertEqual(timeouts, [3, 2, 1])


class CliTests(unittest.TestCase):
    def test_without_explicit_flag_does_not_read_credentials_or_make_requests(self):
        output = io.StringIO()
        with patch.object(probe.sys, 'argv', ['check']), patch.object(probe, 'load_configuration') as load, \
                patch.object(probe, 'run_probe') as run_mock, redirect_stdout(output):
            self.assertEqual(probe.main(), 2)
        load.assert_not_called(); run_mock.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['recommendationPostAttempts'], 0)

    def test_fixed_cli_failures_do_not_print_exception_or_secret_arguments(self):
        errors = io.StringIO()
        with patch.object(probe.sys, 'argv', ['check', '--execute-model']), \
                patch.object(probe, 'load_configuration', side_effect=ValueError(CANARY)), redirect_stderr(errors):
            self.assertEqual(probe.main(), 1)
        self.assertNotIn(CANARY, errors.getvalue())
        errors = io.StringIO()
        with patch.object(probe.sys, 'argv', ['check', '--wrong', CANARY]), redirect_stderr(errors), self.assertRaises(SystemExit):
            probe.main()
        self.assertNotIn(CANARY, errors.getvalue())

    def test_memory_summary_only_contains_fixed_service_names_and_numbers(self):
        ids = [str(index) * 64 for index in (1, 2, 3)]
        ps = [{'ID': identifier, 'Service': service, 'Names': CANARY} for identifier, service in zip(ids, probe.SERVICES)]
        stats = b'\n'.join(json.dumps({'ID': identifier[:12], 'MemUsage': '128MiB / 1GiB', 'Name': CANARY}).encode() for identifier in ids)
        with patch.object(probe.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=json.dumps(ps).encode()), SimpleNamespace(returncode=0, stdout=stats)]) as process:
            result = probe.memory_snapshot(Path('/unused'))
        self.assertTrue(result['available'])
        self.assertEqual(set(result['services']), set(probe.SERVICES))
        self.assertEqual(result['services']['langflow'], {'usageMiB': 128.0, 'limitMiB': 1024.0})
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertNotIn('exec', process.call_args_list[0].args[0])
        self.assertEqual(process.call_args_list[1].args[0][:3], ['docker', 'stats', '--no-stream'])


if __name__ == '__main__':
    unittest.main()
