#!/usr/bin/env python3
"""Inventory actual CSV files without changing sources or exposing row values.

All counts are lexical file observations, not legal, semantic-quality, or freshness
approval. The default null policy counts whitespace-only fields and case-insensitive
NaN; other strings are preserved unless --null-token explicitly adds a marker.
"""
from __future__ import annotations

import argparse
import codecs
import collections
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path('/workspace/scratch/reference-repos/jeju-tour')
SOURCE_SUBDIR = Path('prototype/data')
MAX_FIELD_SIZE = 32 * 1024 * 1024
INVENTORY_FIELDS = [
    'relative_path', 'source_commit', 'git_blob', 'worktree_git_blob',
    'head_blob_matches_worktree', 'sha256', 'byte_size', 'encoding',
    'encoding_detection', 'delimiter', 'delimiter_detection', 'logical_records',
    'data_rows', 'column_count', 'max_columns', 'blank_records',
    'is_empty_file', 'is_empty_table', 'is_header_only', 'ragged_rows',
    'short_rows', 'long_rows', 'has_duplicate_headers', 'duplicate_headers_json',
    'blank_header_count', 'column_names_json', 'replacement_character_count',
    'nul_character_count', 'status', 'error',
]
COLUMN_FIELDS = [
    'relative_path', 'column_index', 'original_column_name', 'header_present',
    'duplicate_header', 'data_rows', 'null_count', 'nonempty_count',
    'blank_count', 'nan_literal_count', 'other_null_literal_count',
    'missing_field_count', 'present_nonempty_count',
]


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        # Do not expose Git stderr or a possibly credential-bearing remote URL.
        raise RuntimeError(f'Git read failed: {args[0]} (exit {result.returncode})')
    return result.stdout


def safe_remote_identity(remote: str) -> dict:
    """Return only the hostname and repository path, never credentials or query data."""
    raw = remote.strip()
    try:
        parsed = urlsplit(raw)
        if parsed.hostname:
            host, repository = parsed.hostname.lower(), parsed.path.strip('/')
        elif ':' in raw and '@' in raw.split(':', 1)[0]:
            authority, repository = raw.split(':', 1)
            host = authority.rsplit('@', 1)[1].lower()
        else:
            return {'status': 'unrecognized_remote', 'host': None, 'repository': None}
        if repository.endswith('.git'):
            repository = repository[:-4]
        return {'status': 'parsed', 'host': host, 'repository': repository}
    except ValueError:
        return {'status': 'unrecognized_remote', 'host': None, 'repository': None}


def git_snapshot(repo: Path) -> dict:
    head = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    object_format = git(repo, 'rev-parse', '--show-object-format').decode().strip()
    status_bytes = git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    # Store status bytes as a hash, and paths/codes only; never read dirty file contents.
    entries = []
    parts = status_bytes.split(b'\0')
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        code = item[:2].decode('ascii')
        entry = {'status': code, 'path': os.fsdecode(item[3:])}
        if 'R' in code or 'C' in code:
            if index < len(parts):
                entry['original_path'] = os.fsdecode(parts[index])
                index += 1
        entries.append(entry)
    remote = safe_remote_identity(git(repo, 'config', '--get', 'remote.origin.url').decode())
    return {
        'commit': head, 'object_format': object_format, 'origin': remote,
        'dirty': bool(status_bytes), 'status_sha256': hashlib.sha256(status_bytes).hexdigest(),
        'status_entries': entries,
    }


def tracked_blobs(repo: Path) -> dict[str, str]:
    result = {}
    for entry in git(repo, 'ls-tree', '-r', '-z', 'HEAD', '--', SOURCE_SUBDIR.as_posix()).split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        _mode, kind, oid = metadata.split(b' ', 2)
        if kind == b'blob':
            result[os.fsdecode(name)] = oid.decode('ascii')
    return result


