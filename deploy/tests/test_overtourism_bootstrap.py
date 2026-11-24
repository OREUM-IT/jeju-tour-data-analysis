"""Overtourism diagnosis flow builder with fake Langflow/MCP clients; no model or network."""
import ast
import copy
import importlib.util
import io
import json
from pathlib import Path
import contextlib
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway_tests = load('overtourism_gateway_test_fixtures', ROOT / 'deploy/tests/test_gateway_bootstrap.py')
base_tests = gateway_tests.base_tests
overtourism = load('overtourism_bootstrap', ROOT / 'deploy/langflow/overtourism-bootstrap.py')
GATEWAY_SOURCE = (ROOT / 'deploy/langflow/gateway-bootstrap.py').read_text()
RULE_SOURCE = (ROOT / 'services/langflow/gateway_components.py').read_text()
COMPONENTS_SOURCE = (ROOT / 'services/langflow/overtourism_components.py').read_text()
gateway = overtourism.load_gateway(GATEWAY_SOURCE)
SETUP = base_tests.bootstrap
ENV = base_tests.ENVIRONMENT
DISPLAY = {'JejuPrepareRequest': 'Jeju Prepare Request', 'JejuFinalValidation': 'Jeju Final Validation',
           'JejuScopedTools': 'Jeju Scoped Tools', 'JejuOvertourismPrepare': 'Jeju Overtourism Prepare',
           'JejuOvertourismValidation': 'Jeju Overtourism Validation', 'JejuOvertourismTools': 'Jeju Overtourism Tools'}


class Langflow(gateway_tests.Langflow):
    def __init__(self, state=None, *, openai=True):
        super().__init__(state)
        if openai and not any(item['name'] == gateway.OPENAI_TOKEN_NAME for item in self.state['variables']):
            self.state['variables'].append({'name': gateway.OPENAI_TOKEN_NAME, 'type': 'Credential', 'has_value': True})

    def post(self, route, **kwargs):
        if route == '/api/v1/custom_component':
            self.calls.append(('POST', route, kwargs))
            source = kwargs['json']['code']
            classes = [node.name for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
                       and any(isinstance(base, ast.Name) and base.id == 'Component' for base in node.bases)]
            assert len(classes) == 1
            name = classes[0]
            tools = name in ('JejuScopedTools', 'JejuOvertourismTools')
            return base_tests.Response({'data': {'display_name': DISPLAY[name],
                'template': {'code': {'value': source}, 'service_token': {'value': ''}},
                'outputs': [{'name': 'tools' if tools else 'message'}]}})
        if route == '/api/v1/flows/':
            self.calls.append(('POST', route, kwargs))
            flow = copy.deepcopy(kwargs['json'])
            flow_id = str(uuid.uuid4())
            flow.update(id=flow_id, user_id='database-owner', version_token=str(uuid.uuid4()))
            self.state['flows'][flow_id] = flow
            return base_tests.Response(flow, 201)
        return super().post(route, **kwargs)


class MCP:
    def __init__(self, status=404, code='OVERTOURISM_SCOPE_UNAVAILABLE'):
        self.status, self.code, self.calls = status, code, []

    def post(self, route, **kwargs):
        self.calls.append((route, kwargs))
        assert route == '/gateway/overtourism/prepare'
        return base_tests.Response({'error': {'code': self.code}}, self.status)


def run(client, mode='provision', mcp=None):
    # Each real run opens a new anonymous HTTP client.
    client.headers = {}
    return overtourism.configure(client, mcp or MCP(), ENV, base_tests.TEMPLATE, gateway, RULE_SOURCE,
                                 COMPONENTS_SOURCE, SETUP, mode=mode)


def stored(client):
    flows = [flow for flow in client.state['flows'].values() if flow['name'] == overtourism.FLOW_NAME]
    assert len(flows) == 1
    return flows[0]


