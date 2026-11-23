#!/usr/bin/env python3
"""Preserve private Gateway settings; capture provisioning secrets without printing them."""
import argparse
import contextlib
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
# Single-file bind mounts keep the inode from Langflow container creation, so a
# git update is invisible there. Stream the reviewed host script over stdin.
BOOTSTRAP = ROOT / 'langflow' / 'gateway-bootstrap.py'
FLOW_LIMITS = {'update': 8, 'rollback': 4, 'check': 8,
               'rule-action': 8, 'rule-action-rollback': 8, 'check-rule-action': 8}
CHECK_MODES = ('check', 'check-rule-action')
SCOPES = {'update': 'gateway-flow-updated', 'rollback': 'gateway-flow-rolled-back',
          'rule-action': 'gateway-rule-action-applied', 'rule-action-rollback': 'gateway-rule-action-rolled-back'}
MIGRATION_KEYS = {'status', 'flowStatus', 'flowId', 'flowSha256', 'modelCallLimit', 'modelExecuted', 'productionReady'}
CHECK_KEYS = {'status', 'flowMatches', 'nodeCount', 'edgeCount', 'toolCount', 'modelCallLimit', 'flowSha256',
              'modelExecuted', 'productionReady'}
# Present from the start of a flow switch until the apply helper has re-pinned
# the Gateway and passed every readiness check. While it exists, the Gateway may
# refuse work with FLOW_CHANGED, so a rerun must resume instead of aborting.
PENDING = '.gateway-flow-change'
NOT_PENDING_EXIT = 3


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


def compose_command():
    compose = ['docker', 'compose', '-p', os.environ.get('COMPOSE_PROJECT_NAME', 'oreumit-bootstrap')]
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml', 'compose.gateway.yaml'):
        compose += ['-f', str(ROOT / name)]
    return compose


def failure_code(stdout, fallback):
    # The inner bootstrap emits fixed codes on failure; preserve only that
    # bounded code, never its stdout/stderr or a credential value.
    try:
        failure = json.loads(stdout) if len(stdout) <= 16 * 1024 else {}
        code = failure.get('code') if failure.get('status') == 'failed' else None
    except (ValueError, AttributeError):
        code = None
    return code if isinstance(code, str) and re.fullmatch(r'GATEWAY_[A-Z_]{1,80}', code) else fallback


def patch_not_attempted(stdout):
    """True only for a bounded failure report stating that no flow PATCH was sent."""
    try:
        failure = json.loads(stdout) if len(stdout) <= 16 * 1024 else {}
        return failure.get('status') == 'failed' and failure.get('flowPatchAttempted') is False
    except (ValueError, AttributeError):
        return False


def replace_gateway(root, values):
    with tempfile.TemporaryDirectory(prefix='.gateway-complete-', dir=root) as temporary:
        staged = Path(temporary) / 'gateway.env'
        write_private(staged, encode(values))
        # Target was opened safely by the caller under the same advisory lock.
        os.replace(staged, root / 'gateway.env')


@contextlib.contextmanager
def locked_settings(root):
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
        yield root, mcp


def pending_marker(root):
    """Return whether a flow switch is unfinished; refuse a linked or replaced marker."""
    marker = root / PENDING
    if not os.path.lexists(marker):
        return False
    info = os.lstat(marker)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('GATEWAY_FLOW_CHANGE_MARKER_INVALID')
    return True


def flow_change(root, action):
    """Read or clear the unfinished-switch marker. Never contacts Langflow or Docker."""
    if action not in ('pending', 'complete'):
        raise ValueError('GATEWAY_MODE_INVALID')
    with locked_settings(root) as (root, _mcp):
        pending = pending_marker(root)
        if action == 'complete' and pending:
            # Called only after the Gateway was re-pinned and readiness passed.
            (root / PENDING).unlink()
        return {'status': 'passed', 'flowChangePending': pending if action == 'pending' else False,
                **({'markerCleared': pending} if action == 'complete' else {}),
                'modelExecuted': False, 'productionReady': False}


