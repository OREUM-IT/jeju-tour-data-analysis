"""Offline contracts of the Gateway evaluation collector against a fake Gateway; no AWS, network or model calls."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'collect-gateway-eval.py'
SPEC = importlib.util.spec_from_file_location('collect_gateway_eval', SOURCE)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
NOW = 1795046400
VERSION = 'sha256:' + 'd' * 64
SECRET_PLACE = 'place-PRIVATE-CANARY'


def edge(status):
    return status, {'cloudflare_error': True, 'status': status, 'title': 'Edge error', 'retryable': True}


EDGE_502 = edge(502)
CODE_STATUS = {'RECOMMENDATION_BLOCKED': 422, 'JOB_NOT_FOUND': 404, 'JOB_EXPIRED': 410}


class Clock:
    def __init__(self): self.elapsed = 0.0
    def monotonic(self): return self.elapsed
    def wall(self): return NOW + self.elapsed
    def sleep(self, seconds): self.elapsed += seconds


def entry(index):
    return {'caseId': 'case-%03d' % index, 'requestSha256': 'r' * 64,
            'runtimeRequest': {'requestId': 'baseline-%08d' % index, 'bodySha256': 'b' * 64, 'preparedRequestSha256': 'p' * 64},
            'payload': {'schemaVersion': '1.0', 'query': '질의 %d' % index, 'timezone': 'Asia/Seoul', 'visitDate': '2026-12-07',
                        'filters': {}, 'excludedIds': [], 'requirements': []}}


class FakeGateway:
    """Each POST gets the next scripted outcome; accepted jobs finish after `duration` fake seconds.

    'hidden' finishes with a stored 502 whose body the edge replaces until the result is dropped after 300 s (then 404).
    'hidden-<status>' answers that edge status instead (504: a stored timeout; 503 or 530: an edge outage that
    outlasts the stored result).
    """
    def __init__(self, clock, outcomes, *, duration=10):
        self.clock, self.outcomes, self.duration = clock, list(outcomes), duration
        self.jobs, self.posts, self.subjects = {}, [], []
        self.edge_failures, self.edge_reads, self.lost_reads_when_gone = 0, 0, 0

    def request(self, method, path, subject, payload=None, *, timeout=20):
        assert collector.SUBJECT.fullmatch(subject), subject
        if path == '/readyz':
            return 200, {'ready': True, 'mode': 'access', 'flowVerified': True, 'catalogPlaces': 981, 'productionReady': False,
                         'asyncJobs': True, 'maxConcurrent': 2, 'jobs': {'queued': 0, 'running': 0, 'uncertain': 0}}
        if method == 'POST':
            self.posts.append(payload['query'])
            self.subjects.append(subject)
            outcome = self.outcomes.pop(0)
            if outcome == 'ambiguous':
                raise OSError('connection reset after send')
            if outcome in ('RATE_LIMITED', 'BUSY'):
                return 429, {'error': {'code': outcome}}
            job = 'gw-%032x' % len(self.posts)
            self.jobs[job] = (outcome, self.clock.elapsed, collector.iso(self.clock.wall() + 600))
            return 202, self.pending(job, 'running')
        job = path.rsplit('/', 1)[1]
        outcome, started, _ = self.jobs[job]
        if self.edge_failures:
            self.edge_failures -= 1
            self.edge_reads += 1
            return EDGE_502
        if self.clock.elapsed - started < self.duration:
            return 202, self.pending(job, 'running')
        if outcome.startswith('hidden'):
            if self.clock.elapsed - started < self.duration + 300:
                self.edge_reads += 1
                return edge(int(outcome.partition('-')[2] or 502))
            if self.lost_reads_when_gone:
                self.lost_reads_when_gone -= 1
                raise OSError('read timed out')
            return 404, {'error': {'code': 'JOB_NOT_FOUND'}}
        if outcome == 'odd':
            return 200, {'schemaVersion': '1.0', 'status': 'accepted', 'requestId': job, 'catalogVersion': VERSION,
                         'policyVersion': 'recommendation-validation-v1', 'productionReady': False, 'extra': SECRET_PLACE,
                         'response': {'schemaVersion': '1.0', 'requestId': job, 'action': 'recommend', 'placeIds': [SECRET_PLACE],
                                      'requirementClaims': [], 'confirmationNotice': False, 'noticeTopics': []}}
        if outcome == 'accepted':
            return 200, {'schemaVersion': '1.0', 'status': 'accepted', 'requestId': job, 'catalogVersion': VERSION,
                         'policyVersion': 'recommendation-validation-v1', 'productionReady': False,
                         'response': {'schemaVersion': '1.0', 'requestId': job, 'action': 'recommend', 'placeIds': [SECRET_PLACE],
                                      'requirementClaims': [], 'confirmationNotice': False, 'noticeTopics': []}}
        return CODE_STATUS.get(outcome, 503), {'error': {'code': outcome}}

    def pending(self, job, state):
        expiry = self.jobs[job][2]
        return {'schemaVersion': '1.0', 'status': state, 'requestId': job, 'position': 0, 'pollAfterSeconds': 2,
                'expiresAt': expiry, 'productionReady': False}


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.results = Path(self.temp.name) / 'gpt-results.jsonl'
        self.clock = Clock()

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, gateway, entries, **kwargs):
        out = io.StringIO()
        result = collector.run(gateway, entries, self.results, monotonic=self.clock.monotonic, wall=self.clock.wall,
                               sleep=self.clock.sleep, out=out, **kwargs)
        return result, out.getvalue()

    def lines(self):
        return [json.loads(line) for line in self.results.read_text('utf-8').splitlines()]

    def test_accepted_blocked_refused_and_ambiguous(self):
        gateway = FakeGateway(self.clock, ['accepted', 'RECOMMENDATION_BLOCKED', 'RATE_LIMITED', 'accepted', 'ambiguous', 'accepted'])
        result, out = self.collect(gateway, [entry(i) for i in range(5)], budget=45)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['outcomesThisRun'], {'accepted': 3, 'failed:RECOMMENDATION_BLOCKED': 1, 'unknown:SUBMISSION_OUTCOME_UNKNOWN': 1})
        # The refused case is sent again, the ambiguous one is not.
        self.assertEqual(gateway.posts, ['질의 0', '질의 1', '질의 2', '질의 2', '질의 3', '질의 4'])
        self.assertEqual(result['submissionsThisRun'], 6)
        records = self.lines()
        self.assertEqual([r['caseId'] for r in records], ['case-000', 'case-001', 'case-002', 'case-003', 'case-004'])
        self.assertEqual(records[0]['response']['placeIds'], [SECRET_PLACE])
        self.assertEqual(records[0]['gateway']['catalogVersion'], VERSION)
        self.assertEqual(records[0]['runtimeRequestId'], 'baseline-00000000')
        self.assertGreaterEqual(records[0]['totalSeconds'], 10)
        self.assertNotIn(SECRET_PLACE, out)
        self.assertNotIn('질의', out)
        self.assertEqual(stat.S_IMODE(os.stat(self.results).st_mode), 0o600)

    def test_resume_skips_recorded_cases_and_limit(self):
        gateway = FakeGateway(self.clock, ['accepted'] * 4)
        result, _ = self.collect(gateway, [entry(i) for i in range(4)], budget=45, limit=2)
        self.assertEqual((result['status'], result['casesRemaining']), ('partial', 2))
        result, _ = self.collect(gateway, [entry(i) for i in range(4)], budget=45)
        self.assertEqual((result['status'], result['casesRemaining']), ('completed', 0))
        self.assertEqual(gateway.posts, ['질의 0', '질의 1', '질의 2', '질의 3'])

    def test_hourly_budget_paces_submissions(self):
        gateway = FakeGateway(self.clock, ['accepted'] * 3, duration=2)
        self.collect(gateway, [entry(i) for i in range(3)], budget=2)
        submitted = [json.loads(line)['submittedAt'] for line in self.results.read_text('utf-8').splitlines()]
        self.assertGreaterEqual(collector.datetime.fromisoformat(submitted[2].replace('Z', '+00:00')).timestamp() - NOW, 3600)

    def test_consecutive_infrastructure_failures_stop(self):
        gateway = FakeGateway(self.clock, ['GATEWAY_UNAVAILABLE'] * 3 + ['accepted'])
        result, _ = self.collect(gateway, [entry(i) for i in range(4)], budget=45)
        self.assertEqual((result['status'], result['stoppedReason']), ('stopped', 'CONSECUTIVE_INFRASTRUCTURE_FAILURES'))
        self.assertEqual(len(gateway.posts), 3)

    def test_unexpected_reply_is_described_without_values_and_can_be_retried(self):
        gateway = FakeGateway(self.clock, ['odd', 'accepted'])
        result, out = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['outcomesThisRun'], {'unknown:RESULT_RESPONSE_INVALID': 1})
        detail = self.lines()[0]['diagnostic']
        self.assertEqual(detail['http'], 200)
        self.assertIn('extra', detail['bodyKeys'])
        self.assertEqual((detail['status'], detail['action'], detail['placeCount'], detail['requestIdMatches']), ('accepted', 'recommend', 1, True))
        self.assertEqual(detail['reason'], 'RESULT_RESPONSE_INVALID')
        self.assertNotIn(SECRET_PLACE, out)
        self.assertNotIn(SECRET_PLACE, json.dumps(detail))
        result, _ = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['submissionsThisRun'], 0)
        result, _ = self.collect(gateway, [entry(0)], budget=45, retry_unknown=True)
        self.assertEqual(result['outcomesThisRun'], {'accepted': 1})
        self.assertEqual(len(gateway.posts), 2)

    def test_edge_error_on_a_read_is_read_again_not_recorded(self):
        gateway = FakeGateway(self.clock, ['accepted', 'GATEWAY_UNAVAILABLE'])
        gateway.edge_failures = 2
        result, _ = self.collect(gateway, [entry(0), entry(1)], budget=45)
        records = self.lines()
        self.assertEqual(result['outcomesThisRun'], {'accepted': 1, 'failed:GATEWAY_UNAVAILABLE': 1})
        self.assertEqual(records[0]['edgeRetries'], 2)
        # A Gateway error code on the same status is still a result, and POSTs are never repeated.
        self.assertEqual(records[1]['code'], 'GATEWAY_UNAVAILABLE')
        self.assertEqual(len(gateway.posts), 2)

    def test_result_hidden_by_the_edge_until_it_is_dropped_is_failed_not_unknown(self):
        gateway = FakeGateway(self.clock, ['hidden'])
        result, out = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['outcomesThisRun'], {'failed:RESULT_HIDDEN_BY_EDGE': 1})
        record = self.lines()[0]
        self.assertEqual((record['final'], record['code'], record['edgeStatus'], record['gatewayHttp']),
                         ('failed', 'RESULT_HIDDEN_BY_EDGE', 502, 404))
        self.assertEqual(record['edgeRetries'], gateway.edge_reads)
        self.assertEqual(gateway.edge_reads, 60)
        self.assertGreaterEqual(record['totalSeconds'], 300)
        self.assertIn('failed:RESULT_HIDDEN_BY_EDGE', out)
        # Recorded as failed, so --retry-unknown does not send it again.
        result, _ = self.collect(gateway, [entry(0)], budget=45, retry_unknown=True)
        self.assertEqual(result['submissionsThisRun'], 0)
        self.assertEqual(len(gateway.posts), 1)

    def test_lost_read_between_edge_reads_and_the_dropped_job_does_not_clear_them(self):
        gateway = FakeGateway(self.clock, ['hidden'])
        gateway.lost_reads_when_gone = 1
        self.collect(gateway, [entry(0)], budget=45)
        record = self.lines()[0]
        self.assertEqual((record['code'], record['edgeStatus'], record['edgeRetries']), ('RESULT_HIDDEN_BY_EDGE', 502, 60))

    def test_only_edge_502_or_504_before_the_dropped_job_mark_it_hidden(self):
        # 503 and 530 come from the edge itself and hide no stored result: the job stays JOB_NOT_FOUND.
        gateway = FakeGateway(self.clock, ['hidden-504', 'accepted', 'hidden-530', 'accepted', 'hidden-503'])
        self.collect(gateway, [entry(i) for i in range(5)], budget=45)
        records = self.lines()
        self.assertEqual([(r['code'], r.get('edgeStatus'), r['gatewayHttp'], r['edgeRetries']) for r in records[0::2]],
                         [('RESULT_HIDDEN_BY_EDGE', 504, 404, 60), ('JOB_NOT_FOUND', None, 404, 60),
                          ('JOB_NOT_FOUND', None, 404, 60)])

    def test_edge_read_just_before_a_queue_expiry_stays_job_expired(self):
        # 410 JOB_EXPIRED is the Gateway's own visible answer for a job that never ran; a dropped result answers 404.
        gateway = FakeGateway(self.clock, ['JOB_EXPIRED'], duration=0)
        gateway.edge_failures = 1
        result, _ = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['outcomesThisRun'], {'failed:JOB_EXPIRED': 1})
        record = self.lines()[0]
        self.assertEqual((record['gatewayHttp'], record['edgeRetries']), (410, 1))
        self.assertNotIn('edgeStatus', record)

    def test_hidden_result_that_outlives_the_collector_deadline_is_failed_not_unknown(self):
        # The job ends 430 s after submission, so its stored 502 stays until 730 s, past the 720 s deadline.
        gateway = FakeGateway(self.clock, ['hidden'], duration=430)
        result, _ = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['outcomesThisRun'], {'failed:RESULT_HIDDEN_BY_EDGE': 1})
        record = self.lines()[0]
        self.assertEqual((record['edgeStatus'], record['edgeRetries'], record['totalSeconds']), (502, gateway.edge_reads, 720))
        self.assertNotIn('gatewayHttp', record)
        result, _ = self.collect(gateway, [entry(0)], budget=45, retry_unknown=True)
        self.assertEqual(result['submissionsThisRun'], 0)

    def test_edge_read_then_normal_polls_then_not_found_stays_job_not_found(self):
        gateway = FakeGateway(self.clock, ['JOB_NOT_FOUND'])
        gateway.edge_failures = 1
        result, _ = self.collect(gateway, [entry(0)], budget=45)
        self.assertEqual(result['outcomesThisRun'], {'failed:JOB_NOT_FOUND': 1})
        record = self.lines()[0]
        self.assertEqual((record['gatewayHttp'], record['edgeRetries']), (404, 1))
        self.assertNotIn('edgeStatus', record)

    def test_requires_explicit_flag(self):
        out = io.StringIO()
        with patch('sys.argv', ['collect-gateway-eval.py', '--label', 'gpt']), redirect_stdout(out):
            self.assertEqual(collector.main(), 2)
        self.assertEqual(json.loads(out.getvalue())['code'], 'EXPLICIT_MODEL_FLAG_REQUIRED')

    def test_requests_file_shape(self):
        path = Path(self.temp.name) / 'gpt-requests.json'
        path.write_text(json.dumps([entry(0), entry(0)]), 'utf-8')
        with self.assertRaises(collector.ProbeError):
            collector.read_requests(path)
        path.write_text(json.dumps([entry(0)]), 'utf-8')
        self.assertEqual(len(collector.read_requests(path)), 1)


if __name__ == '__main__':
    unittest.main()
