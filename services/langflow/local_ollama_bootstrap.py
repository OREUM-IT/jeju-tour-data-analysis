#!/usr/bin/env python3
"""Prepare a local Langflow for the Ollama evaluation flow without running any model.

Single-machine counterpart of deploy/langflow/bootstrap.py for the evaluation PC:
Langflow 1.13.3 on 127.0.0.1:7860, the v2 MCP catalog on 127.0.0.1:8789 and the
Ollama daemon on 127.0.0.1:11434. Ollama is asked for model metadata only
(/api/version, /api/show, /api/tags); nothing is pulled, loaded, generated or run.
Langflow lists the MCP tools through the stored credential before the flow is imported.
Prints one JSON line of non-secret values; a failure prints only a fixed code.
"""
import argparse
import ast
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import uuid


ROOT = Path(__file__).resolve().parent
LANGFLOW_URL = 'http://127.0.0.1:7860'
MCP_URL = 'http://127.0.0.1:8789'
OLLAMA_URL = 'http://127.0.0.1:11434'
SERVER_NAME = 'jeju-mcp-v2'
VERSION = '1.13.3'
TOOLS = ['constraints.check', 'places.get', 'places.search']
MCP_TOKEN_NAME = 'JEJU_MCP_TOKEN'
API_KEY_NAME = 'jeju-ollama-eval'
DEFAULT_FLOW = ROOT / 'jeju-tour-agent-ollama-v2.flow.json'
DEFAULT_MODEL = 'qwen3:4b'
VALIDATION_ASSIGNMENT = 'VALIDATION_BASE_URL = "http://127.0.0.1:8789"'
NODE_TYPES = {'ChatInput', 'ChatOutput', 'Agent', 'MCPTools', 'JejuPrepareRequest', 'JejuFinalValidation'}
EDGES = {
    ('ChatInput', 'JejuPrepareRequest', 'original_request'),
    ('JejuPrepareRequest', 'Agent', 'input_value'),
    ('JejuPrepareRequest', 'JejuFinalValidation', 'prepared_request'),
    ('MCPTools', 'Agent', 'tools'), ('Agent', 'JejuFinalValidation', 'agent_response'),
    ('JejuFinalValidation', 'ChatOutput', 'input_value'),
}
FLOW_QUERY = {'get_all': 'true', 'header_flows': 'true', 'remove_example_flows': 'true'}
OLLAMA_LABEL = re.compile(r'[0-9A-Za-z._+-]{1,64}')
# Header sources for the Langflow-side tool listing, in order, with the code each one fails with.
MCP_PROBES = (('LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED', {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'}),
              ('LANGFLOW_MCP_REGISTRATION_PROBE_FAILED', {}))
PROBE_TOOL_TIMEOUT = 10.0


class BootstrapError(Exception):
    """Messages are fixed diagnostic codes, never HTTP response contents."""


def require(condition, code):
    if not condition:
        raise BootstrapError(code)


def payload(response, expected=(200,), code='HTTP_STATUS_REJECTED', kind=dict):
    require(response.status_code in expected, code)
    try:
        value = response.json()
    except ValueError:
        raise BootstrapError(code) from None
    require(isinstance(value, kind), code)
    return value


def canonical_id(value, code):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F-]{32,36}', value), code)
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise BootstrapError(code) from None


def without_secrets(value, secrets):
    # A value shorter than 16 characters (e.g. Langflow's default password "langflow") also
    # occurs in ordinary component JSON, so it cannot identify a pasted credential. The MCP
    # token is always checked: it must be at least 32 characters.
    text = json.dumps(value, ensure_ascii=False)
    require(not any(len(secret) >= 16 and secret in text for secret in secrets), 'INLINE_SECRET_REJECTED')


class Service:
    """One loopback service; a transport failure becomes its fixed code, never exception text."""

    def __init__(self, client, unavailable):
        self.client, self.unavailable = client, unavailable
        self.headers = client.headers

    def request(self, method, route, **kwargs):
        try:
            return self.client.request(method, route, **kwargs)
        except Exception:
            raise BootstrapError(self.unavailable) from None

    def get(self, route, **kwargs):
        return self.request('GET', route, **kwargs)

    def post(self, route, **kwargs):
        return self.request('POST', route, **kwargs)

    def delete(self, route, **kwargs):
        return self.request('DELETE', route, **kwargs)


def ollama_destinations(code):
    """Every base_url literal in the Agent code, as dict keys or keyword arguments."""
    found = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Dict):
            found += [value for key, value in zip(node.keys, node.values)
                      if isinstance(key, ast.Constant) and key.value == 'base_url']
        elif isinstance(node, ast.keyword) and node.arg == 'base_url':
            found.append(node.value)
    return [value.value if isinstance(value, ast.Constant) else None for value in found]


