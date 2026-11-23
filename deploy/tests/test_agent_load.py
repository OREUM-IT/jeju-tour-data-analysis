"""Offline bounded load-check contracts against a fake Gateway; no AWS, Docker, network or model calls."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'check-agent-load.py'
SPEC = importlib.util.spec_from_file_location('agent_load_check', SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
NOW = 1795046400
KEY = 'a' * 64
SERVICE = {'CF_ACCESS_CLIENT_ID': 'b' * 32 + '.access', 'CF_ACCESS_CLIENT_SECRET': 'cfast_' + 'C' * 48}
VERSION = 'sha256:' + 'd' * 64
CANARY = 'PRIVATE_CANARY_MUST_NEVER_APPEAR'
MAX_RUNNING, MAX_QUEUED = 2, 8


def job_id(index):
    return 'gw-%032x' % index


class Clock:
    def __init__(self, step=None): self.elapsed, self.step = 0, step
    def monotonic(self): return self.elapsed
    def wall(self): return NOW + self.elapsed
    def sleep(self, seconds): self.elapsed += seconds if self.step is None else self.step


class FakeGateway:
    """Two running, eight waiting, first in first out; fake time only moves when the check sleeps."""
    def __init__(self, clock, *, duration=10, ready=None, post=None, results=None, reads=None, foreign=None,
                 ready_status=200):
        self.clock, self.duration, self.ready, self.ready_status = clock, duration, ready or {}, ready_status
        self.post, self.results, self.reads = post or {}, results or {}, reads or {}
        self.foreign = foreign  # (start, end): another user's job is running in that fake-time window
        self.jobs, self.calls, self.lock = {}, [], threading.Lock()
        self.system_at_post, self.most_running, self.most_queued, self.read_counts = [], 0, 0, {}

    def settle(self):
        now = self.clock.elapsed
        while True:
            running = [job for job in self.jobs.values() if job['state'] == 'running']
            due = sorted((job for job in running if job['started'] + job['duration'] <= now),
                         key=lambda job: job['started'] + job['duration'])
            waiting = [job for job in self.jobs.values() if job['state'] == 'queued']
            if due:
                due[0]['state'] = 'done'
                if waiting:
                    waiting[0].update(state='running', started=due[0]['started'] + due[0]['duration'])
            elif waiting and len(running) < MAX_RUNNING:
                waiting[0].update(state='running', started=now)
            else:
                return

    def counts(self):
        states = [job['state'] for job in self.jobs.values()]
        return states.count('running'), states.count('queued')

    def pending(self, index):
        job = self.jobs[index]
        waiting = [key for key, item in self.jobs.items() if item['state'] == 'queued']
        return {'schemaVersion': '1.0', 'status': job['state'], 'requestId': job_id(index),
                'position': 0 if job['state'] == 'running' else waiting.index(index) + 1, 'pollAfterSeconds': 2,
                'expiresAt': datetime.fromtimestamp(job['expires'], timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                'productionReady': False}

    def accepted(self, index):
        return {'schemaVersion': '1.0', 'status': 'accepted', 'requestId': job_id(index), 'catalogVersion': VERSION,
                'policyVersion': 'recommendation-validation-v1', 'productionReady': False,
                'response': {'schemaVersion': '1.0', 'requestId': job_id(index), 'action': 'cannot_confirm',
                             'placeIds': ['fictional-place-1', 'fictional-place-2'], 'confirmationNotice': True,
                             'requirementClaims': [{'placeId': 'fictional-place-1', 'note': CANARY}],
                             'noticeTopics': ['indoor_unverified']}}

    def request(self, method, path, subject, payload=None, **kwargs):
        with self.lock:
            self.calls.append((method, path, subject, payload))
            self.settle()
            running, queued = self.counts()
            if path == '/readyz':
                other = 1 if self.foreign and self.foreign[0] <= self.clock.elapsed < self.foreign[1] else 0
                return self.ready_status, {'ready': True, 'mode': 'access', 'flowVerified': True, 'catalogPlaces': 981,
                             'productionReady': False, 'asyncJobs': True, 'maxConcurrent': 2,
                             'jobs': {'queued': queued, 'running': running + other, 'uncertain': 0}, **self.ready}
            index = int(subject[-2:])
            if method == 'POST':
                assert path == '/v1/jobs' and index not in self.jobs
                self.system_at_post.append(running + queued)
                behaviour = self.post.get(index)
                if isinstance(behaviour, Exception): raise behaviour
                if callable(behaviour): return behaviour()
                if behaviour is not None: return behaviour
                if queued >= MAX_QUEUED: return 429, {'error': {'code': 'QUEUE_FULL'}}
                self.jobs[index] = {'state': 'queued', 'started': None, 'duration': self.duration,
                                    'expires': NOW + self.clock.elapsed + 720}
                self.settle()
                self.most_running = max(self.most_running, self.counts()[0])
                self.most_queued = max(self.most_queued, self.counts()[1])
                return 202, self.pending(index)
            assert method == 'GET' and path == '/v1/jobs/' + job_id(index)
            count = self.read_counts[index] = self.read_counts.get(index, 0) + 1
            failure = self.reads.get(index, {}).get(count)
            if failure is not None: raise failure
            if self.jobs[index]['state'] != 'done': return 202, self.pending(index)
            return self.results.get(index, (200, self.accepted(index)))


def received(fake):
    counts = {}
    for method, path, _subject, _payload in fake.calls:
        kind = method + ' ' + ('/v1/jobs/<id>' if path.startswith('/v1/jobs/') else path)
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def run(gateway, clock, requests=10, wave_size=6, **kwargs):
    return probe.run_load(gateway, requests, wave_size, monotonic=clock.monotonic, wall=clock.wall,
                          sleep=clock.sleep, **kwargs)


def gateway(**kwargs):
    clock = Clock(kwargs.pop('step', None))
    return FakeGateway(clock, **kwargs), clock


class LoadTests(unittest.TestCase):
    def assert_bounded(self, fake, posts, result):
        submitted = [call for call in fake.calls if call[0] == 'POST']
        self.assertEqual(len(submitted), posts)
        # The reported counts are what the fake Gateway received, by kind; nothing else was sent.
        self.assertEqual(result['requestCounts'], received(fake))
        self.assertEqual(set(result['requestCounts']) - {'GET /readyz', 'GET /v1/jobs/<id>', 'POST /v1/jobs'}, set())
        self.assertEqual(result['recommendationPostAttempts'], posts)
        self.assertIs(result['modelExecutionRequested'], posts > 0)
        self.assertIs(result['automaticResubmission'], False)
        self.assertEqual(result['pollRequests'], result['requestCounts'].get('GET /v1/jobs/<id>', 0))
        self.assertLessEqual(len(submitted), probe.MAX_REQUESTS)
        self.assertEqual({call[1] for call in submitted} - {'/v1/jobs'}, set())
        self.assertEqual(len({call[2] for call in submitted}), len(submitted))  # one POST per subject, no resubmission
        self.assertEqual({call[0] for call in fake.calls} - {'GET', 'POST'}, set())  # never DELETE (cancel)
        self.assertLessEqual(fake.most_running, MAX_RUNNING)
        self.assertLessEqual(fake.most_queued, MAX_QUEUED)
        for method, path, subject, payload in fake.calls:
            self.assertTrue(probe.SUBJECT.fullmatch(subject))
            if method == 'GET': self.assertIsNone(payload)

    def test_default_ten_requests_run_in_two_waves_from_an_idle_gateway(self):
        fake, clock = gateway()
        result = run(fake, clock)
        self.assert_bounded(fake, 10, result)
        self.assertEqual((result['status'], result['stoppedReason'], result['wavesStarted']), ('completed', None, 2))
        self.assertEqual((result['recommendationPostAttempts'], result['acceptedResults']), (10, 10))
        self.assertEqual(result['outcomes'], {'accepted': 10})
        self.assertEqual(fake.system_at_post, [0, 1, 2, 3, 4, 5, 0, 1, 2, 3])  # the second wave began idle
        self.assertEqual((fake.most_running, fake.most_queued), (2, 4))
        self.assertEqual((result['maxRunningObserved'], result['maxQueuedObserved']), (2, 4))
        self.assertEqual([item['wave'] for item in result['requests']], [1] * 6 + [2] * 4)
        self.assertEqual([item['queueWaitSeconds'] for item in result['requests'][:6]], [0, 0, 10, 10, 20, 20])
        self.assertTrue(all(item['runSeconds'] == 10 and item['submitHttp'] == 202 and item['finalHttp'] == 200
                            and item['action'] == 'cannot_confirm' and item['placeCount'] == 2 for item in result['requests']))
        self.assertEqual(result['timing']['totalSeconds'], {'count': 10, 'p50': 20.0, 'p95': 30.0, 'max': 30})
        self.assertEqual(result['timing']['runSeconds']['count'], 10)
        self.assertEqual(result['requestCounts'], {'GET /readyz': 2, 'GET /v1/jobs/<id>': result['pollRequests'], 'POST /v1/jobs': 10})
        self.assertFalse(result['productionReady'])
        text = json.dumps(result, ensure_ascii=False)
        for forbidden in ('gw-', 'fictional-', 'load-check-', CANARY, '합성', KEY, SERVICE['CF_ACCESS_CLIENT_SECRET']):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set().union(*(set(item) for item in result['requests'])),
                         {'wave', 'submitHttp', 'final', 'finalHttp', 'totalSeconds', 'queueWaitSeconds',
                          'runSeconds', 'action', 'placeCount'})

    def test_twenty_requests_in_full_waves_never_exceed_two_running_and_eight_waiting(self):
        fake, clock = gateway(duration=4)
        result = run(fake, clock, 20, 10)
        self.assert_bounded(fake, 20, result)
        self.assertEqual((result['status'], result['acceptedResults'], result['wavesStarted']), ('completed', 20, 2))
        self.assertEqual((fake.most_running, fake.most_queued), (2, 8))
        self.assertEqual(fake.system_at_post[10], 0)
        self.assertNotIn('failed:QUEUE_FULL', result['outcomes'])

    def test_request_and_wave_limits_are_hard(self):
        for requests, wave in ((0, 6), (21, 6), (10, 0), (10, 11), (True, 6), ('10', 6), (10.0, 6)):
            fake, clock = gateway()
            with self.subTest(requests=requests, wave=wave), self.assertRaisesRegex(probe.ProbeError, 'LOAD_CHECK_LIMIT_INVALID'):
                run(fake, clock, requests, wave)
            self.assertEqual(fake.calls, [])
        self.assertEqual((probe.DEFAULT_REQUESTS, probe.MAX_REQUESTS, probe.MAX_WAVE), (10, 20, 10))

    def test_busy_or_unexpected_gateway_never_receives_a_post(self):
        for overrides in ({'asyncJobs': False}, {'maxConcurrent': 1}, {'maxConcurrent': 4}, {'maxConcurrent': True},
                          {'jobs': {'queued': 1, 'running': 0, 'uncertain': 0}},
                          {'jobs': {'queued': 0, 'running': 1, 'uncertain': 0}},
                          {'jobs': {'queued': 0, 'running': 0, 'uncertain': 1}},
                          {'jobs': {'queued': 0, 'running': 0}}, {'productionReady': True}, {'flowVerified': False},
                          {'ready': False}, {'mode': 'private'}, {'catalogPlaces': 980}, {'catalogPlaces': '981'},
                          {'jobs': {'queued': 0, 'running': 0, 'uncertain': 0, 'other': 0}},
                          {'jobs': {'queued': 0, 'running': -1, 'uncertain': 0}}, {'status': 503}, {'status': 404}):
            overrides = dict(overrides)
            fake, clock = gateway(ready_status=overrides.pop('status', 200), ready=overrides)
            started, stamped = [], []
            monitor = SimpleNamespace(start=lambda: started.append(1), stop=lambda: {})
            with self.subTest(overrides=overrides), self.assertRaisesRegex(probe.ProbeError, 'LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY'):
                run(fake, clock, monitor=monitor, before_first_post=lambda: stamped.append(1))
            self.assertFalse(any(call[0] == 'POST' for call in fake.calls))
            self.assertEqual((started, stamped), ([], []))  # no hourly stamp and no sampling without a submission

    def test_next_wave_waits_for_another_users_job_and_does_not_post_meanwhile(self):
        fake, clock = gateway(foreign=(25, 61))
        result = run(fake, clock, 8, 6)
        self.assert_bounded(fake, 8, result)
        self.assertEqual((result['status'], result['acceptedResults']), ('completed', 8))
        second_wave = [index for index, call in enumerate(fake.calls) if call[0] == 'POST'][6]
        self.assertTrue(all(call[1] == '/readyz' for call in fake.calls[second_wave - 3:second_wave]))
        self.assertEqual(fake.system_at_post[6:], [0, 1])
        self.assertGreaterEqual(result['elapsedSeconds'], 61)

    def test_gateway_that_stays_busy_between_waves_stops_the_run(self):
        fake, clock = gateway(foreign=(25, 100000))
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 6, result)
        self.assertEqual((result['status'], result['stoppedReason']), ('stopped', 'GATEWAY_NOT_IDLE_BETWEEN_WAVES'))
        self.assertEqual(result['outcomes'], {'accepted': 6, 'not_submitted': 6})
        self.assertLessEqual(clock.elapsed, 30 + probe.IDLE_WAIT_SECONDS + 4)

    def test_gateway_that_stops_being_ready_between_waves_stops_at_once(self):
        fake, clock = gateway()
        original = fake.request
        def request(method, path, subject, payload=None, **kwargs):
            if path == '/readyz' and fake.jobs:
                with fake.lock: fake.calls.append((method, path, subject, payload))
                return 503, {'error': {'code': 'FLOW_CHANGED'}}
            return original(method, path, subject, payload, **kwargs)
        fake.request = request
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 6, result)
        self.assertEqual(result['stoppedReason'], 'GATEWAY_NOT_READY_BETWEEN_WAVES')
        self.assertEqual(sum(call[1] == '/readyz' for call in fake.calls), 2)

    def test_refused_submission_stops_all_later_submissions_without_retry(self):
        for code, status in (('RATE_LIMITED', 429), ('QUEUE_FULL', 429), ('BUSY', 429), ('FLOW_CHANGED', 503)):
            fake, clock = gateway(post={3: (status, {'error': {'code': code, 'secret': CANARY}})})
            result = run(fake, clock, 12, 6)
            with self.subTest(code=code):
                self.assert_bounded(fake, 4, result)
                self.assertEqual((result['status'], result['stoppedReason']), ('stopped', 'SUBMISSION_REJECTED'))
                self.assertEqual(result['outcomes'], {'accepted': 3, 'failed:' + code: 1, 'not_submitted': 8})
                self.assertEqual(result['requests'][3], {'wave': 1, 'submitHttp': status, 'final': 'failed', 'code': code})
                self.assertEqual(result['recommendationPostAttempts'], 4)
                self.assertNotIn(CANARY, json.dumps(result))

    def test_ambiguous_submission_is_never_repeated_and_other_jobs_are_still_read(self):
        fake, clock = gateway(post={1: TimeoutError(CANARY)})
        result = run(fake, clock, 10, 6)
        self.assert_bounded(fake, 2, result)
        self.assertEqual(result['stoppedReason'], 'SUBMISSION_OUTCOME_UNKNOWN')
        self.assertEqual(result['outcomes'], {'accepted': 1, 'unknown:SUBMISSION_OUTCOME_UNKNOWN': 1, 'not_submitted': 8})
        self.assertEqual(result['requests'][1]['submitHttp'], None)
        self.assertNotIn(CANARY, json.dumps(result))

    def test_second_submission_answered_with_an_earlier_request_id_is_unknown_and_stops_the_run(self):
        fake, clock = gateway()
        fake.post = {1: lambda: (202, fake.pending(0))}  # the Gateway repeats the id it gave to the first request
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 2, result)
        self.assertEqual((result['status'], result['stoppedReason']), ('stopped', 'SUBMISSION_OUTCOME_UNKNOWN'))
        self.assertEqual(result['outcomes'], {'accepted': 1, 'unknown:SUBMISSION_OUTCOME_UNKNOWN': 1, 'not_submitted': 10})
        self.assertEqual(result['requests'][1], {'wave': 1, 'submitHttp': 202, 'final': 'unknown', 'code': 'SUBMISSION_OUTCOME_UNKNOWN'})
        # The repeated id is read for the first request only: one job, one subject.
        self.assertEqual({call[2][-2:] for call in fake.calls if call[0] == 'GET' and call[1] != '/readyz'}, {'00'})

    def test_malformed_submission_answers_are_unknown_and_stop_the_run(self):
        bad = [(202, {'status': 'queued', 'secret': CANARY}), (200, {'ok': True}), (500, {'error': {'code': [CANARY]}}),
               (429, {'error': {'code': 'NOT_A_GATEWAY_CODE_' + CANARY}})]
        for answer in bad:
            fake, clock = gateway(post={0: answer})
            result = run(fake, clock, 4, 2)
            with self.subTest(answer=answer[0]):
                self.assert_bounded(fake, 1, result)
                self.assertEqual(result['requests'][0]['code'], 'SUBMISSION_OUTCOME_UNKNOWN')
                self.assertEqual(result['outcomes'], {'unknown:SUBMISSION_OUTCOME_UNKNOWN': 1, 'not_submitted': 3})
                self.assertNotIn(CANARY, json.dumps(result))

    def test_infrastructure_failure_finishes_the_wave_but_starts_no_other_wave(self):
        fake, clock = gateway(results={1: (504, {'error': {'code': 'UPSTREAM_TIMEOUT_NO_RETRY', 'secret': CANARY}})})
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 6, result)
        self.assertEqual((result['status'], result['stoppedReason'], result['wavesStarted']),
                         ('stopped', 'UNEXPECTED_RESULT_IN_WAVE', 1))
        self.assertEqual(result['outcomes'], {'accepted': 5, 'failed:UPSTREAM_TIMEOUT_NO_RETRY': 1, 'not_submitted': 6})
        self.assertEqual(result['requests'][1]['finalHttp'], 504)
        self.assertNotIn(CANARY, json.dumps(result))

    def test_blocked_recommendation_is_an_outcome_and_the_run_continues(self):
        fake, clock = gateway(results={2: (422, {'error': {'code': 'RECOMMENDATION_BLOCKED'}})})
        result = run(fake, clock, 8, 4)
        self.assert_bounded(fake, 8, result)
        self.assertEqual((result['status'], result['wavesStarted']), ('completed', 2))
        self.assertEqual(result['outcomes'], {'accepted': 7, 'failed:RECOMMENDATION_BLOCKED': 1})
        self.assertEqual(result['requests'][2]['runSeconds'], 10)

    def test_wave_without_any_accepted_result_stops_the_run(self):
        blocked = (422, {'error': {'code': 'RECOMMENDATION_BLOCKED'}})
        fake, clock = gateway(results={index: blocked for index in range(4)})
        result = run(fake, clock, 8, 4)
        self.assert_bounded(fake, 4, result)
        self.assertEqual(result['stoppedReason'], 'NO_ACCEPTED_RESULT_IN_WAVE')

    def test_read_transport_error_repeats_only_the_read(self):
        fake, clock = gateway(reads={0: {1: TimeoutError(CANARY), 2: TimeoutError(CANARY)}})
        result = run(fake, clock, 4, 4)
        self.assert_bounded(fake, 4, result)
        self.assertEqual((result['status'], result['acceptedResults']), ('completed', 4))
        self.assertNotIn(CANARY, json.dumps(result))

    def test_invalid_results_are_unknown_without_leaking_their_content(self):
        fake, clock = gateway()
        wrong = fake.accepted(0); wrong['requestId'] = job_id(9)
        odd = fake.accepted(1); odd['response']['action'] = CANARY
        many = fake.accepted(2); many['response']['placeIds'] = [CANARY] * 6
        fake.results = {0: (200, wrong), 1: (200, odd), 2: (200, many), 3: (500, {'error': {'code': CANARY}})}
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 6, result)
        self.assertEqual(result['outcomes'], {'accepted': 2, 'unknown:RESULT_RESPONSE_INVALID': 4, 'not_submitted': 6})
        self.assertEqual(result['stoppedReason'], 'UNEXPECTED_RESULT_IN_WAVE')
        self.assertNotIn(CANARY, json.dumps(result))

    def test_running_to_queued_regression_is_rejected(self):
        fake, clock = gateway()
        original = fake.pending
        def pending(index):
            value = original(index)
            if index == 0 and fake.read_counts.get(0): value.update(status='queued', position=1)
            return value
        fake.pending = pending
        result = run(fake, clock, 2, 2)
        self.assertEqual(result['requests'][0]['code'], 'RESULT_RESPONSE_INVALID')
        self.assertEqual(result['requests'][1]['final'], 'accepted')

    def test_poll_deadline_bounds_reads_and_starts_no_other_wave(self):
        fake, clock = gateway(duration=10 ** 9, step=180)
        result = run(fake, clock, 12, 6)
        self.assert_bounded(fake, 6, result)
        self.assertEqual(result['outcomes'], {'unknown:POLL_DEADLINE_REACHED': 6, 'not_submitted': 6})
        self.assertEqual((result['stoppedReason'], result['elapsedSeconds']), ('UNEXPECTED_RESULT_IN_WAVE', 720))
        self.assertEqual(result['pollRequests'], 24)
        self.assertEqual(result['timing']['totalSeconds'], {'count': 0})

    def test_first_submission_records_the_hourly_stamp_and_starts_sampling_once(self):
        fake, clock = gateway()
        events = []
        monitor = SimpleNamespace(start=lambda: events.append(('start', len(fake.jobs))),
                                  stop=lambda: events.append(('stop', len(fake.jobs))) or {'samplesDuring': 3})
        result = run(fake, clock, 4, 2, monitor=monitor, before_first_post=lambda: events.append(('stamp', len(fake.jobs))))
        self.assertEqual(events, [('stamp', 0), ('start', 0), ('stop', 4)])
        self.assertEqual(result['memorySamples'], {'samplesDuring': 3})


class TrafficTests(unittest.TestCase):
    def test_counts_and_resubmission_flag_come_from_the_requests_handed_to_the_transport(self):
        sent = []
        def request(method, path, subject, payload=None, **kwargs):
            sent.append((method, path, subject, payload, kwargs))
            if method == 'POST' and subject.endswith('-01'): raise TimeoutError(CANARY)
            return 200, {}
        traffic = probe.Traffic(SimpleNamespace(request=request))
        self.assertEqual((traffic.counts(), traffic.submissions(), traffic.resubmitted()), ({}, 0, False))
        subject = 'load-check-' + '0' * 32
        traffic.request('GET', '/readyz', subject + '-p')
        traffic.request('POST', '/v1/jobs', subject + '-00', {'query': 'x'})
        with self.assertRaises(TimeoutError): traffic.request('POST', '/v1/jobs', subject + '-01', {'query': 'x'})
        traffic.request('GET', '/v1/jobs/' + job_id(0), subject + '-00', timeout=7)
        traffic.request('GET', '/v1/jobs/' + job_id(1), subject + '-01')
        # A failed submission is still a counted attempt, and arguments pass through unchanged.
        self.assertEqual(traffic.counts(), {'GET /readyz': 1, 'GET /v1/jobs/<id>': 2, 'POST /v1/jobs': 2})
        self.assertEqual((traffic.submissions(), traffic.resubmitted()), (2, False))
        self.assertEqual(sent[3], ('GET', '/v1/jobs/' + job_id(0), subject + '-00', None, {'timeout': 7}))
        # Anything else that were ever sent would be visible under its own kind, and a repeated subject is flagged.
        traffic.request('DELETE', '/v1/jobs/' + job_id(0), subject + '-00')
        traffic.request('POST', '/v1/jobs', subject + '-00', {'query': 'x'})
        self.assertEqual(traffic.counts(), {'DELETE /v1/jobs/<id>': 1, 'GET /readyz': 1, 'GET /v1/jobs/<id>': 2, 'POST /v1/jobs': 3})
        self.assertEqual((traffic.submissions(), traffic.resubmitted()), (3, True))
        self.assertNotIn(subject, json.dumps(traffic.counts()))

    def test_run_report_reflects_a_resubmission_if_one_were_sent(self):
        fake, clock = gateway()
        traffic = probe.Traffic(fake)
        original, repeated = fake.request, []
        def request(method, path, subject, payload=None, **kwargs):
            if method == 'POST' and subject.endswith('-00') and not repeated:
                repeated.append(subject)
                original(method, path, subject, payload, **kwargs)
                fake.jobs.clear()
                return traffic.request(method, path, subject, payload, **kwargs)  # a second POST of the same subject
            return original(method, path, subject, payload, **kwargs)
        fake.request = request
        result = run(traffic, clock, 2, 2)
        self.assertIs(result['automaticResubmission'], True)
        self.assertEqual((result['recommendationPostAttempts'], result['requestCounts']['POST /v1/jobs']), (3, 3))


class ResultShapeTests(unittest.TestCase):
    def test_finished_keeps_only_action_and_count(self):
        fake, _ = gateway()
        self.assertEqual(probe.finished(fake.accepted(5), job_id(5)), {'final': 'accepted', 'action': 'cannot_confirm', 'placeCount': 2})
        corruptions = [lambda value: value.update(requestId=job_id(6)), lambda value: value.update(extra=CANARY),
                       lambda value: value.update(status='blocked'), lambda value: value.update(productionReady=True),
                       lambda value: value.update(catalogVersion=CANARY), lambda value: value['response'].update(requestId=job_id(6)),
                       lambda value: value['response'].update(placeIds='fictional-place-1'),
                       lambda value: value['response'].update(placeIds=[1]), lambda value: value.update(response=[])]
        for corrupt in corruptions:
            value = fake.accepted(5); corrupt(value)
            with self.assertRaises(probe.ProbeError): probe.finished(value, job_id(5))

    def test_distribution_interpolates_like_percentile_cont(self):
        self.assertEqual(probe.distribution([]), {'count': 0})
        self.assertEqual(probe.distribution([None, 4]), {'count': 1, 'p50': 4.0, 'p95': 4.0, 'max': 4})
        self.assertEqual(probe.distribution([10, 20, 30, 40, None]), {'count': 4, 'p50': 25.0, 'p95': 38.5, 'max': 40})

    def test_source_has_no_cancel_state_file_or_key_writing_path(self):
        source = SOURCE.read_text(encoding='utf-8')
        for forbidden in ('DELETE', 'sqlite', '/v1/recommend', 'subprocess', 'docker', 'gateway.env', 'os.remove', 'unlink'):
            self.assertFalse(forbidden in source, forbidden)
        self.assertEqual(source.count("'POST'"), 1)  # the single submission call site


class TransportTests(unittest.TestCase):
    def test_reviewed_client_signs_only_load_check_subjects_for_the_fixed_host(self):
        calls = []
        class Response:
            status, headers = 202, {'Content-Type': 'application/json'}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda _value: None)))
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read1(self, _limit): return b'{"ok":true}'
            def isclosed(self): return True
        class Opener:
            def open(self, request, timeout): calls.append(request); return Response()
        nonces = iter(('1' * 32, '2' * 32))
        client = probe.base.PublicClient(KEY, SERVICE, wall=lambda: NOW, nonce=lambda: next(nonces), opener=Opener())
        subject = 'load-check-' + '0' * 32 + '-07'
        client.request('POST', '/v1/jobs', subject, {'test': '합성'})
        client.request('GET', '/v1/jobs/' + job_id(7), subject)
        for request in calls:
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(request.full_url, 'https://agent.oreumit.com' + request.selector)
            self.assertNotIn('cookie', headers); self.assertNotIn('origin', headers)
            canonical = '\n'.join((request.method, request.selector, subject, str(NOW),
                headers['x-jeju-nonce'], hashlib.sha256(request.data or b'').hexdigest()))
            self.assertEqual(headers['x-jeju-signature'], hmac.new(KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest())
        for refused in ('queue-model-' + '0' * 32 + '-a', 'load-check-' + '0' * 32 + '-100', 'load-check-' + '0' * 32 + '-7'):
            with self.assertRaises(probe.ProbeError): client.request('GET', '/readyz', refused)
        with self.assertRaises(probe.ProbeError): client.request('DELETE', '/v1/jobs/' + job_id(7), subject)
        with self.assertRaises(probe.ProbeError): client.request('POST', '/v1/recommend', subject, {'test': 1})
        self.assertEqual(len(calls), 2)

    def test_other_check_keeps_its_own_subject_pattern(self):
        other = importlib.util.spec_from_file_location('queue_model_check_again', SOURCE.with_name('check-agent-queue-model.py'))
        module = importlib.util.module_from_spec(other)
        other.loader.exec_module(module)
        self.assertTrue(module.SUBJECT.fullmatch('queue-model-' + '0' * 32 + '-a'))
        self.assertIsNone(module.SUBJECT.fullmatch('load-check-' + '0' * 32 + '-07'))


class HourlyStampTests(unittest.TestCase):
    def stamp_file(self, folder, content=b''):
        path = Path(folder) / 'stamp.lock'
        path.write_bytes(content)
        os.chmod(path, 0o600)
        return path

    def test_one_started_run_per_hour(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.stamp_file(folder)
            with open(path, 'r+b') as stream:
                record = probe.reserve_run(stream, NOW, uid=os.getuid())
                self.assertEqual(path.read_bytes(), b'')  # nothing is recorded before the first submission
                record()
            self.assertEqual(path.read_bytes(), b'%d\n' % NOW)
            for later in (NOW, NOW + 3599, NOW - 10, NOW - 7200):  # the last two: a stamp from the future
                with open(path, 'r+b') as stream, self.assertRaisesRegex(probe.ProbeError, 'LOAD_CHECK_RECENT_RUN'):
                    probe.reserve_run(stream, later, uid=os.getuid())
            with open(path, 'r+b') as stream:
                probe.reserve_run(stream, NOW + 3600, uid=os.getuid())()
            self.assertEqual(path.read_bytes(), b'%d\n' % (NOW + 3600))

    def test_unexpected_stamp_file_or_second_holder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            for content in (b'soon\n', b'1795046400', b'1795046400\n\n', b'9' * 40):
                path = self.stamp_file(folder, content)
                with self.subTest(content=content), open(path, 'r+b') as stream, self.assertRaises(probe.ProbeError) as raised:
                    probe.reserve_run(stream, NOW + 10 ** 6, uid=os.getuid())
                self.assertNotEqual(raised.exception.args[0], 'LOAD_CHECK_RECENT_RUN')
            path = self.stamp_file(folder)
            os.chmod(path, 0o644)
            with open(path, 'r+b') as stream, self.assertRaises(probe.ProbeError): probe.reserve_run(stream, NOW, uid=os.getuid())
            os.chmod(path, 0o600)
            with open(path, 'r+b') as first, open(path, 'r+b') as second:
                probe.reserve_run(first, NOW, uid=os.getuid())
                with self.assertRaises(OSError): probe.reserve_run(second, NOW, uid=os.getuid())


class MemoryMonitorTests(unittest.TestCase):
    def sample(self, usage):
        return {'available': True, 'services': {name: {'usageMiB': usage + index, 'limitMiB': 1024.0, 'name': CANARY}
                                                 for index, name in enumerate(probe.SERVICES)}}

    def test_summary_holds_before_after_counts_and_peaks_only(self):
        values = iter([self.sample(10), self.sample(30)])
        monitor = probe.MemoryMonitor(lambda: next(values), interval=3600)
        monitor.start()
        for value in (self.sample(20), {'available': False}, self.sample(50), {'available': True, 'services': {}}, None):
            monitor.record(value)
        summary = monitor.stop()
        self.assertEqual((summary['samplesDuring'], summary['failedSamplesDuring']), (2, 3))
        self.assertEqual(summary['peakUsageMiBDuring'], {name: 50.0 + index for index, name in enumerate(probe.SERVICES)})
        self.assertEqual(summary['before'], self.sample(10)); self.assertEqual(summary['after'], self.sample(30))
        self.assertFalse(monitor.thread.is_alive())

    def test_background_sampling_runs_until_stopped(self):
        seen = threading.Event()
        def sample():
            seen.set()
            return self.sample(1)
        monitor = probe.MemoryMonitor(sample, interval=0.01)
        monitor.start(); seen.clear()
        self.assertTrue(seen.wait(3))
        summary = monitor.stop()
        self.assertGreaterEqual(summary['samplesDuring'], 1)
        self.assertFalse(monitor.thread.is_alive())


class CliTests(unittest.TestCase):
    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(probe.sys, 'argv', ['check', *arguments]), redirect_stdout(output), redirect_stderr(errors):
            try: code = probe.main()
            except SystemExit as stopped: code = stopped.code
        return code, output.getvalue(), errors.getvalue()

    def test_without_explicit_flag_or_within_limits_nothing_is_read_or_sent(self):
        with patch.object(probe, 'load_configuration') as load, patch.object(probe, 'run_load') as run_mock:
            for arguments in ((), ('--requests', '5'), ('--execute-model', '--requests', '21'),
                              ('--execute-model', '--requests', '0'), ('--execute-model', '--requests', '-3'),
                              ('--execute-model', '--wave-size', '11'), ('--execute-model', '--wave-size', '0')):
                code, output, _ = self.invoke(*arguments)
                with self.subTest(arguments=arguments):
                    self.assertEqual(code, 2)
                    value = json.loads(output)
                    self.assertEqual((value['status'], value['recommendationPostAttempts']), ('not_executed', 0))
                    self.assertEqual(value['code'], 'REQUEST_COUNT_OUT_OF_RANGE' if '--execute-model' in arguments
                                     else 'EXPLICIT_MODEL_FLAG_REQUIRED')
            for arguments in (('--execute-model', '--requests', 'ten'), ('--wrong', CANARY), ('--execute-model', CANARY)):
                code, output, errors = self.invoke(*arguments)
                self.assertEqual((code, output), (2, ''))
                self.assertNotIn(CANARY, errors)
        load.assert_not_called(); run_mock.assert_not_called()

    def test_failures_print_fixed_codes_only(self):
        with patch.object(probe, 'load_configuration', side_effect=ValueError(CANARY)):
            code, output, errors = self.invoke('--execute-model')
        self.assertEqual((code, output), (1, ''))
        self.assertNotIn(CANARY, errors)
        value = json.loads(errors)
        self.assertEqual((value['code'], value['phase'], value['submissionOutcomeMayBeUnknown']),
                         ('LOAD_CHECK_FAILED', 'configuration', False))

    def test_run_uses_requested_limits_and_reports_named_refusals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for error, expected, unknown in ((probe.ProbeError('LOAD_CHECK_RECENT_RUN'), 'LOAD_CHECK_RECENT_RUN', False),
                                             (probe.ProbeError('LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY'),
                                              'LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY', False),
                                             (probe.ProbeError(CANARY), 'LOAD_CHECK_FAILED', True)):
                with patch.object(probe, 'load_configuration', return_value=(root, KEY, SERVICE)), \
                        patch.object(probe, 'reserve_run', return_value=lambda: None), \
                        patch.object(probe, 'run_load', side_effect=error):
                    code, output, errors = self.invoke('--execute-model')
                value = json.loads(errors)
                self.assertEqual((code, value['code'], value['submissionOutcomeMayBeUnknown']), (1, expected, unknown))
                self.assertNotIn(CANARY, errors)
            done = {'status': 'completed', 'acceptedResults': 7}
            with patch.object(probe, 'load_configuration', return_value=(root, KEY, SERVICE)), \
                    patch.object(probe, 'reserve_run', return_value=lambda: None), \
                    patch.object(probe, 'run_load', return_value=done) as run_mock:
                code, output, _ = self.invoke('--execute-model', '--requests', '7', '--wave-size', '3')
            self.assertEqual((code, json.loads(output)), (0, done))
            self.assertEqual(run_mock.call_args.args[1:], (7, 3))
            self.assertEqual(len(output.splitlines()), 1)
            with patch.object(probe, 'load_configuration', return_value=(root, KEY, SERVICE)), \
                    patch.object(probe, 'reserve_run', return_value=lambda: None), \
                    patch.object(probe, 'run_load', return_value={'status': 'completed', 'acceptedResults': 9}):
                self.assertEqual(self.invoke('--execute-model')[0], 1)  # default is ten requests


class MainWiringTests(unittest.TestCase):
    """main() with its real reserve_run, Traffic, MemoryMonitor and run_load; only time, transport,
    configuration and the Docker memory sample are replaced."""
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.lock = self.root / '.agent-load-check.lock'
        self.sample = {'available': True, 'services': {name: {'usageMiB': 100.0 + index, 'limitMiB': 1024.0}
                                                          for index, name in enumerate(probe.SERVICES)}}
        self.snapshots, self.clients, self.monitors, self.states = [], [], [], []
        self.use(gateway())
        real_run, real_monitor = probe.run_load, probe.MemoryMonitor
        self.real_monitor = real_monitor

        def run_on_fake_time(client, requests, wave_size, **kwargs):
            return real_run(client, requests, wave_size, monotonic=self.clock.monotonic, wall=self.clock.wall,
                            sleep=self.clock.sleep, **kwargs)

        def monitor(sample, *args, **kwargs):
            self.monitors.append(real_monitor(sample, *args, **kwargs))
            return self.monitors[-1]

        def snapshot(deploy):
            self.snapshots.append(deploy)
            return self.sample

        def client(key, service):
            self.clients.append((key, service))
            return self.fake

        for patcher in (patch.object(probe, 'ROOT_UID', os.getuid()),
                        patch.object(probe, 'load_configuration', return_value=(self.root, KEY, SERVICE)),
                        patch.object(probe, 'run_load', side_effect=run_on_fake_time),
                        patch.object(probe, 'MemoryMonitor', side_effect=monitor),
                        patch.object(probe.base, 'PublicClient', side_effect=client),
                        patch.object(probe.base, 'memory_snapshot', side_effect=snapshot)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.stop_monitors)  # runs before the patches above are removed

    def use(self, pair):
        self.fake, self.clock = pair
        original = self.fake.request

        def request(method, path, subject, payload=None, **kwargs):
            self.states.append((method, self.lock.read_bytes()))  # the stamp file as each request leaves
            return original(method, path, subject, payload, **kwargs)
        self.fake.request = request

    def stop_monitors(self):
        for monitor in self.monitors:
            monitor.stopped.set()
            if monitor.thread is not None:
                monitor.thread.join(timeout=15)
                self.assertFalse(monitor.thread.is_alive())

    def invoke(self, *arguments):
        return CliTests.invoke(self, '--execute-model', *arguments)

    def stamp(self):
        content = self.lock.read_bytes()
        self.assertRegex(content, rb'\A[0-9]{10}\n\Z')
        return int(content)

    def test_main_records_the_hour_at_the_first_submission_and_samples_memory(self):
        before = time.time()
        code, output, errors = self.invoke('--requests', '4', '--wave-size', '2')
        self.assertEqual((code, errors, len(output.splitlines())), (0, '', 1))
        result = json.loads(output)
        self.assertEqual((result['status'], result['acceptedResults'], result['wavesStarted']), ('completed', 4, 2))
        self.assertEqual(self.clients, [(KEY, SERVICE)])
        # Nothing is recorded while the Gateway is only being read; the stamp exists when the first POST leaves.
        first_post = [method for method, _ in self.states].index('POST')
        self.assertGreater(first_post, 0)
        self.assertEqual({content for _, content in self.states[:first_post]}, {b''})
        self.assertEqual(len({content for _, content in self.states[first_post:]}), 1)
        self.assertEqual(self.states[first_post][1], self.lock.read_bytes())
        self.assertTrue(before - 1 <= self.stamp() <= time.time())
        info = self.lock.stat()
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_nlink), (0o600, os.getuid(), 1))
        # The numbers in the report are the ones the fake Gateway received.
        self.assertEqual(result['requestCounts'], received(self.fake))
        self.assertEqual((result['recommendationPostAttempts'], result['automaticResubmission']), (4, False))
        # The monitor of this run is a real MemoryMonitor fed by the reviewed sampler for this deploy folder.
        self.assertEqual(len(self.monitors), 1)
        self.assertIsInstance(self.monitors[0], self.real_monitor)
        self.assertGreaterEqual(len(self.snapshots), 2)
        self.assertEqual(set(self.snapshots), {probe.DEPLOY})
        self.assertEqual((result['memorySamples']['before'], result['memorySamples']['after']), (self.sample, self.sample))
        self.assertEqual(result['memorySamples']['intervalSeconds'], probe.MEMORY_INTERVAL_SECONDS)
        self.assertFalse(self.monitors[0].thread.is_alive())
        for forbidden in (KEY, SERVICE['CF_ACCESS_CLIENT_SECRET'], 'gw-', 'load-check-', CANARY, str(self.root)):
            self.assertNotIn(forbidden, output)

    def test_second_run_within_the_hour_is_refused_before_any_request(self):
        self.assertEqual(self.invoke('--requests', '2', '--wave-size', '2')[0], 0)
        first, calls = self.lock.read_bytes(), len(self.fake.calls)
        code, output, errors = self.invoke('--requests', '2', '--wave-size', '2')
        value = json.loads(errors)
        self.assertEqual((code, output), (1, ''))
        self.assertEqual((value['code'], value['phase'], value['submissionOutcomeMayBeUnknown'], value['recommendationPostAttempts']),
                         ('LOAD_CHECK_RECENT_RUN', 'recent_run_check', False, 0))
        self.assertEqual((self.lock.read_bytes(), len(self.fake.calls), len(self.clients), len(self.monitors)), (first, calls, 1, 1))

    def test_hour_is_measured_from_the_recorded_start_on_the_real_clock(self):
        for age, allowed in ((probe.RUN_INTERVAL_SECONDS + 5, True), (probe.RUN_INTERVAL_SECONDS - 60, False), (-120, False),
                             (-2 * probe.RUN_INTERVAL_SECONDS, False)):
            self.use(gateway())
            written = b'%010d\n' % int(time.time() - age)
            self.lock.write_bytes(written)
            os.chmod(self.lock, 0o600)
            code, output, errors = self.invoke('--requests', '1', '--wave-size', '1')
            with self.subTest(age=age):
                posts = sum(call[0] == 'POST' for call in self.fake.calls)
                if allowed:
                    self.assertEqual((code, posts), (0, 1))
                    self.assertTrue(time.time() - 5 <= self.stamp() <= time.time())
                else:
                    self.assertEqual((code, json.loads(errors)['code'], self.fake.calls), (1, 'LOAD_CHECK_RECENT_RUN', []))
                    self.assertEqual(self.lock.read_bytes(), written)

    def test_stamp_file_of_another_owner_is_refused_before_any_request(self):
        self.assertEqual(SOURCE.read_text(encoding='utf-8').count('\nROOT_UID = 0  #'), 1)  # root outside this test seam
        with patch.object(probe, 'ROOT_UID', os.getuid() + 1):
            code, output, errors = self.invoke()
        value = json.loads(errors)
        self.assertEqual((code, output, value['code'], value['phase']), (1, '', 'LOAD_CHECK_FAILED', 'recent_run_check'))
        self.assertEqual((self.fake.calls, self.clients, self.lock.read_bytes()), ([], [], b''))

    def test_refusal_before_the_first_submission_does_not_use_up_the_hour(self):
        for overrides in ({'jobs': {'queued': 0, 'running': 1, 'uncertain': 0}}, {'ready': False}):
            self.use(gateway(ready=overrides))
            code, output, errors = self.invoke()
            value = json.loads(errors)
            self.assertEqual((code, output, value['code'], value['phase']),
                             (1, '', 'LOAD_CHECK_REQUIRES_IDLE_TWO_SLOT_GATEWAY', 'preflight_or_execution'))
            self.assertEqual((value['submissionOutcomeMayBeUnknown'], value['recommendationPostAttempts']), (False, 0))
            self.assertEqual([call[:2] for call in self.fake.calls], [('GET', '/readyz')])
            self.assertEqual(self.lock.read_bytes(), b'')
            self.assertEqual(self.snapshots, [])  # no Docker sample without a submission
        self.use(gateway())
        self.assertEqual(self.invoke('--requests', '2', '--wave-size', '2')[0], 0)  # the earlier refusals left the hour free
        self.stamp()

    def test_interrupted_run_reports_the_submissions_already_sent_and_keeps_the_stamp(self):
        self.use(gateway(reads={0: {1: KeyboardInterrupt()}}))
        code, output, errors = self.invoke('--requests', '6', '--wave-size', '3')
        value = json.loads(errors)
        self.assertEqual((code, output), (1, ''))
        self.assertEqual((value['code'], value['phase'], value['submissionOutcomeMayBeUnknown']),
                         ('LOAD_CHECK_FAILED', 'preflight_or_execution', True))
        posts = sum(call[0] == 'POST' for call in self.fake.calls)
        self.assertEqual((posts, value['recommendationPostAttempts'], value['automaticResubmission']), (3, 3, False))
        self.stamp()  # the hour is used: three paid submissions are on the server
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(sum(call[0] == 'POST' for call in self.fake.calls), 3)


if __name__ == '__main__':
    unittest.main()
