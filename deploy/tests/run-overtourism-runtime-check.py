#!/usr/bin/env python3
"""CI-only bridge: send the control credential and reviewed sources on stdin, never on the command line."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
phase = 'settings'
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
    # No container file is written; the credential never enters argv or the environment.
    launch = ('import io,json,sys; p=json.load(sys.stdin); '
              'sys.stdin=io.StringIO(json.dumps({"controlToken":p["controlToken"]})); '
              'exec(compile(p["source"],"<overtourism-ci-runtime>","exec"),'
              '{"__name__":"__main__","COMPONENTS_SOURCE":p["components"],"BOOTSTRAP_SOURCE":p["bootstrap"]})')
    phase = 'runtime'
    checked = subprocess.run(compose + ['exec', '-T', '-e', 'CI=true', 'langflow', 'python', '-c', launch],
                             input=json.dumps({'controlToken': control,
                                               'source': (root / 'tests/check-overtourism-runtime.py').read_text(),
                                               'components': (root.parent / 'services/langflow/overtourism_components.py').read_text(),
                                               'bootstrap': (root / 'langflow/overtourism-bootstrap.py').read_text()}),
                             text=True, capture_output=True, timeout=90, check=False)
    summary = json.loads(checked.stdout)
    if checked.returncode:
        error_type = summary.get('errorType')
        print(json.dumps({'status': 'failed', 'code': 'OVERTOURISM_RUNTIME_CHECK_FAILED', 'phase': phase,
                          'errorType': error_type if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z]{1,60}', error_type) else 'Unknown',
                          'modelExecuted': False}))
        raise SystemExit(1)
    if summary.get('status') != 'passed' or summary.get('modelExecuted') is not False:
        raise ValueError('RUNTIME_CHECK_FAILED')
    print(json.dumps(summary))
except SystemExit:
    raise
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'OVERTOURISM_RUNTIME_CHECK_FAILED', 'phase': phase, 'modelExecuted': False}))
    raise SystemExit(1)
