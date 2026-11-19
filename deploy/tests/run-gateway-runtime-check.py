#!/usr/bin/env python3
"""CI-only bridge: send the scope credential on stdin, never on the command line."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
try:
    if os.environ.get('CI') != 'true':
        raise ValueError('CI_ONLY')
    spec = importlib.util.spec_from_file_location('private_settings', root / 'prepare-langflow.py')
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    control = settings.read_env(root / '.local/gateway-control.env', private_mode=True)['GATEWAY_CONTROL_TOKEN']
    compose = ['docker', 'compose']
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml', 'compose.gateway.yaml'):
        compose += ['-f', str(root / name)]
    subprocess.run(compose + ['cp', str(root / 'tests/check-gateway-runtime.py'),
                             'langflow:/tmp/check-gateway-runtime.py'], check=True, capture_output=True)
    checked = subprocess.run(compose + ['exec', '-T', '-e', 'CI=true', 'langflow', 'python',
                                       '/tmp/check-gateway-runtime.py'],
                             input=json.dumps({'controlToken': control}), text=True,
                             capture_output=True, timeout=90, check=False)
    # The helper deliberately emits only fixed aggregate fields, never service bodies.
    if checked.returncode:
        raise ValueError('RUNTIME_CHECK_FAILED')
    summary = json.loads(checked.stdout)
    if summary.get('status') != 'passed' or summary.get('modelExecuted') is not False:
        raise ValueError('RUNTIME_CHECK_FAILED')
    print(json.dumps(summary))
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'GATEWAY_RUNTIME_CHECK_FAILED', 'modelExecuted': False}))
    raise SystemExit(1)
