#!/usr/bin/env python3
"""Disposable-CI-only: run the stored Gateway flow once and count what Langflow kept as messages.

Stream into the official Langflow container through deploy/tests/run-gateway-message-store-check.py,
which sends {"controlToken", "apiKey", "expectStored"} on stdin, never in arguments or logs. It
registers one MCP scope for a synthetic request carrying a random marker word, runs the stored
Gateway flow through /api/v1/run with the Gateway's own Langflow key as the Gateway does, releases
the scope, then lists that session's messages through the monitor API.

In CI the Vertex credential is a placeholder, so the Agent stops while building its client and the
run ends with Langflow's error message; no provider is called. lfx stores that error message for
any flow, so this checks the request text: with storage off no stored message may carry the marker
and every stored message must be an error; with storage on the request message must be there.
Only counts are printed.
"""
import json
import os
import re
import sys
import time
import uuid

FLOW_NAME = '제주 관광 Agent v2 — Gateway 제한 실행'


def check(secrets, environment):
    import httpx
    request_id = 'gw-' + uuid.uuid4().hex
    marker = 'CIMARK' + uuid.uuid4().hex[:16]
    request = {'schemaVersion': '1.0', 'requestId': request_id, 'timezone': 'Asia/Seoul', 'filters': {},
               'excludedIds': [], 'query': f'CI 합성 설문 {marker}: 실내 조건 확인 필요 안내',
               'requirements': [{'type': 'indoor_required', 'value': True}]}
    with httpx.Client(base_url='http://127.0.0.1:7860', timeout=60, follow_redirects=False, trust_env=False) as langflow, \
         httpx.Client(base_url='http://jeju-mcp:8788', timeout=10, follow_redirects=False, trust_env=False) as mcp:
        login = langflow.post('/api/v1/login', data={'username': environment['LANGFLOW_SUPERUSER'],
                                                     'password': environment['LANGFLOW_SUPERUSER_PASSWORD']})
        assert login.status_code == 200
        bearer = {'Authorization': 'Bearer ' + login.json()['access_token']}
        listed = langflow.get('/api/v1/flows/', headers=bearer, params={'get_all': 'true', 'header_flows': 'true',
                                                                        'remove_example_flows': 'true'})
        assert listed.status_code == 200
        matches = [item['id'] for item in listed.json() if item.get('name') == FLOW_NAME]
        assert len(matches) == 1
        flow_id = matches[0]
        control = {'Authorization': 'Bearer ' + secrets['controlToken']}
        registered = mcp.post('/gateway/scopes', headers=control, json={
            'request': request, 'principal': 'e' * 64, 'deadlineMs': int(time.time() * 1000) + 60_000})
        assert registered.status_code == 200, 'CI_REQUIRES_FREE_SCOPE'
        try:
            run = langflow.post(f'/api/v1/run/{flow_id}', params={'stream': 'false'},
                                headers={'x-api-key': secrets['apiKey']},
                                json={'input_value': json.dumps(request), 'input_type': 'chat', 'output_type': 'chat',
                                      'output_component': 'ChatOutput-jejuV2', 'session_id': request_id})
        finally:
            released = mcp.delete(f'/gateway/scopes/{request_id}', headers=control)
        assert released.status_code == 200
        listed = langflow.get('/api/v1/monitor/messages', headers=bearer,
                              params={'flow_id': flow_id, 'session_id': request_id})
        assert listed.status_code == 200
        rows = listed.json()
        assert isinstance(rows, list)
    with_text = sum(marker in json.dumps(row, ensure_ascii=False) for row in rows)
    other = sum(row.get('category') != 'error' for row in rows)
    if secrets['expectStored']:
        assert with_text >= 1, 'REQUEST_MESSAGE_MISSING'
    else:
        assert with_text == 0 and other == 0, 'MESSAGE_STORED'
    return {'status': 'passed', 'messageStore': secrets['expectStored'], 'messages': len(rows),
            'messagesWithRequestText': with_text, 'errorMessages': len(rows) - other, 'runHttpStatus': run.status_code,
            'modelExecuted': False, 'productionReady': False}


def main():
    try:
        assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
        raw = sys.stdin.read(8193)
        assert len(raw.encode()) <= 8192
        secrets = json.loads(raw)
        assert isinstance(secrets, dict) and set(secrets) == {'controlToken', 'apiKey', 'expectStored'}
        assert isinstance(secrets['controlToken'], str) and re.fullmatch(r'[a-f0-9]{64}', secrets['controlToken'])
        assert isinstance(secrets['apiKey'], str) and re.fullmatch(r'sk-[A-Za-z0-9_-]{10,256}', secrets['apiKey'])
        assert isinstance(secrets['expectStored'], bool)
        print(json.dumps(check(secrets, os.environ)))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_MESSAGE_STORE_CHECK_FAILED',
                          'errorType': type(error).__name__, 'modelExecuted': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
