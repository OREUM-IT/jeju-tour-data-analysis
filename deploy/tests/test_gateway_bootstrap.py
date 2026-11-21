import ast
import asyncio
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
import uuid


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
            # ApiKeyBase._serialize_expires_last_used -> _as_utc_iso in
            # Langflow 1.13.3 drops microseconds in the POST/GET response.
            expiry = dt.datetime.fromisoformat(value['expires_at']).replace(microsecond=0).isoformat()
            return base_tests.Response({**value, 'expires_at': expiry})
        return super().post(route, **kwargs)

    def delete(self, route, **kwargs):
        self.calls.append(('DELETE', route, kwargs))
        self.state['keys'].pop(route.rsplit('/', 1)[1], None)
        return base_tests.Response({}, 204)

    def patch(self, route, **kwargs):
        self.calls.append(('PATCH', route, kwargs))
        flow = self.state['flows'].get(route.rsplit('/', 1)[1])
        if flow is None or not route.startswith('/api/v1/flows/'):
            raise AssertionError('Unexpected flow mutation.')
        if kwargs['headers']['If-Match'] != flow['version_token']:
            return base_tests.Response(status=409)
        if set(kwargs['json']) != {'data', 'description'}:
            raise AssertionError('Gateway migration may only patch graph data and description.')
        flow.update(copy.deepcopy(kwargs['json']))
        flow['version_token'] = str(uuid.uuid4())
        return base_tests.Response(flow)


def run(client, *, provision=True, now=NOW):
    return gateway.configure(client, base_tests.MCP(), ENV, TEMPLATE, SOURCE, SETUP, provision=provision, now=now)


def migrate(client, mode):
    return gateway.configure(client, base_tests.MCP(), ENV, TEMPLATE, SOURCE, SETUP, migrate=mode)


def agent_fields(flow):
    return next(node for node in flow['data']['nodes'] if node['data']['type'] == 'Agent')['data']['node']['template']


def mutations(client):
    return [(method, route) for method, route, _ in client.calls
            if method in ('PATCH', 'DELETE') or route in ('/api/v1/flows/', '/api/v1/api_key/') and method == 'POST'
            or route.startswith('/api/v1/run')]


class GuardRequest:
    """The ModelRequest fields the guard reads, with LangChain's immutable override."""

    def __init__(self, *, state, messages, system_message, tool_choice=None):
        self.state, self.messages = state, messages
        self.system_message, self.tool_choice = system_message, tool_choice

    def override(self, **changes):
        values = {name: getattr(self, name) for name in ('state', 'messages', 'system_message', 'tool_choice')}
        return GuardRequest(**{**values, **changes})