def checked_flow(flow, model):
    """Reviewed local shape: six components, six edges, local validators, MCP and Ollama only."""
    try:
        require(isinstance(flow, dict) and isinstance(flow.get('name'), str) and flow['name'].strip(),
                'FLOW_FILE_INVALID')
        nodes, edges = flow['data']['nodes'], flow['data']['edges']
        by_type = {node['data']['type']: node for node in nodes}
        require(len(nodes) == len(by_type) == 6 and set(by_type) == NODE_TYPES, 'FLOW_COMPONENTS_MISMATCH')
        kind_by_id = {node['id']: node['data']['type'] for node in nodes}
        require(len(kind_by_id) == 6, 'FLOW_IDS_MISMATCH')
        connections = {(kind_by_id.get(edge['source']), kind_by_id.get(edge['target']),
                        edge['data']['targetHandle']['fieldName']) for edge in edges}
        require(len(edges) == len(connections) == 6 and connections == EDGES, 'FLOW_VALIDATION_BYPASS_REJECTED')
        fields = {kind: node['data']['node']['template'] for kind, node in by_type.items()}
        for kind in ('JejuPrepareRequest', 'JejuFinalValidation'):
            code = fields[kind]['code']['value']
            assignments = [node for node in ast.parse(code).body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == 'VALIDATION_BASE_URL'
                                   for target in node.targets)]
            require(len(assignments) == 1 and code.count(VALIDATION_ASSIGNMENT) == 1,
                    'VALIDATION_DESTINATION_MISMATCH')
            require(fields[kind]['service_token'].get('value') == MCP_TOKEN_NAME
                    and fields[kind]['service_token'].get('load_from_db') is True, 'MCP_CREDENTIAL_REFERENCE_MISMATCH')
        mcp = fields['MCPTools']
        require(mcp['mcp_server'].get('value') == {'name': SERVER_NAME, 'config': {}}
                and mcp['headers'].get('value') == {}, 'MCP_SERVER_REFERENCE_MISMATCH')
        require(sorted(row['name'] for row in mcp['tools_metadata']['value']) == TOOLS, 'MCP_TOOL_SET_MISMATCH')
        agent = fields['Agent']
        selected = agent['model'].get('value')
        require(isinstance(selected, list) and len(selected) == 1 and isinstance(selected[0], dict)
                and selected[0].get('provider') == 'Ollama' and selected[0].get('name') == model,
                'OLLAMA_MODEL_SELECTION_MISMATCH')
        destinations = ollama_destinations(agent['code']['value'])
        require(destinations and all(value == OLLAMA_URL for value in destinations), 'OLLAMA_DESTINATION_MISMATCH')
        require(agent['max_iterations']['value'] == 4 and agent['n_messages']['value'] == 0
                and all(agent[name]['value'] is False for name in
                        ('add_calculator_tool', 'add_current_date_tool', 'handle_parsing_errors')),
                'AGENT_LIMITS_MISMATCH')
    except (KeyError, TypeError, AttributeError, ValueError, SyntaxError):
        raise BootstrapError('FLOW_FILE_INVALID') from None
    # Import without a fixed ID or endpoint alias, as the AWS bootstrap does.
    imported = {key: value for key, value in flow.items() if key != 'id'}
    imported['endpoint_name'] = None
    return imported


def check_key_path(path):
    """Refuse before any key exists: never overwrite a file or follow a dangling link."""
    require(not os.path.lexists(path), 'API_KEY_FILE_EXISTS')
    require(Path(path).parent.is_dir(), 'API_KEY_DIRECTORY_MISSING')


