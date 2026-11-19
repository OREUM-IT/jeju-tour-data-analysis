"""Real Langflow/Pydantic/StructuredTool contracts with mocked HTTP; no model calls.

Run in the Langflow image or its pinned local venv. Minimal host test jobs skip
this file; deploy/tests/check-gateway-runtime.py also exercises the real classes.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    import httpx
    from lfx.custom.custom_component.component import Component
    from lfx.custom.utils import build_custom_component_template
    from lfx.schema.message import Message
    from langchain_core.tools import StructuredTool
except ImportError:
    raise unittest.SkipTest('Pinned Langflow runtime required') from None


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('gateway_component_runtime_tests', ROOT / 'services/langflow/gateway_components.py')
component = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = component
spec.loader.exec_module(component)
bootstrap_spec = importlib.util.spec_from_file_location('gateway_bootstrap_runtime_tests', ROOT / 'deploy/langflow/gateway-bootstrap.py')
bootstrap = importlib.util.module_from_spec(bootstrap_spec)
bootstrap_spec.loader.exec_module(bootstrap)
REQUEST = {'schemaVersion': '1.0', 'requestId': 'gw-' + 'a' * 32, 'query': '합성 요청', 'timezone': 'Asia/Seoul',
           'filters': {}, 'excludedIds': [], 'requirements': []}
PREPARED = {'schemaVersion': '1.0', 'status': 'prepared', 'request': REQUEST, 'requestSha256': 'b' * 64,
            'catalogSha256': 'c' * 64, 'catalogVersion': 'sha256:' + 'c' * 64, 'policyVersion': 'test-policy',
            'evaluatedAt': '2026-11-19T00:00:00.000Z', 'candidateCount': 1}
TOKEN = 'synthetic-token-' + 'x' * 40
RESPONSE = {'schemaVersion': '1.0', 'requestId': REQUEST['requestId'], 'action': 'recommend', 'placeIds': ['synthetic-1'],
            'requirementClaims': [], 'confirmationNotice': False, 'noticeTopics': []}


def message(value):
    return Message(text=json.dumps(value), session_id=REQUEST['requestId'])


class GatewayComponentsTests(unittest.IsolatedAsyncioTestCase):
    def client(self, body=None, *, status=200, raw=None, error=None):
        calls, options = [], []
        original = httpx.AsyncClient
        async def handler(request):
            calls.append(request)
            if error:
                raise error
            return httpx.Response(status, content=raw if raw is not None else json.dumps(body).encode(), request=request)
        def factory(**kwargs):
            options.append(kwargs)
            return original(**kwargs, transport=httpx.MockTransport(handler))
        return patch.object(component.httpx, 'AsyncClient', side_effect=factory), calls, options

    def scoped(self):
        instance = component.JejuScopedTools()
        instance.set(prepared_request=message(PREPARED), service_token=TOKEN)
        return instance

    async def test_three_real_tools_pin_scope_and_token_even_if_component_inputs_change(self):
        scoped = self.scoped()
        tools = {tool.name: tool for tool in scoped.build_tools()}
        self.assertEqual(set(tools), {'places.search', 'places.get', 'constraints.check'})
        self.assertTrue(all(isinstance(tool, StructuredTool) for tool in tools.values()))
        scoped.set(service_token='other-' + 'z' * 40, prepared_request=message({**PREPARED, 'request': {**REQUEST, 'requestId': 'gw-' + 'd' * 32}}))
        mocked, calls, options = self.client({'status': 'ok', 'data': {'items': []}})
        with mocked:
            for name, args in [('places.search', {'maxResults': 2}), ('places.get', {'ids': ['synthetic-1']}), ('constraints.check', {'ids': ['synthetic-1']})]:
                value = await tools[name].ainvoke(args)
                self.assertEqual(json.loads(value)['status'], 'ok')
                sent = json.loads(calls[-1].content)
                self.assertEqual(sent['requestId'], REQUEST['requestId'])
                self.assertEqual(sent['name'], name)
                self.assertEqual(calls[-1].headers['Authorization'], 'Bearer ' + TOKEN)
                self.assertEqual(str(calls[-1].url), 'http://jeju-mcp:8788/gateway/tools/call')
                self.assertEqual(set(sent), {'requestId', 'name', 'arguments'})
        self.assertTrue(all(option == {'timeout': 10.0, 'follow_redirects': False, 'trust_env': False} for option in options))
        self.assertTrue(all('requestId' not in tool.args and 'service_token' not in tool.args for tool in tools.values()))

    async def test_strict_schema_rejects_scope_override_extra_url_duplicate_ids_and_coercion_before_http(self):
        tools = {tool.name: tool for tool in self.scoped().build_tools()}
        mocked, calls, _ = self.client({})
        with mocked:
            for name, args in [('places.search', {'requestId': 'gw-' + 'd' * 32}), ('places.search', {'url': 'https://private.invalid'}),
                               ('places.search', {'maxResults': '2'}), ('places.search', {'maxResults': True}),
                               ('places.get', {'ids': ['synthetic-1', 'synthetic-1']}), ('places.get', {'ids': []}),
                               ('constraints.check', {'ids': ['synthetic-1'], 'requirements': {'indoorRequired': 'true'}})]:
                self.assertEqual(await tools[name].ainvoke(args), 'GATEWAY_TOOL_ARGUMENTS_INVALID')
        self.assertEqual(calls, [])

    async def test_http_redirect_errors_timeout_and_oversized_responses_are_fixed_errors(self):
        tool = self.scoped().build_tools()[0]
        for options in [{'status': 302, 'body': {'secret': TOKEN}}, {'status': 500, 'body': {'secret': TOKEN}},
                        {'error': httpx.ReadTimeout('private: ' + TOKEN)},
                        {'raw': b'{"a":1,"a":2}'}, {'raw': b'{"a":NaN}'},
                        {'raw': json.dumps({'raw': 'x' * (component.MAX_RESPONSE_BYTES + 1)}).encode()}]:
            mocked, _, _ = self.client(**options)
            with mocked:
                result = await tool.ainvoke({})
                self.assertEqual(result, 'GATEWAY_TOOL_FAILED')
                self.assertNotIn(TOKEN, result)

    async def test_prepare_uses_gateway_route_and_stops_unregistered_or_changed_scope(self):
        prepare = component.JejuPrepareRequest()
        prepare.set(original_request=message(REQUEST), service_token=TOKEN)
        mocked, calls, _ = self.client(PREPARED)
        with mocked:
            result = await prepare.prepare_request()
        self.assertEqual(json.loads(result.text), PREPARED)
        self.assertEqual(str(calls[0].url), 'http://jeju-mcp:8788/gateway/prepare-request')
        for returned in [{**PREPARED, 'request': {**REQUEST, 'requestId': 'gw-' + 'd' * 32}}, {**PREPARED, 'request': {**REQUEST, 'requestId': 'unregistered-format'}}]:
            mocked, _, _ = self.client(returned)
            with mocked, self.assertRaisesRegex(ValueError, '^REQUEST_PREPARATION_FAILED$'):
                await prepare.prepare_request()

    async def test_final_only_publishes_accepted_bound_response_without_untrusted_trace(self):
        final = component.JejuFinalValidation()
        final.set(prepared_request=message(PREPARED), agent_response=message(RESPONSE), service_token=TOKEN)
        accepted = {**{key: value for key, value in PREPARED.items() if key != 'request'},
                    'status': 'accepted', 'reasons': [], 'checks': [], 'response': RESPONSE}
        mocked, calls, _ = self.client(accepted)
        with mocked:
            result = await final.validate_response()
        self.assertEqual(json.loads(result.text), RESPONSE)
        self.assertEqual(str(calls[0].url), 'http://jeju-mcp:8788/gateway/validate-response')
        self.assertEqual(json.loads(calls[0].content)['request'], REQUEST)
        mocked, _, _ = self.client({**accepted, 'requestSha256': 'd' * 64, 'response': {'private': TOKEN}})
        with mocked:
            blocked = await final.validate_response()
        self.assertEqual(json.loads(blocked.text)['code'], 'RECOMMENDATION_BLOCKED')
        self.assertNotIn(TOKEN, blocked.text)

    async def test_cancellation_is_not_converted_to_a_retry_or_normal_tool_result(self):
        tool = self.scoped().build_tools()[0]
        mocked, calls, _ = self.client(error=asyncio.CancelledError())
        with mocked, self.assertRaises(asyncio.CancelledError):
            await tool.ainvoke({})
        self.assertEqual(len(calls), 1)

    def test_official_langflow_builder_accepts_each_isolated_class_and_tool_output_type(self):
        source = (ROOT / 'services/langflow/gateway_components.py').read_text()
        for name in bootstrap.COMPONENTS:
            code = bootstrap.component_source(source, name)
            frontend, instance = build_custom_component_template(Component(_code=code), class_name=name)
            self.assertEqual(frontend['template']['code']['value'], code)
            self.assertEqual(frontend['outputs'][0]['types'], ['Tool'] if name == 'JejuScopedTools' else ['Message'])
            self.assertEqual(instance.name, name)
            if name == 'JejuScopedTools':
                instance.set(prepared_request=message(PREPARED), service_token=TOKEN)
                tools = instance.build_tools()
                self.assertEqual(len(tools), 3)
                self.assertTrue(all(isinstance(tool.args, dict) for tool in tools))


if __name__ == '__main__':
    unittest.main()
