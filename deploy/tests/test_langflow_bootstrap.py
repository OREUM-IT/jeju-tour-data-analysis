import ast
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
VERSION_TOKEN = '2a4ee450-c0fe-47ae-a8ca-413b2b4b889c'


class Response:
    def __init__(self, value=None, status=200):
        self.value = value
        self.status_code = status

    def json(self):
        return copy.deepcopy(self.value)


class Langflow:
    def __init__(self, state=None):
        self.state = state if state is not None else {'variables': [], 'server': None, 'flows': {}}
        self.state.setdefault('credential_values', {})
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
            self.state['credential_values'][body['name']] = body['value']
            return Response(self.state['variables'][-1], 201)
        if route == '/api/v2/mcp/servers/jeju-mcp-v2':
            self.state['server'] = copy.deepcopy(kwargs['json'])
            return Response({}, 200)
        if route == '/api/v1/custom_component/update':
            body = kwargs['json']
            node = copy.deepcopy(body['frontend_node'])
            if body['field'] == 'mcp_server':
                node['template']['mcp_server']['value'] = body['field_value']
            # Langflow merges component headers over stored registration, then
            # resolves global variables before authenticating MCP discovery.
            headers = {**self.state['server']['headers'], **node['template']['headers']['value']}
            authorization = headers.get('Authorization', '').replace(
                '{{JEJU_MCP_TOKEN}}', self.state['credential_values'].get(bootstrap.MCP_TOKEN_NAME, 'missing'))
            if authorization != 'Bearer ' + ENVIRONMENT['MCP_SERVICE_TOKEN']:
                node['template']['tools_metadata']['value'] = []
            return Response(node)
        if route == '/api/v1/flows/':
            flow = copy.deepcopy(kwargs['json'])
            flow.update(id=FLOW_ID, updated_at='2026-11-19T00:00:00Z', user_id='database-owner',
                        version_token=VERSION_TOKEN)
            self.state['flows'][FLOW_ID] = flow
            return Response(flow, 201)
        raise AssertionError('Unexpected endpoint; model execution is forbidden in bootstrap tests.')

    def patch(self, route, **kwargs):
        self.calls.append(('PATCH', route, kwargs))
        if route == f'/api/v1/flows/{FLOW_ID}':
            flow = self.state['flows'][FLOW_ID]
            if kwargs['headers']['If-Match'] != flow['version_token']:
                return Response(status=409)
            if set(kwargs['json']) != {'data'}:
                raise AssertionError('Migration may only patch graph data.')
            flow['data'] = copy.deepcopy(kwargs['json']['data'])
            flow['version_token'] = 'f243ca63-2d0a-4c97-a418-bbd51eb420ca'
            return Response(flow)
        raise AssertionError('Unexpected flow mutation.')


class MCP:
    def get(self, route, **kwargs):
        if route != '/readyz':
            raise AssertionError('Bootstrap only checks fixed MCP readiness before discovery.')
        return Response({'ready': True, 'catalogSchemaVersion': '2.0', 'dataMode': 'app-export', 'placeCount': 981,
                         'readOnly': True, 'productionReady': False, 'dataVersion': 'sha256:' + 'b' * 64})


