#!/usr/bin/env python3
"""Disposable-CI-only: run the stored Gateway Agent code with a fake chat model.

Stream into the official Langflow container: `exec -T -e CI=true langflow python -
--expect 8|4 < deploy/tests/check-gateway-call-budget.py`. It logs in only to read
the stored flow, compiles its Agent code with Langflow's own loader and runs the
real LangChain agent loop against an in-process fake model that always requests a
tool unless tool use is forbidden. No model provider, MCP tool or Gateway request
is called, and only fixed aggregate fields are printed.
"""
import argparse
import asyncio
import json
import os
from typing import Any

FLOW_NAME = '제주 관광 Agent v2 — Gateway 제한 실행'
FINAL = json.dumps({'schemaVersion': '1.0', 'requestId': 'ci-call-budget', 'action': 'cannot_confirm',
                    'placeIds': [], 'requirementClaims': [], 'confirmationNotice': True,
                    'noticeTopics': ['indoor_unverified']}, separators=(',', ':'))
EXPECTED = {
    8: {'middleware': ['ToolCallIDMiddleware', '_GatewayFinalAnswerMiddleware', 'ModelCallLimitMiddleware'],
        'choices': [None] * 7 + ['none'], 'final': 'json'},
    4: {'middleware': ['ToolCallIDMiddleware', 'ModelCallLimitMiddleware'],
        'choices': [None] * 4, 'final': 'limit-notice'},
}


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
    agents = [node for node in stored.json()['data']['nodes'] if node['data']['type'] == 'Agent']
    assert len(agents) == 1
    return agents[0]['data']['node']['template']


async def exercise(fields, *, use_async):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import StructuredTool
    from lfx.custom.custom_component.component import Component
    from lfx.custom.utils import build_custom_component_template

    calls = []

    class FakeToolModel(BaseChatModel):
        bound_choice: Any = None
        bound_tools: int = 0

        @property
        def _llm_type(self):
            return 'ci-fake-tool-model'

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self.model_copy(update={'bound_choice': tool_choice, 'bound_tools': len(tools)})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            system = messages[0].content if messages and isinstance(messages[0], SystemMessage) else ''
            calls.append((self.bound_choice, self.bound_tools, isinstance(system, str) and '마지막 모델 호출' in system))
            if self.bound_choice == 'none':
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=FINAL))])
            call = {'name': 'places.search', 'args': {'query': 'ci'}, 'id': f'ci-call-{len(calls)}', 'type': 'tool_call'}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[call]))])

    def search(query: str) -> str:
        """Return an empty fake result."""
        return '{"places":[]}'

    tool = StructuredTool.from_function(func=search, name='places.search', description='Fake CI search.')
    frontend, instance = build_custom_component_template(Component(_code=fields['code']['value']))
    assert frontend['template']['code']['value'] == fields['code']['value']
    instance.set(max_iterations=fields['max_iterations']['value'], handle_parsing_errors=False, tools=[tool])
    model = FakeToolModel()
    middleware = instance._build_middleware(model)
    agent = create_agent(model=model, tools=[tool], system_prompt=fields['system_prompt']['value'], middleware=middleware)
    state = {'messages': [HumanMessage(content='{"request":{"requestId":"ci-call-budget"}}')]}
    # Use the Agent's own LangGraph step budget, as Langflow does at run time.
    config = {'recursion_limit': instance._compute_recursion_limit()}
    result = await agent.ainvoke(state, config) if use_async else agent.invoke(state, config)
    final = result['messages'][-1].content
    return {'middleware': [type(item).__name__ for item in middleware], 'choices': [item[0] for item in calls],
            'notice': [index + 1 for index, item in enumerate(calls) if item[2]],
            'toolsDeclared': all(item[1] == 1 for item in calls),
            'final': 'json' if final == FINAL else 'limit-notice' if final.startswith('Model call limits exceeded') else 'other'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect', type=int, choices=sorted(EXPECTED), required=True)
    limit = parser.parse_args().expect
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        fields = stored_agent(os.environ)
        assert fields['max_iterations']['value'] == limit and fields['n_messages']['value'] == 0
        expected = EXPECTED[limit]
        for use_async in (False, True):
            observed = asyncio.run(exercise(fields, use_async=use_async))
            assert observed['toolsDeclared'] is True
            assert {key: observed[key] for key in ('middleware', 'choices', 'final')} == expected
            assert observed['notice'] == ([len(expected['choices'])] if limit == 8 else [])
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_CALL_BUDGET_CHECK_FAILED',
                          'errorType': type(error).__name__, 'modelExecuted': False}))
        return 1
    print(json.dumps({'status': 'passed', 'modelCallLimit': limit, 'fakeModelCalls': len(expected['choices']),
                      'finalAnswer': expected['final'], 'runs': ['sync', 'async'], 'modelExecuted': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
