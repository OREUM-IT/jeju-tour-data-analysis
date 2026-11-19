#!/usr/bin/env python3
"""Seed only disposable CI variables; the Vertex value cannot authorize a model."""
import json
import os
import sys

try:
    assert os.environ.get('CI') == 'true', 'DISPOSABLE_CI_ONLY'
    sys.path.insert(0, '/opt/oreumit')
    import bootstrap as setup
    import httpx
    with httpx.Client(base_url=setup.LANGFLOW_URL, timeout=20, trust_env=False, follow_redirects=False) as client:
        login = client.post('/api/v1/login', data={'username': os.environ['LANGFLOW_SUPERUSER'],
                                                 'password': os.environ['LANGFLOW_SUPERUSER_PASSWORD']})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        listed = client.get('/api/v1/variables/')
        assert listed.status_code == 200
        assert not any(row.get('name') == setup.VERTEX_TOKEN_NAME for row in listed.json()), 'CI_REQUIRES_UNCONFIGURED_VERTEX'
        setup.ensure_variables(client, os.environ['MCP_SERVICE_TOKEN'])
        created = client.post('/api/v1/variables/', json={'name': setup.VERTEX_TOKEN_NAME,
                              'value': 'CI_ONLY_NO_MODEL_EXECUTION', 'type': 'Credential', 'default_fields': []})
        assert created.status_code == 201
    print(json.dumps({'status': 'passed', 'ciPlaceholderOnly': True, 'modelExecuted': False}))
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'CI_SEED_FAILED'}))
    raise SystemExit(1)