def load_guard():
    class AgentMiddleware:
        pass

    class AIMessage:
        def __init__(self, content=''):
            self.content = content

    class SystemMessage:
        def __init__(self, content):
            self.content = content

    middleware = types.ModuleType('langchain.agents.middleware')
    middleware.AgentMiddleware = AgentMiddleware
    messages = types.ModuleType('langchain_core.messages')
    messages.AIMessage, messages.SystemMessage = AIMessage, SystemMessage
    stubs = {'langchain': types.ModuleType('langchain'), 'langchain.agents': types.ModuleType('langchain.agents'),
             'langchain.agents.middleware': middleware, 'langchain_core': types.ModuleType('langchain_core'),
             'langchain_core.messages': messages}
    namespace = {}
    with patch.dict(sys.modules, stubs):
        exec(compile(gateway.FINAL_ANSWER_GUARD, '<gateway-final-answer-guard>', 'exec'), namespace)
    return namespace, AIMessage, SystemMessage


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
        self.assertEqual(stored['access_type'], 'PRIVATE')
        self.assertFalse(stored['webhook'] or stored['mcp_enabled'] or stored['is_component'])
        self.assertNotIn('last_tested_version', stored)
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
        self.assertEqual((agent['max_tokens']['value'], agent['max_iterations']['value'], agent['n_messages']['value']), (2048, 8, 0))
        code = agent['code']['value']
        self.assertEqual(code.count(gateway.GUARDED_LIMIT_LINES), 1)
        self.assertIn('GUARDED_AGENT_MODEL_CALL_LIMIT = 8\n', code)
        self.assertTrue(code.endswith('\n\n' + gateway.FINAL_ANSWER_GUARD))
        self.assertEqual(flow['description'], gateway.DESCRIPTION)
        ast.parse(code)
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
                                 'toolCount': 3, 'modelCallLimit': 8,
                                 'flowSha256': gateway.flow_sha256(next(iter(first.state['flows'].values()))['data']),
                                 'modelExecuted': False, 'productionReady': False})
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
        for field in ('data', 'description', 'endpoint_name', 'access_type'):
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

    def test_microsecond_clock_matches_langflow_seconds_only_expiry_serializer(self):
        client = Langflow()
        current = NOW.replace(hour=15, minute=16, second=17, microsecond=987654)
        result = run(client, now=current)
        self.assertEqual(result['apiKey'], SECRET)
        issued = next(kwargs['json'] for method, route, kwargs in client.calls
                      if method == 'POST' and route == '/api/v1/api_key/')
        self.assertEqual(issued['expires_at'], '2026-12-19T15:16:17+00:00')
        self.assertLessEqual(dt.datetime.fromisoformat(issued['expires_at']) - current, dt.timedelta(days=30))
        self.assertFalse(any(method == 'DELETE' for method, _, _ in client.calls))

    def test_expiry_check_still_rejects_different_missing_or_offsetless_lifetimes(self):
        for changed in ('2026-12-19T00:00:01+00:00', '2026-12-18T23:59:59+00:00',
                        '2026-12-19T00:00:00', None):
            with self.subTest(expiry_kind='missing' if changed is None else 'different_or_offsetless'):
                client = Langflow()
                original = client.post
                def altered(route, **kwargs):
                    result = original(route, **kwargs)
                    if route == '/api/v1/api_key/':
                        result.value['expires_at'] = changed
                    return result
                with patch.object(client, 'post', side_effect=altered):
                    with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_API_KEY_INVALID$'):
                        run(client)
                self.assertFalse(client.state['keys'])

    def test_budget_flow_changes_only_agent_limit_guard_and_description(self):
        legacy = gateway.build_flow(Langflow(), TEMPLATE, SOURCE, SETUP)
        pristine = copy.deepcopy(legacy)
        budget = gateway.budget_flow(legacy)
        self.assertEqual(legacy, pristine)
        old, new = agent_fields(legacy), agent_fields(budget)
        self.assertEqual((old['max_iterations']['value'], new['max_iterations']['value']), (4, 8))
        expected = (old['code']['value'].replace(gateway.LIMIT_LINE, gateway.GUARDED_LIMIT_LINES)
                    .replace('GUARDED_AGENT_MODEL_CALL_LIMIT = 4\n', 'GUARDED_AGENT_MODEL_CALL_LIMIT = 8\n')
                    + '\n\n' + gateway.FINAL_ANSWER_GUARD)
        self.assertEqual(new['code']['value'], expected)
        self.assertEqual((legacy['description'], budget['description']), (gateway.LEGACY_DESCRIPTION, gateway.DESCRIPTION))
        def without_budget(flow):
            flow = copy.deepcopy(flow)
            fields = agent_fields(flow)
            for name in ('code', 'max_iterations'):
                fields.pop(name)
            flow.pop('description')
            return flow
        self.assertEqual(without_budget(legacy), without_budget(budget))
        # Langflow 1.13.3 runs only imports, assignments, classes and functions at module level.
        kinds = {type(node).__name__ for node in ast.parse(gateway.FINAL_ANSWER_GUARD).body}
        self.assertLessEqual(kinds, {'Import', 'ImportFrom', 'Assign', 'ClassDef'})
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_TEMPLATE_MISMATCH$'):
            gateway.budget_flow(budget)
        changed = copy.deepcopy(legacy)
        agent_fields(changed)['code']['value'] = agent_fields(changed)['code']['value'].replace(gateway.LIMIT_LINE, '')
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_AGENT_GUARD_MISSING$'):
            gateway.budget_flow(changed)

    def test_final_answer_guard_reserves_only_the_last_allowed_call(self):
        namespace, AIMessage, SystemMessage = load_guard()
        guard_class = namespace['_GatewayFinalAnswerMiddleware']
        notice = namespace['GATEWAY_FINAL_ANSWER_NOTICE']
        for invalid in (True, 1, 0, 8.0, '8', None):
            with self.subTest(run_limit=invalid), self.assertRaisesRegex(ValueError, '^AGENT_EXECUTION_POLICY_INVALID$'):
                guard_class(run_limit=invalid)
        guard = guard_class(run_limit=8)
        system = SystemMessage('기존 지침')
        early = GuardRequest(state={'run_model_call_count': 6}, messages=[AIMessage()] * 6, system_message=system)
        self.assertIs(guard._final_answer_request(early), early)
        last = GuardRequest(state={'run_model_call_count': 7}, messages=[], system_message=system)
        final = guard._final_answer_request(last)
        self.assertEqual((final.tool_choice, final.system_message.content), ('none', '기존 지침\n\n' + notice))
        self.assertEqual((last.tool_choice, system.content), (None, '기존 지침'))
        # A missing or invalid counter falls back to this run's AI replies (n_messages=0).
        for state in ({}, {'run_model_call_count': True}, {'run_model_call_count': -1}, None):
            with self.subTest(state=state):
                request = GuardRequest(state=state, messages=['human', *[AIMessage()] * 7], system_message=None)
                final = guard._final_answer_request(request)
                self.assertEqual((final.tool_choice, final.system_message.content), ('none', notice))
        blocks = GuardRequest(state={'run_model_call_count': 7}, messages=[],
                              system_message=SystemMessage([{'type': 'text', 'text': 'a'}]))
        self.assertEqual(guard._final_answer_request(blocks).system_message.content,
                         [{'type': 'text', 'text': 'a'}, {'type': 'text', 'text': notice}])
        seen = []
        self.assertEqual(guard.wrap_model_call(last, lambda request: seen.append(request.tool_choice) or 'answer'), 'answer')
        async def handler(request):
            seen.append(request.tool_choice)
            return 'answer'
        self.assertEqual(asyncio.run(guard.awrap_model_call(early, handler)), 'answer')
        self.assertEqual(seen, ['none', None])

    def test_update_and_rollback_switch_only_exact_reviewed_graphs_without_keys(self):
        client = Langflow()
        provisioned = run(client)
        flow_id = provisioned['flowId']
        current = copy.deepcopy(client.state['flows'][flow_id])
        keys = copy.deepcopy(client.state['keys'])
        roller = Langflow(client.state)
        rolled = migrate(roller, 'rollback')
        stored = client.state['flows'][flow_id]
        self.assertEqual(rolled, {'status': 'passed', 'flowStatus': 'updated', 'flowId': flow_id,
                                  'flowSha256': gateway.flow_sha256(stored['data']), 'modelCallLimit': 4,
                                  'modelExecuted': False, 'productionReady': False})
        self.assertEqual(agent_fields(stored)['max_iterations']['value'], 4)
        self.assertNotIn('_GatewayFinalAnswerMiddleware', agent_fields(stored)['code']['value'])
        self.assertEqual(stored['description'], gateway.LEGACY_DESCRIPTION)
        for key in set(current) - {'data', 'description', 'version_token'}:
            self.assertEqual(stored[key], current[key])
        patches = [kwargs for method, _, kwargs in roller.calls if method == 'PATCH']
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]['headers'], {'If-Match': current['version_token']})
        updater = Langflow(client.state)
        updated = migrate(updater, 'update')
        self.assertEqual((updated['flowStatus'], updated['modelCallLimit'], updated['flowSha256']),
                         ('updated', 8, provisioned['flowSha256']))
        self.assertEqual({key: value for key, value in client.state['flows'][flow_id].items() if key != 'version_token'},
                         {key: value for key, value in current.items() if key != 'version_token'})
        again = Langflow(client.state)
        self.assertEqual(migrate(again, 'update')['flowStatus'], 'already-current')
        for checker in (roller, updater, again):
            self.assertEqual([method for method, _ in mutations(checker)], ['PATCH'] if checker is not again else [])
        self.assertEqual(client.state['keys'], keys)
        self.assertEqual(len(client.state['flows']), 1)
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_MODE_INVALID$'):
            gateway.configure(Langflow(client.state), base_tests.MCP(), ENV, TEMPLATE, SOURCE, SETUP,
                              provision=True, migrate='update')

    def test_migration_refuses_unknown_graphs_missing_flow_and_concurrent_edits(self):
        client = Langflow()
        flow_id = run(client)['flowId']
        for field in ('data', 'description', 'access_type'):
            state = copy.deepcopy(client.state)
            stored = state['flows'][flow_id]
            if field == 'data':
                stored['data']['edges'].pop()
            else:
                stored[field] = 'unreviewed-change'
            before = copy.deepcopy(state)
            for mode in ('update', 'rollback'):
                with self.subTest(field=field, mode=mode):
                    altered = Langflow(state)
                    with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_EXISTING_FLOW_DIFFERS$'):
                        migrate(altered, mode)
                    self.assertEqual(before, state)
                    self.assertEqual(mutations(altered), [])
        empty = Langflow()
        with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_FLOW_NOT_PROVISIONED$'):
            migrate(empty, 'update')
        self.assertEqual(mutations(empty), [])
        state = copy.deepcopy(client.state)
        before = copy.deepcopy(state)
        racer = Langflow(state)
        with patch.object(racer, 'patch', return_value=base_tests.Response(status=409)):
            with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_BOOTSTRAP_HTTP_REJECTED$'):
                migrate(racer, 'rollback')
        self.assertEqual(before, state)

    def test_unverified_update_restores_previous_graph_and_fails_closed(self):
        client = Langflow()
        flow_id = run(client)['flowId']
        migrate(Langflow(client.state), 'rollback')
        legacy = copy.deepcopy(client.state['flows'][flow_id])
        lossy = Langflow(client.state)
        original = lossy.patch
        def normalize_first(route, **kwargs):
            result = original(route, **kwargs)
            if sum(method == 'PATCH' for method, _, _ in lossy.calls) == 1:
                client.state['flows'][flow_id]['data']['nodes'][0]['position'] = {'x': 1, 'y': 1}
            return result
        with patch.object(lossy, 'patch', side_effect=normalize_first):
            with self.assertRaisesRegex(gateway.GatewayBootstrapError, '^GATEWAY_FLOW_UPDATE_UNVERIFIED$'):
                migrate(lossy, 'update')
        restored = client.state['flows'][flow_id]
        self.assertEqual((restored['data'], restored['description']), (legacy['data'], legacy['description']))
        self.assertEqual(sum(method == 'PATCH' for method, _, _ in lossy.calls), 2)

    def test_main_maps_modes_without_printing_credentials(self):
        class Client:
            def __init__(self, **_kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *_args): pass
        for argv, expected in ((['--check'], (False, None)), (['--provision'], (True, None)),
                               (['--update'], (False, 'update')), (['--rollback'], (False, 'rollback'))):
            seen = {}
            def configure(*_args, provision, migrate):
                seen.update(provision=provision, migrate=migrate)
                return {'status': 'passed', 'modelExecuted': False}
            output = io.StringIO()
            with self.subTest(argv=argv), patch.dict(sys.modules, {'httpx': types.SimpleNamespace(Client=Client)}), \
                 patch.object(gateway, 'load_setup', return_value=SETUP), \
                 patch.object(Path, 'read_text', side_effect=[json.dumps(TEMPLATE), SOURCE]), \
                 patch.object(gateway, 'configure', side_effect=configure), contextlib.redirect_stdout(output):
                self.assertEqual(gateway.main(argv), 0)
                self.assertEqual((seen['provision'], seen['migrate']), expected)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gateway.main(['--update', '--rollback'])

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
