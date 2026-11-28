#!/usr/bin/env python3
"""Disposable-CI-only: construct the stored rule-action OpenAI client and stream calls without network.

Stream into the official Langflow container: `exec -T -e CI=true langflow python -
< deploy/tests/check-openai-client.py`. It logs in only to read the stored Gateway
flow, extracts the Agent's reviewed `_get_llm` method and calls it with a
placeholder key while every socket entry point raises. The request payload is
built locally to show the parameters gpt-5.4-mini would receive.

Then one sync and one async call stream through an in-process stand-in for the
OpenAI Chat Completions endpoint: the httpx transports are replaced, so no socket
opens. Like the OpenAI API, the stand-in sends the final usage chunk only when the
request asks for it with stream_options.include_usage. Langflow's own native trace
handler records each call, and the check reads the span attributes that
deploy/agent-run-metrics.sql reads (gen_ai.usage.input_tokens/output_tokens).

The same calls run once more with the client of the build stored on AWS before
stream_usage (OPENAI_GET_LLM_NO_USAGE of /opt/oreumit/gateway-bootstrap.py): it
must ask for no usage and record no tokens, as the AWS traces showed. No provider,
MCP tool or Gateway request is called, and only fixed aggregate fields print.
"""
import ast
import asyncio
import importlib.metadata
import importlib.util
import json
import os
import re
import types
from unittest.mock import patch
import uuid

FLOW_NAME = '제주 관광 Agent v2 — Gateway 제한 실행'
PLACEHOLDER = 'sk-ci-placeholder-' + 'x' * 32
ENDPOINT = 'https://api.openai.com/v1/chat/completions'
ANSWER = '{"placeIds":[]}'
USAGE = {'prompt_tokens': 321, 'completion_tokens': 12, 'total_tokens': 333}
TOOL_NAMES = ['places_search', 'places_get', 'constraints_check']


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


def get_llm(source):
    """Compile one reviewed `_get_llm` method out of Agent code (or the method text itself)."""
    methods = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == '_get_llm']
    assert len(methods) == 1
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 '<reviewed-rule-action-method>', 'exec'), namespace)
    return namespace['_get_llm']