def login(langflow, username, password):
    require(langflow.get('/api/v1/users/whoami').status_code in (401, 403), 'ANONYMOUS_ACCESS_ALLOWED')
    require(langflow.get('/api/v1/auto_login').status_code == 403, 'AUTO_LOGIN_ENABLED')
    logged_in = payload(langflow.post('/api/v1/login', data={'username': username, 'password': password}),
                        code='LOGIN_FAILED')
    access_token = logged_in.get('access_token')
    require(isinstance(access_token, str) and len(access_token) >= 32, 'LOGIN_RESPONSE_INVALID')
    langflow.headers['Authorization'] = 'Bearer ' + access_token
    require(payload(langflow.get('/api/v1/users/whoami'), code='LOGIN_CHECK_FAILED').get('is_active') is True,
            'LOGIN_CHECK_FAILED')
    require(payload(langflow.get('/health_check'), code='LANGFLOW_HEALTH_FAILED').get('status') == 'ok',
            'LANGFLOW_HEALTH_FAILED')
    require(payload(langflow.get('/api/v1/version'), code='LANGFLOW_VERSION_MISMATCH').get('version') == VERSION,
            'LANGFLOW_VERSION_MISMATCH')


def check_mcp(mcp, token):
    response = mcp.get('/readyz', headers={'Authorization': 'Bearer ' + token})
    require(response.status_code not in (401, 403), 'MCP_TOKEN_REJECTED')
    ready = payload(response, code='MCP_CATALOG_NOT_READY')
    data_version = ready.get('dataVersion')
    require(ready.get('ready') is True and ready.get('catalogSchemaVersion') == '2.0'
            and ready.get('dataMode') == 'app-export' and ready.get('placeCount') == 981
            and ready.get('readOnly') is True and ready.get('productionReady') is False
            and isinstance(data_version, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', data_version),
            'MCP_CATALOG_NOT_READY')
    return ready


def label(value):
    return value if isinstance(value, str) and OLLAMA_LABEL.fullmatch(value) else None


def check_ollama(ollama, model):
    """Metadata only: the model must be present locally and declare completion and tool calling."""
    version = label(payload(ollama.get('/api/version'), code='OLLAMA_UNAVAILABLE').get('version'))
    require(version is not None, 'OLLAMA_UNAVAILABLE')
    response = ollama.post('/api/show', json={'model': model})
    require(response.status_code != 404, 'OLLAMA_MODEL_MISSING')
    shown = payload(response, code='OLLAMA_UNAVAILABLE')
    # A cloud-hosted tag would send prompts off this machine; the evaluation is local only.
    require(not shown.get('remote_host') and not shown.get('remote_model'), 'OLLAMA_REMOTE_MODEL_REJECTED')
    capabilities = shown.get('capabilities')
    require(isinstance(capabilities, list) and all(isinstance(item, str) for item in capabilities)
            and {'completion', 'tools'} <= set(capabilities), 'OLLAMA_TOOLS_UNSUPPORTED')
    listed = payload(ollama.get('/api/tags'), code='OLLAMA_UNAVAILABLE').get('models')
    entries = [row for row in listed if isinstance(row, dict) and model in (row.get('name'), row.get('model'))] \
        if isinstance(listed, list) else []
    digest = entries[0].get('digest') if len(entries) == 1 else None
    details = shown.get('details') if isinstance(shown.get('details'), dict) else {}
    return {'ollamaVersion': version, 'model': model,
            'modelDigest': digest if isinstance(digest, str)
            and re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', digest) else None,
            'modelQuantization': label(details.get('quantization_level')),
            'modelParameterSize': label(details.get('parameter_size'))}


def ensure_variables(langflow, token):
    variables = payload(langflow.get('/api/v1/variables/'), code='VARIABLE_LIST_INVALID', kind=list)
    names = [item for item in variables if isinstance(item, dict) and item.get('name') == MCP_TOKEN_NAME]
    require(len(names) <= 1, 'MCP_CREDENTIAL_CONFLICT')
    status = 'preserved-existing'
    if not names:
        names = [payload(langflow.post('/api/v1/variables/', json={
            'name': MCP_TOKEN_NAME, 'value': token, 'type': 'Credential', 'default_fields': [],
        }), (201,))]
        status = 'created'
    require(names[0].get('type') == 'Credential' and names[0].get('has_value') is True,
            'MCP_CREDENTIAL_UNAVAILABLE')
    return status


def ensure_server(langflow):
    route = f'/api/v2/mcp/servers/{SERVER_NAME}'
    response = langflow.get(route)
    status = 'preserved-existing'
    if response.status_code == 404:
        created = langflow.post(route, json={
            'url': MCP_URL + '/mcp', 'mode': 'Streamable_HTTP',
            'headers': {'Authorization': 'Bearer {{JEJU_MCP_TOKEN}}'},
        })
        require(created.status_code in (200, 201, 409), 'MCP_REGISTRATION_FAILED')
        response = langflow.get(route)
        status = 'registered'
    config = payload(response)
    # Langflow masks every header value, so only the header names can be compared.
    require(config.get('url') == MCP_URL + '/mcp' and config.get('mode') == 'Streamable_HTTP'
            and isinstance(config.get('headers'), dict) and set(config['headers']) == {'Authorization'}
            and not any(config.get(name) for name in ('command', 'args', 'env')),
            'MCP_REGISTRATION_CONFLICT')
    return status


def probe_mcp_tools(langflow):
    """Read-only: Langflow's own MCPTools component must list exactly the three MCP tools.

    Langflow masks Credential values and registered header values, so ensure_variables and
    ensure_server only see that both exist. A stale JEJU_MCP_TOKEN, or a registration whose
    header carries another token, would pass them and then fail every run at JejuPrepareRequest.
    As in deploy/langflow/bootstrap.py the tools are listed twice: once forcing the stored
    Credential through a component header (component headers win over the registration's, then
    Langflow resolves {{JEJU_MCP_TOKEN}} from its database), once with the registered header
    alone, which is what the flow's MCPTools node uses at run time. Langflow refuses the
    loopback MCP URL without LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1, so a missing allowlist
    also fails here. A refused or unreachable server leaves the list empty rather than
    failing the request. Nothing is saved, and the returned nodes, which can echo the
    registration, are never printed.
    """
    # Langflow has just answered; a transport failure now is the probe's, e.g. a stalled MCP connection.
    probe = Service(langflow.client, 'LANGFLOW_MCP_PROBE_FAILED')
    catalog = payload(probe.get('/api/v1/all'), code='LANGFLOW_MCP_PROBE_FAILED')
    reference = {'name': SERVER_NAME, 'config': {}}
    for code, headers in MCP_PROBES:
        try:
            node = copy.deepcopy(catalog['models_and_agents']['MCPTools'])
            for field, value, tool_mode in (('mcp_server', reference, False), ('tool_mode', True, True)):
                template = node['template']
                template['headers']['value'] = headers
                # A cached tool list could hide a credential that no longer works.
                template['use_cache']['value'] = False
                template['tool_execution_timeout']['value'] = PROBE_TOOL_TIMEOUT
                if tool_mode:
                    template['mcp_server']['value'] = reference  # never the echoed registration
                node = payload(probe.post('/api/v1/custom_component/update', json={
                    'code': template['code']['value'], 'frontend_node': node, 'field': field,
                    'field_value': value, 'template': template, 'tool_mode': tool_mode,
                }), code='LANGFLOW_MCP_PROBE_FAILED')
            names = sorted(row['name'] for row in node['template']['tools_metadata']['value'])
        except (KeyError, TypeError, AttributeError):
            raise BootstrapError('LANGFLOW_MCP_PROBE_FAILED') from None
        require(names, code)
        require(names == TOOLS, 'MCP_TOOL_SET_MISMATCH')
    return 'passed'


def ensure_flow(langflow, flow):
    name = flow['name']
    listed = payload(langflow.get('/api/v1/flows/', params=FLOW_QUERY), code='FLOW_LIST_INVALID', kind=list)
    matches = [item for item in listed if isinstance(item, dict) and item.get('name') == name]
    require(len(matches) <= 1, 'FLOW_NAME_CONFLICT')
    if matches:
        flow_id, status = matches[0].get('id'), 'preserved-existing'
    else:
        created = payload(langflow.post('/api/v1/flows/', json=flow), (201,))
        require(created.get('name') == name, 'FLOW_NAME_CONFLICT')
        flow_id, status = created.get('id'), 'imported'
    flow_id = canonical_id(flow_id, 'FLOW_ID_INVALID')
    stored = payload(langflow.get(f'/api/v1/flows/{flow_id}'))
    # A same-named flow is never overwritten; an edited copy must be removed by the operator.
    require(stored.get('name') == name and stored.get('data') == flow['data'],
            'EXISTING_FLOW_DIFFERS' if status == 'preserved-existing' else 'IMPORTED_FLOW_DIFFERS')
    return flow_id, status


def revoke_and_fail(langflow, key_id, code):
    """Delete a key that was never recorded; report a failed cleanup instead of the cause."""
    try:
        revoked = langflow.delete(f'/api/v1/api_key/{key_id}').status_code == 200
    except BootstrapError:
        revoked = False
    raise BootstrapError(code if revoked else 'API_KEY_CLEANUP_FAILED')


def write_api_key(langflow, path):
    created = payload(langflow.post('/api/v1/api_key/', json={'name': API_KEY_NAME}), code='API_KEY_CREATE_FAILED')
    key, key_id = created.get('api_key'), canonical_id(created.get('id'), 'API_KEY_RESPONSE_INVALID')
    if not (isinstance(key, str) and re.fullmatch(r'sk-[A-Za-z0-9_-]{32,256}', key)):
        revoke_and_fail(langflow, key_id, 'API_KEY_RESPONSE_INVALID')
    opened = False
    try:
        # O_EXCL also refuses a file created after the pre-check. The mode applies on POSIX;
        # Windows keeps the ACL inherited from the target directory.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        opened = True
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(f'LANGFLOW_API_KEY={key}\n'.encode('ascii'))
    except Exception as error:
        if opened:
            with contextlib.suppress(OSError):
                os.unlink(path)
        revoke_and_fail(langflow, key_id, 'API_KEY_FILE_EXISTS' if isinstance(error, FileExistsError)
                        else 'API_KEY_FILE_WRITE_FAILED')


def bootstrap(client, mcp_client, ollama_client, environment, flow, *, model=DEFAULT_MODEL, api_key_path=None):
    username = environment.get('LANGFLOW_SUPERUSER') or ''
    password = environment.get('LANGFLOW_SUPERUSER_PASSWORD') or ''
    token = environment.get('MCP_SERVICE_TOKEN') or ''
    require(username and password and re.fullmatch(r'[\x21-\x7e]{32,256}', token), 'BOOTSTRAP_CREDENTIALS_INVALID')
    flow = checked_flow(flow, model)
    without_secrets(flow, [token, password])
    if api_key_path is not None:
        check_key_path(api_key_path)
    langflow = Service(client, 'LANGFLOW_UNAVAILABLE')
    login(langflow, username, password)
    ready = check_mcp(Service(mcp_client, 'MCP_UNAVAILABLE'), token)
    model_info = check_ollama(Service(ollama_client, 'OLLAMA_UNAVAILABLE'), model)
    # Every check above is read-only; Langflow is changed only after all of them pass.
    credential = ensure_variables(langflow, token)
    server = ensure_server(langflow)
    # The probe needs both; no flow is imported and no key is created unless it passes.
    mcp_probe = probe_mcp_tools(langflow)
    flow_id, flow_status = ensure_flow(langflow, flow)
    if api_key_path is not None:
        write_api_key(langflow, api_key_path)
    return {'status': 'passed', 'langflowVersion': VERSION, 'authenticated': True,
            'flowStatus': flow_status, 'flowId': flow_id,
            'endpoint': f'{LANGFLOW_URL}/api/v1/run/{flow_id}?stream=false',
            'mcpCredential': credential, 'mcpServer': server, 'mcpProbe': mcp_probe, 'dataMode': ready['dataMode'],
            'placeCount': ready['placeCount'], 'dataVersion': ready['dataVersion'], **model_info,
            'apiKeyWritten': api_key_path is not None, 'modelExecuted': False, 'productionReady': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flow', type=Path, default=DEFAULT_FLOW,
                        help='flow file to import by its name (default: the committed Ollama evaluation flow)')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help='exact local Ollama tag that the flow selects (default: %(default)s)')
    parser.add_argument('--write-api-key', type=Path, metavar='PATH',
                        help=f'also create the Langflow API key "{API_KEY_NAME}" and write LANGFLOW_API_KEY=<key> '
                             'to this new file; the key is never printed and no file is overwritten')
    arguments = parser.parse_args(argv)
    try:
        try:
            import httpx
        except ImportError:
            raise BootstrapError('HTTPX_UNAVAILABLE') from None
        try:
            flow = json.loads(arguments.flow.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise BootstrapError('FLOW_FILE_UNREADABLE') from None
        options = {'follow_redirects': False, 'trust_env': False}
        with httpx.Client(base_url=LANGFLOW_URL, timeout=60, **options) as client, \
             httpx.Client(base_url=MCP_URL, timeout=10, **options) as mcp_client, \
             httpx.Client(base_url=OLLAMA_URL, timeout=30, **options) as ollama_client:
            result = bootstrap(client, mcp_client, ollama_client, os.environ, flow,
                               model=arguments.model, api_key_path=arguments.write_api_key)
    except Exception as error:
        code = str(error) if isinstance(error, BootstrapError) else 'LOCAL_BOOTSTRAP_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'modelExecuted': False}))
        return 1
    # ASCII-only JSON, so a redirected Windows console code page cannot fail after setup.
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
