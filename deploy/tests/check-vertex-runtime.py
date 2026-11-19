#!/usr/bin/env python3
"""Exercise the real Vertex constructor and a disposable flow migration, without inference."""
import argparse
import ast
import json
import os
from pathlib import Path
import sys
import types
from unittest.mock import patch


def constructor_check(setup, template):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    agent = next(n for n in template['data']['nodes'] if n['data']['type'] == 'Agent')
    code = setup.patch_vertex_client(agent['data']['node']['template']['code']['value'])
    methods = [node for node in ast.walk(ast.parse(code))
               if isinstance(node, ast.FunctionDef) and node.name == '_get_llm']
    assert len(methods) == 1
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 '<reviewed-vertex-method>', 'exec'), namespace)
    # Fictional identity and a freshly generated throwaway RSA key. No cloud key is used.
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    config = {'serviceAccount': {'type': 'service_account', 'project_id': 'ci-fictional-vertex',
              'client_email': 'ci-only@ci-fictional-vertex.iam.gserviceaccount.com',
              'private_key': pem, 'token_uri': 'https://oauth2.googleapis.com/token'}, 'location': 'global'}
    fake = types.SimpleNamespace(api_key=json.dumps(config),
        model=[{'provider': 'Google Generative AI', 'name': 'gemini-3.8-flash'}],
        _get_max_tokens_value=lambda: None)
    clean = {k: v for k, v in os.environ.items() if k not in
             ('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS')}
    def no_network(*_args, **_kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_IN_CONSTRUCTOR_CHECK')
    with patch.dict(os.environ, clean, clear=True), \
         patch('socket.socket.connect', side_effect=no_network) as connect, \
         patch('socket.create_connection', side_effect=no_network), \
         patch('socket.getaddrinfo', side_effect=no_network):
        model = namespace['_get_llm'](fake)
        try:
            assert model.model == 'gemini-3.8-flash' and model.client.vertexai is True
            assert model.credentials is not None and model.google_api_key is None
            assert model.max_retries == 1 and model.timeout == 60 and model.streaming is True
            assert not model.credentials.valid and connect.call_count == 0
        finally:
            model.client.close()
    return {'status': 'passed', 'vertexClientInitialized': True,
            'developerApiKeyRequired': False, 'modelExecuted': False, 'networkCalls': 0}


def migration_check(setup, template):
    import httpx
    assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'

    def client():
        return httpx.Client(base_url=setup.LANGFLOW_URL, timeout=60, follow_redirects=False, trust_env=False)

    def login(c):
        response = c.post('/api/v1/login', data={'username': os.environ['LANGFLOW_SUPERUSER'],
                                               'password': os.environ['LANGFLOW_SUPERUSER_PASSWORD']})
        assert response.status_code == 200
        c.headers['Authorization'] = 'Bearer ' + response.json()['access_token']

    def flows(c):
        response = c.get('/api/v1/flows/', params={'get_all': 'true', 'header_flows': 'true',
                                                 'remove_example_flows': 'true'})
        assert response.status_code == 200
        return [row for row in response.json() if row.get('name') == setup.FLOW_NAME]

    def stored(c, flow_id):
        response = c.get('/api/v1/flows/' + flow_id)
        assert response.status_code == 200
        return response.json()

    # Refuse to seed/downgrade an existing deployment, even if CI=true is set by mistake.
    with client() as c:
        login(c)
        assert flows(c) == [], 'CI_REQUIRES_EMPTY_FLOW_STATE'
    original = setup.deployment_flow
    def legacy(*args, **kwargs):
        kwargs['direct_vertex'] = False
        return original(*args, **kwargs)

    with httpx.Client(base_url=setup.MCP_URL, timeout=10, follow_redirects=False, trust_env=False) as mcp:
        with patch.object(setup, 'deployment_flow', side_effect=legacy), client() as c:
            assert setup.bootstrap(c, mcp, os.environ, template)['flowStatus'] == 'imported'
            flow_id = flows(c)[0]['id']
            before = stored(c, flow_id)
            variables_before = c.get('/api/v1/variables/').json()
        with client() as c:
            try:
                setup.bootstrap(c, mcp, os.environ, template)
                raise AssertionError('LEGACY_FLOW_MUST_REQUIRE_UPGRADE_FLAG')
            except setup.BootstrapError as error:
                assert str(error) == 'EXISTING_FLOW_DIFFERS'
            assert stored(c, flow_id)['data'] == before['data']
        with client() as c:
            assert setup.bootstrap(c, mcp, os.environ, template,
                                   upgrade_vertex_client=True)['flowStatus'] == 'upgraded-vertex-client'
            after = stored(c, flow_id)
            for key in ('id', 'name', 'description', 'folder_id', 'endpoint_name'):
                assert before.get(key) == after.get(key), key
            assert c.get('/api/v1/variables/').json() == variables_before
            assert before['data'] != after['data']
        with client() as c:
            assert setup.bootstrap(c, mcp, os.environ, template,
                                   upgrade_vertex_client=True)['flowStatus'] == 'preserved-existing'
    return {'status': 'passed', 'knownLegacyFlowUpgraded': True,
            'flowIdentityAndCredentialMetadataPreserved': True, 'modelExecuted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migration', action='store_true')
    parser.add_argument('--template', default='/opt/oreumit/agent-v2.flow.json')
    args = parser.parse_args()
    try:
        sys.path.insert(0, '/opt/oreumit')
        import bootstrap as setup
        template = json.loads(Path(args.template).read_text())
        print(json.dumps(constructor_check(setup, template)))
        if args.migration:
            print(json.dumps(migration_check(setup, template)))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': 'VERTEX_RUNTIME_CHECK_FAILED',
                          'errorType': type(error).__name__, 'modelExecuted': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
