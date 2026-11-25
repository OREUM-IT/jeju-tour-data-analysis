#!/usr/bin/env python3
"""Remove old raw text of Gateway runs from the Langflow DB, keeping what the metrics SQL reads.

Opt-in and dry-run by default: without --execute it opens a read-only transaction and prints
counts only. With --execute, one transaction (statement and lock timeouts) touches only rows of
Gateway sessions (session id gw-...) older than --older-than-days (default 30, minimum 1):

- span rows of those traces: inputs removed, outputs replaced by a fixed marker (the final
  validation span keeps only "finalStatus": accepted|blocked), error text replaced by "redacted",
  attributes reduced to the model name, provider, operation and token counts. Names, types,
  status, times, latency and the trace rows themselves stay, so deploy/agent-run-metrics.sql
  reports the same runs, outcomes, latency, model calls and tokens.
- message rows of those sessions (the request JSON with survey text, final output, errors),
  transaction rows of those sessions and vertex_build rows of those traces: deleted.

It verifies inside the same transaction that no raw text of the selected rows is left and rolls
back otherwise. Newer rows and every non-Gateway session are never touched. It never prints row
contents, session IDs or credentials, never contacts a model provider and restarts nothing.
Runs psql inside the langflow-db container with the same role as the metrics SQL. When an
--execute times out, is interrupted (Ctrl+C) or ends with unreadable output, it reports
changed: "unknown"; a dry run then shows what is left. Run --execute while no web submissions
are in flight: Langflow's own retention sweep deletes old rows of the same tables.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
DEFAULT_DAYS = 30
MAX_DAYS = 3650
FINAL_SPAN = 'Jeju Final Validation'
KEEP_ATTRIBUTES = ('gen_ai.response.model', 'gen_ai.provider.name', 'gen_ai.operation.name',
                   'gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens')
MARKER = '{"redacted":true}'
FINAL_MARKERS = {status: '{"redacted":true,"finalStatus":"%s"}' % status for status in ('accepted', 'blocked')}
COUNTS = ('old_traces', 'kept_newer_traces', 'spans_with_text', 'messages', 'transactions', 'vertex_builds')
PSQL = ('PGPASSWORD="$LANGFLOW_DB_PASSWORD" psql -h 127.0.0.1 -U langflow_app -d langflow '
        '-X -q -A -t -F "|" -v ON_ERROR_STOP=1')


def quoted(values):
    return ', '.join("'" + value + "'" for value in values)


def render(days, execute):
    """The complete psql script. days is a validated integer; nothing else varies."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise ValueError('REDACTION_DAYS_INVALID')
    old = f"t.session_id LIKE 'gw-%' AND t.start_time < now() - interval '{days} days'"
    markers = quoted((MARKER, *FINAL_MARKERS.values()))
    raw = (f"(sp.inputs IS NOT NULL\n"
           f"    OR (sp.outputs IS NOT NULL AND sp.outputs::text NOT IN ({markers}))\n"
           f"    OR (sp.error IS NOT NULL AND sp.error <> 'redacted')\n"
           f"    OR CASE WHEN json_typeof(sp.attributes) = 'object' THEN EXISTS (SELECT 1 FROM json_object_keys(sp.attributes) k\n"
           f"      WHERE k NOT IN ({quoted(KEEP_ATTRIBUTES)})) ELSE sp.attributes IS NOT NULL END)")
    message = f"m.session_id LIKE 'gw-%' AND m.\"timestamp\" < now() - interval '{days} days'"
    transaction = f"x.session_id LIKE 'gw-%' AND x.\"timestamp\" < now() - interval '{days} days'"
    lines = [
        '\\set ON_ERROR_STOP on',
        '\\set VERBOSITY sqlstate',
        "SET statement_timeout = '60s';",
        "SET lock_timeout = '5s';",
        "SET TIME ZONE 'UTC';",
        'BEGIN;' if execute else 'BEGIN READ ONLY;',
        f"""SELECT 'cutoff_utc', to_char(now() - interval '{days} days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');""",
        f"SELECT 'old_traces', count(*) FROM trace t WHERE {old};",
        f"SELECT 'kept_newer_traces', count(*) FROM trace t WHERE t.session_id LIKE 'gw-%' "
        f"AND t.start_time >= now() - interval '{days} days';",
    ]
    if not execute:
        lines += [
            f"SELECT 'spans_with_text', count(*) FROM span sp JOIN trace t ON t.id = sp.trace_id WHERE {old}\n  AND {raw};",
            f"SELECT 'messages', count(*) FROM message m WHERE {message};",
            f"SELECT 'transactions', count(*) FROM \"transaction\" x WHERE {transaction};",
            f"SELECT 'vertex_builds', count(*) FROM vertex_build v JOIN trace t ON t.id = v.job_id WHERE {old};",
            'ROLLBACK;',
        ]
        return '\n'.join(lines) + '\n'
    # Only the final validation span's own output is read, and only for its fixed blocked prefix.
    outputs = (f"CASE WHEN sp.outputs IS NULL THEN NULL\n"
               f"    WHEN sp.outputs::text IN ({markers}) THEN sp.outputs\n"
               f"    WHEN sp.parent_span_id IS NOT NULL OR sp.name <> '{FINAL_SPAN}' THEN '{MARKER}'::json\n"
               f"    WHEN sp.outputs -> 'message' ->> 'text' LIKE '{{\"status\":\"blocked\"%' THEN '{FINAL_MARKERS['blocked']}'::json\n"
               f"    WHEN sp.outputs -> 'message' ->> 'text' IS NOT NULL THEN '{FINAL_MARKERS['accepted']}'::json\n"
               f"    ELSE '{MARKER}'::json END")
    attributes = (f"CASE WHEN json_typeof(sp.attributes) = 'object' THEN (SELECT coalesce(json_object_agg(a.key, a.value), '{{}}'::json)\n"
                  f"    FROM json_each(sp.attributes) a WHERE a.key IN ({quoted(KEEP_ATTRIBUTES)})) ELSE '{{}}'::json END")
    lines += [
        f"WITH changed AS (UPDATE span sp SET inputs = NULL,\n  outputs = {outputs},\n"
        f"  error = CASE WHEN sp.error IS NULL THEN NULL ELSE 'redacted' END,\n  attributes = {attributes}\n"
        f"  FROM trace t WHERE t.id = sp.trace_id AND {old}\n  AND {raw} RETURNING 1)\n"
        f"SELECT 'spans_with_text', count(*) FROM changed;",
        f"WITH removed AS (DELETE FROM message m WHERE {message} RETURNING 1) SELECT 'messages', count(*) FROM removed;",
        f"WITH removed AS (DELETE FROM \"transaction\" x WHERE {transaction} RETURNING 1) "
        f"SELECT 'transactions', count(*) FROM removed;",
        f"WITH removed AS (DELETE FROM vertex_build v USING trace t WHERE t.id = v.job_id AND {old} RETURNING 1) "
        f"SELECT 'vertex_builds', count(*) FROM removed;",
        f"SELECT NOT EXISTS (SELECT 1 FROM span sp JOIN trace t ON t.id = sp.trace_id WHERE {old}\n  AND {raw})\n"
        f"  AND NOT EXISTS (SELECT 1 FROM message m WHERE {message})\n"
        f"  AND NOT EXISTS (SELECT 1 FROM \"transaction\" x WHERE {transaction})\n"
        f"  AND NOT EXISTS (SELECT 1 FROM vertex_build v JOIN trace t ON t.id = v.job_id WHERE {old}) AS clean \\gset",
        '\\if :clean',
        'COMMIT;',
        "SELECT 'committed', 1;",
        '\\else',
        'ROLLBACK;',
        "SELECT 'committed', 0;",
        '\\endif',
    ]
    return '\n'.join(lines) + '\n'


