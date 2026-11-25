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
    """Each POST gets the next scripted outcome; accepted jobs finish after `duration` fake seconds."""
    def __init__(self, clock, outcomes, *, duration=10):
        self.clock, self.outcomes, self.duration = clock, list(outcomes), duration
        self.jobs, self.posts, self.subjects = {}, [], []

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
        if self.clock.elapsed - started < self.duration:
            return 202, self.pending(job, 'running')
        if outcome == 'accepted':
            return 200, {'schemaVersion': '1.0', 'status': 'accepted', 'requestId': job, 'catalogVersion': VERSION,
                         'policyVersion': 'recommendation-validation-v1', 'productionReady': False,
                         'response': {'schemaVersion': '1.0', 'requestId': job, 'action': 'recommend', 'placeIds': [SECRET_PLACE],
                                      'requirementClaims': [], 'confirmationNotice': False, 'noticeTopics': []}}
        return (422 if outcome == 'RECOMMENDATION_BLOCKED' else 503), {'error': {'code': outcome}}

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