def decode_csv(data: bytes) -> tuple[str, str, str]:
    """Strict decoding; no replacement/error-ignore fallback or guessed Latin-1."""
    for bom, decoder, label in [
        (codecs.BOM_UTF32_LE, 'utf-32', 'utf-32-le-bom'),
        (codecs.BOM_UTF32_BE, 'utf-32', 'utf-32-be-bom'),
        (codecs.BOM_UTF8, 'utf-8-sig', 'utf-8-sig'),
        (codecs.BOM_UTF16_LE, 'utf-16', 'utf-16-le-bom'),
        (codecs.BOM_UTF16_BE, 'utf-16', 'utf-16-be-bom'),
    ]:
        if data.startswith(bom):
            return data.decode(decoder, errors='strict'), label, 'bom-strict'
    try:
        return data.decode('utf-8', errors='strict'), 'utf-8', 'strict-utf8-preferred'
    except UnicodeDecodeError:
        return data.decode('cp949', errors='strict'), 'cp949', 'strict-cp949-fallback'


def detect_delimiter(text: str) -> tuple[str, str]:
    if not text:
        return ',', 'empty-default-comma'
    try:
        dialect = csv.Sniffer().sniff(text[:131_072], delimiters=',\t;|')
        return dialect.delimiter, 'csv-sniffer'
    except csv.Error:
        pass
    # Read a bounded number of complete logical records from the full string.
    # This avoids treating a sample cut in a quoted multiline field as malformed.
    scores = []
    for delimiter in [',', '\t', ';', '|']:
        widths = []
        try:
            for row in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True):
                if row:
                    widths.append(len(row))
                if len(widths) >= 100:
                    break
        except csv.Error:
            continue
        if widths and widths[0] > 1:
            match_fraction = sum(width == widths[0] for width in widths) / len(widths)
            scores.append((match_fraction, widths[0], -[',', '\t', ';', '|'].index(delimiter), delimiter))
    if scores:
        return max(scores)[-1], 'header-width-consistency-fallback'
    return ',', 'single-column-or-unresolved-default-comma'


def new_counter() -> dict[str, int]:
    return dict(null_count=0, nonempty_count=0, blank_count=0, nan_literal_count=0,
                other_null_literal_count=0, missing_field_count=0, present_nonempty_count=0)


def inspect_csv(data: bytes, relative_path: str, null_tokens: set[str]) -> tuple[dict, list[dict]]:
    """Inspect bytes, returning only schema/statistics, never example cell values."""
    info = {
        'relative_path': relative_path, 'byte_size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(), 'encoding': '',
        'encoding_detection': '', 'delimiter': '', 'delimiter_detection': '',
        'logical_records': 0, 'data_rows': 0, 'column_count': 0, 'max_columns': 0,
        'blank_records': 0, 'is_empty_file': len(data) == 0, 'is_empty_table': True,
        'is_header_only': False, 'ragged_rows': 0, 'short_rows': 0, 'long_rows': 0,
        'has_duplicate_headers': False, 'duplicate_headers_json': '[]',
        'blank_header_count': 0, 'column_names_json': '[]',
        'replacement_character_count': 0, 'nul_character_count': 0,
        'status': 'ok', 'error': '',
    }
    try:
        text, info['encoding'], info['encoding_detection'] = decode_csv(data)
    except UnicodeDecodeError:
        info.update(status='decode_error', error='Strict BOM/UTF-8/CP949 decoding failed; source bytes were not changed.')
        return info, []
    info['replacement_character_count'] = text.count('\ufffd')
    info['nul_character_count'] = text.count('\0')
    delimiter, method = detect_delimiter(text)
    info.update(delimiter=delimiter, delimiter_detection=method)
    headers = None
    counters = []
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, quotechar='"',
                        doublequote=True, skipinitialspace=False, strict=True)
    try:
        for record in reader:
            info['logical_records'] += 1
            if not record:
                info['blank_records'] += 1
                continue
            if headers is None:
                headers = record
                info['column_count'] = len(headers)
                counters = [new_counter() for _ in headers]
                continue
            info['data_rows'] += 1
            if len(record) < len(headers):
                info['short_rows'] += 1
            elif len(record) > len(headers):
                info['long_rows'] += 1
            # Extra columns are inventoried by position with no invented header.
            while len(counters) < len(record):
                counter = new_counter()
                counter['missing_field_count'] = info['data_rows'] - 1
                counter['null_count'] = info['data_rows'] - 1
                counters.append(counter)
            for index, counter in enumerate(counters):
                if index >= len(record):
                    counter['missing_field_count'] += 1
                    counter['null_count'] += 1
                    continue
                value = record[index].strip()
                if not value:
                    counter['blank_count'] += 1
                    counter['null_count'] += 1
                    continue
                counter['present_nonempty_count'] += 1
                if value.casefold() in null_tokens:
                    counter['null_count'] += 1
                    counter['nan_literal_count' if value.casefold() == 'nan' else 'other_null_literal_count'] += 1
                else:
                    counter['nonempty_count'] += 1
    except csv.Error:
        # The message deliberately omits source values; partial counts remain labeled.
        info.update(status='csv_parse_error', error=f'CSV parse failed at physical line {reader.line_num}; counts are partial.')
    headers = [] if headers is None else headers
    counts = collections.Counter(headers)
    duplicates = [{'name': name, 'count': count, 'positions': [i + 1 for i, h in enumerate(headers) if h == name]}
                  for name, count in counts.items() if count > 1]
    info.update(
        max_columns=len(counters), is_empty_table=not headers and info['data_rows'] == 0,
        is_header_only=bool(headers) and info['data_rows'] == 0,
        ragged_rows=info['short_rows'] + info['long_rows'],
        has_duplicate_headers=bool(duplicates),
        duplicate_headers_json=json.dumps(duplicates, ensure_ascii=False, separators=(',', ':')),
        blank_header_count=sum(not h.strip() for h in headers),
        column_names_json=json.dumps(headers, ensure_ascii=False, separators=(',', ':')),
    )
    columns = []
    for index, counter in enumerate(counters):
        assert counter['null_count'] + counter['nonempty_count'] == info['data_rows']
        name = headers[index] if index < len(headers) else ''
        columns.append({
            'relative_path': relative_path, 'column_index': index + 1,
            'original_column_name': name, 'header_present': index < len(headers),
            'duplicate_header': index < len(headers) and counts[name] > 1,
            'data_rows': info['data_rows'], **counter,
        })
    return info, columns


