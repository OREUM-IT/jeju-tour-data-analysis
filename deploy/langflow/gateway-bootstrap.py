#!/usr/bin/env python3
"""Provision a separate restricted Gateway flow without executing a model.

Run inside the Langflow container via the host preparation script. --provision
stdout contains a dedicated API secret: NEVER run it directly in an interactive
terminal or capture it in logs. The host must privately capture JSON and persist
only its required credentials in mode-0600 files. --check never issues a key.

Langflow 1.13.3 keys belong to the logged-in admin user, not to a single flow.
The runtime Gateway receives only the new 30-day key, never the admin password
or login bearer token; network and endpoint restrictions remain necessary.

--update / --rollback change only the stored graph and description of the one
existing Gateway flow, between the exact reviewed 4-call build and the 8-call
build whose last allowed call has no tool use. They never create a flow or key.
"""
import argparse
import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import uuid


FLOW_NAME = '제주 관광 Agent v2 — Gateway 제한 실행'
LANGFLOW_URL = 'http://127.0.0.1:7860'
MCP_URL = 'http://jeju-mcp:8788'
VERSION = '1.13.3'
COMPONENTS = ('JejuPrepareRequest', 'JejuFinalValidation', 'JejuScopedTools')
SCOPED_ID = 'JejuScopedTools-gateway'
KEY_PREFIX = 'oreumit-agent-gateway-'
LEGACY_MODEL_CALLS = 4
MODEL_CALLS = 8
FLOW_KEYS = ('name', 'data', 'endpoint_name', 'description', 'tags',
             'access_type', 'webhook', 'mcp_enabled', 'is_component')
LEGACY_DESCRIPTION = ('별도 Gateway flow. 원본 요청 범위의 세 도구만 사용하며 모델4회/90초·출력2048토큰과 '
                      '최종 검증을 적용한다. 운영 준비 완료를 뜻하지 않는다.')
DESCRIPTION = ('별도 Gateway flow. 원본 요청 범위의 세 도구만 사용하며 모델8회(마지막 호출은 도구 없는 최종 답변)'
               '/90초·출력2048토큰과 최종 검증을 적용한다. 운영 준비 완료를 뜻하지 않는다.')
LIMIT_LINE = '            middleware.append(ModelCallLimitMiddleware(run_limit=run_limit))\n'
GUARDED_LIMIT_LINES = ('            middleware.append(_GatewayFinalAnswerMiddleware(run_limit=run_limit))\n'
                       + LIMIT_LINE)
LIMIT_MARKER = 'GUARDED_AGENT_MODEL_CALL_LIMIT = {}\n'
# Appended to the Gateway Agent code only. Langflow 1.13.3 executes the imports
# first and then only assignments/classes/functions at module level, so keep it
# to those statement kinds.
FINAL_ANSWER_GUARD = '''# Gateway-only final-answer guard. This flow sets max_iterations=8, which
# Langflow 1.13.3 maps to ModelCallLimitMiddleware(run_limit=8). Without this
# guard, a run whose calls are all tool calls ends with the limit notice instead
# of the JSON answer. On the last allowed call the tools stay declared for valid
# history, but tool_choice="none" forbids a new call. Validators still decide.
from collections.abc import Mapping as _GatewayMapping
from langchain.agents.middleware import AgentMiddleware as _GatewayAgentMiddleware
from langchain_core.messages import AIMessage as _GatewayAIMessage
from langchain_core.messages import SystemMessage as _GatewaySystemMessage

GATEWAY_FINAL_ANSWER_NOTICE = (
    "서버 제한: 이번이 이 요청의 마지막 모델 호출이며 도구를 더 호출할 수 없다. "
    "지금까지 받은 도구 결과만 사용해 위 형식의 최종 JSON 객체 하나만 코드 블록이나 설명 없이 출력한다. "
    "도구 결과에 없는 장소 ID는 쓰지 않는다."
)


class _GatewayFinalAnswerMiddleware(_GatewayAgentMiddleware):
    """Reserve the last allowed model call of one run for the final answer."""

    def __init__(self, *, run_limit):
        super().__init__()
        if isinstance(run_limit, bool) or not isinstance(run_limit, int) or run_limit < 2:
            raise ValueError("AGENT_EXECUTION_POLICY_INVALID")
        self.run_limit = run_limit

    def _final_answer_request(self, request):
        state = request.state if isinstance(request.state, _GatewayMapping) else {}
        counted = state.get("run_model_call_count", 0)
        if isinstance(counted, bool) or not isinstance(counted, int) or counted < 0:
            counted = 0
        # n_messages=0: every AI message in this request came from this run.
        replies = sum(isinstance(message, _GatewayAIMessage) for message in request.messages)
        if max(counted, replies) < self.run_limit - 1:
            return request
        system = request.system_message
        content = "" if system is None else system.content
        if isinstance(content, str):
            content = (content + "\\n\\n" if content else "") + GATEWAY_FINAL_ANSWER_NOTICE
        else:
            content = [*content, {"type": "text", "text": GATEWAY_FINAL_ANSWER_NOTICE}]
        return request.override(tool_choice="none", system_message=_GatewaySystemMessage(content=content))

    def wrap_model_call(self, request, handler):
        return handler(self._final_answer_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._final_answer_request(request))
'''


