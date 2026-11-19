#!/usr/bin/env python3
"""Run one synthetic AWS Agent request; publish only fixed codes and counts.

Execute through docker exec stdin. --check-only tests preparation and temporary
API-key cleanup without inference. --run-once can incur existing Vertex charges.
The remote Agent has its existing 90-second/four-model-call guard. HTTP timeout
does not establish remote cancellation; this command never retries inference.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import uuid


MAX_BYTES = 2 * 1024 * 1024
FINAL_COMPONENT = 'ChatOutput-jejuV2'
EVIDENCE_FIELDS = {'traceCount', 'unfinishedTraces', 'failedTraces', 'modelCallsObserved',
                   'successfulModelCalls', 'successfulMcpToolCalls', 'unexpectedToolCalls'}


class SmokeError(Exception):
    """Only fixed diagnostic codes, never remote exception text."""


def require(condition, code):
    if not condition:
        raise SmokeError(code)


def make_request(session_id):
    return {'schemaVersion': '1.0', 'requestId': session_id,
            'query': '제주에서 실내 방문 조건이 필요한 장소 후보를 최대 3곳 찾아주세요. '
                     '실내 여부가 검증되지 않았으면 확인이 필요하다고 안내해 주세요.',
            'timezone': 'Asia/Seoul', 'filters': {}, 'excludedIds': [],
            'requirements': [{'type': 'indoor_required', 'value': True}]}


def read_json(response, expected=200):
    require(response.status_code == expected, 'LANGFLOW_HTTP_REJECTED')
    require(len(response.content) <= MAX_BYTES, 'RESPONSE_TOO_LARGE')
    return response.json()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_RESPONSE_KEY')
        result[key] = value
    return result


def final_response(raw, session_id):
    require(isinstance(raw, dict) and raw.get('session_id') == session_id, 'SESSION_MISMATCH')
    outputs = raw.get('outputs')
    require(isinstance(outputs, list) and len(outputs) == 1, 'OUTPUT_GROUP_MISMATCH')
    entries = outputs[0].get('outputs', [])
    require(isinstance(entries, list), 'OUTPUT_COMPONENT_MISMATCH')
    selected = [item for item in entries if item.get('component_id') == FINAL_COMPONENT]
    require(len(selected) == 1, 'OUTPUT_COMPONENT_MISMATCH')
    message = selected[0].get('results', {}).get('message', {})
    source = message.get('properties', {}).get('source', {})
    require(message.get('sender') == 'Machine' and source.get('id') == 'JejuFinalValidation-v2'
            and source.get('source') == 'Jeju Final Validation', 'FINAL_VALIDATOR_SOURCE_MISMATCH')
    text = message.get('text')
    require(isinstance(text, str) and len(text.encode()) <= 256 * 1024, 'FINAL_MESSAGE_INVALID')
    value = json.loads(text, object_pairs_hook=unique_object)
    require(isinstance(value, dict), 'FINAL_MESSAGE_INVALID')
    if value.get('status') == 'blocked':
        raise SmokeError('FINAL_VALIDATION_BLOCKED')
    require(value.get('requestId') == session_id, 'REQUEST_ID_MISMATCH')
    return value


def trace_summary(traces, spans):
    model = [s for s in spans if s[0] == 'llm']
    tools = [s for s in spans if s[0] == 'tool']
    succeeded = lambda rows: sum(row[1] == 'ok' and row[2] is not None for row in rows)
    known = {'places.search', 'places.get', 'constraints.check'}
    return {'traceCount': len(traces),
            'unfinishedTraces': sum(row[1] is None for row in traces),
            'failedTraces': sum(row[0] == 'error' for row in traces),
            'modelCallsObserved': len(model), 'successfulModelCalls': succeeded(model),
            'successfulMcpToolCalls': succeeded([s for s in tools if s[3] in known]),
            'unexpectedToolCalls': sum(s[3] not in known for s in tools)}


def checked_evidence(value):
    require(isinstance(value, dict) and set(value) == EVIDENCE_FIELDS
            and all(type(n) is int and n >= 0 for n in value.values()), 'RUNTIME_EVIDENCE_INCOMPLETE')
    return value


def observe(database_url, flow_id, session_id):
    # Read metadata only: no credentials, messages, prompts, outputs or errors.
    import psycopg
    require(database_url.startswith('postgresql+psycopg://'), 'DATABASE_URL_UNSUPPORTED')
    with psycopg.connect(database_url.replace('postgresql+psycopg://', 'postgresql://', 1),
                         connect_timeout=5, autocommit=True,
                         options='-c default_transaction_read_only=on -c statement_timeout=5000') as db:
        for attempt in range(5):
            binding = (session_id, uuid.UUID(flow_id))
            traces = db.execute('SELECT status::text,end_time FROM trace '
                                'WHERE session_id=%s AND flow_id=%s', binding).fetchall()
            spans = db.execute('SELECT s.span_type::text,s.status::text,s.end_time,s.name '
                               'FROM span s JOIN trace t ON t.id=s.trace_id '
                               'WHERE t.session_id=%s AND t.flow_id=%s', binding).fetchall()
            summary = trace_summary(traces, spans)
            if summary['traceCount'] and summary['unfinishedTraces'] == 0:
                return summary
            if attempt < 4:
                time.sleep(1)
    return summary


def execute(client, mcp, environ, template, setup, *, check_only=False, observer=observe):
    session_id = 'aws-smoke-' + uuid.uuid4().hex
    result = {'status': 'failed', 'phase': 'preflight', 'sessionId': session_id,
              'runSubmitted': False, 'modelExecuted': False, 'finalValidationAccepted': False,
              'temporaryApiKeyRevoked': None, 'productionReady': False}
    key_id = None
    flow_id = None
    started = time.monotonic()
    try:
        preflight = setup.bootstrap(client, mcp, environ, template)
        require(check_only or preflight['vertexConfigured'], 'VERTEX_CREDENTIAL_MISSING')
        listed = read_json(client.get('/api/v1/flows/', params={
            'get_all': 'true', 'header_flows': 'true', 'remove_example_flows': 'true'}))
        matches = [row for row in listed if row.get('name') == setup.FLOW_NAME]
        require(len(matches) == 1, 'FLOW_NAME_CONFLICT')
        flow_id = str(uuid.UUID(matches[0]['id']))
        request = make_request(session_id)
        auth = {'Authorization': 'Bearer ' + environ['MCP_SERVICE_TOKEN']}
        prepared = read_json(mcp.post('/prepare-request', headers=auth, json=request))
        require(prepared.get('status') == 'prepared' and prepared.get('request') == request
                and prepared.get('candidateCount', 0) > 0, 'REQUEST_PREPARATION_FAILED')
        result['phase'] = 'temporary_api_key'
        expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        created = read_json(client.post('/api/v1/api_key/', json={
            'name': session_id, 'expires_at': expiry.isoformat()}))
        key_id = str(uuid.UUID(created['id']))
        require(isinstance(created.get('api_key'), str) and created['api_key'].startswith('sk-'),
                'TEMPORARY_API_KEY_INVALID')
        if check_only:
            result['runtimeEvidence'] = checked_evidence(observer(environ['LANGFLOW_DATABASE_URL'], flow_id, session_id))
            require(all(value == 0 for value in result['runtimeEvidence'].values()), 'SESSION_ALREADY_USED')
            result['status'] = 'preflight_passed_without_inference'
        else:
            result.update(phase='agent_request', runSubmitted=True, modelExecuted=None)
            # One HTTP submission, no inference retry and no runtime tweaks.
            with client.stream('POST', f'/api/v1/run/{flow_id}?stream=false',
                               headers={'x-api-key': created['api_key']}, timeout=120,
                               json={'input_value': json.dumps(request, ensure_ascii=False),
                                     'input_type': 'chat', 'output_type': 'chat',
                                     'output_component': FINAL_COMPONENT, 'session_id': session_id}) as response:
                result['httpStatus'] = response.status_code
                require(response.status_code == 200, 'AGENT_HTTP_REJECTED')
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    require(len(content) <= MAX_BYTES, 'RESPONSE_TOO_LARGE')
            result['phase'] = 'final_validation'
            final = final_response(json.loads(content, object_pairs_hook=unique_object), session_id)
            checked = read_json(mcp.post('/validate-response', headers=auth, json={
                'request': request, 'response': final,
                'expectedCatalogVersion': prepared['catalogVersion']}))
            require(checked.get('status') == 'accepted' and checked.get('reasons') == []
                    and checked.get('response') == final
                    and all(checked.get(k) == prepared[k] for k in
                            ('requestSha256', 'catalogSha256', 'catalogVersion', 'policyVersion', 'candidateCount')),
                    'INDEPENDENT_VALIDATION_REJECTED')
            result['finalValidationAccepted'] = True
            # This synthetic probe specifically tests unverified indoor conditions.
            require(final.get('action') == 'cannot_confirm' and 1 <= len(final.get('placeIds', [])) <= 3
                    and final.get('confirmationNotice') is True
                    and final.get('noticeTopics') == ['indoor_unverified'], 'SMOKE_EXPECTATION_NOT_MET')
            result.update(finalValidationAccepted=True, action='cannot_confirm',
                          placeCount=len(final['placeIds']), confirmationNotice=True,
                          noticeTopics=['indoor_unverified'], status='response_validated')
    except Exception as error:
        if isinstance(error, (SmokeError, setup.BootstrapError)):
            result['code'] = str(error)
        elif type(error).__name__ in ('ReadTimeout', 'ConnectTimeout', 'WriteTimeout', 'PoolTimeout'):
            result['code'] = 'HTTP_TIMEOUT_NO_AUTOMATIC_RETRY'
        else:
            result['code'] = 'SMOKE_CHECK_FAILED'
    finally:
        if key_id is not None:
            try:
                result['temporaryApiKeyRevoked'] = client.delete(f'/api/v1/api_key/{key_id}').status_code == 200
            except Exception:
                result['temporaryApiKeyRevoked'] = False
        if result['runSubmitted']:
            try:
                evidence = checked_evidence(observer(environ['LANGFLOW_DATABASE_URL'], flow_id, session_id))
                result['runtimeEvidence'] = evidence
                if evidence['successfulModelCalls'] > 0:
                    result['modelExecuted'] = True
                if result['status'] == 'response_validated':
                    require(evidence['traceCount'] == 1 and evidence['unfinishedTraces'] == 0
                            and evidence['failedTraces'] == 0 and 1 <= evidence['modelCallsObserved'] <= 4
                            and evidence['successfulModelCalls'] >= 1
                            and evidence['successfulMcpToolCalls'] >= 1
                            and evidence['unexpectedToolCalls'] == 0, 'RUNTIME_EVIDENCE_INCOMPLETE')
                    result.update(status='passed', phase='complete')
            except Exception:
                if result['status'] == 'response_validated':
                    result.update(status='incomplete', code='RUNTIME_EVIDENCE_INCOMPLETE')
        if key_id is not None and result['temporaryApiKeyRevoked'] is not True:
            result.update(status='failed', code='TEMPORARY_API_KEY_CLEANUP_FAILED')
        result['elapsedSeconds'] = round(time.monotonic() - started, 1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check-only', action='store_true')
    modes.add_argument('--run-once', action='store_true')
    args = parser.parse_args()
    result = None
    try:
        import httpx
        sys.path.insert(0, '/opt/oreumit')
        import bootstrap as setup
        template = json.loads(Path('/opt/oreumit/agent-v2.flow.json').read_text())
        with httpx.Client(base_url=setup.LANGFLOW_URL, timeout=30, trust_env=False, follow_redirects=False) as client, \
             httpx.Client(base_url=setup.MCP_URL, timeout=10, trust_env=False, follow_redirects=False) as mcp:
            result = execute(client, mcp, os.environ, template, setup, check_only=args.check_only)
    except Exception:
        if result is None:
            result = {'status': 'failed', 'code': 'SMOKE_INITIALIZATION_FAILED', 'runSubmitted': False,
                      'modelExecuted': False, 'productionReady': False}
        else:
            result.update(status='failed', code='CLIENT_CLEANUP_FAILED')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('passed', 'preflight_passed_without_inference') else 1


if __name__ == '__main__':
    raise SystemExit(main())