def reviewed_previous_method(path='/opt/oreumit/gateway-bootstrap.py'):
    """The client text of the build stored on AWS before stream_usage, from the reviewed bootstrap."""
    spec = importlib.util.spec_from_file_location('gateway_ci_previous_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OPENAI_GET_LLM_NO_USAGE


def openai_stand_in(seen):
    """httpx transport methods answering like POST /v1/chat/completions with stream=true, in process."""
    import httpx

    def respond(request):
        body = json.loads(request.content)
        include = (body.get('stream_options') or {}).get('include_usage')
        seen.append({'endpoint': str(request.url) == ENDPOINT and request.method == 'POST',
                     'authorized': request.headers.get('authorization') == 'Bearer ' + PLACEHOLDER,
                     'model': body.get('model'), 'stream': body.get('stream'), 'streamOptions': body.get('stream_options'),
                     'reasoningEffort': body.get('reasoning_effort'), 'maxCompletionTokens': body.get('max_completion_tokens'),
                     'tools': [tool['function']['name'] for tool in body.get('tools') or []]})
        base = {'id': 'chatcmpl-ci', 'object': 'chat.completion.chunk', 'created': 0, 'model': body.get('model')}
        chunks = [{**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
                  {**base, 'choices': [{'index': 0, 'delta': {'content': ANSWER}, 'finish_reason': None}]},
                  {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
        if include is True:
            # OpenAI's last chunk when stream_options.include_usage is true: no choices, only usage.
            chunks.append({**base, 'choices': [], 'usage': USAGE})
        data = b''.join(b'data: ' + json.dumps(chunk).encode() + b'\n\n' for chunk in chunks) + b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=data, request=request)

    def handle_request(_transport, request):
        request.read()
        return respond(request)

    async def handle_async_request(_transport, request):
        await request.aread()
        return respond(request)
    return handle_request, handle_async_request


def streamed(model, tools):
    """One sync and one async tool-bound call through the stand-in, traced by Langflow's native handler."""
    import httpx
    from langchain_core.messages import HumanMessage
    from langflow.services.tracing.native import NativeTracer
    from langflow.services.tracing.native_callback import NativeCallbackHandler
    seen = []
    handle_request, handle_async_request = openai_stand_in(seen)
    tracer = NativeTracer(trace_name='ci-openai-client', trace_type='chain', project_name='ci',
                          trace_id=uuid.uuid4(), session_id='gw-' + '0' * 32)
    bound = model.bind_tools(tools)
    config = {'callbacks': [NativeCallbackHandler(tracer)]}
    with patch.object(httpx.HTTPTransport, 'handle_request', handle_request), \
         patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', handle_async_request):
        replies = [bound.invoke([HumanMessage(content='ci')], config=config),
                   asyncio.run(bound.ainvoke([HumanMessage(content='ci')], config=config))]
    spans = [span for span in tracer.completed_spans if str(span.get('span_type')).lower().endswith('llm')]
    return seen, replies, spans


def observed(seen, replies, spans):
    assert len(seen) == 2 and len(replies) == 2 and len(spans) == 2
    for request in seen:
        assert request['endpoint'] and request['authorized'] and request['model'] == 'gpt-5.4-mini'
        assert request['stream'] is True and request['tools'] == TOOL_NAMES
        assert request['reasoningEffort'] == 'none' and request['maxCompletionTokens'] == 4096
    assert all(reply.content == ANSWER for reply in replies)
    assert all((span['attributes'] or {}).get('gen_ai.response.model') == 'gpt-5.4-mini' for span in spans)
    options = {json.dumps(request['streamOptions'], sort_keys=True) for request in seen}
    usage = [reply.usage_metadata for reply in replies]
    tokens = [((span['attributes'] or {}).get('gen_ai.usage.input_tokens'),
               (span['attributes'] or {}).get('gen_ai.usage.output_tokens')) for span in spans]
    assert len(options) == 1 and len(set(map(json.dumps, usage))) == 1 and len(set(tokens)) == 1
    return json.loads(options.pop()), usage[0], tokens[0]


def main():
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        fields, scoped_code = stored_agent(os.environ)
        assert fields['api_key']['value'] == 'JEJU_OPENAI_API_KEY' and fields['api_key']['load_from_db'] is True
        current = get_llm(fields['code']['value'])
        previous = get_llm(reviewed_previous_method())
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
            model = current(fake)
            assert type(model).__name__ == 'ChatOpenAI'
            assert model.model_name == 'gpt-5.4-mini' and model.streaming is True and model.max_retries == 1
            assert model.openai_api_base == 'https://api.openai.com/v1' and model.reasoning_effort == 'none'
            # The explicit base_url turns off langchain-openai's default; the reviewed client turns it on.
            assert model.stream_usage is True and model._should_stream_usage() is True
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
            assert names == TOOL_NAMES
            assert all(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) for name in names)
            # Streamed calls ask for usage, the usage chunk arrives and Langflow's trace keeps the tokens.
            options, usage, tokens = observed(*streamed(model, tools))
            assert options == {'include_usage': True}
            assert (usage['input_tokens'], usage['output_tokens'], usage['total_tokens']) == (321, 12, 333)
            assert tokens == (321, 12)
            # The client stored on AWS before this change: no usage asked, none traced.
            old = previous(fake)
            assert old.stream_usage is not True and old._should_stream_usage() is False
            old_options, old_usage, old_tokens = observed(*streamed(old, tools))
            assert old_options is None and old_usage is None and old_tokens == (None, None)
            assert connect.call_count == 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'OPENAI_CLIENT_CHECK_FAILED',
                          'errorType': type(error).__name__, 'modelExecuted': False}))
        return 1
    print(json.dumps({'status': 'passed', 'openaiClientInitialized': True, 'model': 'gpt-5.4-mini',
                      'openaiToolNames': TOOL_NAMES, 'maxCompletionTokens': 4096, 'reasoningEffort': 'none',
                      'streamUsage': True, 'streamedCalls': ['sync', 'async'], 'streamOptions': options,
                      'tracedTokens': {'input': tokens[0], 'output': tokens[1]},
                      'previousBuild': {'build': 'rule-action-no-usage', 'streamOptions': None, 'tracedTokens': None},
                      'langchainOpenai': importlib.metadata.version('langchain-openai'),
                      'networkCalls': 0, 'modelExecuted': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