def mutations(client):
    return [(method, route) for method, route, _ in client.calls
            if method in ('PATCH', 'DELETE') or (method == 'POST' and route in ('/api/v1/flows/', '/api/v1/api_key/'))
            or route.startswith('/api/v1/run')]


class OvertourismBootstrapTest(unittest.TestCase):
    def test_provision_derives_the_diagnosis_flow_from_the_rule_action_build(self):
        client = Langflow()
        result = run(client)
        self.assertEqual(result['flowStatus'], 'created')
        self.assertEqual((result['nodeCount'], result['edgeCount'], result['toolCount'], result['modelCallLimit']), (6, 7, 3, 8))
        self.assertIs(result['modelExecuted'], False)
        flow = stored(client)
        self.assertEqual(result['flowSha256'], gateway.flow_sha256(flow['data']))
        self.assertEqual(flow['access_type'], 'PRIVATE')
        self.assertEqual(flow['description'], overtourism.DESCRIPTION)
        types = {node['data']['type']: node for node in flow['data']['nodes']}
        self.assertEqual(set(types), {'ChatInput', 'JejuOvertourismPrepare', 'JejuOvertourismTools', 'Agent',
                                      'JejuOvertourismValidation', 'ChatOutput'})
        self.assertEqual(types['ChatOutput']['id'], 'ChatOutput-jejuOvertourism')
        self.assertEqual(types['JejuOvertourismValidation']['id'], 'JejuOvertourismValidation-v1')
        for name in overtourism.COMPONENTS:
            node = types[name]['data']['node']
            self.assertEqual(node['template']['service_token'], {'value': SETUP.MCP_TOKEN_NAME, 'load_from_db': True})
            self.assertEqual(node['template']['code']['value'], overtourism.component_source(COMPONENTS_SOURCE, name))
        fields = types['Agent']['data']['node']['template']
        self.assertEqual(fields['system_prompt']['value'], overtourism.PROMPT)
        self.assertEqual(fields['max_tokens']['value'], 8192)
        self.assertEqual(fields['max_iterations']['value'], 8)
        self.assertEqual(fields['api_key'], {**fields['api_key'], 'value': gateway.OPENAI_TOKEN_NAME, 'load_from_db': True})
        code = fields['code']['value']
        ast.parse(code)
        self.assertIn(overtourism.TIMEOUT_NEW, code)
        self.assertNotIn(overtourism.TIMEOUT_OLD, code)
        self.assertIn(overtourism.NOTICE_NEW, code)
        self.assertIn('model="gpt-5.4-mini"', code)
        self.assertIn('_GatewayFinalAnswerMiddleware', code)
        edges = {(edge['source'], edge['target'], edge['data']['targetHandle']['fieldName']) for edge in flow['data']['edges']}
        agent = types['Agent']['id']
        self.assertEqual(edges, {
            (types['ChatInput']['id'], 'JejuOvertourismPrepare-v1', 'original_request'),
            ('JejuOvertourismPrepare-v1', agent, 'input_value'),
            ('JejuOvertourismPrepare-v1', 'JejuOvertourismTools-v1', 'prepared_request'),
            ('JejuOvertourismTools-v1', agent, 'tools'),
            ('JejuOvertourismPrepare-v1', 'JejuOvertourismValidation-v1', 'prepared_request'),
            (agent, 'JejuOvertourismValidation-v1', 'agent_response'),
            ('JejuOvertourismValidation-v1', 'ChatOutput-jejuOvertourism', 'input_value'),
        })
        text = json.dumps(flow, ensure_ascii=False)
        for secret in (ENV['MCP_SERVICE_TOKEN'], ENV['LANGFLOW_SUPERUSER_PASSWORD'], 'a' * 64):
            self.assertNotIn(secret, text)
        # No API key, model run or recommendation flow change.
        self.assertEqual(mutations(client), [('POST', '/api/v1/flows/')])
        again = run(client)
        self.assertEqual((again['flowStatus'], again['flowId'], again['flowSha256']), ('already-current', result['flowId'], result['flowSha256']))
        checked = run(client, 'check')
        self.assertEqual((checked['flowStatus'], checked['flowSha256']), ('checked', result['flowSha256']))
        self.assertEqual(mutations(client), [('POST', '/api/v1/flows/')])

    def test_an_edited_flow_is_only_replaced_on_request_with_if_match(self):
        client = Langflow()
        first = run(client)
        flow = stored(client)
        flow['data']['nodes'][0]['data']['edited'] = True
        flow['description'] = 'edited in the UI'
        with self.assertRaisesRegex(overtourism.OvertourismBootstrapError, '^OVERTOURISM_EXISTING_FLOW_DIFFERS$'):
            run(client)
        with self.assertRaisesRegex(overtourism.OvertourismBootstrapError, '^OVERTOURISM_EXISTING_FLOW_DIFFERS$'):
            run(client, 'check')
        self.assertFalse(any(method == 'PATCH' for method, _ in mutations(client)))
        replaced = run(client, 'replace')
        self.assertEqual((replaced['flowStatus'], replaced['flowId'], replaced['flowSha256']), ('updated', first['flowId'], first['flowSha256']))
        patch = [kwargs for method, route, kwargs in client.calls if method == 'PATCH']
        self.assertEqual(len(patch), 1)
        self.assertEqual(set(patch[0]['json']), {'data', 'description'})
        self.assertIn('If-Match', patch[0]['headers'])
        self.assertEqual(run(client, 'check')['flowStatus'], 'checked')

    def test_missing_prerequisites_stop_before_any_flow_change(self):
        for client, mcp, code in [
            (Langflow(openai=False), MCP(), 'OVERTOURISM_CREDENTIAL_UNAVAILABLE'),
            (Langflow(), MCP(status=404, code='GATEWAY_ROUTE_UNAVAILABLE'), 'OVERTOURISM_MCP_NOT_READY'),
            (Langflow(), MCP(status=401, code='GATEWAY_UNAUTHORIZED'), 'OVERTOURISM_MCP_NOT_READY'),
        ]:
            with self.subTest(code=code), self.assertRaisesRegex(overtourism.OvertourismBootstrapError, f'^{code}$'):
                run(client, mcp=mcp)
            self.assertEqual(mutations(client), [])
        with self.assertRaisesRegex(overtourism.OvertourismBootstrapError, '^OVERTOURISM_FLOW_NOT_PROVISIONED$'):
            run(Langflow(), 'replace')
        with self.assertRaisesRegex(overtourism.OvertourismBootstrapError, '^OVERTOURISM_FLOW_NOT_PROVISIONED$'):
            run(Langflow(), 'check')

    def test_component_sources_are_isolated_and_must_match_the_reviewed_set(self):
        for name in overtourism.COMPONENTS:
            code = overtourism.component_source(COMPONENTS_SOURCE, name)
            classes = [node.name for node in ast.parse(code).body if isinstance(node, ast.ClassDef)
                       and any(isinstance(base, ast.Name) and base.id == 'Component' for base in node.bases)]
            self.assertEqual(classes, [name])
        with self.assertRaisesRegex(overtourism.OvertourismBootstrapError, '^OVERTOURISM_COMPONENT_SOURCE_MISMATCH$'):
            overtourism.component_source(RULE_SOURCE, 'JejuOvertourismPrepare')

    def test_main_reports_only_fixed_codes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = overtourism.main(['--check'], sources={'GATEWAY_BOOTSTRAP_SOURCE': 'raise RuntimeError("private: ' + ENV['MCP_SERVICE_TOKEN'] + '")',
                                                            'RULE_COMPONENTS_SOURCE': '', 'OVERTOURISM_COMPONENTS_SOURCE': ''})
        self.assertEqual(status, 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value, {'status': 'failed', 'code': 'OVERTOURISM_BOOTSTRAP_FAILED', 'modelExecuted': False,
                                 'flowPatchAttempted': False})


if __name__ == '__main__':
    unittest.main()
