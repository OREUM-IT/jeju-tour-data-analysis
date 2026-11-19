#!/usr/bin/env python3
"""CI-only real Langflow component / scoped MCP check, without a model call.

Execute inside the disposable Langflow container with CI=true. Feed a private
JSON object {"controlToken":"..."} on stdin; never put the token in arguments or
logs. MCP_SERVICE_TOKEN stays in the container environment. Output is aggregate
only. This does not execute the Agent or claim deployed model quality.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import uuid
from unittest.mock import patch


BASE = 'http://jeju-mcp:8788'


def load_bootstrap():
    spec = importlib.util.spec_from_file_location('gateway_ci_bootstrap', '/opt/oreumit/gateway-bootstrap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(request_id):
    return {'schemaVersion': '1.0', 'requestId': request_id, 'query': 'CI 합성 요청: 실내 조건 확인 필요 안내',
            'timezone': 'Asia/Seoul', 'filters': {}, 'excludedIds': [],
            'requirements': [{'type': 'indoor_required', 'value': True}]}


async def check(control_token):
    import httpx
    from lfx.custom.custom_component.component import Component
    from lfx.custom.utils import build_custom_component_template
    from lfx.schema.message import Message

    assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
    service_token = os.environ['MCP_SERVICE_TOKEN']
    assert re.fullmatch(r'[\x21-\x7e]{32,256}', service_token)
    assert re.fullmatch(r'[\x21-\x7e]{32,256}', control_token) and service_token != control_token
    setup = load_bootstrap()
    source = Path('/opt/oreumit/gateway_components.py').read_text(encoding='utf-8')
    instances = {}
    for name in setup.COMPONENTS:
        code = setup.component_source(source, name)
        frontend, instance = build_custom_component_template(Component(_code=code), class_name=name)
        assert frontend['template']['code']['value'] == code
        instance.set(service_token=service_token)
        instances[name] = instance
    assert instances['JejuFinalValidation'].display_name == 'Jeju Final Validation'

    async with httpx.AsyncClient(base_url=BASE, timeout=10, follow_redirects=False, trust_env=False) as client:
        headers = {'Authorization': 'Bearer ' + control_token}
        ready = await client.get('/gateway/readyz', headers=headers)
        assert ready.status_code == 200 and ready.json()['activeScopes'] == 0, 'CI_REQUIRES_IDLE_SCOPE_STATE'
        active = []
        try:
            request_id = 'gw-' + uuid.uuid4().hex
            original = request(request_id)
            registered = await client.post('/gateway/scopes', headers=headers, json={
                'request': original, 'principal': 'c' * 64, 'deadlineMs': int(time.time() * 1000) + 60_000})
            assert registered.status_code == 200
            active.append(request_id)
            prepare = instances['JejuPrepareRequest']
            prepare.set(original_request=Message(text=json.dumps(original), session_id=request_id))
            prepared_message = await prepare.prepare_request()
            prepared = json.loads(prepared_message.text)
            assert prepared == registered.json() and prepared['request'] == original
            scoped = instances['JejuScopedTools']
            scoped.set(prepared_request=prepared_message)
            tools = {tool.name: tool for tool in scoped.build_tools()}
            assert set(tools) == {'places.search', 'places.get', 'constraints.check'}
            for tool in tools.values():
                assert 'requestId' not in tool.args and 'service_token' not in tool.args
            found = json.loads(await tools['places.search'].ainvoke({'maxResults': 1}))
            ids = [item['id'] for item in found['data']['items']]
            assert len(ids) == 1
            fetched = json.loads(await tools['places.get'].ainvoke({'ids': ids}))
            assert [item['id'] for item in fetched['data']['items']] == ids
            checked = json.loads(await tools['constraints.check'].ainvoke({'ids': ids}))
            rows = checked['data']['results']
            assert len(rows) == 1 and rows[0]['checks'][0]['status'] == 'unknown'
            response = {'schemaVersion': '1.0', 'requestId': request_id, 'action': 'cannot_confirm', 'placeIds': ids,
                        'requirementClaims': [{'placeId': ids[0], 'requirement': 'indoor_required', 'verdict': 'unknown'}],
                        'confirmationNotice': True, 'noticeTopics': ['indoor_unverified']}
            final = instances['JejuFinalValidation']
            final.set(prepared_request=prepared_message, agent_response=Message(text=json.dumps(response), session_id=request_id))
            accepted = await final.validate_response()
            assert json.loads(accepted.text) == response
            released = await client.delete('/gateway/scopes/' + request_id, headers=headers)
            assert released.status_code == 200
            active.remove(request_id)
            assert await tools['places.get'].ainvoke({'ids': ids}) == 'GATEWAY_TOOL_FAILED'

            expired_id = 'gw-' + uuid.uuid4().hex
            expiring = await client.post('/gateway/scopes', headers=headers, json={
                'request': request(expired_id), 'principal': 'c' * 64, 'deadlineMs': int(time.time() * 1000) + 1000})
            assert expiring.status_code == 200
            active.append(expired_id)
            scoped.set(prepared_request=Message(text=json.dumps(expiring.json()), session_id=expired_id))
            expired_tools = {tool.name: tool for tool in scoped.build_tools()}
            await asyncio.sleep(1.2)
            assert await expired_tools['places.search'].ainvoke({'maxResults': 1}) == 'GATEWAY_TOOL_FAILED'
        finally:
            for request_id in active:
                released = await client.delete('/gateway/scopes/' + request_id, headers=headers)
                assert released.status_code == 200
        ready = await client.get('/gateway/readyz', headers=headers)
        assert ready.status_code == 200 and ready.json()['activeScopes'] == 0
    return {'status': 'passed', 'compiledComponents': 3, 'structuredTools': 3,
            'successfulScopedToolCalls': 3, 'finalValidationAccepted': True,
            'releasedScopeRejected': True, 'expiredScopeRejected': True,
            'activeScopesAfter': 0, 'externalModelCalls': 0, 'modelExecuted': False, 'productionReady': False}


def main():
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        raw = sys.stdin.read(4097)
        assert len(raw.encode()) <= 4096
        secrets = json.loads(raw)
        assert isinstance(secrets, dict) and set(secrets) == {'controlToken'}
        assert isinstance(secrets['controlToken'], str)
        original_dns = socket.getaddrinfo
        def only_mcp(host, *args, **kwargs):
            if host not in ('jeju-mcp', b'jeju-mcp'):
                raise RuntimeError('NON_MCP_NETWORK_FORBIDDEN')
            return original_dns(host, *args, **kwargs)
        # These components contain no provider constructor. Restrict DNS as an
        # additional CI tripwire: only the private MCP host may be contacted.
        with patch('socket.getaddrinfo', side_effect=only_mcp):
            result = asyncio.run(check(secrets['controlToken']))
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_RUNTIME_CHECK_FAILED',
                          'errorType': type(error).__name__, 'externalModelCalls': 0, 'modelExecuted': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
