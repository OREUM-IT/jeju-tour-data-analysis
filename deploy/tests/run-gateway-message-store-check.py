#!/usr/bin/env python3
"""CI-only bridge: send the scope and Gateway credentials on stdin, never on the command line.

Usage: python3 deploy/tests/run-gateway-message-store-check.py --expect-stored|--expect-not-stored
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
expectation = parser.add_mutually_exclusive_group(required=True)
expectation.add_argument('--expect-stored', action='store_true')
expectation.add_argument('--expect-not-stored', action='store_true')
arguments = parser.parse_args()
phase = 'settings'
try:
    if os.environ.get('CI') != 'true':
        raise ValueError('CI_ONLY')
    spec = importlib.util.spec_from_file_location('private_settings', root / 'prepare-langflow.py')
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    control = settings.read_env(root / '.local/gateway-control.env', private_mode=True)['GATEWAY_CONTROL_TOKEN']
    api_key = settings.read_env(root / '.local/gateway.env', private_mode=True)['LANGFLOW_GATEWAY_API_KEY']
    compose = ['docker', 'compose']
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml', 'compose.gateway.yaml'):
        compose += ['-f', str(root / name)]
    # Same launcher as run-gateway-runtime-check.py: reviewed code and CI-only credentials travel in
    # stdin, with no container file and nothing in argv or the environment.
    launch = ('import io,json,sys; p=json.load(sys.stdin); '
              'sys.stdin=io.StringIO(json.dumps({k:p[k] for k in ("controlToken","apiKey","expectStored")})); '
              'exec(compile(p["source"],"<gateway-message-store-check>","exec"),{"__name__":"__main__"})')
    phase = 'runtime'
    checked = subprocess.run(compose + ['exec', '-T', '-e', 'CI=true', 'langflow', 'python', '-c', launch],
                             input=json.dumps({'controlToken': control, 'apiKey': api_key,
                                               'expectStored': arguments.expect_stored,
                                               'source': (root / 'tests/check-gateway-message-store.py').read_text()}),
                             text=True, capture_output=True, timeout=150, check=False)
    # The check emits only fixed aggregate fields, never message bodies or credentials.
    summary = json.loads(checked.stdout)
    if checked.returncode:
        error_type = summary.get('errorType')
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_MESSAGE_STORE_CHECK_FAILED', 'phase': phase,
                          'errorType': error_type if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z]{1,60}', error_type) else 'Unknown',
                          'modelExecuted': False}))
        raise SystemExit(1)
    if summary.get('status') != 'passed' or summary.get('messageStore') is not arguments.expect_stored:
        raise ValueError('MESSAGE_STORE_CHECK_FAILED')
    print(json.dumps(summary))
except SystemExit:
    raise
except Exception:
    print(json.dumps({'status': 'failed', 'code': 'GATEWAY_MESSAGE_STORE_CHECK_FAILED', 'phase': phase, 'modelExecuted': False}))
    raise SystemExit(1)
