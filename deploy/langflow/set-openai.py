#!/usr/bin/env python3
"""Store an OpenAI API key from stdin as a Langflow Credential, without inference.

Run only through deploy/set-openai-key.sh, which reads the key with a hidden
prompt and streams it here; the key is never an argument, environment value or
log line. Only the local authenticated Langflow API is contacted. An existing
credential is never replaced or read, and OpenAI is intentionally not called.
"""
import json
import os
import re
import sys


MAX_BYTES = 1024
VARIABLE = 'JEJU_OPENAI_API_KEY'
LANGFLOW_URL = 'http://127.0.0.1:7860'
KEY = re.compile(r'sk-[A-Za-z0-9_-]{20,400}')


class OpenAISetupError(Exception):
    """Only fixed diagnostic codes may be raised or displayed."""


def require(condition, code):
    if not condition:
        raise OpenAISetupError(code)


def key_from_stdin(stream):
    raw = stream.read(MAX_BYTES + 1)
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, 'INPUT_EMPTY_OR_TOO_LARGE')
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        raise OpenAISetupError('INVALID_OPENAI_KEY') from None
    # One trailing newline from a terminal is allowed; any other whitespace is not.
    key = text[:-1] if text.endswith('\n') else text
    require(KEY.fullmatch(key) is not None, 'INVALID_OPENAI_KEY')
    return key


def response_json(response, statuses=(200,)):
    require(response.status_code in statuses, 'LANGFLOW_API_REJECTED')
    return response.json()


def run(stream, environ, client_factory=None):
    key = key_from_stdin(stream)
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
        existing = [item for item in variables if item.get('name') == VARIABLE]
        if existing:
            # Never inspect or rotate an existing value here; replace it in the Langflow UI.
            require(len(existing) == 1 and existing[0].get('type') == 'Credential', 'OPENAI_CREDENTIAL_CONFLICT')
            return {'status': 'existing_preserved', 'modelExecuted': False, 'providerAccessVerified': False}
        response = client.post('/api/v1/variables/', json={
            'name': VARIABLE, 'type': 'Credential', 'default_fields': [], 'value': key,
        })
        require(response.status_code == 201, 'LANGFLOW_CREDENTIAL_SAVE_FAILED')
    return {'status': 'configured_without_inference', 'modelExecuted': False, 'providerAccessVerified': False}


def main():
    try:
        result = run(sys.stdin.buffer, os.environ)
        exit_code = 0
    except OpenAISetupError as error:
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