class GatewayBootstrapError(Exception):
    """Fixed diagnostic codes only; never provider bodies or secret values."""


def require(condition, code):
    if not condition:
        raise GatewayBootstrapError(code)


def payload(response, expected=(200,)):
    require(response.status_code in expected, 'GATEWAY_BOOTSTRAP_HTTP_REJECTED')
    return response.json()


def load_setup(path=Path('/opt/oreumit/bootstrap.py')):
    spec = importlib.util.spec_from_file_location('gateway_base_bootstrap', path)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    return setup


def component_source(source, class_name):
    """Keep the selected Component and all helpers/Pydantic argument classes."""
    require(class_name in COMPONENTS, 'GATEWAY_COMPONENT_UNKNOWN')
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and any(isinstance(base, ast.Name) and base.id == 'Component' for base in node.bases)]
    require({node.name for node in classes} == set(COMPONENTS), 'GATEWAY_COMPONENT_SOURCE_MISMATCH')
    remove = {line for node in classes if node.name != class_name
              for line in range(node.lineno - 1, node.end_lineno)}
    result = ''.join(line for index, line in enumerate(source.splitlines(keepends=True)) if index not in remove)
    ast.parse(result)
    return result


def flow_sha256(data):
    # JSON.stringify uses integral rendering for e.g. 90.0; normalize before
    # sorted-key Python JSON so the Gateway can compare the exact stored graph.
    def normalize(value):
        if isinstance(value, float):
            require(math.isfinite(value), 'GATEWAY_FLOW_NUMBER_INVALID')
            return int(value) if value.is_integer() else value
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            require(all(isinstance(key, str) and key.isascii() for key in value), 'GATEWAY_FLOW_KEY_INVALID')
            return {key: normalize(item) for key, item in value.items()}
        return value
    encoded = json.dumps(normalize(data), sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def edge(source, source_type, output, target, field, *, tool=False):
    types = ['Tool'] if tool else ['Message']
    source_handle = {'dataType': source_type, 'id': source, 'name': output, 'output_types': types}
    target_handle = {'fieldName': field, 'id': target, 'inputTypes': types, 'type': 'other'}
    encode = lambda value: json.dumps(value, separators=(',', ':')).replace('"', 'œ')
    return {'id': f'edge-{source}-{output}-{target}-{field}', 'source': source, 'target': target,
            'sourceHandle': encode(source_handle), 'targetHandle': encode(target_handle),
            'data': {'sourceHandle': source_handle, 'targetHandle': target_handle},
            'animated': False, 'selected': False}


def build_flow(client, template, source, setup):
    original_mcp = [node['data']['node'] for node in template['data']['nodes'] if node['data']['type'] == 'MCPTools']
    require(len(original_mcp) == 1, 'GATEWAY_TEMPLATE_MISMATCH')
    # Reuse the reviewed direct Vertex transformation and original graph guard.
    # The original MCPTools node is removed, never connected in this new flow.
    flow = setup.deployment_flow(template, original_mcp[0])
    custom = {}
    for name in COMPONENTS:
        code = component_source(source, name)
        result = payload(client.post('/api/v1/custom_component', json={'code': code}))
        node = result.get('data')
        require(isinstance(node, dict) and node.get('template', {}).get('code', {}).get('value') == code,
                'GATEWAY_COMPONENT_BUILD_MISMATCH')
        require(set(node['template']) >= {'service_token', 'code'}, 'GATEWAY_COMPONENT_BUILD_MISMATCH')
        node['template']['service_token'].update(value=setup.MCP_TOKEN_NAME, load_from_db=True)
        custom[name] = node
    nodes = flow['data']['nodes']
    by_type = {node['data']['type']: node for node in nodes}
    for name in COMPONENTS[:2]:
        by_type[name]['data']['node'] = custom[name]
    scoped = by_type['MCPTools']
    scoped['id'] = scoped['data']['id'] = SCOPED_ID
    scoped['data']['type'] = 'JejuScopedTools'
    scoped['data']['node'] = custom['JejuScopedTools']
    fields = by_type['Agent']['data']['node']['template']
    fields['max_tokens']['value'] = 2048
    require(fields['max_iterations']['value'] == 4 and fields['n_messages']['value'] == 0
            and all(fields[name]['value'] is False for name in
                    ('add_calculator_tool', 'add_current_date_tool', 'handle_parsing_errors')),
            'GATEWAY_AGENT_LIMITS_MISMATCH')
    code = fields['code']['value']
    require('GUARDED_AGENT_MAX_SECONDS = 90.0' in code and 'GUARDED_AGENT_MODEL_CALL_LIMIT = 4' in code
            and 'result = await _guarded_agent_run(self, _run_once)' in code,
            'GATEWAY_AGENT_GUARD_MISSING')
    require([item['name'] for item in by_type['Agent']['data']['node']['outputs']] == ['response'],
            'GATEWAY_AGENT_OUTPUT_MISMATCH')
    require(by_type['ChatOutput']['id'] == 'ChatOutput-jejuV2'
            and by_type['JejuFinalValidation']['id'] == 'JejuFinalValidation-v2'
            and custom['JejuFinalValidation'].get('display_name') == 'Jeju Final Validation',
            'GATEWAY_FINAL_OUTPUT_MISMATCH')
    ids = {kind: node['id'] for kind, node in by_type.items()}
    flow['data']['edges'] = [
        edge(ids['ChatInput'], 'ChatInput', 'message', ids['JejuPrepareRequest'], 'original_request'),
        edge(ids['JejuPrepareRequest'], 'JejuPrepareRequest', 'prepared_request', ids['Agent'], 'input_value'),
        edge(ids['JejuPrepareRequest'], 'JejuPrepareRequest', 'prepared_request', SCOPED_ID, 'prepared_request'),
        edge(SCOPED_ID, 'JejuScopedTools', 'tools', ids['Agent'], 'tools', tool=True),
        edge(ids['JejuPrepareRequest'], 'JejuPrepareRequest', 'prepared_request', ids['JejuFinalValidation'], 'prepared_request'),
        edge(ids['Agent'], 'Agent', 'response', ids['JejuFinalValidation'], 'agent_response'),
        edge(ids['JejuFinalValidation'], 'JejuFinalValidation', 'message', ids['ChatOutput'], 'input_value'),
    ]
    # last_tested_version belongs to exported templates, not FlowCreate/FlowRead
    # in Langflow 1.13.3. Do not require an ignored field in stored metadata.
    flow.pop('last_tested_version', None)
    flow.update(name=FLOW_NAME, endpoint_name=None, access_type='PRIVATE',
                webhook=False, mcp_enabled=False, is_component=False,
                description=LEGACY_DESCRIPTION)
    return flow


def budget_flow(legacy):
    """Return the 8-call Gateway flow derived from the exact 4-call build."""
    flow = copy.deepcopy(legacy)
    agents = [node for node in flow['data']['nodes'] if node['data']['type'] == 'Agent']
    require(len(agents) == 1 and legacy.get('description') == LEGACY_DESCRIPTION, 'GATEWAY_TEMPLATE_MISMATCH')
    fields = agents[0]['data']['node']['template']
    code = fields['code']['value']
    require(fields['max_iterations']['value'] == LEGACY_MODEL_CALLS and code.endswith('\n')
            and code.count(LIMIT_LINE) == 1 and code.count(LIMIT_MARKER.format(LEGACY_MODEL_CALLS)) == 1
            and '_GatewayFinalAnswerMiddleware' not in code, 'GATEWAY_AGENT_GUARD_MISSING')
    code = (code.replace(LIMIT_LINE, GUARDED_LIMIT_LINES)
            .replace(LIMIT_MARKER.format(LEGACY_MODEL_CALLS), LIMIT_MARKER.format(MODEL_CALLS)))
    code += '\n\n' + FINAL_ANSWER_GUARD
    ast.parse(code)
    fields['code']['value'] = code
    fields['max_iterations']['value'] = MODEL_CALLS
    flow['description'] = DESCRIPTION
    return flow


def same_flow(stored, flow):
    return all(stored.get(key) == flow.get(key) for key in FLOW_KEYS)


def stored_flow_id(client, *, required):
    listed = payload(client.get('/api/v1/flows/', params={'get_all': 'true', 'header_flows': 'true', 'remove_example_flows': 'true'}))
    require(isinstance(listed, list), 'GATEWAY_FLOW_LIST_INVALID')
    matches = [item for item in listed if item.get('name') == FLOW_NAME]
    require(len(matches) <= 1, 'GATEWAY_FLOW_NAME_CONFLICT')
    if not matches:
        require(not required, 'GATEWAY_FLOW_NOT_PROVISIONED')
        return None
    return parse_flow_id(matches[0].get('id'))


def parse_flow_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise GatewayBootstrapError('GATEWAY_FLOW_ID_INVALID') from None


def patch_flow(client, flow_id, stored, flow):
    try:
        version = str(uuid.UUID(stored.get('version_token')))
    except (ValueError, TypeError, AttributeError):
        raise GatewayBootstrapError('GATEWAY_FLOW_VERSION_UNAVAILABLE') from None
    # If-Match makes Langflow reject a concurrent graph edit. Only the graph and
    # its description change; name, access, endpoint and credentials stay put.
    payload(client.patch(f'/api/v1/flows/{flow_id}', headers={'If-Match': version},
                         json={'data': flow['data'], 'description': flow['description']}))
    return payload(client.get(f'/api/v1/flows/{flow_id}'))


def migrate_flow(client, target, previous):
    """Switch the existing flow from exactly `previous` to exactly `target`."""
    flow_id = stored_flow_id(client, required=True)
    stored = payload(client.get(f'/api/v1/flows/{flow_id}'))
    if same_flow(stored, target):
        return flow_id, 'already-current'
    require(same_flow(stored, previous), 'GATEWAY_EXISTING_FLOW_DIFFERS')
    updated = patch_flow(client, flow_id, stored, target)
    if not same_flow(updated, target):
        # Best effort: put back the exact previous graph so the operator can retry.
        try:
            patch_flow(client, flow_id, updated, previous)
        except Exception:
            pass
        raise GatewayBootstrapError('GATEWAY_FLOW_UPDATE_UNVERIFIED')
    return flow_id, 'updated'


def checked_flow(client, flow, *, provision):
    flow_id = stored_flow_id(client, required=not provision)
    if flow_id is None:
        created = payload(client.post('/api/v1/flows/', json=flow), (201,))
        flow_id = parse_flow_id(created.get('id'))
    stored = payload(client.get(f'/api/v1/flows/{flow_id}'))
    require(same_flow(stored, flow), 'GATEWAY_EXISTING_FLOW_DIFFERS')
    return flow_id


def configure(client, mcp, environment, template, source, setup, *, provision=False, migrate=None, now=None):
    require(migrate in (None, 'update', 'rollback') and not (provision and migrate), 'GATEWAY_MODE_INVALID')
    require(client.get('/api/v1/users/whoami').status_code in (401, 403), 'GATEWAY_ANONYMOUS_ACCESS_ALLOWED')
    require(client.get('/api/v1/auto_login').status_code == 403, 'GATEWAY_AUTO_LOGIN_ENABLED')
    username = environment['LANGFLOW_SUPERUSER']
    password = environment['LANGFLOW_SUPERUSER_PASSWORD']
    token = environment['MCP_SERVICE_TOKEN']
    require(username and password and re.fullmatch(r'[\x21-\x7e]{32,256}', token), 'GATEWAY_CREDENTIALS_INVALID')
    logged_in = payload(client.post('/api/v1/login', data={'username': username, 'password': password}))
    access_token = logged_in.get('access_token')
    require(isinstance(access_token, str) and len(access_token) >= 32, 'GATEWAY_LOGIN_INVALID')
    client.headers['Authorization'] = 'Bearer ' + access_token
    require(payload(client.get('/api/v1/users/whoami')).get('is_active') is True, 'GATEWAY_LOGIN_INVALID')
    require(payload(client.get('/health_check')).get('status') == 'ok', 'GATEWAY_LANGFLOW_UNHEALTHY')
    require(payload(client.get('/api/v1/version')).get('version') == VERSION, 'GATEWAY_LANGFLOW_VERSION_MISMATCH')
    ready = payload(mcp.get('/readyz', headers={'Authorization': 'Bearer ' + token}))
    require(ready.get('ready') is True and ready.get('catalogSchemaVersion') == '2.0'
            and ready.get('dataMode') == 'app-export' and ready.get('placeCount') == 981
            and ready.get('readOnly') is True and ready.get('productionReady') is False,
            'GATEWAY_CATALOG_NOT_READY')
    variables = payload(client.get('/api/v1/variables/'))
    require(isinstance(variables, list), 'GATEWAY_VARIABLES_INVALID')
    for name in (setup.MCP_TOKEN_NAME, setup.VERTEX_TOKEN_NAME):
        entries = [item for item in variables if item.get('name') == name]
        require(len(entries) == 1 and entries[0].get('type') == 'Credential'
                and entries[0].get('has_value') is True, 'GATEWAY_CREDENTIAL_UNAVAILABLE')
    legacy = build_flow(client, template, source, setup)
    flow = budget_flow(legacy)
    for built in (legacy, flow):
        setup.without_secrets(built, [token, password, access_token])
    if migrate is not None:
        target, previous = (flow, legacy) if migrate == 'update' else (legacy, flow)
        flow_id, status = migrate_flow(client, target, previous)
        return {'status': 'passed', 'flowStatus': status, 'flowId': flow_id,
                'flowSha256': flow_sha256(target['data']),
                'modelCallLimit': MODEL_CALLS if migrate == 'update' else LEGACY_MODEL_CALLS,
                'modelExecuted': False, 'productionReady': False}
    flow_id = checked_flow(client, flow, provision=provision)
    if not provision:
        return {'status': 'passed', 'flowMatches': True, 'nodeCount': 6, 'edgeCount': 7,
                'toolCount': 3, 'modelCallLimit': MODEL_CALLS, 'flowSha256': flow_sha256(flow['data']),
                'modelExecuted': False, 'productionReady': False}
    current = dt.datetime.now(dt.timezone.utc) if now is None else now
    require(isinstance(current, dt.datetime) and current.tzinfo is not None, 'GATEWAY_KEY_EXPIRY_INVALID')
    # Langflow 1.13.3 ApiKeyBase serializes expires_at via _as_utc_iso(),
    # which deliberately drops microseconds (and supplies UTC for old naive
    # DB rows). Request that same precision so exact metadata checking works
    # without tolerating an unexpected lifetime extension or omitted expiry.
    expiry = (current.astimezone(dt.timezone.utc) + dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    name = KEY_PREFIX + str(uuid.uuid4())
    created = payload(client.post('/api/v1/api_key/', json={'name': name, 'expires_at': expiry, 'is_active': True}))
    key_id = None
    try:
        key_id = str(uuid.UUID(created.get('id')))
        require(isinstance(created.get('api_key'), str) and re.fullmatch(r'sk-[A-Za-z0-9_-]{32,256}', created['api_key'])
                and created.get('name') == name and created.get('is_active') is True
                and dt.datetime.fromisoformat(created.get('expires_at').replace('Z', '+00:00')) == dt.datetime.fromisoformat(expiry),
                'GATEWAY_API_KEY_INVALID')
        return {'flowId': flow_id, 'apiKey': created['api_key'], 'flowSha256': flow_sha256(flow['data'])}
    except Exception:
        if key_id is not None:
            # Best effort cleanup of only the key just created; never revoke an existing key.
            try:
                client.delete(f'/api/v1/api_key/{key_id}')
            except Exception:
                pass
        raise GatewayBootstrapError('GATEWAY_API_KEY_INVALID') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--provision', action='store_true', help='Private host capture only: stdout includes a new 30-day API key.')
    mode.add_argument('--check', action='store_true', help='Check exact flow metadata without creating a flow or API key.')
    mode.add_argument('--update', action='store_true', help='Switch the existing exact 4-call flow to the 8-call flow; no key.')
    mode.add_argument('--rollback', action='store_true', help='Switch the existing exact 8-call flow back to the 4-call flow; no key.')
    arguments = parser.parse_args(argv)
    migrate = 'update' if arguments.update else 'rollback' if arguments.rollback else None
    try:
        import httpx
        setup = load_setup()
        template = json.loads(Path('/opt/oreumit/agent-v2.flow.json').read_text(encoding='utf-8'))
        source = Path('/opt/oreumit/gateway_components.py').read_text(encoding='utf-8')
        with httpx.Client(base_url=LANGFLOW_URL, timeout=60, follow_redirects=False, trust_env=False) as client, \
             httpx.Client(base_url=MCP_URL, timeout=10, follow_redirects=False, trust_env=False) as mcp:
            result = configure(client, mcp, os.environ, template, source, setup,
                               provision=arguments.provision, migrate=migrate)
    except Exception as error:
        code = str(error) if isinstance(error, GatewayBootstrapError) else 'GATEWAY_BOOTSTRAP_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'modelExecuted': False}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
