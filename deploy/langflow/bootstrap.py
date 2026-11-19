#!/usr/bin/env python3
"""Provision the private AWS flow and discover MCP tools without running an Agent."""
import ast
import copy
import json
import os
from pathlib import Path
import re


LANGFLOW_URL = 'http://127.0.0.1:7860'
MCP_URL = 'http://jeju-mcp:8788'
SERVER_NAME = 'jeju-mcp-v2'
FLOW_NAME = '제주 관광 Agent v2 — AWS 내부 연결'
VERSION = '1.13.3'
TOOLS = ['constraints.check', 'places.get', 'places.search']
MCP_TOKEN_NAME = 'JEJU_MCP_TOKEN'
VERTEX_TOKEN_NAME = 'JEJU_VERTEX_SERVICE_ACCOUNT_JSON'
LOCAL_ASSIGNMENT = 'VALIDATION_BASE_URL = "http://127.0.0.1:8789"'
AWS_ASSIGNMENT = 'VALIDATION_BASE_URL = "http://jeju-mcp:8788"'


class BootstrapError(Exception):
    """Messages are fixed diagnostic codes, never HTTP response contents."""


def require(condition, code):
    if not condition:
        raise BootstrapError(code)


def payload(response, expected=(200,)):
    require(response.status_code in expected, 'HTTP_STATUS_REJECTED')
    return response.json()


def without_secrets(value, secrets):
    text = json.dumps(value, ensure_ascii=False)
    require(not any(secret and secret in text for secret in secrets), 'INLINE_SECRET_REJECTED')


def update_node(client, node, field, value, tool_mode):
    return payload(client.post('/api/v1/custom_component/update', json={
        'code': node['template']['code']['value'], 'frontend_node': node,
        'field': field, 'field_value': value, 'template': node['template'], 'tool_mode': tool_mode,
    }))


def discover_tools(client):
    catalog = payload(client.get('/api/v1/all'))
    node = copy.deepcopy(catalog['models_and_agents']['MCPTools'])
    reference = {'name': SERVER_NAME, 'config': {}}
    node = update_node(client, node, 'mcp_server', reference, False)
    node['template']['mcp_server']['value'] = reference
    node = update_node(client, node, 'tool_mode', True, True)
    node['template']['mcp_server']['value'] = reference
    node['template']['headers']['value'] = {}
    node['template']['tool_execution_timeout']['value'] = 10.0
    node['tool_mode'] = True
    names = sorted(row['name'] for row in node['template']['tools_metadata']['value'])
    require(names == TOOLS, 'MCP_TOOL_SET_MISMATCH')
    return node


