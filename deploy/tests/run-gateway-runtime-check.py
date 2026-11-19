#!/usr/bin/env python3
"""CI-only bridge: send the scope credential on stdin, never on the command line."""
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
    # Docker cp rejects a read-only container even when its target is tmpfs.
    # Send reviewed code and the CI-only credential in stdin, without writing a
    # container file or placing either credential in argv/environment.
    launch = ('import io,json,sys; p=json.load(sys.stdin); '
              'sys.stdin=io.StringIO(json.dumps({"controlToken":p["controlToken"]})); '
              'exec(compile(p["source"],"<gateway-ci-runtime>","exec"),{"__name__":"__main__"})')
    phase = 'runtime'
    checked = subprocess.run(compose + ['exec', '-T', '-e', 'CI=true', 'langflow', 'python', '-c', launch],
                             input=json.dumps({'controlToken': control,
                                               'source': (root / 'tests/check-gateway-runtime.py').read_text()}), text=True,
                             capture_output=True, timeout=90, check=False)
    # The helper deliberately emits only fixed aggregate fields, never service bodies.
    summary = json.loads(checked.stdout)
    if checked.returncode:
        error_type = summary.get('errorType')
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_RUNTIME_CHECK_FAILED', 'phase': phase,
                          'errorType': error_type if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z]{1,60}', error_type) else 'Unknown',
                          'modelExecuted': False}))
        raise SystemExit(1)
    if summary.get('status') != 'passed' or summary.get('modelExecuted') is not False:
        raise ValueError('RUNTIME_CHECK_FAILED')
    print(json.dumps(summary))
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'GATEWAY_RUNTIME_CHECK_FAILED', 'phase': phase, 'modelExecuted': False}))
    raise SystemExit(1)
