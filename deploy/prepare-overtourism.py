#!/usr/bin/env python3
"""Provision, check or disable the Gateway's overtourism diagnosis flow pin.

Streams deploy/langflow/overtourism-bootstrap.py into the Langflow container with
the reviewed Gateway builder and component sources embedded (no bind mount, so a
git update is never invisible and Langflow is not restarted). Writes only the
non-secret flow ID and SHA-256 to .local/gateway-overtourism.env (mode 0600),
which compose.gateway.yaml loads when present. Never creates an API key, reads
or prints a credential, or runs a model. Uses the Gateway settings lock.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('gateway_prepare_for_overtourism', ROOT / 'prepare-gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
SETTINGS = 'gateway-overtourism.env'
BOOTSTRAP = ROOT / 'langflow' / 'overtourism-bootstrap.py'
SOURCES = {
    'GATEWAY_BOOTSTRAP_SOURCE': ROOT / 'langflow' / 'gateway-bootstrap.py',
    'RULE_COMPONENTS_SOURCE': ROOT.parent / 'services' / 'langflow' / 'gateway_components.py',
    'OVERTOURISM_COMPONENTS_SOURCE': ROOT.parent / 'services' / 'langflow' / 'overtourism_components.py',
}
RESULT_KEYS = {'status', 'flowStatus', 'flowId', 'flowSha256', 'nodeCount', 'edgeCount', 'toolCount',
               'modelCallLimit', 'modelExecuted', 'productionReady'}
STATUSES = {'provision': ('created', 'already-current'), 'replace': ('updated', 'already-current'), 'check': ('checked',)}
FLOW_ID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
SETTING_KEYS = {'LANGFLOW_OVERTOURISM_FLOW_ID', 'LANGFLOW_OVERTOURISM_FLOW_SHA256', 'GATEWAY_OVERTOURISM_DAILY_LIMIT'}


def program():
    """The bootstrap with each reviewed source as a Python literal before it."""
    header = ''.join(f'{name} = {path.read_text(encoding="utf-8")!r}\n' for name, path in SOURCES.items())
    return (header + BOOTSTRAP.read_text(encoding='utf-8')).encode('utf-8')


def failure_code(stdout, fallback):
    try:
        failure = json.loads(stdout) if len(stdout) <= 16 * 1024 else {}
        code = failure.get('code') if failure.get('status') == 'failed' else None
    except (ValueError, AttributeError):
        code = None
    return code if isinstance(code, str) and re.fullmatch(r'(?:OVERTOURISM|GATEWAY)_[A-Z_]{1,80}', code) else fallback


def read_settings(root):
    path = root / SETTINGS
    if not os.path.lexists(path):
        return None
    values = gateway.settings.read_env(path, private_mode=True)
    if (not {'LANGFLOW_OVERTOURISM_FLOW_ID', 'LANGFLOW_OVERTOURISM_FLOW_SHA256'} <= set(values) <= SETTING_KEYS
            or not FLOW_ID.fullmatch(values['LANGFLOW_OVERTOURISM_FLOW_ID'])
            or not gateway.HEX.fullmatch(values['LANGFLOW_OVERTOURISM_FLOW_SHA256'])
            or not re.fullmatch(r'[1-9]|[1-3]\d|4[0-8]', values.get('GATEWAY_OVERTOURISM_DAILY_LIMIT', '8'))):
        raise ValueError('OVERTOURISM_SETTINGS_INVALID')
    return values


def write_settings(root, values):
    with tempfile.TemporaryDirectory(prefix='.overtourism-stage-', dir=root) as temporary:
        staged = Path(temporary) / SETTINGS
        gateway.write_private(staged, gateway.encode(values))
        os.replace(staged, root / SETTINGS)


def ready_gateway(root, mcp):
    present = [os.path.lexists(root / name) for name in gateway.NAMES]
    if not all(present):
        raise ValueError('GATEWAY_PROVISIONING_REQUIRED')
    control, settings = [gateway.settings.read_env(root / name, private_mode=True) for name in gateway.NAMES]
    gateway.validate_pair(control, settings, mcp)
    if settings['GATEWAY_BOOTSTRAP_STATUS'] != 'ready' or os.path.lexists(root / '.gateway-provisioning'):
        raise ValueError('GATEWAY_PROVISIONING_REQUIRED')


def bootstrap(mode, runner):
    result = runner(gateway.compose_command() + ['exec', '-T', 'langflow', 'python', '-', '--' + mode],
                    input=program(), capture_output=True, timeout=180, check=False)
    if result.returncode or len(result.stdout) > 16 * 1024:
        raise ValueError(failure_code(result.stdout, 'OVERTOURISM_FLOW_FAILED_OUTPUT_WITHHELD'))
    try:
        value = json.loads(result.stdout)
        if not (set(value) == RESULT_KEYS and value['status'] == 'passed' and value['flowStatus'] in STATUSES[mode]
                and isinstance(value['flowId'], str) and FLOW_ID.fullmatch(value['flowId'])
                and isinstance(value['flowSha256'], str) and gateway.HEX.fullmatch(value['flowSha256'])
                and (value['nodeCount'], value['edgeCount'], value['toolCount'], value['modelCallLimit']) == (6, 7, 3, 8)
                and value['modelExecuted'] is False and value['productionReady'] is False):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('OVERTOURISM_FLOW_RESULT_INVALID') from None
    return value


def flow(root, mode, *, runner=subprocess.run):
    """provision/replace: pin the reviewed flow; check: verify it is stored and pinned."""
    if mode not in STATUSES:
        raise ValueError('OVERTOURISM_MODE_INVALID')
    with gateway.locked_settings(root) as (root, mcp):
        ready_gateway(root, mcp)
        current = read_settings(root)
        if mode == 'check' and current is None:
            raise ValueError('OVERTOURISM_NOT_PROVISIONED')
        value = bootstrap(mode, runner)
        summary = {'status': 'passed', 'flowStatus': value['flowStatus'], 'modelCallLimit': value['modelCallLimit'],
                   'modelExecuted': False, 'productionReady': False}
        if mode == 'check':
            if (value['flowId'], value['flowSha256']) != (current['LANGFLOW_OVERTOURISM_FLOW_ID'],
                                                         current['LANGFLOW_OVERTOURISM_FLOW_SHA256']):
                raise ValueError('OVERTOURISM_FLOW_PIN_MISMATCH')
            return {**summary, 'scope': 'overtourism-flow-checked', 'settingsChanged': False}
        values = {'LANGFLOW_OVERTOURISM_FLOW_ID': value['flowId'], 'LANGFLOW_OVERTOURISM_FLOW_SHA256': value['flowSha256'],
                  **({'GATEWAY_OVERTOURISM_DAILY_LIMIT': current['GATEWAY_OVERTOURISM_DAILY_LIMIT']}
                     if current and 'GATEWAY_OVERTOURISM_DAILY_LIMIT' in current else {})}
        changed = values != current
        if changed:
            write_settings(root, values)
        return {**summary, 'scope': 'overtourism-flow-pinned', 'settingsChanged': changed}


def disable(root):
    """Remove only the pin; the next Gateway recreate answers OVERTOURISM_UNAVAILABLE. The flow stays stored."""
    with gateway.locked_settings(root) as (root, _mcp):
        existed = read_settings(root) is not None
        if existed:
            (root / SETTINGS).unlink()
        return {'status': 'passed', 'scope': 'overtourism-disabled', 'settingsChanged': existed,
                'modelExecuted': False, 'productionReady': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--provision', action='store_true', help='Create the diagnosis flow if missing and pin it.')
    modes.add_argument('--replace', action='store_true', help='Switch the stored diagnosis flow to this reviewed build and pin it.')
    modes.add_argument('--check', action='store_true', help='Read-only: the stored flow is this build and is pinned.')
    modes.add_argument('--disable', action='store_true', help='Remove the pin; recreate agent-gateway afterwards.')
    args = parser.parse_args()
    try:
        if args.disable:
            print(json.dumps(disable(ROOT / '.local')))
        else:
            print(json.dumps(flow(ROOT / '.local', 'provision' if args.provision else 'replace' if args.replace else 'check')))
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[A-Z_]{1,90}', str(error)) else 'OVERTOURISM_PREPARATION_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'modelExecuted': False}))
        raise SystemExit(1)
