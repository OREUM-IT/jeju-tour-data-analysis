#!/usr/bin/env python3
"""Build the pinned catalog locally with Docker; never replace an existing export."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


APP_COMMIT = 'b0602a175c1d08959b7187da2b29ac450b52e0ba'
CATALOG_NAME = 'agent-catalog-b0602a1.json'
IMAGE = 'oreumit-catalog-builder:b0602a1'
MAX_BYTES = 32 * 1024 * 1024


def private_directory(path):
    if path.is_symlink():
        raise ValueError('Refusing a symlink for a private catalog directory.')
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError('Catalog directories must be real directories.')
    path.chmod(0o700)


def read_catalog(path):
    """Bounded, no-follow read; report metadata only, never destination content."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError('Catalog must be an accessible regular file, not a link.') from error
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError('Catalog must be a nonempty regular file with one link, at most 32 MiB.')
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError('Catalog exceeds 32 MiB.')
    try:
        value = json.loads(content.decode('utf-8'))
        source = value['source']
        places = value['places']
        ids = [place['id'] for place in places]
        valid = (
            value['schemaVersion'] == '2.0'
            and value['dataMode'] == 'app-export'
            and source['repository'] == 'OREUM-IT/jeju-tour'
            and source['commit'] == APP_COMMIT
            and len(places) == 981
            and all(isinstance(place_id, str) and place_id for place_id in ids)
            and len(set(ids)) == 981
        )
    except (ValueError, UnicodeError, KeyError, TypeError) as error:
        raise ValueError('Catalog JSON or pinned source metadata is invalid.') from error
    if not valid:
        raise ValueError('Expected schema v2 and 981 distinct places from the pinned app commit.')
    return content, 'sha256:' + hashlib.sha256(content).hexdigest()


def report(status, digest):
    print(json.dumps({
        'status': status, 'placeCount': 981, 'dataMode': 'app-export',
        'sourceCommit': APP_COMMIT, 'dataVersion': digest, 'productionReady': False,
    }))


def export_catalog(repo_root):
    repo_root = Path(repo_root).resolve()
    private = repo_root / 'deploy' / '.local'
    private_directory(private)
    catalog_dir = private / 'catalog'
    private_directory(catalog_dir)
    target = catalog_dir / CATALOG_NAME
    if target.exists() or target.is_symlink():
        _, digest = read_catalog(target)
        report('preserved-existing', digest)
        return target

    print('Building the local catalog exporter; no model API is called.', flush=True)
    subprocess.run([
        'docker', 'build', '--file', str(repo_root / 'deploy/catalog/Dockerfile'),
        '--tag', IMAGE, str(repo_root),
    ], check=True)
    with tempfile.TemporaryDirectory(prefix='.export-', dir=catalog_dir) as directory:
        # Only this empty output directory is mounted, never credentials or the Docker socket.
        if ',' in directory:
            raise ValueError('The catalog path must not contain a comma (Docker mount syntax).')
        subprocess.run([
            'docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--mount', f'type=bind,src={directory},dst=/output/.local', IMAGE,
        ], check=True)
        candidate = Path(directory) / CATALOG_NAME
        _, digest = read_catalog(candidate)
        # A file bind is traversed inside the container, so UID 1000 can read this file
        # without access to either private host directory (both remain mode 0700).
        candidate.chmod(0o644)
        # link() publishes a complete file atomically and refuses any existing target.
        # Removing the temporary name leaves the published file with one link.
        os.link(candidate, target, follow_symlinks=False)
        candidate.unlink()
    report('exported', digest)
    return target


if __name__ == '__main__':
    try:
        export_catalog(Path(__file__).resolve().parents[1])
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print(f'Catalog export failed: Docker exited with status {error.returncode}. Existing files were preserved.')
        else:
            print(f'Catalog export failed: {error}')
        raise SystemExit(1)
