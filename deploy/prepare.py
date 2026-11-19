#!/usr/bin/env python3
"""Generate local-only bootstrap credentials without printing or replacing them."""
import os
from pathlib import Path
import secrets


def prepare(root):
    if root.is_symlink():
        raise ValueError('Refusing a symlink for the private settings directory.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    for name, key in [('mcp.env', 'MCP_SERVICE_TOKEN'), ('db.env', 'POSTGRES_PASSWORD')]:
        target = root / name
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if target.is_symlink() or not target.is_file():
                raise ValueError(f'Refusing unsafe settings path: {name}')
            target.chmod(0o600)
            print(f'Preserved existing {name}; no credential rotation.')
            continue
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(f'{key}={secrets.token_hex(32)}\n')
        print(f'Created private {name}; value not displayed.')


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parent / '.local')
