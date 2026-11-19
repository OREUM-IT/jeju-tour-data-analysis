#!/usr/bin/env python3
"""Store an existing Vertex service account from stdin without OAuth or inference.

Only the local authenticated Langflow API is contacted. Existing credentials are
never replaced, and provider access is intentionally not tested by this command.
"""
import json
import os
import re
import sys


MAX_BYTES = 64 * 1024
VARIABLE = 'JEJU_VERTEX_SERVICE_ACCOUNT_JSON'
LANGFLOW_URL = 'http://127.0.0.1:7860'


class VertexSetupError(Exception):
    """Only fixed diagnostic codes may be raised or displayed."""


def require(condition, code):
    if not condition:
        raise VertexSetupError(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def reject_constant(_value):
    raise VertexSetupError('INVALID_JSON')


def validate_rsa(pem):
    require(isinstance(pem, str) and 0 < len(pem) <= 16 * 1024, 'INVALID_RSA_PRIVATE_KEY')
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = serialization.load_pem_private_key(pem.encode('ascii'), password=None)
        require(isinstance(key, rsa.RSAPrivateKey) and key.key_size >= 2048, 'INVALID_RSA_PRIVATE_KEY')
    except Exception:
        raise VertexSetupError('INVALID_RSA_PRIVATE_KEY') from None


def credential_from_stdin(stream):
    raw = stream.read(MAX_BYTES + 1)
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, 'INPUT_EMPTY_OR_TOO_LARGE')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except VertexSetupError:
        raise
    except (UnicodeError, ValueError):
        raise VertexSetupError('INVALID_JSON') from None
    require(isinstance(value, dict), 'INVALID_SERVICE_ACCOUNT')
    if 'serviceAccount' in value:
        require(set(value) == {'serviceAccount', 'location'} and value['location'] == 'global', 'INVALID_VERTEX_LOCATION')
        account = value['serviceAccount']
    else:
        account = value
        require('location' not in account, 'INVALID_VERTEX_LOCATION')
    require(isinstance(account, dict) and account.get('type') == 'service_account', 'INVALID_SERVICE_ACCOUNT')
    require(isinstance(account.get('project_id'), str)
            and re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', account['project_id']) is not None,
            'INVALID_PROJECT_ID')
    require(isinstance(account.get('client_email'), str) and len(account['client_email']) <= 254
            and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*@[a-z][a-z0-9-]*\.iam\.gserviceaccount\.com',
                             account['client_email']) is not None, 'INVALID_SERVICE_ACCOUNT_EMAIL')
    require(account.get('token_uri') == 'https://oauth2.googleapis.com/token'
            and account.get('universe_domain', 'googleapis.com') == 'googleapis.com', 'UNTRUSTED_GOOGLE_ENDPOINT')
    require(not {'client_secret', 'installed', 'web'} & set(account), 'INVALID_SERVICE_ACCOUNT')
    validate_rsa(account.get('private_key'))
    return {'serviceAccount': account, 'location': 'global'}


def response_json(response, statuses=(200,)):
    require(response.status_code in statuses, 'LANGFLOW_API_REJECTED')
    return response.json()


def run(stream, environ, client_factory=None):
    credential = credential_from_stdin(stream)
    require(bool(environ.get('LANGFLOW_SUPERUSER')) and bool(environ.get('LANGFLOW_SUPERUSER_PASSWORD')),
            'LANGFLOW_ADMIN_CREDENTIALS_MISSING')
    if client_factory is None:
        import httpx
        client_factory = httpx.Client
    with client_factory(base_url=LANGFLOW_URL, timeout=30.0, follow_redirects=False, trust_env=False) as client:
        login = response_json(client.post('/api/v1/login', data={
            'username': environ['LANGFLOW_SUPERUSER'], 'password': environ['LANGFLOW_SUPERUSER_PASSWORD'],
        }))
        token = login.get('access_token') if isinstance(login, dict) else None
        require(isinstance(token, str) and bool(token), 'LANGFLOW_LOGIN_FAILED')
        client.headers['Authorization'] = 'Bearer ' + token
        variables = response_json(client.get('/api/v1/variables/'))
        require(isinstance(variables, list) and all(isinstance(item, dict) for item in variables),
                'LANGFLOW_VARIABLE_LIST_INVALID')
        # Never inspect an existing value or rotate a credential during setup.
        if any(item.get('name') == VARIABLE for item in variables):
            return {'status': 'existing_preserved', 'modelExecuted': False, 'providerAccessVerified': False}
        response = client.post('/api/v1/variables/', json={
            'name': VARIABLE, 'type': 'Credential', 'default_fields': [],
            'value': json.dumps(credential, ensure_ascii=True, allow_nan=False, separators=(',', ':')),
        })
        require(response.status_code == 201, 'LANGFLOW_CREDENTIAL_SAVE_FAILED')
    return {'status': 'configured_without_inference', 'modelExecuted': False, 'providerAccessVerified': False}


def main():
    try:
        result = run(sys.stdin.buffer, os.environ)
        exit_code = 0
    except VertexSetupError as error:
        result = {'status': 'failed', 'code': str(error), 'modelExecuted': False, 'providerAccessVerified': False}
        exit_code = 1
    except Exception:
        result = {'status': 'failed', 'code': 'LANGFLOW_CONFIGURATION_FAILED',
                  'modelExecuted': False, 'providerAccessVerified': False}
        exit_code = 1
    print(json.dumps(result))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
