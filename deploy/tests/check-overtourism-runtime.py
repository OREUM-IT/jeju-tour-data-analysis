#!/usr/bin/env python3
"""CI-only real Langflow diagnosis components / MCP diagnosis scope check, without a model call.

Execute inside the disposable Langflow container with CI=true through
deploy/tests/run-overtourism-runtime-check.py. Reads {"controlToken": "..."} from
stdin; the reviewed component and bootstrap sources arrive as the globals
COMPONENTS_SOURCE and BOOTSTRAP_SOURCE. MCP_SERVICE_TOKEN stays in the container
environment. Fictional snapshot only; output is aggregate only.
"""
import asyncio
import json
import os
import re
import socket
import sys
import time
import types
import uuid
from unittest.mock import patch


BASE = 'http://jeju-mcp:8788'
REGIONS = ('jeju-dong', 'hallim', 'aewol', 'gujwa', 'jocheon', 'hangyeong', 'chuja', 'udo',
           'seogwipo-dong', 'daejeong', 'namwon', 'seongsan', 'andeok', 'pyoseon')


def snapshot():
    now = time.time()
    today = time.strftime('%Y-%m-%d', time.gmtime(now + 9 * 3600))
    at = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(now))
    return {'schemaVersion': '1.0', 'today': today, 'dataVersion': 'ci-fictional', 'crowd': {
        'ok': True, 'stale': False, 'at': at, 'total': 4, 'spots': [
            ['CI 합성 관광지 A', '제주시', 'udo', 'name', today, [70, 65, 50]],
            ['CI 합성 관광지 B', '제주시', 'udo', 'hub', today, [61, 20, 30]],
            ['CI 합성 관광지 C', '제주시', 'udo', 'near', today, [10, 80]],
            ['CI 합성 관광지 D', '서귀포시', None, None, today, [10]]]},
        'regions': [{'id': region, 'places': 1, 'lodging': {'businesses': 1, 'rooms': 1},
                     'regulation': {'places': 0, 'lifted': 0, 'cards': []}} for region in REGIONS],
        'sources': []}