def deployment_flow(template, mcp_node):
    flow = copy.deepcopy(template)
    nodes = flow['data']['nodes']
    by_type = {node['data']['type']: node for node in nodes}
    expected_types = {'ChatInput', 'ChatOutput', 'Agent', 'MCPTools', 'JejuPrepareRequest', 'JejuFinalValidation'}
    require(len(nodes) == len(by_type) == 6 and set(by_type) == expected_types, 'FLOW_COMPONENTS_MISMATCH')
    for kind in ('JejuPrepareRequest', 'JejuFinalValidation'):
        fields = by_type[kind]['data']['node']['template']
        code = fields['code']['value']
        assignments = [node for node in ast.parse(code).body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'VALIDATION_BASE_URL'
                               for target in node.targets)]
        require(len(assignments) == 1 and code.count(LOCAL_ASSIGNMENT) == 1,
                'VALIDATION_DESTINATION_MISMATCH')
        fields['code']['value'] = code.replace(LOCAL_ASSIGNMENT, AWS_ASSIGNMENT)
        ast.parse(fields['code']['value'])
        require(fields['service_token'].get('value') == MCP_TOKEN_NAME
                and fields['service_token'].get('load_from_db') is True, 'MCP_CREDENTIAL_REFERENCE_MISMATCH')
    by_type['MCPTools']['data']['node'] = copy.deepcopy(mcp_node)
    fields = by_type['Agent']['data']['node']['template']
    require(fields['api_key'].get('value') == VERTEX_TOKEN_NAME
            and fields['api_key'].get('load_from_db') is True, 'VERTEX_CREDENTIAL_REFERENCE_MISMATCH')
    require(fields['max_iterations']['value'] == 4 and fields['n_messages']['value'] == 0
            and all(fields[name]['value'] is False for name in
                    ('add_calculator_tool', 'add_current_date_tool', 'handle_parsing_errors')),
            'AGENT_LIMITS_MISMATCH')
    kind_by_id = {node['id']: node['data']['type'] for node in nodes}
    require(len(kind_by_id) == 6, 'FLOW_IDS_MISMATCH')
    edges = flow['data']['edges']
    connections = {(kind_by_id[edge['source']], kind_by_id[edge['target']],
                    edge['data']['targetHandle']['fieldName']) for edge in edges}
    require(len(edges) == len(connections) == 6 and connections == {
        ('ChatInput', 'JejuPrepareRequest', 'original_request'),
        ('JejuPrepareRequest', 'Agent', 'input_value'),
        ('JejuPrepareRequest', 'JejuFinalValidation', 'prepared_request'),
        ('MCPTools', 'Agent', 'tools'), ('Agent', 'JejuFinalValidation', 'agent_response'),
        ('JejuFinalValidation', 'ChatOutput', 'input_value'),
    }, 'FLOW_VALIDATION_BYPASS_REJECTED')
    flow.pop('id', None)
    flow.update(name=FLOW_NAME, endpoint_name=None, last_tested_version=VERSION,
                description='AWS 내부 MCP 981곳 연결. 모델 인증·추론·공개 Gateway 연결 전 단계.')
    return flow


def ensure_variables(client, token):
    variables = payload(client.get('/api/v1/variables/'))
    require(isinstance(variables, list), 'VARIABLE_LIST_INVALID')
    names = [item for item in variables if item.get('name') == MCP_TOKEN_NAME]
    require(len(names) <= 1, 'MCP_CREDENTIAL_CONFLICT')
    if not names:
        created = payload(client.post('/api/v1/variables/', json={
            'name': MCP_TOKEN_NAME, 'value': token, 'type': 'Credential', 'default_fields': [],
        }), (201,))
        names = [created]
    require(names[0].get('type') == 'Credential' and names[0].get('has_value') is True,
            'MCP_CREDENTIAL_UNAVAILABLE')
    vertex = [item for item in variables if item.get('name') == VERTEX_TOKEN_NAME]
    require(len(vertex) <= 1 and all(item.get('type') == 'Credential' for item in vertex),
            'VERTEX_CREDENTIAL_CONFLICT')
    return bool(vertex and vertex[0].get('has_value') is True)


