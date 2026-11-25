#!/usr/bin/env python3
"""Disposable-CI-only: construct the stored rule-action OpenAI client without network.

Stream into the official Langflow container: `exec -T -e CI=true langflow python -
< deploy/tests/check-openai-client.py`. It logs in only to read the stored Gateway
flow, extracts the Agent's reviewed `_get_llm` method and calls it with a
placeholder key while every socket entry point raises. The request payload is
built locally to show the parameters gpt-5.4-mini would receive. No provider,
MCP tool or Gateway request is called, and only fixed aggregate fields print.
"""
import ast
import json
import os
import re
import types
from unittest.mock import patch

FLOW_NAME = '제주 관광 Agent v2 — Gateway 제한 실행'
PLACEHOLDER = 'sk-ci-placeholder-' + 'x' * 32


def stored_agent(environment):
    import httpx
    with httpx.Client(base_url='http://127.0.0.1:7860', timeout=30, follow_redirects=False, trust_env=False) as client:
        login = client.post('/api/v1/login', data={'username': environment['LANGFLOW_SUPERUSER'],
                                                   'password': environment['LANGFLOW_SUPERUSER_PASSWORD']})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        listed = client.get('/api/v1/flows/', params={'get_all': 'true', 'header_flows': 'true',
                                                      'remove_example_flows': 'true'})
        assert listed.status_code == 200
        matches = [item['id'] for item in listed.json() if item.get('name') == FLOW_NAME]
        assert len(matches) == 1
        stored = client.get(f'/api/v1/flows/{matches[0]}')
        assert stored.status_code == 200
    nodes = stored.json()['data']['nodes']
    agents = [node for node in nodes if node['data']['type'] == 'Agent']
    scoped = [node for node in nodes if node['data']['type'] == 'JejuScopedTools']
    assert len(agents) == 1 and len(scoped) == 1
    return agents[0]['data']['node']['template'], scoped[0]['data']['node']['template']['code']['value']


def scoped_tools(code):
    """Build the stored scoped tools with Langflow's own loader; no MCP call happens until a tool runs."""
    from lfx.custom.custom_component.component import Component
    from lfx.custom.utils import build_custom_component_template
    from lfx.schema.message import Message
    _frontend, instance = build_custom_component_template(Component(_code=code), class_name='JejuScopedTools')
    prepared = {'schemaVersion': '1.0', 'status': 'prepared', 'request': {'requestId': 'gw-' + '0' * 32},
                'requestSha256': '0' * 64, 'catalogSha256': '0' * 64, 'catalogVersion': 'sha256:' + '0' * 64,
                'policyVersion': 'recommendation-validation-v1', 'evaluatedAt': '2026-11-25T00:00:00.000Z', 'candidateCount': 1}
    instance.set(service_token='ci-placeholder-token-' + 'x' * 32,
                 prepared_request=Message(text=json.dumps(prepared), session_id='gw-' + '0' * 32))
    return instance.build_tools()


def main():
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        fields, scoped_code = stored_agent(os.environ)
        assert fields['api_key']['value'] == 'JEJU_OPENAI_API_KEY' and fields['api_key']['load_from_db'] is True
        methods = [node for node in ast.walk(ast.parse(fields['code']['value']))
                   if isinstance(node, ast.FunctionDef) and node.name == '_get_llm']
        assert len(methods) == 1
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                     '<stored-rule-action-method>', 'exec'), namespace)
        fake = types.SimpleNamespace(api_key=PLACEHOLDER, model=fields['model']['value'],
                                     _get_max_tokens_value=lambda: fields['max_tokens']['value'])
        clean = {k: v for k, v in os.environ.items() if not k.startswith('OPENAI_')}

        def no_network(*_args, **_kwargs):
            raise AssertionError('NETWORK_FORBIDDEN_IN_CONSTRUCTOR_CHECK')
        from langchain_core.messages import HumanMessage
        with patch.dict(os.environ, clean, clear=True), \
             patch('socket.socket.connect', side_effect=no_network) as connect, \
             patch('socket.create_connection', side_effect=no_network), \
             patch('socket.getaddrinfo', side_effect=no_network):
            model = namespace['_get_llm'](fake)
            assert type(model).__name__ == 'ChatOpenAI'
            assert model.model_name == 'gpt-5.4-mini' and model.streaming is True and model.max_retries == 1
            assert model.openai_api_base == 'https://api.openai.com/v1' and model.reasoning_effort == 'none'
            payload = model._get_request_payload([HumanMessage(content='ci')])
            assert payload['model'] == 'gpt-5.4-mini' and payload['stream'] is True
            assert payload.get('reasoning_effort') == 'none'
            assert payload.get('max_completion_tokens') == 4096 and 'max_tokens' not in payload
            assert 'temperature' not in payload
            # The exact tool declarations OpenAI would receive: names must match ^[a-zA-Z0-9_-]{1,64}$.
            tools = scoped_tools(scoped_code)
            bound = model.bind_tools(tools)
            with_tools = model._get_request_payload([HumanMessage(content='ci')], **bound.kwargs)
            names = [tool['function']['name'] for tool in with_tools['tools']]
            assert names == ['places_search', 'places_get', 'constraints_check']
            assert all(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) for name in names)
            assert connect.call_count == 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'OPENAI_CLIENT_CHECK_FAILED',
                          'errorType': type(error).__name__, 'modelExecuted': False}))
        return 1
    print(json.dumps({'status': 'passed', 'openaiClientInitialized': True, 'model': 'gpt-5.4-mini',
                      'openaiToolNames': ['places_search', 'places_get', 'constraints_check'],
                      'maxCompletionTokens': 4096, 'reasoningEffort': 'none', 'networkCalls': 0,
                      'modelExecuted': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
