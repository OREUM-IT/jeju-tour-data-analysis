#!/usr/bin/env python3
"""Provision the private AWS flow and discover MCP tools without running an Agent."""
import argparse
import ast
import copy
import json
import os
from pathlib import Path
import re
import textwrap
import uuid


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
LEGACY_VERTEX_RETURN = '''return get_llm(
            model=self.model,
            user_id=self.user_id,
            api_key=None,
            stream=True,
            max_tokens=self._get_max_tokens_value(),
            watsonx_url=getattr(self, "base_url_ibm_watsonx", None),
            watsonx_project_id=getattr(self, "project_id", None),
            overrides={**(getattr(self, "_model_overrides", None) or {}),
                "vertexai": True, "credentials": vertex_credentials,
                "project": vertex_runtime_config["serviceAccount"]["project_id"], "location": vertex_runtime_config["location"],
                "google_api_key": None, "max_retries": 1, "timeout": 60},
        )'''
DIRECT_VERTEX_RETURN = '''# Langflow's generic get_llm requires a Developer API key before applying
# Vertex overrides. Construct this reviewed Vertex-only client directly.
from langchain_google_genai import ChatGoogleGenerativeAI
selected = self.model
if (not isinstance(selected, list) or len(selected) != 1
    or not isinstance(selected[0], dict)
    or selected[0].get("provider") != "Google Generative AI"
    or selected[0].get("name") != "gemini-3.8-flash"):
    raise ValueError("VERTEX_MODEL_SELECTION_INVALID")
if vertex_runtime_config.get("location") != "global":
    raise ValueError("VERTEX_LOCATION_INVALID")
maximum = self._get_max_tokens_value()
return ChatGoogleGenerativeAI(
    model="gemini-3.8-flash", streaming=True, vertexai=True,
    credentials=vertex_credentials,
    project=vertex_runtime_config["serviceAccount"]["project_id"], location="global",
    google_api_key=None, max_retries=1, timeout=60,
    **({"max_output_tokens": maximum} if maximum is not None else {}),
)'''


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
    reference = {'name': SERVER_NAME, 'config': {}}
    # Registration GET masks header values. Force this Credential reference during
    # discovery so a valid literal registration cannot hide a stale global token.
    # Langflow merges component headers first, then resolves DB variables.
    probe_headers = {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}
    # Also verify the registered header without an override. Save only that second
    # node: Langflow treats a composed Bearer placeholder as an inline flow secret.
    for headers in (probe_headers, {}):
        node = copy.deepcopy(catalog['models_and_agents']['MCPTools'])
        node['template']['headers']['value'] = headers
        node['template']['use_cache']['value'] = False
        node['template']['tool_execution_timeout']['value'] = 10.0
        node = update_node(client, node, 'mcp_server', reference, False)
        node['template']['mcp_server']['value'] = reference
        node['template']['headers']['value'] = headers
        node['template']['use_cache']['value'] = False
        node = update_node(client, node, 'tool_mode', True, True)
        node['template']['mcp_server']['value'] = reference
        node['template']['headers']['value'] = headers
        node['template']['tool_execution_timeout']['value'] = 10.0
        node['tool_mode'] = True
        names = sorted(row['name'] for row in node['template']['tools_metadata']['value'])
        require(names == TOOLS, 'MCP_TOOL_SET_MISMATCH')
    return node


def patch_vertex_client(code):
    """Replace only the reviewed legacy client call; keep guards and policy hooks."""
    methods = [node for node in ast.walk(ast.parse(code))
               if isinstance(node, ast.FunctionDef) and node.name == '_get_llm']
    require(len(methods) == 1, 'VERTEX_CLIENT_METHOD_MISMATCH')
    returns = [node for node in methods[0].body if isinstance(node, ast.Return)]
    require(len(returns) == 1 and ast.get_source_segment(code, returns[0]) == LEGACY_VERTEX_RETURN,
            'VERTEX_CLIENT_SOURCE_MISMATCH')
    old = returns[0]
    lines = code.splitlines(keepends=True)
    replacement = textwrap.indent(DIRECT_VERTEX_RETURN, ' ' * old.col_offset) + '\n'
    result = ''.join(lines[:old.lineno - 1]) + replacement + ''.join(lines[old.end_lineno:])
    ast.parse(result)
    return result


def deployment_flow(template, mcp_node, *, direct_vertex=True):
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
    if direct_vertex:
        fields['code']['value'] = patch_vertex_client(fields['code']['value'])
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


def ensure_flow(client, flow, *, previous_flow=None, upgrade_vertex_client=False):
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
    if stored.get('name') == FLOW_NAME and stored.get('data') != flow['data']:
        require(upgrade_vertex_client and previous_flow is not None
                and stored.get('data') == previous_flow['data'], 'EXISTING_FLOW_DIFFERS')
        version = stored.get('version_token')
        require(isinstance(version, str), 'FLOW_VERSION_UNAVAILABLE')
        try:
            version = str(uuid.UUID(version))
        except ValueError:
            raise BootstrapError('FLOW_VERSION_UNAVAILABLE') from None
        # Only the exact known old graph may be upgraded. The server rejects a
        # concurrent graph edit via If-Match; credentials and outer metadata stay put.
        payload(client.patch(f'/api/v1/flows/{flow_id}', headers={'If-Match': version},
                             json={'data': flow['data']}))
        stored = payload(client.get(f'/api/v1/flows/{flow_id}'))
        status = 'upgraded-vertex-client'
    require(stored.get('name') == FLOW_NAME and stored.get('data') == flow['data'], 'EXISTING_FLOW_DIFFERS')
    return status


def bootstrap(client, mcp_client, environment, template, *, upgrade_vertex_client=False):
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
    previous_flow = deployment_flow(template, mcp_node, direct_vertex=False) if upgrade_vertex_client else None
    flow_status = ensure_flow(client, flow, previous_flow=previous_flow, upgrade_vertex_client=upgrade_vertex_client)
    return {'status': 'passed', 'langflowVersion': VERSION, 'authenticated': True,
            'dataMode': ready['dataMode'], 'placeCount': ready['placeCount'], 'toolCount': len(TOOLS),
            'flowStatus': flow_status, 'vertexConfigured': vertex_configured,
            'modelExecuted': False, 'productionReady': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upgrade-vertex-client', action='store_true',
                        help='Upgrade only the exact known AWS flow to the direct Vertex client; no inference.')
    arguments = parser.parse_args(argv)
    try:
        import httpx
        template = json.loads(Path('/opt/oreumit/agent-v2.flow.json').read_text(encoding='utf-8'))
        with httpx.Client(base_url=LANGFLOW_URL, timeout=60, follow_redirects=False, trust_env=False) as client, \
             httpx.Client(base_url=MCP_URL, timeout=10, follow_redirects=False, trust_env=False) as mcp_client:
            result = bootstrap(client, mcp_client, os.environ, template,
                               upgrade_vertex_client=arguments.upgrade_vertex_client)
    except Exception as error:
        code = str(error) if isinstance(error, BootstrapError) else 'LANGFLOW_BOOTSTRAP_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'modelExecuted': False}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