def ensure_server(client):
    route = f'/api/v2/mcp/servers/{SERVER_NAME}'
    response = client.get(route)
    if response.status_code == 404:
        created = client.post(route, json={
            'url': MCP_URL + '/mcp', 'mode': 'Streamable_HTTP',
            'headers': {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'},
        })
        require(created.status_code in (200, 201, 409), 'MCP_REGISTRATION_FAILED')
        response = client.get(route)
    config = payload(response)
    require(config.get('url') == MCP_URL + '/mcp' and config.get('mode') == 'Streamable_HTTP'
            and set(config.get('headers', {})) == {'Authorization'}
            and not any(config.get(name) for name in ('command', 'args', 'env')),
            'MCP_REGISTRATION_CONFLICT')


def ensure_flow(client, flow):
    listed = payload(client.get('/api/v1/flows/', params={'get_all': 'true', 'header_flows': 'true',
                                                       'remove_example_flows': 'true'}))
    require(isinstance(listed, list), 'FLOW_LIST_INVALID')
    matches = [item for item in listed if item.get('name') == FLOW_NAME]
    require(len(matches) <= 1, 'FLOW_NAME_CONFLICT')
    if matches:
        flow_id = matches[0]['id']
        status = 'preserved-existing'
    else:
        created = payload(client.post('/api/v1/flows/', json=flow), (201,))
        require(created.get('name') == FLOW_NAME, 'FLOW_NAME_CONFLICT')
        flow_id = created['id']
        status = 'imported'
    require(isinstance(flow_id, str) and re.fullmatch(r'[0-9a-fA-F-]{32,36}', flow_id), 'FLOW_ID_INVALID')
    stored = payload(client.get(f'/api/v1/flows/{flow_id}'))
    require(stored.get('name') == FLOW_NAME and stored.get('data') == flow['data'], 'EXISTING_FLOW_DIFFERS')
    return status


def bootstrap(client, mcp_client, environment, template):
    require(client.get('/api/v1/users/whoami').status_code in (401, 403), 'ANONYMOUS_ACCESS_ALLOWED')
    require(client.get('/api/v1/auto_login').status_code == 403, 'AUTO_LOGIN_ENABLED')
    username = environment['LANGFLOW_SUPERUSER']
    password = environment['LANGFLOW_SUPERUSER_PASSWORD']
    token = environment['MCP_SERVICE_TOKEN']
    require(username and password and re.fullmatch(r'[\x21-\x7e]{32,256}', token), 'BOOTSTRAP_CREDENTIALS_INVALID')
    logged_in = payload(client.post('/api/v1/login', data={'username': username, 'password': password}))
    access_token = logged_in['access_token']
    require(isinstance(access_token, str) and len(access_token) >= 32, 'LOGIN_RESPONSE_INVALID')
    client.headers['Authorization'] = 'Bearer ' + access_token
    require(payload(client.get('/api/v1/users/whoami')).get('is_active') is True, 'LOGIN_CHECK_FAILED')
    require(payload(client.get('/health_check')).get('status') == 'ok', 'LANGFLOW_HEALTH_FAILED')
    require(payload(client.get('/api/v1/version')).get('version') == VERSION, 'LANGFLOW_VERSION_MISMATCH')
    ready = payload(mcp_client.get('/readyz', headers={'Authorization': 'Bearer ' + token}))
    require(ready.get('ready') is True and ready.get('catalogSchemaVersion') == '2.0'
            and ready.get('dataMode') == 'app-export' and ready.get('placeCount') == 981
            and ready.get('readOnly') is True and ready.get('productionReady') is False
            and re.fullmatch(r'sha256:[a-f0-9]{64}', ready.get('dataVersion', '')),
            'MCP_CATALOG_NOT_READY')
    vertex_configured = ensure_variables(client, token)
    ensure_server(client)
    mcp_node = discover_tools(client)
    flow = deployment_flow(template, mcp_node)
    without_secrets(flow, [token, password, access_token])
    flow_status = ensure_flow(client, flow)
    return {'status': 'passed', 'langflowVersion': VERSION, 'authenticated': True,
            'dataMode': ready['dataMode'], 'placeCount': ready['placeCount'], 'toolCount': len(TOOLS),
            'flowStatus': flow_status, 'vertexConfigured': vertex_configured,
            'modelExecuted': False, 'productionReady': False}


def main():
    try:
        import httpx
        template = json.loads(Path('/opt/oreumit/agent-v2.flow.json').read_text(encoding='utf-8'))
        with httpx.Client(base_url=LANGFLOW_URL, timeout=60, follow_redirects=False, trust_env=False) as client, \
             httpx.Client(base_url=MCP_URL, timeout=10, follow_redirects=False, trust_env=False) as mcp_client:
            result = bootstrap(client, mcp_client, os.environ, template)
    except Exception as error:
        code = str(error) if isinstance(error, BootstrapError) else 'LANGFLOW_BOOTSTRAP_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'modelExecuted': False}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
