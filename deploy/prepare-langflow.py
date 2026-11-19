#!/usr/bin/env python3
"""Prepare private Langflow credentials without replacing an existing pair.

Complete files are published exclusively from a staging directory. A publication
failure rolls back this call's links; a crash leaving a partial pair fails closed
on the next run and requires recovery, never automatic password rotation.
"""
import base64
import binascii
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile


NAMES = ('langflow.env', 'langflow-db.env')
HEX_SECRET = re.compile(r'[a-f0-9]{64}')
MAX_ENV_BYTES = 16 * 1024


def read_env(path, *, private_mode=False):
    """Read bounded regular files without following links or echoing their data."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError(f'Cannot safely read {path.name}; a regular settings file is required.') from error
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_ENV_BYTES):
            raise ValueError(f'{path.name} must be a nonempty regular file with one link, at most 16 KiB.')
        if private_mode and stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f'{path.name} must have permission 0600; its contents were preserved.')
        content = stream.read(MAX_ENV_BYTES + 1)
    if len(content) > MAX_ENV_BYTES:
        raise ValueError(f'{path.name} exceeds 16 KiB.')
    values = {}
    try:
        for line in content.decode('utf-8').splitlines():
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in values:
                raise ValueError('Invalid settings syntax')
            if not value or any(ord(character) < 32 or ord(character) > 126 for character in value):
                raise ValueError('Invalid settings value')
            values[key] = value
    except (UnicodeError, ValueError) as error:
        raise ValueError(f'{path.name} has invalid settings syntax; values were not displayed.') from error
    return values


def database_url(password):
    return f'postgresql+psycopg://langflow_app:{password}@langflow-db:5432/langflow'


def validate_pair(app, database, mcp_token):
    if (set(app) != {'LANGFLOW_SUPERUSER', 'LANGFLOW_SUPERUSER_PASSWORD',
                     'LANGFLOW_SECRET_KEY', 'LANGFLOW_DATABASE_URL'}
            or set(database) != {'POSTGRES_PASSWORD', 'LANGFLOW_DB_PASSWORD'}):
        raise ValueError('Existing Langflow settings have unexpected keys; restore the matching settings pair.')
    passwords = [app['LANGFLOW_SUPERUSER_PASSWORD'], database['POSTGRES_PASSWORD'], database['LANGFLOW_DB_PASSWORD']]
    if (app['LANGFLOW_SUPERUSER'] != 'oreumit_admin'
            or any(not HEX_SECRET.fullmatch(value) for value in passwords)
            or len(set(passwords + [mcp_token])) != 4
            or app['LANGFLOW_DATABASE_URL'] != database_url(database['LANGFLOW_DB_PASSWORD'])):
        raise ValueError('Existing Langflow credentials or database URL do not match; no values were changed.')
    key = app['LANGFLOW_SECRET_KEY']
    try:
        decoded = base64.b64decode(key, altchars=b'-_', validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('The Langflow encryption key is invalid; do not rotate it to repair existing data.') from error
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode('ascii') != key:
        raise ValueError('The Langflow encryption key is invalid; do not rotate it to repair existing data.')


def new_pair(mcp_token):
    passwords = []
    while len(passwords) < 3:
        candidate = secrets.token_hex(32)
        if candidate != mcp_token and candidate not in passwords:
            passwords.append(candidate)
    admin_password, database_admin, database_app = passwords
    app = {
        'LANGFLOW_SUPERUSER': 'oreumit_admin',
        'LANGFLOW_SUPERUSER_PASSWORD': admin_password,
        'LANGFLOW_SECRET_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii'),
        'LANGFLOW_DATABASE_URL': database_url(database_app),
    }
    database = {'POSTGRES_PASSWORD': database_admin, 'LANGFLOW_DB_PASSWORD': database_app}
    validate_pair(app, database, mcp_token)
    return app, database


def publish_pair(root, pair):
    with tempfile.TemporaryDirectory(prefix='.langflow-prepare-', dir=root) as temporary:
        staged = []
        for name, values in zip(NAMES, pair):
            candidate = Path(temporary) / name
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
                stream.flush()
                os.fsync(stream.fileno())
            staged.append(candidate)
        published = []
        try:
            for candidate in staged:
                target = root / candidate.name
                os.link(candidate, target, follow_symlinks=False)
                published.append((candidate, target))
        except OSError:
            for candidate, target in published:
                if target.lstat().st_ino == candidate.stat().st_ino:
                    target.unlink()
            raise
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def prepare(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('An existing real deploy/.local directory is required; run deploy/prepare.py first.')
    root.chmod(0o700)
    mcp = read_env(root / 'mcp.env')
    token = mcp.get('MCP_SERVICE_TOKEN', '')
    if not re.fullmatch(r'[\x21-\x7e]{32,256}', token):
        raise ValueError('Existing mcp.env must contain a valid MCP_SERVICE_TOKEN; it was not changed.')
    lock_path = root / '.langflow-prepare.lock'
    try:
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as error:
        raise ValueError('Cannot safely open the Langflow preparation lock.') from error
    with os.fdopen(lock_fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Langflow preparation lock must be a regular file with one link.')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        present = [os.path.lexists(root / name) for name in NAMES]
        if any(present) and not all(present):
            raise ValueError('Partial Langflow settings pair: restore the missing file from its matching backup. '
                             'Do not regenerate database passwords or the encryption key.')
        if all(present):
            pair = tuple(read_env(root / name, private_mode=True) for name in NAMES)
            validate_pair(*pair, token)
            print('Preserved existing Langflow settings; no password or encryption-key rotation.')
            return
        publish_pair(root, new_pair(token))
        print('Created private Langflow settings and separate database credentials; values not displayed.')


if __name__ == '__main__':
    try:
        prepare(Path(__file__).resolve().parent / '.local')
    except (OSError, ValueError) as error:
        print(f'Langflow preparation failed: {error}')
        raise SystemExit(1)