def compose_command():
    compose = ['docker', 'compose', '-p', os.environ.get('COMPOSE_PROJECT_NAME', 'oreumit-bootstrap')]
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml'):
        compose += ['-f', str(ROOT / name)]
    return compose + ['exec', '-T', 'langflow-db', 'sh', '-eu', '-c', PSQL]


def parse(stdout, execute):
    """Accept only the fixed label|value lines this script prints; anything else is withheld."""
    values = {}
    for line in stdout.splitlines():
        label, separator, value = line.partition('|')
        if (not separator or label in values or label not in ('cutoff_utc', 'committed', *COUNTS)
                or not (re.fullmatch(r'\d{1,12}', value) if label != 'cutoff_utc'
                        else re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value))):
            raise ValueError('REDACTION_OUTPUT_UNEXPECTED')
        values[label] = value
    expected = {'cutoff_utc', *COUNTS} | ({'committed'} if execute else set())
    if set(values) != expected or (execute and values['committed'] not in ('0', '1')):
        raise ValueError('REDACTION_OUTPUT_UNEXPECTED')
    return values


def redact(days, execute, *, runner=None):
    # Four writes and one check of at most 60 s each fit well inside this limit.
    result = (runner or subprocess.run)(compose_command(), input=render(days, execute).encode(),
                                         capture_output=True, timeout=600, check=False)
    if result.returncode:
        # VERBOSITY=sqlstate: psql reports only the five-character SQLSTATE, never row data.
        codes = re.findall(rb'^(?:psql:[^\n]*?)?ERROR:\s+([0-9A-Z]{5})\s*$', result.stderr[-4096:], re.M)
        failure = ValueError('REDACTION_SQL_FAILED')
        failure.sqlstate = codes[-1].decode() if codes else None
        raise failure
    try:
        values = parse(result.stdout.decode('utf-8', 'replace'), execute)
    except ValueError:
        # psql ran the whole script, so an --execute may already have committed.
        failure = ValueError('REDACTION_OUTPUT_UNEXPECTED')
        failure.changed = 'unknown' if execute else False
        raise failure from None
    counts = {label: int(values[label]) for label in COUNTS}
    summary = {'olderThanDays': days, 'cutoffUtc': values['cutoff_utc'],
               'gatewayTraces': {'older': counts['old_traces'], 'keptNewer': counts['kept_newer_traces']},
               'modelExecuted': False, 'productionReady': False}
    found = {'spansWithText': counts['spans_with_text'], 'messages': counts['messages'],
             'transactions': counts['transactions'], 'vertexBuilds': counts['vertex_builds']}
    if not execute:
        return {'status': 'dry-run', **summary, 'toRedact': found, 'changed': False}
    if values['committed'] != '1':
        raise ValueError('REDACTION_VERIFY_FAILED')
    return {'status': 'redacted', **summary, 'redacted': found, 'changed': any(found.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--older-than-days', type=int, default=DEFAULT_DAYS, metavar='N',
                        help=f'Gateway runs started more than N days ago (default {DEFAULT_DAYS}, minimum 1).')
    parser.add_argument('--execute', action='store_true', help='Change the rows. Without it, counts only.')
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.older_than_days <= MAX_DAYS:
        parser.error(f'--older-than-days must be between 1 and {MAX_DAYS}')
    try:
        print(json.dumps(redact(arguments.older_than_days, arguments.execute)))
        return 0
    except subprocess.TimeoutExpired:
        # psql may still be running in the container; whether it committed is unknown until a dry run.
        print(json.dumps({'status': 'failed', 'code': 'REDACTION_TIMEOUT', 'sqlstate': None,
                          'changed': 'unknown' if arguments.execute else False, 'modelExecuted': False}))
        return 1
    except KeyboardInterrupt:
        # Ctrl+C stops the docker client, not necessarily psql in the container: as with a timeout,
        # whether an --execute committed is unknown until a dry run.
        print(json.dumps({'status': 'failed', 'code': 'REDACTION_INTERRUPTED', 'sqlstate': None,
                          'changed': 'unknown' if arguments.execute else False, 'modelExecuted': False}))
        return 130
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'REDACTION_[A-Z_]{1,60}', str(error)) \
            else 'REDACTION_FAILED'
        # ON_ERROR_STOP ends psql before COMMIT, so a failed psql run leaves every row as it was; only an
        # unreadable output after psql finished can hide a commit (changed: unknown).
        changed = getattr(error, 'changed', False)
        print(json.dumps({'status': 'failed', 'code': code, 'sqlstate': getattr(error, 'sqlstate', None),
                          'changed': changed if changed in (False, 'unknown') else False, 'modelExecuted': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
