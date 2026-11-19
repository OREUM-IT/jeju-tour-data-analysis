"""Persisted-span fixtures; no Langflow install, credentials or provider calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'audit_v2_runtime.py'
SPEC = importlib.util.spec_from_file_location('jeju_runtime_audit', SOURCE)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
FLOW_ID = '11111111-1111-1111-1111-111111111111'
SESSION = 'test-session-001'


def fixture():
    artifact = {'data': {'nodes': [], 'edges': []}}
    runtime_request = {'requestId': SESSION, 'bodySha256': 'a' * 64, 'preparedRequestSha256': 'b' * 64}
    record = {'caseId': 'CASE-001-template', 'requestSha256': 'c' * 64, 'runtimeRequest': runtime_request,
              'raw': {'session_id': SESSION, 'privateCanary': 'PRIVATE_RESPONSE_CANARY'},
              'measurements': {'latencyMs': 1500, 'costUsd': None}}
    binding = {'requestMode': 'langflow-v2', 'requestAdapterVersion': '1.0.0'}
    capture = {'kind': 'jeju-baseline-capture', 'planSha256': 'd' * 64, 'requestBinding': binding, 'records': [record]}
    hashes = {record['caseId']: AUDIT.canonical_sha(record)}
    event = {'caseId': record['caseId'], 'sessionId': SESSION, 'captureRecordSha256': hashes[record['caseId']],
             'runtimeRequestSha256': AUDIT.canonical_sha(runtime_request)}
    telemetry = {'kind': 'jeju-baseline-collection-telemetry', 'planSha256': capture['planSha256'],
                 'requestBinding': binding, 'attemptedCases': 1, 'events': [event]}
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE flow(id TEXT, data TEXT);
      CREATE TABLE trace(id TEXT, flow_id TEXT, session_id TEXT, status TEXT, start_time TEXT, end_time TEXT, total_latency_ms INTEGER, total_tokens INTEGER);
      CREATE TABLE span(trace_id TEXT, name TEXT, span_type TEXT, status TEXT, latency_ms INTEGER, attributes TEXT, start_time TEXT, inputs TEXT, outputs TEXT, error TEXT);
      CREATE TABLE message(session_id TEXT, sender TEXT, sender_name TEXT, properties TEXT, text TEXT);
      CREATE TABLE job(status TEXT);''')
    db.execute('INSERT INTO flow VALUES(?,?)', (FLOW_ID.replace('-', ''), json.dumps(artifact['data'])))
    db.execute('INSERT INTO trace VALUES(?,?,?,?,?,?,?,?)', ('trace-one', FLOW_ID.replace('-', ''), SESSION, 'ok', 'start', 'end', 1500, 24))
    for span_type, name, attrs in [('llm', 'PRIVATE_SPAN_NAME', {'gen_ai.response.model': 'gemini-test', 'gen_ai.provider.name': 'google',
                                                           'gen_ai.usage.input_tokens': 20, 'gen_ai.usage.output_tokens': 4}),
                                    ('tool', 'places.search', {})]:
        db.execute('INSERT INTO span VALUES(?,?,?,?,?,?,?,?,?,?)', ('trace-one', name, span_type, 'ok', 100, json.dumps(attrs),
                                                                 'start', 'PRIVATE_INPUT_CANARY', 'PRIVATE_OUTPUT_CANARY', 'PRIVATE_ERROR_CANARY'))
    for sender, name, source in [('User', 'User', None), ('Machine', 'AI', 'JejuFinalValidation-v2')]:
        db.execute('INSERT INTO message VALUES(?,?,?,?,?)', (SESSION, sender, name, json.dumps({'source': {'id': source}}), 'PRIVATE_MESSAGE_CANARY'))
    db.execute("INSERT INTO job VALUES('completed')")
    args = {'capture': capture, 'telemetry': telemetry, 'record_hashes': hashes, 'db': db,
            'flow_artifact_bytes': json.dumps(artifact).encode(), 'flow_id': FLOW_ID,
            'catalog_readiness': {'ready': True, 'catalogSchemaVersion': '2.0', 'placeCount': 981, 'dataVersion': 'sha256:' + 'f' * 64},
            'expected_model': 'gemini-test', 'runtime_versions': {'langflowVersion': '1.13.3', 'googleAdapterVersion': '4.1.3'},
            'collected_at': '2026-11-19T00:00:00.000Z'}
    return args


class RuntimeAuditTests(unittest.TestCase):
    def setUp(self):
        self.args = fixture()
        self.addCleanup(self.args['db'].close)

    def test_exact_session_counts_and_no_private_text_in_aggregate(self):
        # A different session/flow must not inflate these counts.
        self.args['db'].execute('INSERT INTO trace VALUES(?,?,?,?,?,?,?,?)', ('unrelated', '2' * 32, 'unrelated-session', 'ok', 'start', 'end', 9999, 9999))
        result = AUDIT.build_audit(**self.args)
        aggregate = result['aggregate']
        self.assertEqual(aggregate['model']['calls'], 1)
        self.assertEqual(aggregate['model']['inputTokens'], 20)
        self.assertEqual(aggregate['tools']['byName']['places.search'], 1)
        self.assertEqual(aggregate['storedMessages'], {'user': 1, 'finalGate': 1, 'rawAgent': 0, 'otherMachine': 0, 'other': 0})
        self.assertIsNone(aggregate['costUsd'])
        self.assertEqual(result['perCase'][0]['sessionId'], SESSION)
        self.assertNotIn(SESSION, json.dumps(aggregate))
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_failed_attempt_without_trace_remains_missing_not_success(self):
        self.args['db'].execute('DELETE FROM trace')
        result = AUDIT.build_audit(**self.args)['aggregate']
        self.assertEqual(result['sessions'], {'attempted': 1, 'traced': 0, 'missingTrace': 1, 'multipleTraces': 0, 'unfinishedTraces': 0})
        self.assertEqual(result['model']['calls'], 0)
        self.assertIsNone(result['model']['inputTokens'])
        self.assertIsNone(result['model']['outputTokens'])

    def test_missing_usage_model_identity_and_unlisted_tool_are_not_guessed(self):
        self.args['db'].execute("UPDATE span SET attributes=?,status='error' WHERE span_type='llm'",
                                (json.dumps({'gen_ai.response.model': 'PRIVATE_OTHER_MODEL', 'gen_ai.usage.input_tokens': True}),))
        self.args['db'].execute("UPDATE span SET name='PRIVATE_OTHER_TOOL' WHERE span_type='tool'")
        aggregate = AUDIT.build_audit(**self.args)['aggregate']
        self.assertEqual(aggregate['model']['error'], 1)
        self.assertEqual(aggregate['model']['otherModelCalls'], 1)
        self.assertEqual(aggregate['model']['missingProviderCalls'], 1)
        self.assertEqual(aggregate['model']['inputUsageMissingCalls'], 1)
        self.assertIsNone(aggregate['model']['inputTokens'])
        self.assertEqual(aggregate['tools']['otherToolCalls'], 1)
        self.assertNotIn('PRIVATE_', json.dumps(aggregate))

    def test_tampered_record_and_runtime_request_hashes_are_rejected(self):
        event = self.args['telemetry']['events'][0]
        event['captureRecordSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'binding hash'): AUDIT.build_audit(**self.args)
        event['captureRecordSha256'] = self.args['record_hashes'][event['caseId']]
        event['runtimeRequestSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'binding hash'): AUDIT.build_audit(**self.args)

    def test_returned_session_mismatch_and_duplicate_case_are_rejected(self):
        self.args['capture']['records'][0]['raw']['session_id'] = 'different-session'
        with self.assertRaisesRegex(ValueError, 'Returned session'): AUDIT.build_audit(**self.args)
        self.args['capture']['records'][0]['raw']['session_id'] = SESSION
        self.args['telemetry']['events'].append(copy.deepcopy(self.args['telemetry']['events'][0]))
        self.args['telemetry']['attemptedCases'] = 2
        with self.assertRaisesRegex(ValueError, 'Duplicate case'): AUDIT.build_audit(**self.args)

    def test_registered_flow_and_trace_flow_must_match_artifact(self):
        self.args['db'].execute('UPDATE flow SET data=?', (json.dumps({'nodes': ['changed']}),))
        with self.assertRaisesRegex(ValueError, 'Registered flow'): AUDIT.build_audit(**self.args)
        self.args['db'].execute('UPDATE flow SET data=?', (json.dumps({'nodes': [], 'edges': []}),))
        self.args['db'].execute('UPDATE trace SET flow_id=?', ('2' * 32,))
        with self.assertRaisesRegex(ValueError, 'another flow'): AUDIT.build_audit(**self.args)

    def test_unfinished_trace_raw_agent_and_active_jobs_are_exposed(self):
        db = self.args['db']
        db.execute('UPDATE trace SET end_time=NULL')
        db.execute("INSERT INTO job VALUES('in_progress')")
        db.execute('INSERT INTO message VALUES(?,?,?,?,?)', (SESSION, 'Machine', 'Agent', '{}', 'PRIVATE_AGENT_TEXT'))
        db.execute('INSERT INTO message VALUES(?,?,?,?,?)', (SESSION, 'Machine', 'AI', '{}', 'PRIVATE_UNKNOWN_TEXT'))
        aggregate = AUDIT.build_audit(**self.args)['aggregate']
        self.assertEqual(aggregate['sessions']['unfinishedTraces'], 1)
        self.assertEqual(aggregate['storedMessages']['rawAgent'], 1)
        self.assertEqual(aggregate['storedMessages']['otherMachine'], 1)
        self.assertEqual(aggregate['activeJobs'], 1)

    def test_cli_reuses_node_canonicalizer_without_shell_or_raw_stderr(self):
        raw = json.dumps({'records': [{'caseId': 'x', 'latencyMs': 1e-7}]}).encode()
        with patch.object(AUDIT.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"x":"' + b'a' * 64 + b'"}')) as run:
            self.assertEqual(AUDIT.capture_record_hashes(raw), {'x': 'a' * 64})
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ['node', '--input-type=module', '-e'])
        self.assertTrue(Path(args[0][-1]).is_absolute())
        self.assertEqual(kwargs['input'], raw)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertNotIn('shell', kwargs)

    def test_exclusive_writes_and_size_limit_preserve_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audit.json'
            AUDIT.write_exclusive(path, {'original': True}, 0o600)
            with self.assertRaises(FileExistsError): AUDIT.write_exclusive(path, {'overwritten': True}, 0o600)
            with self.assertRaisesRegex(ValueError, 'size limit'): AUDIT.bounded_read(path, 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {'original': True})


if __name__ == '__main__':
    unittest.main()
