import ast
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base_tests = load('gateway_base_test_fixtures', ROOT / 'deploy/tests/test_langflow_bootstrap.py')
gateway = load('gateway_bootstrap', ROOT / 'deploy/langflow/gateway-bootstrap.py')
SOURCE = (ROOT / 'services/langflow/gateway_components.py').read_text()
TEMPLATE = base_tests.TEMPLATE
SETUP = base_tests.bootstrap
ENV = base_tests.ENVIRONMENT
KEY_ID = '2b225fed-37f7-43ba-8d45-91d45fa3f799'
SECRET = 'sk-' + 'synthetic' * 8
NOW = dt.datetime(2026, 11, 19, tzinfo=dt.timezone.utc)


class Langflow(base_tests.Langflow):
    def __init__(self, state=None):
        super().__init__(state)
        if not self.state['variables']:
            self.state['variables'] = [{'name': name, 'type': 'Credential', 'has_value': True}
                                       for name in (SETUP.MCP_TOKEN_NAME, SETUP.VERTEX_TOKEN_NAME)]
        self.state.setdefault('keys', {})

    def post(self, route, **kwargs):
        if route == '/api/v1/custom_component':
            self.calls.append(('POST', route, kwargs))
            source = kwargs['json']['code']
            classes = [node.name for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
                       and any(isinstance(base, ast.Name) and base.id == 'Component' for base in node.bases)]
            assert len(classes) == 1
            name = classes[0]
            display = {'JejuPrepareRequest': 'Jeju Prepare Request', 'JejuFinalValidation': 'Jeju Final Validation', 'JejuScopedTools': 'Jeju Scoped Tools'}[name]
            return base_tests.Response({'data': {'display_name': display,
                'template': {'code': {'value': source}, 'service_token': {'value': ''}},
                'outputs': [{'name': 'tools' if name == 'JejuScopedTools' else 'message'}]}})
        if route == '/api/v1/api_key/':
            self.calls.append(('POST', route, kwargs))
            value = {**kwargs['json'], 'id': KEY_ID, 'api_key': SECRET}
            self.state['keys'][KEY_ID] = value
            return base_tests.Response(value)
        return super().post(route, **kwargs)

    def delete(self, route, **kwargs):
        self.calls.append(('DELETE', route, kwargs))
        self.state['keys'].pop(route.rsplit('/', 1)[1], None)
        return base_tests.Response({}, 204)


def run(client, *, provision=True):
    return gateway.configure(client, base_tests.MCP(), ENV, TEMPLATE, SOURCE, SETUP, provision=provision, now=NOW)


