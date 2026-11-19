#!/usr/bin/env python3
"""Preserve private Gateway settings; capture provisioning secrets without printing them."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('private_settings', ROOT / 'prepare-langflow.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
NAMES = ('gateway-control.env', 'gateway.env')
HEX = re.compile(r'[a-f0-9]{64}')


def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def encode(values):
    return ''.join(f'{key}={value}\n' for key, value in values.items())


def publish(root, entries):
    with tempfile.TemporaryDirectory(prefix='.gateway-stage-', dir=root) as temporary:
        published = []
        try:
            for name, values in entries:
                staged = Path(temporary) / name
                write_private(staged, encode(values))
                os.link(staged, root / name, follow_symlinks=False)
                published.append((root / name, staged.stat().st_ino))
        except OSError:
            for target, inode in published:
                if target.lstat().st_ino == inode:
                    target.unlink()
            raise


def validate_pair(control, gateway, mcp_token):
    required = {'GATEWAY_AUTH_KEY', 'GATEWAY_CONTROL_TOKEN', 'MCP_SERVICE_TOKEN',
                'GATEWAY_MODE', 'GATEWAY_BOOTSTRAP_STATUS'}
    if gateway.get('GATEWAY_BOOTSTRAP_STATUS') == 'ready':
        required |= {'LANGFLOW_GATEWAY_FLOW_ID', 'LANGFLOW_GATEWAY_FLOW_SHA256', 'LANGFLOW_GATEWAY_API_KEY'}
        if (not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', gateway.get('LANGFLOW_GATEWAY_FLOW_ID', ''))
                or not HEX.fullmatch(gateway.get('LANGFLOW_GATEWAY_FLOW_SHA256', ''))
                or not re.fullmatch(r'sk-[A-Za-z0-9_-]{10,256}', gateway.get('LANGFLOW_GATEWAY_API_KEY', ''))):
            raise ValueError('GATEWAY_SETTINGS_INVALID')
    elif gateway.get('GATEWAY_BOOTSTRAP_STATUS') != 'pending':
        raise ValueError('GATEWAY_SETTINGS_INVALID')
    if (set(control) != {'GATEWAY_CONTROL_TOKEN'} or set(gateway) != required
            or not HEX.fullmatch(control.get('GATEWAY_CONTROL_TOKEN', ''))
            or not HEX.fullmatch(gateway.get('GATEWAY_AUTH_KEY', ''))
            or gateway['GATEWAY_CONTROL_TOKEN'] != control['GATEWAY_CONTROL_TOKEN']
            or gateway['MCP_SERVICE_TOKEN'] != mcp_token or gateway['GATEWAY_MODE'] != 'private'
            or len({control['GATEWAY_CONTROL_TOKEN'], gateway['GATEWAY_AUTH_KEY'], mcp_token}) != 3):
        raise ValueError('GATEWAY_SETTINGS_INVALID')


def prepare(root, *, provision=False, runner=subprocess.run):
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError('PRIVATE_SETTINGS_DIRECTORY_REQUIRED')
    mcp = settings.read_env(root / 'mcp.env', private_mode=True)['MCP_SERVICE_TOKEN']
    if not re.fullmatch(r'[\x21-\x7e]{32,256}', mcp):
        raise ValueError('MCP_SETTINGS_INVALID')
    fd = os.open(root / '.gateway-prepare.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('GATEWAY_LOCK_INVALID')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        present = [os.path.lexists(root / name) for name in NAMES]
        if any(present) and not all(present):
            raise ValueError('PARTIAL_GATEWAY_SETTINGS_RESTORE_REQUIRED')
        if not any(present):
            if provision:
                raise ValueError('GATEWAY_INIT_REQUIRED')
            control = {'GATEWAY_CONTROL_TOKEN': secrets.token_hex(32)}
            gateway = {'GATEWAY_AUTH_KEY': secrets.token_hex(32), **control, 'MCP_SERVICE_TOKEN': mcp,
                       'GATEWAY_MODE': 'private', 'GATEWAY_BOOTSTRAP_STATUS': 'pending'}
            publish(root, zip(NAMES, (control, gateway)))
        control, gateway = [settings.read_env(root / name, private_mode=True) for name in NAMES]
        validate_pair(control, gateway, mcp)
        if provision and gateway['GATEWAY_BOOTSTRAP_STATUS'] == 'pending':
            marker = root / '.gateway-provisioning'
            if os.path.lexists(marker):
                raise ValueError('PREVIOUS_PROVISIONING_INCOMPLETE_OPERATOR_REVIEW_REQUIRED')
            # A crash/failure never silently creates a second long-lived API key.
            write_private(marker, 'Provisioning started; inspect named Langflow gateway keys before retry.\n')
            compose = ['docker', 'compose', '-p', os.environ.get('COMPOSE_PROJECT_NAME', 'oreumit-bootstrap')]
            for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml', 'compose.gateway.yaml'):
                compose += ['-f', str(ROOT / name)]
            result = runner(compose + ['exec', '-T', 'langflow', 'python', '/opt/oreumit/gateway-bootstrap.py', '--provision'],
                            capture_output=True, timeout=180, check=False)
            if result.returncode or len(result.stdout) > 16 * 1024:
                raise ValueError('GATEWAY_PROVISIONING_FAILED_OUTPUT_WITHHELD')
            try:
                value = json.loads(result.stdout)
                if set(value) != {'flowId', 'apiKey', 'flowSha256'}:
                    raise ValueError()
                completed = {**gateway, 'GATEWAY_BOOTSTRAP_STATUS': 'ready',
                             'LANGFLOW_GATEWAY_FLOW_ID': value['flowId'], 'LANGFLOW_GATEWAY_API_KEY': value['apiKey'],
                             'LANGFLOW_GATEWAY_FLOW_SHA256': value['flowSha256']}
                validate_pair(control, completed, mcp)
            except (ValueError, KeyError, TypeError):
                raise ValueError('GATEWAY_PROVISIONING_RESULT_INVALID') from None
            with tempfile.TemporaryDirectory(prefix='.gateway-complete-', dir=root) as temporary:
                staged = Path(temporary) / 'gateway.env'
                write_private(staged, encode(completed))
                # Target was opened safely above under the same advisory lock.
                os.replace(staged, root / 'gateway.env')
            marker.unlink()
            gateway = completed
        return {'status': 'preserved_or_prepared', 'stage': gateway['GATEWAY_BOOTSTRAP_STATUS'],
                'modelExecuted': False, 'productionReady': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--init', action='store_true')
    modes.add_argument('--provision', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(ROOT / '.local', provision=args.provision)))
    except Exception:
        print(json.dumps({'status': 'failed', 'code': 'GATEWAY_PREPARATION_FAILED',
                          'details': 'Settings preserved. Review permissions or incomplete provisioning marker; no secret output.'}))
        raise SystemExit(1)