def atomic_write(path: Path, text: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding=encoding, newline='', dir=path.parent,
                                     prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def csv_text(rows: list[dict], fields: list[str]) -> str:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='raise', lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def build(source_repo: Path, output_dir: Path, null_tokens: set[str]) -> dict:
    csv.field_size_limit(MAX_FIELD_SIZE)
    source_repo = source_repo.resolve(strict=True)
    actual_root = Path(git(source_repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve(strict=True)
    if source_repo != actual_root:
        raise ValueError('--source-repo must point to the actual Git root.')
    source_dir = (source_repo / SOURCE_SUBDIR).resolve(strict=True)
    if not source_dir.is_relative_to(source_repo):
        raise ValueError('Source data directory escapes the source checkout.')
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(source_repo):
        raise ValueError('Output must be outside the read-only source checkout.')
    before = git_snapshot(source_repo)
    blobs = tracked_blobs(source_repo)
    files = []
    # os.walk surfaces traversal errors instead of quietly omitting inaccessible dirs.
    def walk_error(error):
        raise error
    for directory, subdirs, names in os.walk(source_dir, onerror=walk_error, followlinks=False):
        subdirs.sort()
        for subdir in subdirs:
            if (Path(directory) / subdir).is_symlink():
                raise ValueError('A symlinked directory prevents a complete bounded source inventory.')
        for name in sorted(names):
            if Path(name).suffix.casefold() != '.csv':
                continue
            candidate = Path(directory) / name
            if candidate.is_symlink():
                raise ValueError('A symlinked CSV requires explicit review before inventory.')
            files.append(candidate)
    files.sort(key=lambda p: p.relative_to(source_repo).as_posix())
    inventory, columns = [], []
    file_hashes = {}
    for file in files:
        relative = file.relative_to(source_repo).as_posix()
        data = file.read_bytes()
        entry, file_columns = inspect_csv(data, relative, null_tokens)
        oid = hashlib.new(before['object_format'], f'blob {len(data)}\0'.encode() + data).hexdigest()
        entry.update(source_commit=before['commit'], git_blob=blobs.get(relative, ''),
                     worktree_git_blob=oid, head_blob_matches_worktree=blobs.get(relative) == oid)
        inventory.append(entry)
        columns.extend(file_columns)
        file_hashes[relative] = entry['sha256']
    after = git_snapshot(source_repo)
    # Check all scanned bytes again so an inventory is not labeled stable on a changed snapshot.
    unchanged = before == after and all(
        hashlib.sha256((source_repo / relative).read_bytes()).hexdigest() == checksum
        for relative, checksum in file_hashes.items()
    )
    if not unchanged:
        raise RuntimeError('Source changed during inspection; no new inventory outputs were written.')
    generated = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    summary = {
        'csv_files': len(inventory), 'total_bytes': sum(r['byte_size'] for r in inventory),
        'data_rows': sum(r['data_rows'] for r in inventory), 'column_records': len(columns),
        'encodings': dict(sorted(collections.Counter(r['encoding'] for r in inventory).items())),
        'statuses': dict(sorted(collections.Counter(r['status'] for r in inventory).items())),
        'empty_files': sum(r['is_empty_file'] for r in inventory),
        'header_only_files': sum(r['is_header_only'] for r in inventory),
        'files_with_ragged_rows': sum(r['ragged_rows'] > 0 for r in inventory),
        'files_with_duplicate_headers': sum(r['has_duplicate_headers'] for r in inventory),
        'head_matching_files': sum(r['head_blob_matches_worktree'] for r in inventory),
    }
    metadata = {
        'schema_version': '1.0', 'generated_at': generated,
        'source_repository_path': str(source_repo), 'source_path': SOURCE_SUBDIR.as_posix(),
        'source_commit': before['commit'], 'source_git_before': before, 'source_git_after': after,
        'source_unchanged_during_run': unchanged,
        'script_path': 'scripts/build_raw_data_inventory.py',
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'Actual local .csv files recursively under prototype/data; file/schema inventory only.',
        'source_row_values_included': False, 'license_or_semantic_quality_approved': False,
        'excel_attachment_coverage_confirmed': False,
        'policies': {
            'output_csv_encoding': 'utf-8-sig',
            'decoding': 'Strict BOM detection, then UTF-8, then CP949. No replacement fallback. ASCII is labeled UTF-8 by precedence, not unique encoding proof.',
            'header': 'First nonempty csv.reader record; leading empty records are counted and skipped. Header strings are not trimmed or renamed.',
            'delimiter': 'Sniffer among comma/tab/semicolon/pipe; complete-record header-width fallback; unresolved single columns default to comma and are labeled.',
            'logical_records': 'All complete csv.reader records including header and empty records; embedded quoted newlines stay within one record.',
            'data_rows': 'Nonempty records after the header. Empty physical records [] are skipped; records containing empty cells still count.',
            'null_tokens_casefold': sorted(null_tokens),
            'null_count': 'Missing fields + whitespace-only strings + explicitly configured lexical null markers. This is not semantic validation.',
            'nonempty_count': 'Present, nonblank fields not matching configured null markers; null_count + nonempty_count = data_rows.',
            'present_nonempty_count': 'Present nonblank fields including textual null markers.',
            'ragged_rows': 'Data records with width different from the header. Extra fields retain their positional index with no invented header.',
            'git_blob': 'Blob ID from source HEAD; worktree_git_blob separately hashes actual bytes using the repository object format.',
            'errors': 'Decode/CSV errors are explicitly labeled; partial parse counts do not establish complete file coverage.',
        },
        'summary': summary, 'files': inventory,
    }
    atomic_write(output_dir / 'raw-data-inventory.csv', csv_text(inventory, INVENTORY_FIELDS), 'utf-8-sig')
    atomic_write(output_dir / 'raw-data-columns.csv', csv_text(columns, COLUMN_FIELDS), 'utf-8-sig')
    atomic_write(output_dir / 'raw-data-inventory.json', json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--null-token', action='append', default=[], help='Additional case-insensitive lexical null marker (default marker: NaN).')
    args = parser.parse_args()
    tokens = {'nan', *(value.strip().casefold() for value in args.null_token if value.strip())}
    metadata = build(args.source_repo, args.output_dir, tokens)
    print(json.dumps({'generated_at': metadata['generated_at'], 'source_commit': metadata['source_commit'],
                      'source_dirty': metadata['source_git_before']['dirty'],
                      'source_unchanged': metadata['source_unchanged_during_run'], **metadata['summary']}, ensure_ascii=False))
    return 0 if all(r['status'] == 'ok' for r in metadata['files']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