class GatewayBootstrapTests(unittest.TestCase):
    def test_provision_only_adds_separate_scoped_flow_and_one_30_day_key(self):
        client = Langflow()
        previous = {'name': SETUP.FLOW_NAME, 'data': {'preserved': True}}
        client.state['flows']['existing-aws-flow'] = copy.deepcopy(previous)
        result = run(client)
        self.assertEqual(set(result), {'flowId', 'apiKey', 'flowSha256'})
        self.assertEqual(result['apiKey'], SECRET)
        stored = client.state['flows'][result['flowId']]
        self.assertEqual(stored['name'], gateway.FLOW_NAME)
        self.assertEqual(client.state['flows']['existing-aws-flow'], previous)
        self.assertEqual(result['flowSha256'], gateway.flow_sha256(stored['data']))
        self.assertEqual(client.state['keys'][KEY_ID]['expires_at'], '2026-12-19T00:00:00+00:00')
        self.assertTrue(client.state['keys'][KEY_ID]['name'].startswith('oreumit-agent-gateway-'))
        self.assertNotIn(SECRET, json.dumps(stored))
        self.assertFalse(any(route.startswith('/api/v1/run') or method == 'PATCH' for method, route, _ in client.calls))

    def test_new_graph_has_only_request_scoped_tools_and_preserves_final_source(self):
        client = Langflow()
        result = run(client)
        flow = client.state['flows'][result['flowId']]
        nodes = {node['data']['type']: node for node in flow['data']['nodes']}
        self.assertEqual(set(nodes), {'ChatInput', 'ChatOutput', 'Agent', 'JejuPrepareRequest', 'JejuFinalValidation', 'JejuScopedTools'})
        agent = nodes['Agent']['data']['node']['template']
        self.assertEqual((agent['max_tokens']['value'], agent['max_iterations']['value'], agent['n_messages']['value']), (2048, 4, 0))
        self.assertEqual(nodes['ChatOutput']['id'], 'ChatOutput-jejuV2')
        self.assertEqual(nodes['JejuFinalValidation']['id'], 'JejuFinalValidation-v2')
        edges = {(edge['source'], edge['target'], edge['data']['targetHandle']['fieldName']) for edge in flow['data']['edges']}
        self.assertEqual(len(edges), 7)
        self.assertIn(('JejuPrepareRequest-v2', gateway.SCOPED_ID, 'prepared_request'), edges)
        self.assertIn((gateway.SCOPED_ID, 'Agent-jejuV2', 'tools'), edges)
        self.assertIn(('JejuPrepareRequest-v2', 'JejuFinalValidation-v2', 'prepared_request'), edges)
        for kind in gateway.COMPONENTS:
            token = nodes[kind]['data']['node']['template']['service_token']
            self.assertEqual(token['value'], 'JEJU_MCP_TOKEN')
            self.assertIs(token['load_from_db'], True)

    def test_check_issues_no_key_and_never_creates_or_changes_stored_flow(self):
        first = Langflow()
        run(first)
        before = copy.deepcopy(first.state)
        checker = Langflow(first.state)
        result = run(checker, provision=False)
        self.assertEqual(result, {'status': 'passed', 'flowMatches': True, 'nodeCount': 6, 'edgeCount': 7,
                                 'toolCount': 3, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(before, checker.state)
        self.assertFalse(any(route == '/api/v1/api_key/' or (method == 'POST' and route == '/api/v1/flows/') for method, route, _ in checker.calls))
        self.assertNotIn(SECRET, json.dumps(result))
        empty = Langflow()
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_FLOW_NOT_PROVISIONED$'):
            run(empty, provision=False)
        self.assertFalse(empty.state['flows'] or empty.state['keys'])

    def test_reprovision_preserves_existing_flow_but_stored_graph_or_metadata_drift_refuses_key(self):
        first = Langflow()
        created = run(first)
        again = Langflow(first.state)
        self.assertEqual(run(again)['flowId'], created['flowId'])
        self.assertFalse(any(method == 'POST' and route == '/api/v1/flows/' for method, route, _ in again.calls))
        for field in ('data', 'description', 'endpoint_name'):
            state = copy.deepcopy(first.state)
            stored = state['flows'][created['flowId']]
            if field == 'data':
                stored['data']['edges'].pop()
            else:
                stored[field] = 'unreviewed-change'
            before = copy.deepcopy(state)
            altered = Langflow(state)
            with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_EXISTING_FLOW_DIFFERS$'):
                run(altered)
            self.assertEqual(before, state)
            self.assertFalse(any(route == '/api/v1/api_key/' for _, route, _ in altered.calls))

    def test_custom_component_mismatch_and_missing_credentials_fail_before_flow_or_key_creation(self):
        client = Langflow()
        client.state['variables'].pop()
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_CREDENTIAL_UNAVAILABLE$'):
            run(client)
        self.assertFalse(client.state['flows'] or client.state['keys'])
        client = Langflow()
        original = client.post
        def altered(route, **kwargs):
            result = original(route, **kwargs)
            if route == '/api/v1/custom_component':
                result.value['data']['template']['code']['value'] += '\n# unreviewed'
            return result
        with patch.object(client, 'post', side_effect=altered):
            with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_COMPONENT_BUILD_MISMATCH$'):
                run(client)
        self.assertFalse(client.state['flows'] or client.state['keys'])

    def test_invalid_key_is_revoked_without_returning_its_value(self):
        client = Langflow()
        original = client.post
        def altered(route, **kwargs):
            result = original(route, **kwargs)
            if route == '/api/v1/api_key/':
                result.value['expires_at'] = '2099-01-01T00:00:00Z'
            return result
        with patch.object(client, 'post', side_effect=altered):
            with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_API_KEY_INVALID$'):
                run(client)
        self.assertFalse(client.state['keys'])
        self.assertEqual(sum(method == 'DELETE' for method, _, _ in client.calls), 1)

    def test_graph_digest_matches_javascript_canonical_sorted_keys(self):
        client = Langflow()
        flow_id = run(client)['flowId']
        data = client.state['flows'][flow_id]['data']
        javascript = '''const fs=require('node:fs'),crypto=require('node:crypto');
const canon=v=>Array.isArray(v)?'['+v.map(canon).join(',')+']':v!==null&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canon(v[k])).join(',')+'}':JSON.stringify(v);
process.stdout.write(crypto.createHash('sha256').update(canon(JSON.parse(fs.readFileSync(0,'utf8')))).digest('hex'));'''
        actual = subprocess.run(['node', '-e', javascript], input=json.dumps(data), text=True, check=True, capture_output=True)
        self.assertEqual(actual.stdout, gateway.flow_sha256(data))

    def test_component_extraction_keeps_schema_classes_and_exactly_one_component(self):
        for name in gateway.COMPONENTS:
            source = gateway.component_source(SOURCE, name)
            classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
            self.assertIn('SearchArguments', [node.name for node in classes])
            self.assertEqual([node.name for node in classes if any(isinstance(base, ast.Name) and base.id == 'Component' for base in node.bases)], [name])

    def test_main_failure_never_prints_provider_body_or_credentials(self):
        output = io.StringIO()
        class Client:
            def __init__(self, **_kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *_args): pass
        with patch.dict(sys.modules, {'httpx': types.SimpleNamespace(Client=Client)}), \
             patch.object(gateway, 'load_setup', return_value=SETUP), \
             patch.object(Path, 'read_text', side_effect=[json.dumps(TEMPLATE), SOURCE]), \
             patch.object(gateway, 'configure', side_effect=RuntimeError('private response ' + SECRET)), \
             contextlib.redirect_stdout(output):
            self.assertEqual(gateway.main(['--provision']), 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'failed', 'code': 'GATEWAY_BOOTSTRAP_FAILED', 'modelExecuted': False})
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == '__main__':
    unittest.main()
