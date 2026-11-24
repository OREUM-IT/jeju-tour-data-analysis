"""Real Langflow/Pydantic/StructuredTool contracts of the diagnosis components with mocked HTTP; no model calls.

Run in the Langflow image or its pinned local venv. Minimal host test jobs skip this file.
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
spec = importlib.util.spec_from_file_location('overtourism_component_runtime_tests', ROOT / 'services/langflow/overtourism_components.py')
component = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = component
spec.loader.exec_module(component)
bootstrap_spec = importlib.util.spec_from_file_location('overtourism_bootstrap_runtime_tests', ROOT / 'deploy/langflow/overtourism-bootstrap.py')
bootstrap = importlib.util.module_from_spec(bootstrap_spec)
bootstrap_spec.loader.exec_module(bootstrap)
ID = 'ot-' + 'a' * 32
PREPARED = {'schemaVersion': '1.0', 'status': 'prepared', 'diagnosisId': ID, 'today': '2026-11-24',
            'policyVersion': 'overtourism-diagnosis-v1', 'indicatorsSha256': 'b' * 64,
            'regionIds': list(component.REGION_IDS), 'toolCallLimit': 6}
ACCEPTED = {'schemaVersion': '1.0', 'status': 'accepted', 'diagnosisId': ID, 'indicatorsSha256': 'b' * 64,
            'diagnosisSha256': 'c' * 64, 'adjustedRegions': 13}
TOKEN = 'synthetic-token-' + 'x' * 40
ANSWER = {'overview': '합성 개요', 'regions': [{'id': 'udo', 'level': 'high', 'summary': '합성', 'evidence': [],
                                                'policy': '합성', 'suggestions': [], 'missing': []}]}


def message(value):
    return Message(text=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), session_id=ID)


class OvertourismComponentsTests(unittest.IsolatedAsyncioTestCase):
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

    def tools(self, prepared=PREPARED):
        instance = component.JejuOvertourismTools()
        instance.set(prepared_request=message(prepared), service_token=TOKEN)
        return instance

    async def test_three_tools_pin_scope_and_token_and_use_openai_safe_names(self):
        scoped = self.tools()
        tools = {tool.name: tool for tool in scoped.build_tools()}
        self.assertEqual(set(tools), {'overtourism_overview', 'overtourism_regions', 'overtourism_rank'})
        self.assertTrue(all(isinstance(tool, StructuredTool) for tool in tools.values()))
        # OpenAI function names allow only letters, digits, underscores and dashes.
        self.assertTrue(all(__import__('re').fullmatch(r'[A-Za-z0-9_-]{1,64}', name) for name in tools))
        scoped.set(service_token='other-' + 'z' * 40, prepared_request=message({**PREPARED, 'diagnosisId': 'ot-' + 'd' * 32}))
        mocked, calls, options = self.client({'schemaVersion': '1.0', 'data': {}})
        with mocked:
            for name, args in [('overtourism_overview', {}), ('overtourism_regions', {'ids': ['udo', 'aewol']}),
                               ('overtourism_rank', {'metric': 'crowd.highShare', 'limit': 3}), ('overtourism_rank', {'metric': 'places'})]:
                value = await tools[name].ainvoke(args)
                self.assertEqual(json.loads(value)['schemaVersion'], '1.0')
                sent = json.loads(calls[-1].content)
                self.assertEqual(sent, {'diagnosisId': ID, 'name': name, 'arguments': args})
                self.assertEqual(calls[-1].headers['Authorization'], 'Bearer ' + TOKEN)
                self.assertEqual(str(calls[-1].url), 'http://jeju-mcp:8788/gateway/overtourism/tools/call')
        self.assertTrue(all(option == {'timeout': 10.0, 'follow_redirects': False, 'trust_env': False} for option in options))
        self.assertTrue(all('diagnosisId' not in tool.args and 'service_token' not in tool.args for tool in tools.values()))

    async def test_strict_arguments_reject_overrides_unknown_regions_and_coercion_before_http(self):
        tools = {tool.name: tool for tool in self.tools().build_tools()}
        mocked, calls, _ = self.client({})
        with mocked:
            for name, args in [('overtourism_regions', {'ids': ['udo'], 'diagnosisId': 'ot-' + 'd' * 32}),
                               ('overtourism_regions', {'ids': ['udo'], 'url': 'https://x.invalid'}),
                               ('overtourism_regions', {'ids': ['udo', 'udo']}), ('overtourism_regions', {'ids': []}),
                               ('overtourism_regions', {'ids': ['nowhere']}), ('overtourism_rank', {'metric': 'crowd.highShare', 'limit': '3'}),
                               ('overtourism_rank', {'metric': 'made.up'}), ('overtourism_rank', {'metric': 'places', 'limit': 99})]:
                self.assertEqual(await tools[name].ainvoke(args), 'OVERTOURISM_TOOL_ARGUMENTS_INVALID')
        self.assertEqual(calls, [])
        # LangChain drops unknown keys for the field-less overview tool before the call;
        # they never reach the MCP and cannot replace the bound scope.
        mocked, calls, _ = self.client({'schemaVersion': '1.0'})
        with mocked:
            await tools['overtourism_overview'].ainvoke({'diagnosisId': 'ot-' + 'd' * 32, 'url': 'https://x.invalid'})
        self.assertEqual(json.loads(calls[0].content), {'diagnosisId': ID, 'name': 'overtourism_overview', 'arguments': {}})

    async def test_http_errors_redirects_and_oversized_or_duplicate_json_are_fixed_errors(self):
        tool = self.tools().build_tools()[0]
        for options in [{'status': 302, 'body': {'secret': TOKEN}}, {'status': 500, 'body': {'secret': TOKEN}},
                        {'error': httpx.ReadTimeout('private: ' + TOKEN)}, {'raw': b'{"a":1,"a":2}'}, {'raw': b'{"a":NaN}'},
                        {'raw': json.dumps({'raw': 'x' * (component.MAX_RESPONSE_BYTES + 1)}).encode()}]:
            mocked, _, _ = self.client(**options)
            with mocked:
                result = await tool.ainvoke({})
                self.assertEqual(result, 'OVERTOURISM_TOOL_FAILED')
                self.assertNotIn(TOKEN, result)

    async def test_prepare_binds_only_a_registered_diagnosis_id(self):
        prepare = component.JejuOvertourismPrepare()
        prepare.set(original_request=message({'diagnosisId': ID}), service_token=TOKEN)
        mocked, calls, _ = self.client(PREPARED)
        with mocked:
            result = await prepare.prepare_request()
        self.assertEqual(json.loads(result.text), PREPARED)
        self.assertEqual(str(calls[0].url), 'http://jeju-mcp:8788/gateway/overtourism/prepare')
        self.assertEqual(json.loads(calls[0].content), {'diagnosisId': ID})
        for returned in [{**PREPARED, 'diagnosisId': 'ot-' + 'd' * 32}, {**PREPARED, 'regionIds': ['udo']},
                         {**PREPARED, 'policyVersion': 'other'}, {**PREPARED, 'extra': 1}]:
            mocked, _, _ = self.client(returned)
            with mocked, self.assertRaisesRegex(ValueError, '^DIAGNOSIS_PREPARATION_FAILED$'):
                await prepare.prepare_request()
        for original in [{'diagnosisId': ID, 'query': 'extra'}, {'diagnosisId': 'gw-' + 'a' * 32}, 'not json']:
            prepare.set(original_request=message(original), service_token=TOKEN)
            mocked, calls, _ = self.client(PREPARED)
            with mocked, self.assertRaisesRegex(ValueError, '^DIAGNOSIS_PREPARATION_FAILED$'):
                await prepare.prepare_request()
            self.assertEqual(calls, [])

    async def test_validation_publishes_only_bound_digests_or_a_fixed_block(self):
        final = component.JejuOvertourismValidation()
        final.set(prepared_request=message(PREPARED), agent_response=message(ANSWER), service_token=TOKEN)
        mocked, calls, _ = self.client(ACCEPTED)
        with mocked:
            result = await final.validate_response()
        self.assertEqual(json.loads(result.text), ACCEPTED)
        self.assertEqual(str(calls[0].url), 'http://jeju-mcp:8788/gateway/overtourism/validate')
        self.assertEqual(json.loads(calls[0].content), {'diagnosisId': ID, 'response': ANSWER})
        # One surrounding code fence is tolerated; the MCP still validates the object.
        final.set(agent_response=message('```json\n' + json.dumps(ANSWER, ensure_ascii=False) + '\n```'))
        mocked, calls, _ = self.client(ACCEPTED)
        with mocked:
            self.assertEqual(json.loads((await final.validate_response()).text), ACCEPTED)
        self.assertEqual(json.loads(calls[0].content)['response'], ANSWER)
        final.set(agent_response=message('설명 문장과 함께 ' + json.dumps(ANSWER, ensure_ascii=False)))
        mocked, calls, _ = self.client(ACCEPTED)
        with mocked:
            self.assertEqual(json.loads((await final.validate_response()).text)['code'], 'DIAGNOSIS_NOT_JSON')
        self.assertEqual(calls, [])
        final.set(agent_response=message(ANSWER))
        for returned in [{**ACCEPTED, 'indicatorsSha256': 'd' * 64}, {**ACCEPTED, 'diagnosisId': 'ot-' + 'd' * 32},
                         {**ACCEPTED, 'diagnosis': {'private': TOKEN}}, {'schemaVersion': '1.0', 'status': 'blocked'}]:
            mocked, _, _ = self.client(returned)
            with mocked:
                blocked = await final.validate_response()
            self.assertEqual(json.loads(blocked.text), {'status': 'blocked', 'code': 'DIAGNOSIS_BLOCKED'})
            self.assertNotIn(TOKEN, blocked.text)
        mocked, _, _ = self.client(error=httpx.ReadTimeout('private: ' + TOKEN))
        with mocked:
            failed = await final.validate_response()
        self.assertEqual(json.loads(failed.text)['code'], 'DIAGNOSIS_VALIDATION_FAILED')

    async def test_cancellation_is_not_converted_to_a_tool_result(self):
        tool = self.tools().build_tools()[0]
        mocked, calls, _ = self.client(error=asyncio.CancelledError())
        with mocked, self.assertRaises(asyncio.CancelledError):
            await tool.ainvoke({})
        self.assertEqual(len(calls), 1)

    def test_official_langflow_builder_accepts_each_isolated_class_and_output_type(self):
        source = (ROOT / 'services/langflow/overtourism_components.py').read_text()
        for name in bootstrap.COMPONENTS:
            code = bootstrap.component_source(source, name)
            frontend, instance = build_custom_component_template(Component(_code=code), class_name=name)
            self.assertEqual(frontend['template']['code']['value'], code)
            self.assertEqual(frontend['outputs'][0]['types'], ['Tool'] if name == 'JejuOvertourismTools' else ['Message'])
            self.assertEqual(instance.name, name)
            if name == 'JejuOvertourismValidation':
                self.assertEqual(frontend['display_name'], 'Jeju Overtourism Validation')
            if name == 'JejuOvertourismTools':
                instance.set(prepared_request=message(PREPARED), service_token=TOKEN)
                tools = instance.build_tools()
                self.assertEqual(len(tools), 3)
                self.assertTrue(all(isinstance(tool.args, dict) for tool in tools))


if __name__ == '__main__':
    unittest.main()
