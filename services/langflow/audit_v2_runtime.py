#!/usr/bin/env python3
"""Audit persisted Langflow spans for exact collector sessions, without inference.

Capture, telemetry, readiness snapshots and per-case details stay in .local.
Only the allowlisted aggregate is suitable for publication. The supplied catalog
readiness is a separately captured authenticated /readyz observation. Spans are
not provider billing records and cannot reveal hidden SDK/network retries.
"""
import argparse
import collections
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

TOOL_NAMES = ('places.search', 'places.get', 'constraints.check')
HASH = re.compile(r'^[a-f0-9]{64}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_sha(value):
    # These contracts contain integers and strings, never floating-point hashes.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_status(rows):
    counts = collections.Counter(str(row['status']).lower() for row in rows)
    return {'calls': len(rows), 'ok': counts['ok'], 'error': counts['error'],
            'otherStatus': sum(n for key, n in counts.items() if key not in ('ok', 'error'))}


def latency_stats(values):
    valid = sorted(value for value in values if nonnegative_int(value))
    return {'measured': len(valid), 'missing': len(values) - len(valid),
            'totalMs': sum(valid) if valid else None,
            'p50Ms': valid[max(0, math.ceil(len(valid) * .5) - 1)] if valid else None,
            'p95Ms': valid[max(0, math.ceil(len(valid) * .95) - 1)] if valid else None,
            'maxMs': max(valid) if valid else None}


def bound_sessions(capture, telemetry, record_hashes):
    """Verify opaque sessions against both immutable collector records and events."""
    require(capture.get('kind') == 'jeju-baseline-capture'
            and telemetry.get('kind') == 'jeju-baseline-collection-telemetry', 'Invalid capture/telemetry kind')
    require(HASH.fullmatch(capture.get('planSha256', '')) is not None
            and capture.get('planSha256') == telemetry.get('planSha256'), 'Plan binding mismatch')
    require(capture.get('requestBinding') == telemetry.get('requestBinding')
            and capture.get('requestBinding', {}).get('requestMode') == 'langflow-v2', 'V2 request binding required')
    events = telemetry['events']
    require(isinstance(events, list) and telemetry['attemptedCases'] == len(events), 'Attempt count mismatch')
    records = {record['caseId']: record for record in capture['records']}
    require(len(records) == len(capture['records'])
            and len({event['caseId'] for event in events}) == len(events), 'Duplicate case identity')
    require(set(event['caseId'] for event in events)
            == {key for key, record in records.items() if record.get('error', {}).get('code') != 'not_executed'},
            'Attempted records and events differ')
    entries = []
    for event in events:
        record = records[event['caseId']]
        binding = record.get('runtimeRequest', {})
        require(record_hashes.get(event['caseId']) == event.get('captureRecordSha256')
                and HASH.fullmatch(event.get('captureRecordSha256', '')) is not None
                and canonical_sha(binding) == event.get('runtimeRequestSha256'), 'Attempt binding hash mismatch')
        session = event['sessionId']
        require(isinstance(session, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', session) is not None
                and binding.get('requestId') == session, 'Session/request identity mismatch')
        reported_session = record.get('raw', {}).get('session_id')
        require(reported_session is None or reported_session == session, 'Returned session identity mismatch')
        entries.append({'caseId': event['caseId'], 'sessionId': session})
    require(len({entry['sessionId'] for entry in entries}) == len(entries), 'Session reused across cases')
    return entries


def build_audit(*, capture, telemetry, record_hashes, db, flow_artifact_bytes, flow_id,
                catalog_readiness, expected_model, runtime_versions, collected_at):
    """Read only metadata/attributes; raw span inputs, outputs and errors are never selected."""
    entries = bound_sessions(capture, telemetry, record_hashes)
    normalized_flow_id = uuid.UUID(flow_id).hex
    artifact = json.loads(flow_artifact_bytes)
    require(isinstance(artifact.get('data'), dict), 'Invalid flow artifact')
    require(isinstance(expected_model, str) and re.fullmatch(r'[a-zA-Z0-9._-]{1,120}', expected_model) is not None,
            'Invalid expected model identifier')
    require(set(runtime_versions) == {'langflowVersion', 'googleAdapterVersion'}
            and all(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', value) for value in runtime_versions.values()),
            'Invalid runtime version observation')
    require(isinstance(collected_at, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', collected_at), 'Invalid audit time')
    ready = catalog_readiness
    require(ready.get('ready') is True and ready.get('catalogSchemaVersion') == '2.0'
            and nonnegative_int(ready.get('placeCount')) and isinstance(ready.get('dataVersion'), str)
            and re.fullmatch(r'sha256:[a-f0-9]{64}', ready['dataVersion']), 'Invalid catalog readiness observation')
    db.row_factory = sqlite3.Row
    row = db.execute('SELECT data FROM flow WHERE id=?', (normalized_flow_id,)).fetchone()
    require(row is not None and json.loads(row['data']) == artifact['data'], 'Registered flow differs from artifact')
    cases, all_model, all_tools, all_traces = [], [], [], []
    stored = {'user': 0, 'validatedFinal': 0, 'rawAgent': 0, 'otherMachine': 0, 'other': 0}
    for entry in entries:
        traces = list(db.execute('SELECT id,flow_id,status,start_time,end_time,total_latency_ms,total_tokens FROM trace WHERE session_id=? ORDER BY start_time', (entry['sessionId'],)))
        require(all(trace['flow_id'] == normalized_flow_id for trace in traces), 'Session reused by another flow')
        spans = [span for trace in traces for span in db.execute('SELECT name,span_type,status,latency_ms,attributes FROM span WHERE trace_id=? ORDER BY start_time', (trace['id'],))]
        model = [span for span in spans if span['span_type'] == 'llm']
        tools = [span for span in spans if span['span_type'] == 'tool']
        all_model.extend(model); all_tools.extend(tools); all_traces.extend(traces)
        cases.append({'caseId': entry['caseId'], 'sessionId': entry['sessionId'],
                      'traceIds': [trace['id'] for trace in traces], 'model': count_status(model), 'tools': count_status(tools),
                      'traceLatency': latency_stats([trace['total_latency_ms'] for trace in traces]),
                      'unfinishedTraces': sum(trace['end_time'] is None for trace in traces)})
        for message in db.execute('SELECT sender,sender_name,properties FROM message WHERE session_id=?', (entry['sessionId'],)):
            source = (json.loads(message['properties'] or '{}').get('source') or {}).get('id')
            if message['sender'] == 'User': stored['user'] += 1
            elif source == 'Agent-jejuV2' or message['sender_name'] == 'Agent': stored['rawAgent'] += 1
            elif message['sender'] == 'Machine' and source == 'JejuFinalValidation-v2': stored['validatedFinal'] += 1
            elif message['sender'] == 'Machine': stored['otherMachine'] += 1
            else: stored['other'] += 1
    active_jobs = db.execute("SELECT count(*) FROM job WHERE status IN ('in_progress','pending','queued')").fetchone()[0]
    model_summary = count_status(all_model)
    attributes = [json.loads(span['attributes'] or '{}') for span in all_model]
    model_summary.update({
        'expectedModel': expected_model,
        'expectedModelCalls': sum(value.get('gen_ai.response.model') == expected_model for value in attributes),
        'otherModelCalls': sum(value.get('gen_ai.response.model') not in (None, expected_model) for value in attributes),
        'missingModelCalls': sum(value.get('gen_ai.response.model') is None for value in attributes),
        'missingProviderCalls': sum(value.get('gen_ai.provider.name') is None for value in attributes),
    })
    for direction in ('input', 'output'):
        observed = [value.get('gen_ai.usage.' + direction + '_tokens') for value in attributes]
        valid = [value for value in observed if nonnegative_int(value)]
        model_summary[direction + 'Tokens'] = sum(valid) if valid else None
        model_summary[direction + 'UsageMissingCalls'] = len(observed) - len(valid)
    tool_summary = count_status(all_tools)
    tool_summary.update({'byName': {name: sum(span['name'] == name for span in all_tools) for name in TOOL_NAMES},
                         'otherToolCalls': sum(span['name'] not in TOOL_NAMES for span in all_tools)})
    aggregate = {
        'schemaVersion': '1.0', 'kind': 'jeju-v2-runtime-audit', 'collectedAt': collected_at,
        'flow': {'artifactCanonicalSha256': canonical_sha(artifact),
                 'artifactByteSha256': hashlib.sha256(flow_artifact_bytes).hexdigest(), 'registeredDataMatchesArtifact': True},
        'catalog': {'schemaVersion': ready['catalogSchemaVersion'], 'placeCount': ready['placeCount'], 'dataVersion': ready['dataVersion']},
        'runtime': dict(runtime_versions),
        'sessions': {'attempted': len(entries), 'traced': sum(bool(case['traceIds']) for case in cases),
                     'missingTrace': sum(not case['traceIds'] for case in cases),
                     'multipleTraces': sum(len(case['traceIds']) > 1 for case in cases),
                     'unfinishedTraces': sum(case['unfinishedTraces'] for case in cases)},
        'model': model_summary, 'tools': tool_summary, 'storedMessages': stored, 'activeJobs': active_jobs, 'costUsd': None,
    }
    return {'aggregate': aggregate, 'perCase': cases,
            'measurements': {'traceLatency': latency_stats([trace['total_latency_ms'] for trace in all_traces]),
                             'modelLatency': latency_stats([span['latency_ms'] for span in all_model]),
                             'toolLatency': latency_stats([span['latency_ms'] for span in all_tools]),
                             'expectedProviderCalls': sum(value.get('gen_ai.provider.name') == 'google' for value in attributes),
                             'otherProviderCalls': sum(value.get('gen_ai.provider.name') not in (None, 'google') for value in attributes)},
            'measurementScope': 'persisted_trace_spans_not_provider_billing_or_hidden_retry_count'}


def write_exclusive(path, value, mode):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def bounded_read(path, limit):
    require(path.is_file() and path.stat().st_size <= limit, 'Evidence file exceeds size limit')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Evidence file grew beyond size limit')
    return data


def capture_record_hashes(capture_bytes):
    """Reuse the JS canonical function, including exact floating-point formatting.

    The immutable bytes already read by Python go over stdin; the only argument
    is the resolved local canonicalizer module. No shell, network, or raw output.
    """
    module = Path(__file__).resolve().parents[1] / 'jeju-mcp/src/evaluation-rng.mjs'
    program = """import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {canonicalSha256} = await import(pathToFileURL(process.argv[1]).href);
const capture = JSON.parse(readFileSync(0, 'utf8'));
if (!Array.isArray(capture.records) || capture.records.length > 192) process.exit(2);
process.stdout.write(JSON.stringify(Object.fromEntries(capture.records.map(r => [r.caseId, canonicalSha256(r)]))));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', program, str(module.resolve())],
                            input=capture_bytes, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=30, check=True)
    require(len(result.stdout) <= 64 * 1024, 'Unexpected canonicalizer output size')
    hashes = json.loads(result.stdout)
    require(isinstance(hashes, dict) and len(hashes) <= 192
            and all(isinstance(value, str) and HASH.fullmatch(value) for value in hashes.values()), 'Invalid canonicalizer output')
    return hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('capture', 'telemetry', 'database', 'flow-artifact', 'catalog-readiness', 'details', 'report'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--flow-id', required=True)
    parser.add_argument('--model-id', required=True)
    args = parser.parse_args(argv)
    for path in (args.capture, args.telemetry, args.database, args.catalog_readiness, args.details):
        require('.local' in path.resolve().parts, 'Private inputs/details must remain in .local')
    require(not args.details.exists() and not args.report.exists(), 'Audit outputs already exist')
    capture_bytes = bounded_read(args.capture, 32 * 1024 * 1024)
    telemetry_bytes = bounded_read(args.telemetry, 32 * 1024 * 1024)
    artifact_bytes = bounded_read(args.flow_artifact, 4 * 1024 * 1024)
    readiness_bytes = bounded_read(args.catalog_readiness, 8 * 1024)
    record_hashes = capture_record_hashes(capture_bytes)
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        result = build_audit(capture=json.loads(capture_bytes), telemetry=json.loads(telemetry_bytes), record_hashes=record_hashes, db=db,
                             flow_artifact_bytes=artifact_bytes, flow_id=args.flow_id,
                             catalog_readiness=json.loads(readiness_bytes), expected_model=args.model_id,
                             runtime_versions={'langflowVersion': importlib.metadata.version('langflow'),
                                               'googleAdapterVersion': importlib.metadata.version('langchain-google-genai')},
                             collected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
    result['captureFileSha256'] = hashlib.sha256(capture_bytes).hexdigest()
    result['telemetryFileSha256'] = hashlib.sha256(telemetry_bytes).hexdigest()
    write_exclusive(args.details, result, 0o600)
    write_exclusive(args.report, result['aggregate'], 0o644)
    print(json.dumps({'status': 'audited', 'attempted': result['aggregate']['sessions']['attempted'],
                      'modelSpans': result['aggregate']['model']['calls'], 'toolSpans': result['aggregate']['tools']['calls']}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError):
        print('Runtime audit failed: missing or inconsistent local evidence; no input contents printed.', file=sys.stderr)
        raise SystemExit(1) from None
