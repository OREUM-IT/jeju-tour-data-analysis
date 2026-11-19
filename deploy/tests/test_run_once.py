import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('run_once', Path(__file__).parents[1] / 'langflow/run-once.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
FLOW_ID = 'd7e1fd54-12bf-4400-9502-dab56418dff8'
KEY_ID = 'b86cd1db-bfa5-4be1-bd30-c82da4a6d86e'
API_KEY = 'sk-private-temporary-key-do-not-print'
MODEL_TEXT = 'private raw model or provider error do not print'
SESSION_ID = 'aws-smoke-' + 'd' * 32
ENV = {'LANGFLOW_SUPERUSER': 'admin', 'LANGFLOW_SUPERUSER_PASSWORD': 'private-admin-password',
       'MCP_SERVICE_TOKEN': 'private-mcp-token-' + 'm' * 40,
       'LANGFLOW_DATABASE_URL': 'postgresql+psycopg://app:private-db-password@db/langflow'}
META = {'requestSha256': 'a' * 64, 'catalogSha256': 'b' * 64,
        'catalogVersion': 'sha256:' + 'b' * 64, 'policyVersion': 'policy-v1', 'candidateCount': 3}
COMPLETE_EVIDENCE = {'traceCount': 1, 'unfinishedTraces': 0, 'failedTraces': 0,
                     'modelCallsObserved': 2, 'successfulModelCalls': 2,
                     'successfulMcpToolCalls': 2, 'unexpectedToolCalls': 0}
ZERO_EVIDENCE = dict.fromkeys(COMPLETE_EVIDENCE, 0)


def final_value(session_id):
    return {'schemaVersion': '1.0', 'requestId': session_id, 'action': 'cannot_confirm',
            'placeIds': ['private-place-id'], 'requirementClaims': [
                {'placeId': 'private-place-id', 'requirement': 'indoor_required', 'verdict': 'unknown'}],
            'confirmationNotice': True, 'noticeTopics': ['indoor_unverified']}


def graph_output(session_id):
    return {'session_id': session_id, 'outputs': [{'outputs': [{
        'component_id': smoke.FINAL_COMPONENT, 'results': {'message': {
            'sender': 'Machine', 'session_id': session_id,
            'properties': {'source': {'id': 'JejuFinalValidation-v2', 'source': 'Jeju Final Validation'}},
            'text': json.dumps(final_value(session_id)),
        }},
    }]}]}


class Response:
    def __init__(self, value=None, status=200):
        self.value = value
        self.status_code = status
        self.content = json.dumps(value).encode()

    def json(self):
        return copy.deepcopy(self.value)


class StreamResponse:
    def __init__(self, raw, status=200, error=None):
        self.raw = raw
        self.status_code = status
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_bytes(self):
        if self.error:
            raise self.error
        halfway = len(self.raw) // 2
        yield self.raw[:halfway]
        yield self.raw[halfway:]


class Langflow:
    def __init__(self, *, output=graph_output, stream_status=200, stream_error=None,
                 delete_status=200, delete_error=None):
        self.output = output
        self.stream_status = stream_status
        self.stream_error = stream_error
        self.delete_status = delete_status
        self.delete_error = delete_error
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(('GET', path, kwargs))
        if path == '/api/v1/flows/':
            return Response([{'id': FLOW_ID, 'name': 'test-reviewed-flow'}])
        raise AssertionError('Unexpected read endpoint')

    def post(self, path, **kwargs):
        self.calls.append(('POST', path, kwargs))
        if path == '/api/v1/api_key/':
            return Response({'id': KEY_ID, 'api_key': API_KEY})
        raise AssertionError('Unexpected write endpoint')

    def stream(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method != 'POST' or path != f'/api/v1/run/{FLOW_ID}?stream=false':
            raise AssertionError('Unexpected inference endpoint')
        raw = self.output(kwargs['json']['session_id'])
        if not isinstance(raw, bytes):
            raw = json.dumps(raw).encode()
        return StreamResponse(raw, self.stream_status, self.stream_error)

    def delete(self, path):
        self.calls.append(('DELETE', path, {}))
        if path != f'/api/v1/api_key/{KEY_ID}':
            raise AssertionError('Only this execution\'s temporary key may be deleted')
        if self.delete_error:
            raise self.delete_error
        return Response(status=self.delete_status)


class MCP:
    def __init__(self, validation_mutation=None, validation_status=200):
        self.validation_mutation = validation_mutation
        self.validation_status = validation_status
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == '/prepare-request':
            return Response({'status': 'prepared', 'request': copy.deepcopy(kwargs['json']), **META})
        if path == '/validate-response':
            value = {'status': 'accepted', 'reasons': [], 'response': copy.deepcopy(kwargs['json']['response']), **META}
            if self.validation_mutation:
                self.validation_mutation(value)
            return Response(value, self.validation_status)
        raise AssertionError('Unexpected MCP route')


class BootstrapError(Exception):
    pass


class RunOnceTest(unittest.TestCase):
    def run_probe(self, client=None, mcp=None, *, vertex=True, check_only=False, observer=None):
        client = client or Langflow()
        mcp = mcp or MCP()
        observer = observer or Mock(return_value=copy.deepcopy(ZERO_EVIDENCE if check_only else COMPLETE_EVIDENCE))
        setup = types.SimpleNamespace(FLOW_NAME='test-reviewed-flow', BootstrapError=BootstrapError,
                                      bootstrap=Mock(return_value={'vertexConfigured': vertex}))
        with patch.object(smoke.uuid, 'uuid4', return_value=types.SimpleNamespace(hex='d' * 32)):
            result = smoke.execute(client, mcp, ENV, {}, setup, check_only=check_only, observer=observer)
        serialized = json.dumps(result)
        for secret in [ENV['LANGFLOW_SUPERUSER_PASSWORD'], ENV['MCP_SERVICE_TOKEN'], ENV['LANGFLOW_DATABASE_URL'],
                       API_KEY, MODEL_TEXT, 'private-place-id']:
            self.assertNotIn(secret, serialized)
        return result, client, mcp, observer

    def inference_calls(self, client):
        return [call for call in client.calls if '/api/v1/run/' in call[1]]

    def test_one_bound_request_is_independently_validated_and_temporary_key_revoked(self):
        before = dt.datetime.now(dt.timezone.utc)
        result, client, mcp, observer = self.run_probe()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['finalValidationAccepted'])
        self.assertTrue(result['modelExecuted'])
        self.assertTrue(result['temporaryApiKeyRevoked'])
        self.assertFalse(result['productionReady'])
        requests = self.inference_calls(client)
        self.assertEqual(len(requests), 1)
        method, _, kwargs = requests[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(kwargs['headers'], {'x-api-key': API_KEY})
        self.assertEqual(kwargs['timeout'], 120)
        self.assertEqual(kwargs['json']['output_component'], smoke.FINAL_COMPONENT)
        self.assertEqual(kwargs['json']['session_id'], SESSION_ID)
        self.assertNotIn('tweaks', kwargs['json'])
        self.assertEqual(json.loads(kwargs['json']['input_value']), mcp.calls[0][1]['json'])
        self.assertEqual([path for path, _ in mcp.calls], ['/prepare-request', '/validate-response'])
        self.assertEqual(mcp.calls[1][1]['json']['expectedCatalogVersion'], META['catalogVersion'])
        self.assertEqual([path for method, path, _ in client.calls if method == 'DELETE'], [f'/api/v1/api_key/{KEY_ID}'])
        expiry = next(kwargs['json']['expires_at'] for _, path, kwargs in client.calls if path == '/api/v1/api_key/')
        self.assertLess(abs((dt.datetime.fromisoformat(expiry) - before).total_seconds() - 600), 5)
        observer.assert_called_once_with(ENV['LANGFLOW_DATABASE_URL'], FLOW_ID, SESSION_ID)

    def test_missing_vertex_stops_before_key_or_inference(self):
        result, client, mcp, observer = self.run_probe(vertex=False)
        self.assertEqual(result['code'], 'VERTEX_CREDENTIAL_MISSING')
        self.assertFalse(result['runSubmitted'])
        self.assertFalse(result['modelExecuted'])
        self.assertEqual(client.calls, [])
        self.assertEqual(mcp.calls, [])
        observer.assert_not_called()

    def test_check_only_prepares_and_cleans_key_without_inference_or_vertex(self):
        result, client, mcp, observer = self.run_probe(vertex=False, check_only=True)
        self.assertEqual(result['status'], 'preflight_passed_without_inference')
        self.assertFalse(result['runSubmitted'])
        self.assertFalse(result['modelExecuted'])
        self.assertTrue(result['temporaryApiKeyRevoked'])
        self.assertEqual(self.inference_calls(client), [])
        self.assertEqual([path for path, _ in mcp.calls], ['/prepare-request'])
        self.assertEqual(result['runtimeEvidence'], ZERO_EVIDENCE)
        observer.assert_called_once_with(ENV['LANGFLOW_DATABASE_URL'], FLOW_ID, SESSION_ID)

    def test_check_only_rejects_used_session_or_observer_failure_and_still_cleans_key(self):
        for label, observer in [
            ('used-session', Mock(return_value=COMPLETE_EVIDENCE)),
            ('missing-schema', Mock(return_value={})),
            ('extra-content', Mock(return_value={**ZERO_EVIDENCE, 'modelText': MODEL_TEXT})),
            ('boolean-count', Mock(return_value={**ZERO_EVIDENCE, 'traceCount': False})),
            ('observer-error', Mock(side_effect=RuntimeError(MODEL_TEXT))),
        ]:
            with self.subTest(case=label):
                result, client, _, _ = self.run_probe(check_only=True, observer=observer)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(result['temporaryApiKeyRevoked'])
                self.assertEqual(self.inference_calls(client), [])

    def test_http_failure_and_timeout_never_retry_and_always_revoke_key(self):
        ReadTimeout = type('ReadTimeout', (Exception,), {})
        cases = [(Langflow(stream_status=503, output=lambda _: {'error': MODEL_TEXT}), 'AGENT_HTTP_REJECTED'),
                 (Langflow(stream_error=ReadTimeout(MODEL_TEXT + API_KEY)), 'HTTP_TIMEOUT_NO_AUTOMATIC_RETRY')]
        for client, code in cases:
            with self.subTest(code=code):
                result, client, _, observer = self.run_probe(client, observer=Mock(return_value=ZERO_EVIDENCE))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['code'], code)
                self.assertTrue(result['runSubmitted'])
                self.assertIsNone(result['modelExecuted'], 'A timeout does not prove whether remote inference ran.')
                self.assertTrue(result['temporaryApiKeyRevoked'])
                self.assertEqual(len(self.inference_calls(client)), 1)
                observer.assert_called_once()

    def test_cleanup_failure_prevents_success_without_leaking_exception(self):
        for client in [Langflow(delete_status=500), Langflow(delete_error=RuntimeError(MODEL_TEXT + API_KEY))]:
            with self.subTest(exception=bool(client.delete_error)):
                result, client, _, _ = self.run_probe(client)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['code'], 'TEMPORARY_API_KEY_CLEANUP_FAILED')
                self.assertFalse(result['temporaryApiKeyRevoked'])
                self.assertEqual(len(self.inference_calls(client)), 1)
                self.assertEqual(len([call for call in client.calls if call[0] == 'DELETE']), 1)

    def test_wrong_output_source_session_request_and_blocked_output_are_rejected(self):
        def message(raw):
            return raw['outputs'][0]['outputs'][0]['results']['message']
        mutations = {
            'outer-session': lambda raw: raw.update(session_id='different-session'),
            'request-id': lambda raw: message(raw).update(text=json.dumps(final_value('different-request'))),
            'component': lambda raw: raw['outputs'][0]['outputs'][0].update(component_id='Agent-jejuV2'),
            'duplicate': lambda raw: raw['outputs'][0]['outputs'].append(copy.deepcopy(raw['outputs'][0]['outputs'][0])),
            'source-id': lambda raw: message(raw)['properties']['source'].update(id='Agent-jejuV2'),
            'source-name': lambda raw: message(raw)['properties']['source'].update(source='Unvalidated Agent'),
            'sender': lambda raw: message(raw).update(sender='User'),
            'groups': lambda raw: raw['outputs'].append(copy.deepcopy(raw['outputs'][0])),
            'blocked': lambda raw: message(raw).update(text=json.dumps({'status': 'blocked', 'message': MODEL_TEXT})),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                def output(session_id):
                    raw = graph_output(session_id)
                    mutate(raw)
                    return raw
                result, client, mcp, _ = self.run_probe(Langflow(output=output))
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['finalValidationAccepted'])
                self.assertTrue(result['temporaryApiKeyRevoked'])
                self.assertEqual([path for path, _ in mcp.calls], ['/prepare-request'])
                self.assertEqual(len(self.inference_calls(client)), 1)

    def test_independent_validator_can_reject_same_source_claimed_response(self):
        mutations = [lambda value: value.update(status='blocked', reasons=[MODEL_TEXT]),
                     lambda value: value.update(catalogSha256='c' * 64),
                     lambda value: value['response'].update(action='recommend')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result, client, _, _ = self.run_probe(mcp=MCP(validation_mutation=mutate))
                self.assertEqual(result['code'], 'INDEPENDENT_VALIDATION_REJECTED')
                self.assertFalse(result['finalValidationAccepted'])
                self.assertTrue(result['temporaryApiKeyRevoked'])
                self.assertEqual(len(self.inference_calls(client)), 1)

    def test_oversized_and_duplicate_responses_fail_without_raw_output(self):
        # Duplicate top-level session keys test the parser before provenance checks.
        duplicate = ('{"session_id":"' + SESSION_ID + '","session_id":"' + SESSION_ID + '"}').encode()
        for raw, expected in [(b'x' * (smoke.MAX_BYTES + 1), 'RESPONSE_TOO_LARGE'),
                              (duplicate, 'DUPLICATE_RESPONSE_KEY')]:
            with self.subTest(code=expected):
                result, _, mcp, _ = self.run_probe(Langflow(output=lambda _, raw=raw: raw))
                self.assertEqual(result['code'], expected)
                self.assertTrue(result['temporaryApiKeyRevoked'])
                self.assertEqual([path for path, _ in mcp.calls], ['/prepare-request'])

    def test_incomplete_runtime_evidence_never_reports_pass(self):
        for field, value in [('traceCount', 0), ('unfinishedTraces', 1), ('failedTraces', 1),
                             ('modelCallsObserved', 5), ('successfulMcpToolCalls', 0), ('unexpectedToolCalls', 1)]:
            evidence = {**COMPLETE_EVIDENCE, field: value}
            with self.subTest(field=field):
                result, _, _, _ = self.run_probe(observer=Mock(return_value=evidence))
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['code'], 'RUNTIME_EVIDENCE_INCOMPLETE')
                self.assertTrue(result['temporaryApiKeyRevoked'])
        result, _, _, _ = self.run_probe(observer=Mock(side_effect=RuntimeError(ENV['LANGFLOW_DATABASE_URL'])))
        self.assertEqual(result['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
