#!/usr/bin/env python3
"""Prepare Access settings and stdin-only secrets; never rotate existing values.

Run as root on the deployment host. This module performs no network requests and
never changes gateway.env. A partial token pair fails closed on the next run.
"""
import argparse
import base64
import binascii
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT_UID = 0
RUNTIME_UID = 65532
RUNTIME_GID = 65532
MAX_BYTES = 16 * 1024
ISSUER = re.compile(r'https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com')
AUDIENCE = re.compile(r'[a-f0-9]{64}')
PUBLIC_HOST = 'agent.oreumit.com'
ACCESS_KEYS = ('GATEWAY_MODE', 'GATEWAY_ACCESS_ISSUER', 'GATEWAY_ACCESS_AUDIENCE', 'GATEWAY_PUBLIC_HOST')
CLIENT_ID = re.compile(r'[a-f0-9]{32}\.access')
CLIENT_SECRET = re.compile(r'(?:[a-f0-9]{64}|cfast_[A-Za-z0-9]{48})')


class ConfigurationError(Exception):
    """Only fixed, nonsecret messages may cross the CLI boundary."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'TUNNEL_ARGUMENTS_INVALID: use --help; secret values must be supplied through stdin.\n')


def require(condition):
    if not condition:
        raise ConfigurationError('TUNNEL_CONFIGURATION_INVALID')


def directory(path, *, root_owned=False, create=False):
    path = Path(path)
    # Reject symlink ancestors too, including .local, rather than resolving them.
    for parent in (path, *path.parents):
        require(not parent.is_symlink())
    if create and not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)
    if root_owned:
        require(info.st_uid == ROOT_UID)
    return path


def read_private(path, *, uid=0, mode=0o600):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == uid and stat.S_IMODE(info.st_mode) == mode
                and 0 < info.st_size <= MAX_BYTES)
        content = stream.read(MAX_BYTES + 1)
    require(0 < len(content) <= MAX_BYTES)
    return content


def env_values(content):
    result = {}
    for line in content.decode('ascii').splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', key)
                and key not in result and value
                and all(32 <= ord(character) <= 126 for character in value))
        result[key] = value
    return result


def encode_env(values):
    return ''.join(f'{key}={value}\n' for key, value in values.items()).encode('ascii')


def access_values(issuer, audience, hostname):
    require(isinstance(issuer, str) and ISSUER.fullmatch(issuer)
            and isinstance(audience, str) and AUDIENCE.fullmatch(audience)
            and hostname == PUBLIC_HOST)
    return dict(zip(ACCESS_KEYS, ('access', issuer, audience, hostname)))


def read_access(root):
    values = env_values(read_private(root / 'gateway-access.env', uid=ROOT_UID))
    require(set(values) == set(ACCESS_KEYS) and values['GATEWAY_MODE'] == 'access')
    require(values == access_values(values['GATEWAY_ACCESS_ISSUER'],
                                    values['GATEWAY_ACCESS_AUDIENCE'], values['GATEWAY_PUBLIC_HOST']))
    return values


def check_gateway_file(root, *, required=False):
    path = root / 'gateway.env'
    if not os.path.lexists(path):
        require(not required)
        return None
    # The existing bootstrap may belong to the SSH operator; preserve it byte for byte.
    owner = path.lstat().st_uid
    require(owner in (ROOT_UID, root.stat().st_uid))
    return env_values(read_private(path, uid=owner))


def tunnel_token(content):
    require(isinstance(content, bytes) and 0 < len(content) <= MAX_BYTES)
    token = content.removesuffix(b'\n').removesuffix(b'\r')
    require(re.fullmatch(rb'[A-Za-z0-9+/]+={0,2}', token) is not None)
    # cloudflared's remotely managed tunnel token is base64 TunnelToken JSON.
    decoded = json.loads(base64.b64decode(token, validate=True))
    require(isinstance(decoded, dict) and {'a', 't', 's'} <= set(decoded) <= {'a', 't', 's', 'e'}
            and decoded.get('e', '') == ''
            and isinstance(decoded['a'], str) and re.fullmatch(r'[a-f0-9]{32}', decoded['a'])
            and isinstance(decoded['t'], str)
            and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', decoded['t'])
            and isinstance(decoded['s'], str))
    require(len(base64.b64decode(decoded['s'], validate=True)) >= 32)
    return token


def service_values(content):
    lines = content.decode('ascii').splitlines()
    require(len(lines) == 2 and CLIENT_ID.fullmatch(lines[0]) and CLIENT_SECRET.fullmatch(lines[1]))
    return {'CF_ACCESS_CLIENT_ID': lines[0], 'CF_ACCESS_CLIENT_SECRET': lines[1]}


def read_service(root):
    folder = directory(root / 'tunnel', root_owned=True)
    values = env_values(read_private(folder / 'service-token.env', uid=ROOT_UID))
    require(set(values) == {'CF_ACCESS_CLIENT_ID', 'CF_ACCESS_CLIENT_SECRET'})
    require(values == service_values((values['CF_ACCESS_CLIENT_ID'] + '\n'
                                     + values['CF_ACCESS_CLIENT_SECRET']).encode('ascii')))
    return values


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(folder, files):
    """Exclusive publication, rollback on failure; existing settings never replaced."""
    with tempfile.TemporaryDirectory(prefix='.tunnel-prepare-', dir=folder) as temporary:
        staged = []
        for name, content, uid, mode in files:
            candidate = Path(temporary) / name
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fchown(stream.fileno(), uid, RUNTIME_GID if uid == RUNTIME_UID else 0)
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            staged.append(candidate)
        published = []
        try:
            for candidate in staged:
                target = folder / candidate.name
                os.link(candidate, target, follow_symlinks=False)
                published.append((candidate, target))
            sync_directory(folder)
        except OSError:
            for candidate, target in published:
                if target.lstat().st_ino == candidate.stat().st_ino:
                    target.unlink()
            raise
    sync_directory(folder)


def preserve_or_publish(folder, name, content):
    path = folder / name
    if os.path.lexists(path):
        require(hmac.compare_digest(read_private(path, uid=ROOT_UID), content))
        return 'preserved'
    publish(folder, [(name, content, ROOT_UID, 0o600)])
    return 'created'


def check_token(root, *, required=False):
    folder = root / 'tunnel'
    if not os.path.lexists(folder):
        require(not required)
        return False
    directory(folder, root_owned=True)
    present = [os.path.lexists(folder / name) for name in ('token', 'token.runtime')]
    require(all(present) or not any(present))
    if not all(present):
        require(not required)
        return False
    original = read_private(folder / 'token', uid=ROOT_UID)
    runtime = read_private(folder / 'token.runtime', uid=RUNTIME_UID, mode=0o400)
    require(hmac.compare_digest(original, runtime) and tunnel_token(original) == original)
    return True


def configure(root, operation, *, issuer=None, audience=None, hostname=None, secret=None):
    require(os.geteuid() == ROOT_UID)
    root = directory(root)
    check_gateway_file(root)
    descriptor = os.open(root / '.tunnel-prepare.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
                         | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, 'wb') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == ROOT_UID
                and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if operation == 'access':
            result = preserve_or_publish(root, 'gateway-access.env',
                                         encode_env(access_values(issuer, audience, hostname)))
        elif operation == 'token':
            value = tunnel_token(secret)
            folder = directory(root / 'tunnel', root_owned=True, create=True)
            if check_token(root):
                require(hmac.compare_digest(read_private(folder / 'token', uid=ROOT_UID), value))
                result = 'preserved'
            else:
                publish(folder, [('token', value, ROOT_UID, 0o600),
                                 ('token.runtime', value, RUNTIME_UID, 0o400)])
                result = 'created'
        elif operation == 'service-token':
            folder = directory(root / 'tunnel', root_owned=True, create=True)
            result = preserve_or_publish(folder, 'service-token.env', encode_env(service_values(secret)))
        elif operation == 'check':
            read_access(root)
            configured = check_token(root)
            return {'status': 'passed', 'scope': 'configuration_only', 'tokenConfigured': configured,
                    'runtimeChecked': False, 'modelExecuted': False, 'productionReady': False}
        else:
            raise ConfigurationError('TUNNEL_CONFIGURATION_INVALID')
    return {'status': result, 'scope': operation, 'valuesDisplayed': False,
            'runtimeChecked': False, 'productionReady': False}


def main():
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    access = commands.add_parser('access', help='Create immutable nonsecret Access override settings.')
    access.add_argument('--issuer', required=True)
    access.add_argument('--audience', required=True)
    access.add_argument('--hostname', default=PUBLIC_HOST)
    commands.add_parser('token', help='Read the tunnel token from stdin, without echoing it.')
    commands.add_parser('service-token', help='Read client ID then client secret from stdin (two lines).')
    commands.add_parser('check', help='Check files only; no gateway control token or external connection needed.')
    args = parser.parse_args()
    try:
        secret = None
        if args.operation in ('token', 'service-token'):
            require(not sys.stdin.isatty())
            secret = sys.stdin.buffer.read(MAX_BYTES + 1)
            require(0 < len(secret) <= MAX_BYTES)
        result = configure(Path(__file__).resolve().parents[1] / '.local', args.operation,
                           issuer=getattr(args, 'issuer', None), audience=getattr(args, 'audience', None),
                           hostname=getattr(args, 'hostname', None), secret=secret)
        print(json.dumps(result, separators=(',', ':')))
    except (OSError, ValueError, UnicodeError, binascii.Error, ConfigurationError):
        print('TUNNEL_CONFIGURATION_FAILED: invalid input, ownership, permissions, or existing/partial settings; values preserved.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
