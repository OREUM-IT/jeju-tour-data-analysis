import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('langflow_bootstrap', ROOT / 'deploy/langflow/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
TEMPLATE = json.loads((ROOT / 'services/langflow/jeju-tour-agent-vertex-v2.flow.json').read_text())
MCP_NODE = next(node['data']['node'] for node in TEMPLATE['data']['nodes'] if node['data']['type'] == 'MCPTools')
ENVIRONMENT = {'LANGFLOW_SUPERUSER': 'operator', 'LANGFLOW_SUPERUSER_PASSWORD': 'private-password-' + 'p' * 40,
               'MCP_SERVICE_TOKEN': 'm' * 64}
FLOW_ID = 'e7b2be4f-a8da-492d-9736-5d234bf91a22'


class Response:
    def __init__(self, value=None, status=200):
        self.value = value
        self.status_code = status

    def json(self):
        return copy.deepcopy(self.value)


class Langflow:
    def __init__(self, state=None):
        self.state = state if state is not None else {'variables': [], 'server': None, 'flows': {}}
        self.headers = {}
        self.calls = []

    def get(self, route, **kwargs):
        self.calls.append(('GET', route, kwargs))
        if route == '/api/v1/users/whoami':
            return Response({'is_active': True}) if self.headers else Response(status=401)
        if route == '/api/v1/auto_login':
            return Response(status=403)
        if route == '/health_check':
            return Response({'status': 'ok'})
        if route == '/api/v1/version':
            return Response({'version': '1.13.3'})
        if route == '/api/v1/variables/':
            return Response(self.state['variables'])
        if route == '/api/v2/mcp/servers/jeju-mcp-v2':
            config = copy.deepcopy(self.state['server'])
            if config:
                config['headers'] = {'Authorization': '********'}
            return Response(config, 200 if config else 404)
        if route == '/api/v1/all':
            return Response({'models_and_agents': {'MCPTools': MCP_NODE}})
        if route == '/api/v1/flows/':
            return Response([{'id': key, 'name': value['name']} for key, value in self.state['flows'].items()])
        if route.startswith('/api/v1/flows/'):
            return Response(self.state['flows'][route.rsplit('/', 1)[1]])
        raise AssertionError('Unexpected endpoint; model execution is forbidden in bootstrap tests.')

    def post(self, route, **kwargs):
        self.calls.append(('POST', route, kwargs))
        if route == '/api/v1/login':
            return Response({'access_token': 'a' * 64})
        if route == '/api/v1/variables/':
            body = kwargs['json']
            self.state['variables'].append({'name': body['name'], 'type': body['type'], 'has_value': True, 'value': None})
            return Response(self.state['variables'][-1], 201)
        if route == '/api/v2/mcp/servers/jeju-mcp-v2':
            self.state['server'] = copy.deepcopy(kwargs['json'])
            return Response({}, 200)
        if route == '/api/v1/custom_component/update':
            body = kwargs['json']
            node = copy.deepcopy(body['frontend_node'])
            if body['field'] == 'mcp_server':
                node['template']['mcp_server']['value'] = body['field_value']
            return Response(node)
        if route == '/api/v1/flows/':
            flow = copy.deepcopy(kwargs['json'])
            flow.update(id=FLOW_ID, updated_at='2026-11-19T00:00:00Z', user_id='database-owner')
            self.state['flows'][FLOW_ID] = flow
            return Response(flow, 201)
        raise AssertionError('Unexpected endpoint; model execution is forbidden in bootstrap tests.')


class MCP:
    def get(self, route, **kwargs):
        if route != '/readyz':
            raise AssertionError('Bootstrap only checks fixed MCP readiness before discovery.')
        return Response({'ready': True, 'catalogSchemaVersion': '2.0', 'dataMode': 'app-export', 'placeCount': 981,
                         'readOnly': True, 'productionReady': False, 'dataVersion': 'sha256:' + 'b' * 64})


class LangflowBootstrapTest(unittest.TestCase):
    def test_first_import_and_idempotent_rerun_preserve_local_flow_and_credentials(self):
        first = Langflow()
        unrelated = {'name': '기존 로컬 실험', 'data': {'keep': True}}
        first.state['flows']['existing'] = copy.deepcopy(unrelated)
        original_template = copy.deepcopy(TEMPLATE)
        result = bootstrap.bootstrap(first, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(result['flowStatus'], 'imported')
        self.assertFalse(result['vertexConfigured'])
        self.assertFalse(result['modelExecuted'])
        self.assertEqual(result['toolCount'], 3)
        self.assertEqual(first.state['flows']['existing'], unrelated)
        self.assertEqual(TEMPLATE, original_template)
        registered = first.state['flows'][FLOW_ID]
        for node in registered['data']['nodes']:
            if node['data']['type'] in ('JejuPrepareRequest', 'JejuFinalValidation'):
                self.assertIn(bootstrap.AWS_ASSIGNMENT, node['data']['node']['template']['code']['value'])
                self.assertNotIn(bootstrap.LOCAL_ASSIGNMENT, node['data']['node']['template']['code']['value'])
        self.assertNotIn(ENVIRONMENT['MCP_SERVICE_TOKEN'], json.dumps(registered))
        self.assertNotIn(ENVIRONMENT['LANGFLOW_SUPERUSER_PASSWORD'], json.dumps(registered))
        second = Langflow(first.state)
        repeated = bootstrap.bootstrap(second, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(repeated['flowStatus'], 'preserved-existing')
        writes = [route for method, route, _ in second.calls if method == 'POST']
        self.assertEqual(writes, ['/api/v1/login', '/api/v1/custom_component/update', '/api/v1/custom_component/update'])
        self.assertEqual(first.state['flows']['existing'], unrelated)

    def test_modified_existing_flow_is_never_overwritten(self):
        first = Langflow()
        bootstrap.bootstrap(first, MCP(), ENVIRONMENT, TEMPLATE)
        first.state['flows'][FLOW_ID]['data']['edges'].pop()
        before = copy.deepcopy(first.state)
        again = Langflow(first.state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^EXISTING_FLOW_DIFFERS$'):
            bootstrap.bootstrap(again, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(again.state, before)
        self.assertNotIn('/api/v1/flows/', [route for method, route, _ in again.calls if method == 'POST'])

    def test_bad_validation_destinations_duplicates_and_bypasses_are_rejected(self):
        for mutation in ('url', 'second_assignment', 'duplicate_node', 'bypass'):
            with self.subTest(mutation=mutation):
                flow = copy.deepcopy(TEMPLATE)
                prepare = next(node for node in flow['data']['nodes'] if node['data']['type'] == 'JejuPrepareRequest')
                code = prepare['data']['node']['template']['code']
                if mutation == 'url':
                    code['value'] = code['value'].replace('http://127.0.0.1:8789', 'https://unreviewed.invalid')
                elif mutation == 'second_assignment':
                    code['value'] += '\nVALIDATION_BASE_URL = "https://unreviewed.invalid"\n'
                elif mutation == 'duplicate_node':
                    flow['data']['nodes'].append(copy.deepcopy(prepare))
                else:
                    flow['data']['edges'].pop()
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.deployment_flow(flow, MCP_NODE)

    def test_existing_secret_and_server_conflicts_are_not_replaced(self):
        invalid = {'name': bootstrap.MCP_TOKEN_NAME, 'type': 'Credential', 'has_value': False}
        client = Langflow({'variables': [invalid], 'server': None, 'flows': {}})
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^MCP_CREDENTIAL_UNAVAILABLE$'):
            bootstrap.ensure_variables(client, ENVIRONMENT['MCP_SERVICE_TOKEN'])
        self.assertFalse(any(method == 'POST' for method, _, _ in client.calls))
        client.state['server'] = {'url': 'http://127.0.0.1:8789/mcp', 'mode': 'Streamable_HTTP',
                                  'headers': {'Authorization': '********'}}
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^MCP_REGISTRATION_CONFLICT$'):
            bootstrap.ensure_server(client)
        self.assertFalse(any(method == 'POST' for method, _, _ in client.calls))

    def test_existing_vertex_credential_is_reported_without_modification(self):
        client = Langflow()
        client.state['variables'].append({'name': bootstrap.VERTEX_TOKEN_NAME, 'type': 'Credential', 'has_value': True})
        result = bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertTrue(result['vertexConfigured'])
        created = [kwargs['json']['name'] for method, route, kwargs in client.calls
                   if method == 'POST' and route == '/api/v1/variables/']
        self.assertEqual(created, [bootstrap.MCP_TOKEN_NAME])

    def test_unexpected_tool_or_inline_secret_cannot_be_imported(self):
        bad_node = copy.deepcopy(MCP_NODE)
        bad_node['template']['tools_metadata']['value'].append({'name': 'system.execute'})
        with patch.dict(MCP_NODE, bad_node, clear=True):
            client = Langflow()
            with self.assertRaisesRegex(bootstrap.BootstrapError, '^MCP_TOOL_SET_MISMATCH$'):
                bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
            self.assertFalse(client.state['flows'])
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^INLINE_SECRET_REJECTED$'):
            bootstrap.without_secrets({'field': ENVIRONMENT['MCP_SERVICE_TOKEN']}, [ENVIRONMENT['MCP_SERVICE_TOKEN']])

    def test_anonymous_access_stops_before_credentials_or_writes(self):
        client = Langflow()
        with patch.object(client, 'get', return_value=Response({'is_active': True})):
            with self.assertRaisesRegex(bootstrap.BootstrapError, '^ANONYMOUS_ACCESS_ALLOWED$'):
                bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertFalse(client.calls)

    def test_main_sanitizes_http_exception_including_response_body(self):
        class Client:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
        output = io.StringIO()
        with patch.dict(sys.modules, {'httpx': types.SimpleNamespace(Client=Client)}), \
             patch.object(Path, 'read_text', return_value=json.dumps(TEMPLATE)), \
             patch.object(bootstrap, 'bootstrap', side_effect=RuntimeError('private response body and token')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(bootstrap.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {
            'status': 'failed', 'code': 'LANGFLOW_BOOTSTRAP_FAILED', 'modelExecuted': False,
        })
        self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
