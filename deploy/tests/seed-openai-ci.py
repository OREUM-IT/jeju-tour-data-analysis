#!/usr/bin/env python3
"""Seed only a disposable CI OpenAI placeholder; the value cannot authorize a model."""
import json
import os

try:
    assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
    import httpx
    with httpx.Client(base_url='http://127.0.0.1:7860', timeout=20, trust_env=False, follow_redirects=False) as client:
        login = client.post('/api/v1/login', data={'username': os.environ['LANGFLOW_SUPERUSER'],
                                                 'password': os.environ['LANGFLOW_SUPERUSER_PASSWORD']})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        listed = client.get('/api/v1/variables/')
        assert listed.status_code == 200
        assert not any(row.get('name') == 'JEJU_OPENAI_API_KEY' for row in listed.json()), 'CI_REQUIRES_UNCONFIGURED_OPENAI'
        created = client.post('/api/v1/variables/', json={'name': 'JEJU_OPENAI_API_KEY',
                              'value': 'sk-ci-only-no-model-execution-' + 'x' * 16, 'type': 'Credential', 'default_fields': []})
        assert created.status_code == 201
    print(json.dumps({'status': 'passed', 'ciPlaceholderOnly': True, 'modelExecuted': False}))
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'CI_OPENAI_SEED_FAILED'}))
    raise SystemExit(1)