def flow(root, mode, *, runner=subprocess.run):
    """Update, roll back or check the pinned Gateway flow. Never issues an API key."""
    if mode not in FLOW_LIMITS:
        raise ValueError('GATEWAY_MODE_INVALID')
    with locked_settings(root) as (root, mcp):
        present = [os.path.lexists(root / name) for name in NAMES]
        if not all(present):
            raise ValueError('PARTIAL_GATEWAY_SETTINGS_RESTORE_REQUIRED' if any(present) else 'GATEWAY_INIT_REQUIRED')
        control, gateway = [settings.read_env(root / name, private_mode=True) for name in NAMES]
        validate_pair(control, gateway, mcp)
        if gateway['GATEWAY_BOOTSTRAP_STATUS'] != 'ready' or os.path.lexists(root / '.gateway-provisioning'):
            raise ValueError('GATEWAY_PROVISIONING_REQUIRED')
        created = mode not in CHECK_MODES and not pending_marker(root)
        if created:
            # Written before Langflow can change: an interrupted switch is resumable.
            write_private(root / PENDING, 'Gateway flow switch started; rerun the same deploy/apply-agent-*.sh helper to finish.\n')
        result = runner(compose_command() + ['exec', '-T', 'langflow', 'python', '-', '--' + mode],
                        input=BOOTSTRAP.read_bytes(), capture_output=True, timeout=180, check=False)
        if result.returncode or len(result.stdout) > 16 * 1024:
            if created and patch_not_attempted(result.stdout):
                # The exited in-container run reported that it sent no PATCH, so
                # Langflow and gateway.env are unchanged: nothing is left to resume.
                (root / PENDING).unlink()
            raise ValueError(failure_code(result.stdout, 'GATEWAY_FLOW_CHANGE_FAILED_OUTPUT_WITHHELD'))
        try:
            value = json.loads(result.stdout)
            common = (value['status'] == 'passed' and value['modelExecuted'] is False
                      and value['productionReady'] is False and value['modelCallLimit'] == FLOW_LIMITS[mode]
                      and isinstance(value['flowSha256'], str) and HEX.fullmatch(value['flowSha256']))
            if mode in CHECK_MODES:
                valid = set(value) == CHECK_KEYS and value['flowMatches'] is True
            else:
                valid = (set(value) == MIGRATION_KEYS and value['flowId'] == gateway['LANGFLOW_GATEWAY_FLOW_ID']
                         and value['flowStatus'] in ('updated', 'already-current'))
            if not (common and valid):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError('GATEWAY_FLOW_RESULT_INVALID') from None
        summary = {'status': 'passed', 'modelCallLimit': value['modelCallLimit'],
                   'modelExecuted': False, 'productionReady': False}
        if mode in CHECK_MODES:
            # The running Gateway pins this SHA; a mismatch means it fails closed.
            if value['flowSha256'] != gateway['LANGFLOW_GATEWAY_FLOW_SHA256']:
                raise ValueError('GATEWAY_FLOW_PIN_MISMATCH')
            return {**summary, 'scope': 'gateway-flow-checked' if mode == 'check' else 'gateway-rule-action-checked',
                    'settingsChanged': False}
        changed = value['flowSha256'] != gateway['LANGFLOW_GATEWAY_FLOW_SHA256']
        if changed:
            # Only the pinned SHA changes; the API key, flow ID and tokens stay byte-identical.
            completed = {**gateway, 'LANGFLOW_GATEWAY_FLOW_SHA256': value['flowSha256']}
            validate_pair(control, completed, mcp)
            replace_gateway(root, completed)
        return {**summary, 'scope': SCOPES[mode],
                'flowStatus': value['flowStatus'], 'settingsChanged': changed}


def prepare(root, *, provision=False, runner=subprocess.run):
    with locked_settings(root) as (root, mcp):
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
            # Same reviewed host builder as the flow modes, never a stale bind mount.
            result = runner(compose_command() + ['exec', '-T', 'langflow', 'python', '-', '--provision'],
                            input=BOOTSTRAP.read_bytes(), capture_output=True, timeout=180, check=False)
            if result.returncode or len(result.stdout) > 16 * 1024:
                raise ValueError(failure_code(result.stdout, 'GATEWAY_PROVISIONING_FAILED_OUTPUT_WITHHELD'))
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
            replace_gateway(root, completed)
            marker.unlink()
            gateway = completed
        return {'status': 'preserved_or_prepared', 'stage': gateway['GATEWAY_BOOTSTRAP_STATUS'],
                'modelExecuted': False, 'productionReady': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--init', action='store_true')
    modes.add_argument('--provision', action='store_true')
    modes.add_argument('--update-flow', action='store_true', help='Pin the 8-call flow; no key or flow creation.')
    modes.add_argument('--rollback-flow', action='store_true', help='Pin the previous 4-call flow; no key or flow creation.')
    modes.add_argument('--check-flow', action='store_true', help='Read-only: stored flow is the 8-call build and is pinned.')
    modes.add_argument('--rule-action-flow', action='store_true',
                       help='Pin the rule-action OpenAI flow (from the exact 8-call flow); no key or flow creation.')
    modes.add_argument('--rule-action-rollback-flow', action='store_true',
                       help='Pin the 8-call Vertex flow again (from the exact rule-action flow); no key or flow creation.')
    modes.add_argument('--check-rule-action-flow', action='store_true',
                       help='Read-only: stored flow is the rule-action build and is pinned.')
    modes.add_argument('--flow-change-pending', action='store_true',
                       help=f'Read-only: exit 0 if a flow switch is unfinished, {NOT_PENDING_EXIT} if not.')
    modes.add_argument('--complete-flow-change', action='store_true',
                       help='Clear the unfinished-switch marker after the Gateway is re-pinned and ready.')
    args = parser.parse_args()
    switching = args.update_flow or args.rollback_flow or args.rule_action_flow or args.rule_action_rollback_flow
    try:
        if switching or args.check_flow or args.check_rule_action_flow:
            mode = ('update' if args.update_flow else 'rollback' if args.rollback_flow
                    else 'rule-action' if args.rule_action_flow else 'rule-action-rollback' if args.rule_action_rollback_flow
                    else 'check-rule-action' if args.check_rule_action_flow else 'check')
            print(json.dumps(flow(ROOT / '.local', mode)))
        elif args.flow_change_pending or args.complete_flow_change:
            result = flow_change(ROOT / '.local', 'pending' if args.flow_change_pending else 'complete')
            print(json.dumps(result))
            if args.flow_change_pending and not result['flowChangePending']:
                raise SystemExit(NOT_PENDING_EXIT)
        else:
            print(json.dumps(prepare(ROOT / '.local', provision=args.provision)))
    except SystemExit:
        raise
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[A-Z_]{1,90}', str(error)) else 'GATEWAY_PREPARATION_FAILED'
        try:
            unfinished = switching and pending_marker(ROOT / '.local')
        except Exception:
            unfinished = switching
        details = ('The Langflow flow switch is unfinished; rerun the same deploy/apply-agent-*.sh helper '
                   '(or its --rollback) to finish. No secret output.' if unfinished else
                   'Settings preserved; this run did not change the Langflow flow. No secret output.' if switching else
                   'Settings preserved. Review permissions or incomplete provisioning marker; no secret output.')
        print(json.dumps({'status': 'failed', 'code': code, 'details': details}))
        raise SystemExit(1)