async def check(control_token, components_source, bootstrap_source):
    import httpx
    from lfx.custom.custom_component.component import Component
    from lfx.custom.utils import build_custom_component_template
    from lfx.schema.message import Message

    assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
    service_token = os.environ['MCP_SERVICE_TOKEN']
    assert re.fullmatch(r'[\x21-\x7e]{32,256}', service_token)
    assert re.fullmatch(r'[\x21-\x7e]{32,256}', control_token) and service_token != control_token
    bootstrap = types.ModuleType('overtourism_ci_bootstrap')
    exec(compile(bootstrap_source, 'overtourism-bootstrap.py', 'exec'), bootstrap.__dict__)
    instances = {}
    for name in bootstrap.COMPONENTS:
        code = bootstrap.component_source(components_source, name)
        frontend, instance = build_custom_component_template(Component(_code=code), class_name=name)
        assert frontend['template']['code']['value'] == code
        instance.set(service_token=service_token)
        instances[name] = instance
    assert instances['JejuOvertourismValidation'].display_name == 'Jeju Overtourism Validation'

    async with httpx.AsyncClient(base_url=BASE, timeout=10, follow_redirects=False, trust_env=False) as client:
        control = {'Authorization': 'Bearer ' + control_token}
        ready = await client.get('/gateway/overtourism/readyz', headers=control)
        assert ready.status_code == 200 and ready.json()['activeScopes'] == 0, 'CI_REQUIRES_IDLE_DIAGNOSIS_SCOPES'
        diagnosis_id = 'ot-' + uuid.uuid4().hex
        registered = await client.post('/gateway/overtourism/scopes', headers=control, json={
            'diagnosisId': diagnosis_id, 'input': snapshot(), 'deadlineMs': int(time.time() * 1000) + 60_000})
        assert registered.status_code == 200
        try:
            prepare = instances['JejuOvertourismPrepare']
            prepare.set(original_request=Message(text=json.dumps({'diagnosisId': diagnosis_id}), session_id=diagnosis_id))
            prepared_message = await prepare.prepare_request()
            assert json.loads(prepared_message.text) == registered.json()
            scoped = instances['JejuOvertourismTools']
            scoped.set(prepared_request=prepared_message)
            tools = {tool.name: tool for tool in scoped.build_tools()}
            assert set(tools) == {'overtourism_overview', 'overtourism_regions', 'overtourism_rank'}
            overview = json.loads(await tools['overtourism_overview'].ainvoke({}))
            assert len(overview['data']['regions']) == 14
            assert next(row for row in overview['data']['regions'] if row['id'] == 'udo')['ruleLevel'] == 'high'
            detail = json.loads(await tools['overtourism_regions'].ainvoke({'ids': ['udo', 'aewol']}))
            assert [row['id'] for row in detail['data']['regions']] == ['udo', 'aewol']
            ranked = json.loads(await tools['overtourism_rank'].ainvoke({'metric': 'crowd.highShare', 'limit': 2}))
            assert ranked['data']['ranking'][0]['id'] == 'udo'
            # Rejected before HTTP: no budget spent.
            assert await tools['overtourism_regions'].ainvoke({'ids': ['nowhere']}) == 'OVERTOURISM_TOOL_ARGUMENTS_INVALID'
            answer = {'overview': 'CI 합성 개요', 'regions': [{'id': 'udo', 'level': 'high', 'summary': 'CI 합성 요약',
                      'evidence': ['crowd.highShare'], 'policy': 'CI 합성 정책', 'suggestions': [], 'missing': []}]}
            final = instances['JejuOvertourismValidation']
            final.set(prepared_request=prepared_message,
                      agent_response=Message(text='```json\n' + json.dumps(answer, ensure_ascii=False) + '\n```', session_id=diagnosis_id))
            accepted = json.loads((await final.validate_response()).text)
            assert accepted['status'] == 'accepted' and accepted['diagnosisId'] == diagnosis_id
            assert accepted['adjustedRegions'] == 13
            result = await client.post('/gateway/overtourism/result', headers=control, json={'diagnosisId': diagnosis_id})
            assert result.status_code == 200 and result.json()['diagnosisSha256'] == accepted['diagnosisSha256']
            denied = await client.post('/gateway/overtourism/result', headers={'Authorization': 'Bearer ' + service_token},
                                       json={'diagnosisId': diagnosis_id})
            assert denied.status_code == 401
            # Three calls spent; three more succeed, then the scope budget is exhausted.
            for _ in range(3):
                assert len(json.loads(await tools['overtourism_overview'].ainvoke({}))['data']['regions']) == 14
            assert await tools['overtourism_overview'].ainvoke({}) == 'OVERTOURISM_TOOL_FAILED'
        finally:
            released = await client.delete('/gateway/overtourism/scopes/' + diagnosis_id, headers=control)
            assert released.status_code == 200
        assert await tools['overtourism_regions'].ainvoke({'ids': ['udo']}) == 'OVERTOURISM_TOOL_FAILED'
        ready = await client.get('/gateway/overtourism/readyz', headers=control)
        assert ready.status_code == 200 and ready.json()['activeScopes'] == 0
    return {'status': 'passed', 'compiledComponents': 3, 'structuredTools': 3, 'successfulScopedToolCalls': 6,
            'finalValidationAccepted': True, 'resultReadByControlOnly': True, 'toolBudgetEnforced': True,
            'releasedScopeRejected': True, 'activeScopesAfter': 0, 'externalModelCalls': 0,
            'modelExecuted': False, 'productionReady': False}


def main():
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        raw = sys.stdin.read(4097)
        assert len(raw.encode()) <= 4096
        secrets = json.loads(raw)
        assert isinstance(secrets, dict) and set(secrets) == {'controlToken'} and isinstance(secrets['controlToken'], str)
        components, bootstrap = globals()['COMPONENTS_SOURCE'], globals()['BOOTSTRAP_SOURCE']
        original_dns = socket.getaddrinfo

        def only_mcp(host, *args, **kwargs):
            if host not in ('jeju-mcp', b'jeju-mcp'):
                raise RuntimeError('NON_MCP_NETWORK_FORBIDDEN')
            return original_dns(host, *args, **kwargs)
        # No provider constructor runs here; DNS is restricted as an extra tripwire.
        with patch('socket.getaddrinfo', side_effect=only_mcp):
            result = asyncio.run(check(secrets['controlToken'], components, bootstrap))
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'OVERTOURISM_RUNTIME_CHECK_FAILED',
                          'errorType': type(error).__name__, 'externalModelCalls': 0, 'modelExecuted': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