class LangflowBootstrapTest(unittest.TestCase):
    def legacy_state(self):
        client = Langflow()
        bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
        existing = client.state['flows'][FLOW_ID]
        node = next(node['data']['node'] for node in existing['data']['nodes'] if node['data']['type'] == 'MCPTools')
        existing['data'] = bootstrap.deployment_flow(TEMPLATE, node, direct_vertex=False)['data']
        return client.state

    def test_known_old_flow_requires_explicit_upgrade_and_preserves_identity_and_credentials(self):
        state = self.legacy_state()
        before = copy.deepcopy(state)
        denied = Langflow(state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^EXISTING_FLOW_DIFFERS$'):
            bootstrap.bootstrap(denied, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(state, before)
        self.assertFalse(any(method == 'PATCH' for method, _, _ in denied.calls))
        client = Langflow(state)
        result = bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE, upgrade_vertex_client=True)
        self.assertEqual(result['flowStatus'], 'upgraded-vertex-client')
        writes = [kwargs for method, _, kwargs in client.calls if method == 'PATCH']
        self.assertEqual(len(writes), 1)
        self.assertEqual(set(writes[0]['json']), {'data'})
        self.assertEqual(writes[0]['headers'], {'If-Match': VERSION_TOKEN})
        for key in ('variables', 'credential_values', 'server'):
            self.assertEqual(state[key], before[key])
        outer = lambda flow: {key: value for key, value in flow.items() if key not in ('data', 'version_token')}
        self.assertEqual(outer(state['flows'][FLOW_ID]), outer(before['flows'][FLOW_ID]))
        again = Langflow(state)
        repeated = bootstrap.bootstrap(again, MCP(), ENVIRONMENT, TEMPLATE, upgrade_vertex_client=True)
        self.assertEqual(repeated['flowStatus'], 'preserved-existing')
        self.assertFalse(any(method == 'PATCH' for method, _, _ in again.calls))

    def test_upgrade_refuses_unrecognized_edits_and_concurrent_graph_changes(self):
        state = self.legacy_state()
        agent = next(node for node in state['flows'][FLOW_ID]['data']['nodes'] if node['data']['type'] == 'Agent')
        agent['data']['node']['template']['code']['value'] += '\n# User edit must survive\n'
        before = copy.deepcopy(state)
        client = Langflow(state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^EXISTING_FLOW_DIFFERS$'):
            bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE, upgrade_vertex_client=True)
        self.assertEqual(state, before)
        self.assertFalse(any(method == 'PATCH' for method, _, _ in client.calls))
        client = Langflow(self.legacy_state())
        original_patch = client.patch

        def edit_before_patch(route, **kwargs):
            client.state['flows'][FLOW_ID]['version_token'] = '5dfb23ce-a520-47dc-a6e9-2f14a98daf42'
            client.state['flows'][FLOW_ID]['data']['edges'].pop()
            return original_patch(route, **kwargs)
        with patch.object(client, 'patch', side_effect=edit_before_patch):
            with self.assertRaisesRegex(bootstrap.BootstrapError, '^HTTP_STATUS_REJECTED$'):
                bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE, upgrade_vertex_client=True)
        self.assertEqual(len(client.state['flows'][FLOW_ID]['data']['edges']), 5)
        self.assertEqual(sum(method == 'PATCH' for method, _, _ in client.calls), 1)

    def test_vertex_patch_preserves_policy_and_agent_guard_and_rejects_unknown_source(self):
        original = next(node['data']['node']['template']['code']['value'] for node in TEMPLATE['data']['nodes']
                        if node['data']['type'] == 'Agent')
        changed = bootstrap.patch_vertex_client(original)

        def without_llm_method(code):
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    node.body = [item for item in node.body
                                 if not (isinstance(item, ast.FunctionDef) and item.name == '_get_llm')]
            return ast.dump(tree, include_attributes=False)
        self.assertEqual(without_llm_method(original), without_llm_method(changed))
        self.assertIn('await self.arequire_model_provider_policy(ModelProviderPolicyPurpose.USE)', changed)
        self.assertIn('result = await _guarded_agent_run(self, _run_once)', changed)
        self.assertIn('GUARDED_AGENT_MAX_SECONDS = 90.0', changed)
        self.assertIn('GUARDED_AGENT_MODEL_CALL_LIMIT = 4', changed)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^VERTEX_CLIENT_SOURCE_MISMATCH$'):
            bootstrap.patch_vertex_client(original.replace('"max_retries": 1, "timeout": 60',
                                                           '"max_retries": 5, "timeout": 60'))

    def test_direct_vertex_client_uses_only_service_account_and_enforces_model_allowlist(self):
        original = next(node['data']['node']['template']['code']['value'] for node in TEMPLATE['data']['nodes']
                        if node['data']['type'] == 'Agent')
        changed = bootstrap.patch_vertex_client(original)
        method = next(node for node in ast.walk(ast.parse(changed))
                      if isinstance(node, ast.FunctionDef) and node.name == '_get_llm')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'vertex-method-test', 'exec'), namespace)
        calls = []
        credentials = object()
        google = types.ModuleType('google')
        oauth2 = types.ModuleType('google.oauth2')
        oauth2.service_account = types.SimpleNamespace(Credentials=types.SimpleNamespace(
            from_service_account_info=lambda *_, **__: credentials))
        google.oauth2 = oauth2
        sdk = types.ModuleType('langchain_google_genai')
        sdk.ChatGoogleGenerativeAI = lambda **kwargs: calls.append(kwargs) or kwargs
        fake = types.SimpleNamespace(
            model=[{'name': 'gemini-3.8-flash', 'provider': 'Google Generative AI'}],
            api_key=json.dumps({'serviceAccount': {'project_id': 'example-project'}, 'location': 'global'}),
            _get_max_tokens_value=lambda: 2048)
        with patch.dict(sys.modules, {'google': google, 'google.oauth2': oauth2, 'langchain_google_genai': sdk}):
            result = namespace['_get_llm'](fake)
            self.assertIs(result['credentials'], credentials)
            self.assertEqual(result, {'model': 'gemini-3.8-flash', 'streaming': True, 'vertexai': True,
                                     'credentials': credentials, 'project': 'example-project', 'location': 'global',
                                     'google_api_key': None, 'max_retries': 1, 'timeout': 60, 'max_output_tokens': 2048})
            fake.model[0]['name'] = 'unreviewed-model'
            with self.assertRaisesRegex(ValueError, '^VERTEX_MODEL_SELECTION_INVALID$'):
                namespace['_get_llm'](fake)
        self.assertEqual(len(calls), 1)

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
        self.assertEqual(writes, ['/api/v1/login'] + ['/api/v1/custom_component/update'] * 4)
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

    def test_literal_registration_cannot_hide_stale_global_token(self):
        client = Langflow()
        client.state['variables'] = [{'name': bootstrap.MCP_TOKEN_NAME, 'type': 'Credential', 'has_value': True}]
        client.state['credential_values'][bootstrap.MCP_TOKEN_NAME] = 'stale-' + 'x' * 64
        client.state['server'] = {'url': bootstrap.MCP_URL + '/mcp', 'mode': 'Streamable_HTTP',
                                  'headers': {'Authorization': 'Bearer ' + ENVIRONMENT['MCP_SERVICE_TOKEN']}}
        previous = copy.deepcopy(client.state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^MCP_TOOL_SET_MISMATCH$'):
            bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(client.state, previous)
        probes = [kwargs['json']['frontend_node']['template'] for method, route, kwargs in client.calls
                  if method == 'POST' and route == '/api/v1/custom_component/update']
        self.assertEqual(len(probes), 2)
        for probe in probes:
            self.assertFalse(probe['use_cache']['value'])
            self.assertEqual(probe['headers']['value'], {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'})

    def test_valid_global_cannot_hide_stale_registered_header(self):
        client = Langflow()
        client.state['variables'] = [{'name': bootstrap.MCP_TOKEN_NAME, 'type': 'Credential', 'has_value': True}]
        client.state['credential_values'][bootstrap.MCP_TOKEN_NAME] = ENVIRONMENT['MCP_SERVICE_TOKEN']
        client.state['server'] = {'url': bootstrap.MCP_URL + '/mcp', 'mode': 'Streamable_HTTP',
                                  'headers': {'Authorization': 'Bearer stale-registration'}}
        previous = copy.deepcopy(client.state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^MCP_TOOL_SET_MISMATCH$'):
            bootstrap.bootstrap(client, MCP(), ENVIRONMENT, TEMPLATE)
        self.assertEqual(client.state, previous)
        probes = [kwargs['json']['frontend_node']['template'] for method, route, kwargs in client.calls
                  if method == 'POST' and route == '/api/v1/custom_component/update']
        self.assertEqual(len(probes), 4)
        self.assertEqual([probe['headers']['value'] for probe in probes],
                         [{'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}] * 2 + [{}, {}])
        self.assertTrue(all(probe['use_cache']['value'] is False for probe in probes))

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
            self.assertEqual(bootstrap.main([]), 1)
        self.assertEqual(json.loads(output.getvalue()), {
            'status': 'failed', 'code': 'LANGFLOW_BOOTSTRAP_FAILED', 'modelExecuted': False,
        })
        self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
