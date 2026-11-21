"""Local Ollama bootstrap against httpx.MockTransport fakes; no server, daemon or model is contacted.

The HTTP fakes need httpx, which Langflow installs: run this file with the pinned Langflow
virtualenv's Python. Minimal hosts without httpx still run the offline flow and path checks.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

try:
    import httpx
except ImportError:  # minimal CI host; the Langflow environment provides httpx
    httpx = None


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_ollama_bootstrap', ROOT / 'local_ollama_bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
ARTIFACT = ROOT / 'jeju-tour-agent-ollama-v2.flow.json'
USER = 'operator'
PASSWORD = 'private-password-' + 'p' * 24
TOKEN = 'synthetic-mcp-token-' + 'm' * 44
ACCESS = 'access-' + 'a' * 60
API_KEY = 'sk-' + 'K' * 43
ENVIRONMENT = {'LANGFLOW_SUPERUSER': USER, 'LANGFLOW_SUPERUSER_PASSWORD': PASSWORD, 'MCP_SERVICE_TOKEN': TOKEN}
SECRETS = (PASSWORD, TOKEN, ACCESS, API_KEY)
FLOW_ID = 'e7b2be4f-a8da-492d-9736-5d234bf91a22'
KEY_ID = '0c7a7f61-3d0f-4c47-9d0b-1f6a8b2b3c4d'
DIGEST = 'd' * 64
READY = {'ready': True, 'catalogSchemaVersion': '2.0', 'dataMode': 'app-export', 'placeCount': 981,
         'readOnly': True, 'productionReady': False, 'dataVersion': 'sha256:' + 'b' * 64}
OLLAMA_METADATA = {('GET', '/api/version'), ('POST', '/api/show'), ('GET', '/api/tags')}


def node(node_id, kind, fields):
    return {'id': node_id, 'type': 'genericNode', 'position': {'x': 0, 'y': 0},
            'data': {'id': node_id, 'type': kind, 'node': {'template': fields}}}


def edge(source, target, field):
    return {'id': f'{source}->{target}:{field}', 'source': source, 'target': target,
            'data': {'targetHandle': {'fieldName': field}}}


def synthetic_flow(model='qwen3:4b'):
    """Six-node stand-in with the reviewed wiring; no real component code or catalog content."""
    validator = {'code': {'value': 'import json\n\nVALIDATION_BASE_URL = "http://127.0.0.1:8789"\n'},
                 'service_token': {'value': 'JEJU_MCP_TOKEN', 'load_from_db': True}}
    agent = {'code': {'value': 'class AgentComponent:\n    def _get_llm(self):\n'
                               '        return {"base_url": "http://127.0.0.1:11434", "reasoning": False}\n'},
             'model': {'value': [{'name': model, 'provider': 'Ollama'}]},
             'api_key': {'value': '', 'load_from_db': False}, 'max_iterations': {'value': 4},
             'n_messages': {'value': 0}, 'add_calculator_tool': {'value': False},
             'add_current_date_tool': {'value': False}, 'handle_parsing_errors': {'value': False}}
    tools = {'mcp_server': {'value': {'name': 'jeju-mcp-v2', 'config': {}}}, 'headers': {'value': {}},
             'tools_metadata': {'value': [{'name': name} for name in
                                          ('places.search', 'places.get', 'constraints.check')]}}
    nodes = [node('ChatInput-s', 'ChatInput', {}),
             node('JejuPrepareRequest-s', 'JejuPrepareRequest', copy.deepcopy(validator)),
             node('Agent-s', 'Agent', agent), node('MCPTools-s', 'MCPTools', tools),
             node('JejuFinalValidation-s', 'JejuFinalValidation', copy.deepcopy(validator)),
             node('ChatOutput-s', 'ChatOutput', {})]
    edges = [edge('ChatInput-s', 'JejuPrepareRequest-s', 'original_request'),
             edge('JejuPrepareRequest-s', 'Agent-s', 'input_value'),
             edge('JejuPrepareRequest-s', 'JejuFinalValidation-s', 'prepared_request'),
             edge('MCPTools-s', 'Agent-s', 'tools'), edge('Agent-s', 'JejuFinalValidation-s', 'agent_response'),
             edge('JejuFinalValidation-s', 'ChatOutput-s', 'input_value')]
    return {'name': '합성 로컬 Ollama 평가 흐름', 'description': '테스트 전용 합성 흐름', 'endpoint_name': None,
            'data': {'nodes': nodes, 'edges': edges, 'viewport': {'x': 0, 'y': 0, 'zoom': 1}}}


def fields(flow, kind):
    return next(item for item in flow['data']['nodes'] if item['data']['type'] == kind)['data']['node']['template']


class OfflineChecksTest(unittest.TestCase):
    def test_reviewed_shape_is_imported_unchanged_without_id_or_endpoint_alias(self):
        flow = synthetic_flow()
        flow.update(id=FLOW_ID, endpoint_name='custom-alias')
        before = copy.deepcopy(flow)
        imported = bootstrap.checked_flow(flow, 'qwen3:4b')
        self.assertEqual(flow, before)
        self.assertEqual(imported['data'], before['data'])
        self.assertNotIn('id', imported)
        self.assertIsNone(imported['endpoint_name'])

    def test_altered_or_malformed_flows_are_rejected_with_fixed_codes(self):
        def bypass(flow):
            flow['data']['edges'][-1] = edge('Agent-s', 'ChatOutput-s', 'input_value')

        def remote_validator(flow):
            code = fields(flow, 'JejuFinalValidation')['code']
            code['value'] = code['value'].replace('http://127.0.0.1:8789', 'https://unreviewed.invalid')

        mutations = {
            'FLOW_FILE_INVALID': lambda flow: flow.pop('data'),
            'FLOW_COMPONENTS_MISMATCH': lambda flow: flow['data']['nodes'].append(copy.deepcopy(flow['data']['nodes'][0])),
            'FLOW_IDS_MISMATCH': lambda flow: flow['data']['nodes'][5].update(id='ChatInput-s'),
            'FLOW_VALIDATION_BYPASS_REJECTED': bypass,
            'VALIDATION_DESTINATION_MISMATCH': remote_validator,
            'MCP_CREDENTIAL_REFERENCE_MISMATCH': lambda flow: fields(flow, 'JejuPrepareRequest')['service_token'].update(
                load_from_db=False),
            'MCP_SERVER_REFERENCE_MISMATCH': lambda flow: fields(flow, 'MCPTools')['headers'].update(
                value={'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}),
            'MCP_TOOL_SET_MISMATCH': lambda flow: fields(flow, 'MCPTools')['tools_metadata']['value'].append(
                {'name': 'system.execute'}),
            'OLLAMA_MODEL_SELECTION_MISMATCH': lambda flow: fields(flow, 'Agent')['model']['value'][0].update(
                provider='Google Generative AI'),
            'OLLAMA_DESTINATION_MISMATCH': lambda flow: fields(flow, 'Agent')['code'].update(
                value='def _get_llm(self):\n    return {"base_url": "https://ollama.invalid"}\n'),
            'AGENT_LIMITS_MISMATCH': lambda flow: fields(flow, 'Agent')['max_iterations'].update(value=8),
        }
        for code, mutate in mutations.items():
            with self.subTest(code=code):
                flow = synthetic_flow()
                mutate(flow)
                with self.assertRaisesRegex(bootstrap.BootstrapError, f'^{code}$'):
                    bootstrap.checked_flow(flow, 'qwen3:4b')
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^OLLAMA_MODEL_SELECTION_MISMATCH$'):
            bootstrap.checked_flow(synthetic_flow(), 'qwen3:8b')

    def test_credentials_and_inline_secrets_stop_before_any_request(self):
        for name in ENVIRONMENT:
            with self.subTest(missing=name):
                with self.assertRaisesRegex(bootstrap.BootstrapError, '^BOOTSTRAP_CREDENTIALS_INVALID$'):
                    bootstrap.bootstrap(None, None, None, {**ENVIRONMENT, name: ''}, synthetic_flow())
        flow = synthetic_flow()
        flow['description'] = 'Bearer ' + TOKEN
        with self.assertRaisesRegex(bootstrap.BootstrapError, '^INLINE_SECRET_REJECTED$'):
            bootstrap.bootstrap(None, None, None, ENVIRONMENT, flow)
        # Short common words such as Langflow's default password occur in real component JSON.
        bootstrap.without_secrets({'dependency': 'langflow'}, [TOKEN, 'langflow'])

    def test_key_path_is_refused_before_any_request(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory, 'existing.env')
            existing.write_text('keep\n', encoding='ascii')
            for path, code in [(existing, 'API_KEY_FILE_EXISTS'),
                               (Path(directory, 'missing', 'key.env'), 'API_KEY_DIRECTORY_MISSING')]:
                with self.subTest(code=code):
                    with self.assertRaisesRegex(bootstrap.BootstrapError, f'^{code}$'):
                        bootstrap.bootstrap(None, None, None, ENVIRONMENT, synthetic_flow(), api_key_path=path)
            if hasattr(os, 'symlink') and os.name == 'posix':
                dangling = Path(directory, 'dangling.env')
                os.symlink(Path(directory, 'absent'), dangling)
                with self.assertRaisesRegex(bootstrap.BootstrapError, '^API_KEY_FILE_EXISTS$'):
                    bootstrap.check_key_path(dangling)
            self.assertEqual(existing.read_text(encoding='ascii'), 'keep\n')

    @unittest.skipUnless(ARTIFACT.exists(), 'committed Ollama evaluation flow is not in this checkout')
    def test_committed_ollama_flow_matches_the_bootstrap_contract(self):
        flow = json.loads(ARTIFACT.read_text(encoding='utf-8'))
        self.assertEqual(bootstrap.checked_flow(flow, bootstrap.DEFAULT_MODEL)['data'], flow['data'])


def reply(value, status=200):
    return httpx.Response(status, json=value)


class FakeLangflow:
    """Stateful subset of the Langflow 1.13.3 routes the bootstrap uses; anything else is recorded."""

    def __init__(self, *, anonymous=False, auto_login=False, version='1.13.3'):
        self.anonymous, self.auto_login, self.version = anonymous, auto_login, version
        self.variables, self.credential_values, self.server, self.flows, self.keys = [], {}, None, {}, {}
        self.calls, self.unexpected, self.flow_queries = [], [], []
        self.alter_import = self.on_key_created = None
        self.delete_status = 200

    def writes(self):
        return [call for call in self.calls if call[0] != 'GET']

    def __call__(self, request):
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        signed_in = request.headers.get('Authorization') == 'Bearer ' + ACCESS
        if (method, path) == ('GET', '/api/v1/users/whoami'):
            return reply({'is_active': True}) if signed_in or self.anonymous else reply({'detail': 'no'}, 401)
        if (method, path) == ('GET', '/api/v1/auto_login'):
            return reply({'access_token': ACCESS}) if self.auto_login else reply({'detail': {'auto_login': False}}, 403)
        if (method, path) == ('POST', '/api/v1/login'):
            form = parse_qs(request.content.decode())
            if form == {'username': [USER], 'password': [PASSWORD]}:
                return reply({'access_token': ACCESS, 'refresh_token': 'r' * 64, 'token_type': 'bearer'})
            return reply({'detail': 'Incorrect password ' + ''.join(form.get('password', []))}, 401)
        if (method, path) == ('GET', '/health_check'):
            return reply({'status': 'ok', 'chat': 'ok', 'db': 'ok'})
        if (method, path) == ('GET', '/api/v1/version'):
            return reply({'version': self.version, 'main_version': self.version, 'package': 'Langflow'})
        if not signed_in:
            return reply({'detail': 'Not authenticated'}, 401)
        if path == '/api/v1/variables/' and method in ('GET', 'POST'):
            if method == 'GET':
                return reply(self.variables)
            body = json.loads(request.content)
            self.credential_values[body['name']] = body['value']
            row = {'id': '4f1f3bb6-4c39-4b51-8f43-6f2e4f0b9a10', 'name': body['name'], 'type': body['type'],
                   'value': None, 'has_value': True, 'default_fields': body['default_fields']}
            self.variables.append(row)
            return reply(row, 201)
        if path == '/api/v2/mcp/servers/jeju-mcp-v2' and method in ('GET', 'POST'):
            if method == 'POST':
                if self.server is not None:
                    return reply({'detail': 'Server already exists.'}, 409)
                self.server = json.loads(request.content)
            if self.server is None:
                return reply({'detail': 'Server not found.'}, 404)
            masked = copy.deepcopy(self.server)
            masked['headers'] = {key: '********' for key in masked.get('headers', {})}
            return reply(masked)
        if path == '/api/v1/flows/' and method == 'GET':
            self.flow_queries.append(dict(request.url.params))
            return reply([{'id': key, 'name': value['name'], 'folder_id': 'folder'} for key, value in self.flows.items()])
        if path == '/api/v1/flows/' and method == 'POST':
            flow = json.loads(request.content)
            self.posted_flow = copy.deepcopy(flow)
            if self.alter_import:
                self.alter_import(flow)
            flow.update(id=FLOW_ID, user_id='owner', folder_id='folder', updated_at='2026-11-21T00:00:00Z')
            self.flows[FLOW_ID] = flow
            return reply(flow, 201)
        if method == 'GET' and path.startswith('/api/v1/flows/') and path.rsplit('/', 1)[1] in self.flows:
            return reply(self.flows[path.rsplit('/', 1)[1]])
        if (method, path) == ('POST', '/api/v1/api_key/'):
            self.keys[KEY_ID] = json.loads(request.content)
            if self.on_key_created:
                self.on_key_created()
            return reply({'id': KEY_ID, 'api_key': API_KEY, 'name': self.keys[KEY_ID].get('name'), 'user_id': 'owner',
                          'is_active': True, 'total_uses': 0, 'last_used_at': None, 'expires_at': None})
        if (method, path) == ('DELETE', f'/api/v1/api_key/{KEY_ID}'):
            if self.delete_status == 200:
                self.keys.pop(KEY_ID, None)
            return reply({'detail': 'API Key deleted'}, self.delete_status)
        self.unexpected.append((method, path))
        return reply({'detail': 'Not Found'}, 404)


class FakeMCP:
    def __init__(self, ready=None, *, down=False):
        self.ready, self.down, self.calls = dict(READY) if ready is None else ready, down, []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError('refused while sending ' + TOKEN, request=request)
        if (request.method, request.url.path) != ('GET', '/readyz'):
            return reply({'error': 'Route not available.'}, 404)
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return reply({'error': 'Unauthorized.'}, 401)
        return reply(self.ready)


class FakeOllama:
    def __init__(self, *, down=False, models=('qwen3:4b',), capabilities=('completion', 'tools', 'thinking'),
                 extra=None):
        self.down, self.models, self.capabilities, self.extra = down, models, capabilities, extra or {}
        self.calls, self.shown = [], []

    def __call__(self, request):
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if self.down:
            raise httpx.ConnectError('connection refused', request=request)
        details = {'format': 'gguf', 'family': 'qwen3', 'parameter_size': '4.0B', 'quantization_level': 'Q4_K_M'}
        if (method, path) == ('GET', '/api/version'):
            return reply({'version': '0.12.6'})
        if (method, path) == ('POST', '/api/show'):
            body = json.loads(request.content)
            self.shown.append(body)
            if body.get('model') not in self.models:
                return reply({'error': 'model not found'}, 404)
            shown = {'modelfile': '# private modelfile', 'template': '{{ .Prompt }}', 'details': details,
                     'model_info': {'general.architecture': 'qwen3'}, **self.extra}
            if self.capabilities is not None:
                shown['capabilities'] = list(self.capabilities)
            return reply(shown)
        if (method, path) == ('GET', '/api/tags'):
            return reply({'models': [{'name': name, 'model': name, 'digest': DIGEST, 'size': 2_500_000_000,
                                      'details': details} for name in self.models]})
        return reply({'error': 'not a metadata route'}, 404)


@unittest.skipIf(httpx is None, 'httpx from the pinned Langflow environment is required')
class LocalOllamaBootstrapTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.flow_path = self.folder / 'flow.json'
        self.flow_path.write_text(json.dumps(synthetic_flow(), ensure_ascii=False), encoding='utf-8')
        self.langflow, self.mcp, self.ollama = FakeLangflow(), FakeMCP(), FakeOllama()

    def run_main(self, *arguments, environment=ENVIRONMENT, flow=True):
        handlers = {bootstrap.LANGFLOW_URL: self.langflow, bootstrap.MCP_URL: self.mcp,
                    bootstrap.OLLAMA_URL: self.ollama}
        original, options = httpx.Client, []

        def client(**kwargs):
            options.append(kwargs)
            return original(**kwargs, transport=httpx.MockTransport(handlers[kwargs['base_url']]))
        stdout, stderr = io.StringIO(), io.StringIO()
        prefix = ['--flow', str(self.flow_path)] if flow else []
        with patch.object(httpx, 'Client', side_effect=client), patch.dict(os.environ, environment), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = bootstrap.main(prefix + list(arguments))
        text = stdout.getvalue()
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(len(text.splitlines()), 1)
        self.assertTrue(text.isascii())
        for secret in (*SECRETS, *environment.values()):
            if len(secret) >= 16:
                self.assertNotIn(secret, text)
        self.assertTrue(all(item['trust_env'] is False and item['follow_redirects'] is False for item in options))
        # Ollama sees metadata routes only, and nothing asks Langflow to build or run a flow.
        self.assertLessEqual(set(self.ollama.calls), OLLAMA_METADATA)
        self.assertEqual(self.langflow.unexpected, [])
        result = json.loads(text)
        self.assertEqual(status, 0 if result['status'] == 'passed' else 1)
        self.assertIs(result['modelExecuted'], False)
        return result

    def assert_failed(self, code, *arguments, **kwargs):
        result = self.run_main(*arguments, **kwargs)
        self.assertEqual(result, {'status': 'failed', 'code': code, 'modelExecuted': False})

    def test_first_run_imports_reviewed_flow_and_reports_endpoint_without_model_execution(self):
        result = self.run_main()
        self.assertEqual(result, {
            'status': 'passed', 'langflowVersion': '1.13.3', 'authenticated': True, 'flowStatus': 'imported',
            'flowId': FLOW_ID, 'endpoint': f'http://127.0.0.1:7860/api/v1/run/{FLOW_ID}?stream=false',
            'mcpCredential': 'created', 'mcpServer': 'registered', 'dataMode': 'app-export', 'placeCount': 981,
            'dataVersion': READY['dataVersion'], 'ollamaVersion': '0.12.6', 'model': 'qwen3:4b',
            'modelDigest': DIGEST, 'modelQuantization': 'Q4_K_M', 'modelParameterSize': '4.0B',
            'apiKeyWritten': False, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(self.langflow.credential_values, {'JEJU_MCP_TOKEN': TOKEN})
        self.assertEqual(self.langflow.variables[0]['type'], 'Credential')
        self.assertEqual(self.langflow.server, {'url': 'http://127.0.0.1:8789/mcp', 'mode': 'Streamable_HTTP',
                                                'headers': {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}})
        self.assertEqual(self.langflow.flows[FLOW_ID]['data'], synthetic_flow()['data'])
        self.assertNotIn('id', self.langflow.posted_flow)
        self.assertEqual(self.langflow.flow_queries, [bootstrap.FLOW_QUERY])
        self.assertEqual(self.ollama.shown, [{'model': 'qwen3:4b'}])
        self.assertEqual(self.mcp.calls, [('GET', '/readyz')])
        self.assertEqual(self.langflow.keys, {})
        self.assertNotIn(TOKEN, json.dumps(self.langflow.flows))

    def test_rerun_preserves_credential_registration_and_flow(self):
        self.run_main()
        state = copy.deepcopy((self.langflow.variables, self.langflow.server, self.langflow.flows))
        self.langflow.calls.clear()
        result = self.run_main()
        self.assertEqual((result['status'], result['flowId'], result['flowStatus'], result['mcpCredential'],
                          result['mcpServer']), ('passed', FLOW_ID, 'preserved-existing', 'preserved-existing',
                                                 'preserved-existing'))
        self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])
        self.assertEqual((self.langflow.variables, self.langflow.server, self.langflow.flows), state)

    def test_anonymous_access_or_auto_login_stops_before_credentials_are_sent(self):
        for options, code in [({'anonymous': True}, 'ANONYMOUS_ACCESS_ALLOWED'),
                              ({'auto_login': True}, 'AUTO_LOGIN_ENABLED')]:
            with self.subTest(code=code):
                self.langflow, self.mcp, self.ollama = FakeLangflow(**options), FakeMCP(), FakeOllama()
                self.assert_failed(code)
                self.assertEqual(self.langflow.writes(), [])
                self.assertEqual((self.mcp.calls, self.ollama.calls), ([], []))

    def test_wrong_langflow_version_and_rejected_login_change_nothing(self):
        self.langflow = FakeLangflow(version='1.13.2')
        self.assert_failed('LANGFLOW_VERSION_MISMATCH')
        self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])
        self.langflow = FakeLangflow()
        # The fake echoes the submitted password in its 401 body; it must not reach the output.
        self.assert_failed('LOGIN_FAILED', environment={**ENVIRONMENT, 'LANGFLOW_SUPERUSER_PASSWORD': 'w' * 24})
        self.assertEqual((self.mcp.calls, self.ollama.calls), ([], []))

    def test_unready_unreachable_or_unauthorized_mcp_stops_before_writes(self):
        mutations = {'ready': False, 'catalogSchemaVersion': '1.0', 'dataMode': 'fixture', 'placeCount': 980,
                     'readOnly': False, 'productionReady': True, 'dataVersion': 'sha256:short'}
        cases = [({**READY, key: value}, {}, 'MCP_CATALOG_NOT_READY') for key, value in mutations.items()]
        cases += [(None, {'down': True}, 'MCP_UNAVAILABLE')]
        for ready, options, code in cases:
            with self.subTest(code=code, ready=ready):
                self.langflow, self.mcp, self.ollama = FakeLangflow(), FakeMCP(ready, **options), FakeOllama()
                self.assert_failed(code)
                self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])
                self.assertEqual(self.ollama.calls, [])
        self.langflow, self.mcp = FakeLangflow(), FakeMCP()
        self.assert_failed('MCP_TOKEN_REJECTED', environment={**ENVIRONMENT, 'MCP_SERVICE_TOKEN': 'other-' + 'z' * 40})
        self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])

    def test_ollama_down_missing_model_or_tools_capability_stops_before_writes(self):
        cases = [(FakeOllama(down=True), 'OLLAMA_UNAVAILABLE'),
                 (FakeOllama(models=('llama3.2:3b',)), 'OLLAMA_MODEL_MISSING'),
                 (FakeOllama(capabilities=('completion',)), 'OLLAMA_TOOLS_UNSUPPORTED'),
                 (FakeOllama(capabilities=('tools', 'embedding')), 'OLLAMA_TOOLS_UNSUPPORTED'),
                 (FakeOllama(capabilities=None), 'OLLAMA_TOOLS_UNSUPPORTED'),
                 (FakeOllama(extra={'remote_host': 'https://ollama.invalid', 'remote_model': 'qwen3:4b'}),
                  'OLLAMA_REMOTE_MODEL_REJECTED')]
        for ollama, code in cases:
            with self.subTest(code=code, capabilities=ollama.capabilities):
                self.langflow, self.mcp, self.ollama = FakeLangflow(), FakeMCP(), ollama
                self.assert_failed(code)
                self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])

    def test_same_named_flow_with_different_data_is_never_overwritten(self):
        self.run_main()
        self.langflow.flows[FLOW_ID]['data']['nodes'][0]['position'] = {'x': 120, 'y': 40}
        before = copy.deepcopy(self.langflow.flows)
        self.langflow.calls.clear()
        self.assert_failed('EXISTING_FLOW_DIFFERS')
        self.assertEqual(self.langflow.flows, before)
        self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])
        self.langflow = FakeLangflow()
        self.langflow.alter_import = lambda flow: flow['data']['edges'].pop()
        self.assert_failed('IMPORTED_FLOW_DIFFERS')

    def test_unusable_or_duplicate_existing_credential_is_not_replaced(self):
        for rows, code in [([{'name': 'JEJU_MCP_TOKEN', 'type': 'Generic', 'has_value': True}], 'MCP_CREDENTIAL_UNAVAILABLE'),
                           ([{'name': 'JEJU_MCP_TOKEN', 'type': 'Credential', 'has_value': False}],
                            'MCP_CREDENTIAL_UNAVAILABLE'),
                           ([{'name': 'JEJU_MCP_TOKEN', 'type': 'Credential', 'has_value': True}] * 2,
                            'MCP_CREDENTIAL_CONFLICT')]:
            with self.subTest(code=code, rows=rows):
                self.langflow = FakeLangflow()
                self.langflow.variables = copy.deepcopy(rows)
                self.assert_failed(code)
                self.assertEqual(self.langflow.variables, rows)
                self.assertEqual(self.langflow.writes(), [('POST', '/api/v1/login')])

    def test_conflicting_mcp_registration_is_kept_and_no_flow_is_imported(self):
        conflicts = [{'url': 'http://127.0.0.1:8788/mcp', 'mode': 'Streamable_HTTP',
                      'headers': {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}},
                     {'url': 'http://127.0.0.1:8789/mcp', 'mode': 'Streamable_HTTP', 'headers': {}},
                     {'command': 'uvx', 'args': ['mcp-proxy'], 'url': 'http://127.0.0.1:8789/mcp',
                      'mode': 'Streamable_HTTP', 'headers': {'Authorization': 'x'}}]
        for server in conflicts:
            with self.subTest(server=server):
                self.langflow = FakeLangflow()
                self.langflow.server = copy.deepcopy(server)
                self.assert_failed('MCP_REGISTRATION_CONFLICT')
                self.assertEqual(self.langflow.server, server)
                self.assertNotIn(('POST', '/api/v2/mcp/servers/jeju-mcp-v2'), self.langflow.calls)
                self.assertEqual(self.langflow.flows, {})

    def test_secrets_and_exception_text_never_reach_the_output(self):
        self.mcp = FakeMCP(down=True)  # the transport error message contains the MCP token
        self.assert_failed('MCP_UNAVAILABLE')
        self.mcp = FakeMCP()
        with patch.object(bootstrap, 'bootstrap', side_effect=RuntimeError('private body ' + PASSWORD + TOKEN)):
            self.assert_failed('LOCAL_BOOTSTRAP_FAILED')
        self.assert_failed('FLOW_FILE_UNREADABLE', '--flow', str(self.folder / 'absent.json'), flow=False)
        self.flow_path.write_text('{"name": ', encoding='utf-8')
        self.assert_failed('FLOW_FILE_UNREADABLE')

    def test_api_key_is_written_once_to_a_private_file_and_never_printed(self):
        path = self.folder / 'langflow-api-key.env'
        result = self.run_main('--write-api-key', str(path))
        self.assertEqual((result['status'], result['apiKeyWritten']), ('passed', True))
        self.assertEqual(path.read_bytes(), f'LANGFLOW_API_KEY={API_KEY}\n'.encode('ascii'))
        self.assertEqual(self.langflow.keys, {KEY_ID: {'name': 'jeju-ollama-eval'}})
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_api_key_file_is_not_overwritten_and_no_key_is_created(self):
        path = self.folder / 'langflow-api-key.env'
        path.write_text('LANGFLOW_API_KEY=keep\n', encoding='ascii')
        self.assert_failed('API_KEY_FILE_EXISTS', '--write-api-key', str(path))
        self.assertEqual(path.read_text(encoding='ascii'), 'LANGFLOW_API_KEY=keep\n')
        self.assertEqual(self.langflow.calls, [])
        # A file that appears after the pre-check is kept too, and the new key is revoked.
        path.unlink()
        self.langflow.on_key_created = lambda: path.write_text('other process\n', encoding='ascii')
        self.assert_failed('API_KEY_FILE_EXISTS', '--write-api-key', str(path))
        self.assertEqual(path.read_text(encoding='ascii'), 'other process\n')
        self.assertEqual(self.langflow.keys, {})
        self.assertIn(('DELETE', f'/api/v1/api_key/{KEY_ID}'), self.langflow.calls)
        path.unlink()
        self.langflow.delete_status = 500
        self.assert_failed('API_KEY_CLEANUP_FAILED', '--write-api-key', str(path))

    @unittest.skipUnless(ARTIFACT.exists(), 'committed Ollama evaluation flow is not in this checkout')
    def test_default_flow_is_the_committed_ollama_flow(self):
        result = self.run_main(flow=False)
        self.assertEqual((result['status'], result['flowStatus'], result['model']), ('passed', 'imported', 'qwen3:4b'))
        self.assertEqual(self.langflow.flows[FLOW_ID]['data'], json.loads(ARTIFACT.read_text(encoding='utf-8'))['data'])


if __name__ == '__main__':
    unittest.main()
