"""Static and fake-command contracts of the opt-in redaction tool; no database, Docker, AWS or model calls.

These tests pin what the generated psql script may read, write and print. That the script removes the
raw text while deploy/agent-run-metrics.sql keeps the same numbers was checked once on a scratch
PostgreSQL 16 copy of real Langflow 1.13.3 rows (docs/32); this suite and CI do not repeat that.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('redact_agent_history', ROOT / 'deploy/redact-agent-history.py')
redact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact)
METRICS = (ROOT / 'deploy/agent-run-metrics.sql').read_text(encoding='utf-8')
GATEWAY = (ROOT / 'deploy/langflow/gateway-bootstrap.py').read_text(encoding='utf-8')
OLD = "t.session_id LIKE 'gw-%' AND t.start_time < now() - interval '30 days'"
DRY_OUTPUT = ('cutoff_utc|2026-10-25T16:01:16Z\nold_traces|8\nkept_newer_traces|12\nspans_with_text|114\n'
              'messages|11\ntransactions|44\nvertex_builds|42\n')


def statements(script):
    """psql meta-commands on their own lines, SQL statements split on the terminating semicolon."""
    meta = [line for line in script.splitlines() if line.startswith('\\')]
    sql = '\n'.join(line for line in script.splitlines() if not line.startswith('\\')) + '\n'
    return meta, [part.strip() for part in sql.split(';\n') if part.strip()]


def completed(stdout='', returncode=0, stderr=b''):
    return subprocess.CompletedProcess([], returncode, stdout.encode(), stderr)


class RedactAgentHistoryTests(unittest.TestCase):
    def test_days_are_validated_and_only_that_integer_varies(self):
        for days in (0, -1, redact.MAX_DAYS + 1, True, '30', 1.5, None):
            with self.subTest(days=days), self.assertRaisesRegex(ValueError, '^REDACTION_DAYS_INVALID$'):
                redact.render(days, False)
        for execute in (False, True):
            script = redact.render(7, execute)
            self.assertEqual(script, redact.render(30, execute).replace("interval '30 days'", "interval '7 days'"))
            self.assertNotIn("interval '30 days'", script)
        for argv in (['--older-than-days', '0'], ['--older-than-days', '3651'], ['--older-than-days', 'x'], ['--force']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                redact.main(argv)
            self.assertEqual(stop.exception.code, 2)

    def test_dry_run_is_a_read_only_transaction_that_prints_counts(self):
        script = redact.render(30, False)
        meta, sql = statements(script)
        self.assertEqual(meta, ['\\set ON_ERROR_STOP on', '\\set VERBOSITY sqlstate'])
        self.assertEqual(sql[:4], ["SET statement_timeout = '60s'", "SET lock_timeout = '5s'", "SET TIME ZONE 'UTC'",
                                   'BEGIN READ ONLY'])
        self.assertEqual(sql[-1], 'ROLLBACK')
        words = set(re.findall(r'[A-Z_]+', re.sub(r"'[^']*'", "''", script.upper())))
        for forbidden in ('UPDATE', 'DELETE', 'INSERT', 'COMMIT', 'TRUNCATE', 'DROP', 'ALTER', 'CREATE', 'COPY'):
            self.assertNotIn(forbidden, words)
        labels = [re.match(r"SELECT '([a-z_]+)', ", statement).group(1) for statement in sql[4:-1]]
        self.assertEqual(labels, ['cutoff_utc', *redact.COUNTS])
        for statement in sql[5:-1]:
            self.assertRegex(statement, r"^SELECT '[a-z_]+', count\(\*\) FROM ")

    def test_writes_touch_only_old_gateway_rows_of_four_tables_and_never_traces(self):
        _meta, sql = statements(redact.render(30, True))
        writes = [statement for statement in sql if re.search(r'\b(UPDATE|DELETE|INSERT)\b', statement)]
        self.assertEqual([re.search(r'\b(UPDATE|DELETE FROM) ("?\w+"?)', statement).group(0) for statement in writes],
                         ['UPDATE span', 'DELETE FROM message', 'DELETE FROM "transaction"', 'DELETE FROM vertex_build'])
        self.assertIn(f'FROM trace t WHERE t.id = sp.trace_id AND {OLD}', writes[0])
        self.assertIn("WHERE m.session_id LIKE 'gw-%' AND m.\"timestamp\" < now() - interval '30 days' RETURNING 1", writes[1])
        self.assertIn("WHERE x.session_id LIKE 'gw-%' AND x.\"timestamp\" < now() - interval '30 days' RETURNING 1", writes[2])
        self.assertIn(f'USING trace t WHERE t.id = v.job_id AND {OLD} RETURNING 1', writes[3])
        script = redact.render(30, True)
        self.assertNotIn('NOT LIKE', script)
        self.assertNotIn('UPDATE trace', script)
        self.assertNotIn('DELETE FROM trace', script)
        self.assertNotIn('DELETE FROM span', script)
        self.assertEqual(len(re.findall(r'\b(?:UPDATE|DELETE FROM)\b', script)), 4)
        # The span update keeps names, types, status, times and latency: it sets only these four columns.
        self.assertEqual(re.findall(r'(?:SET |,\n  )(\w+) = ', writes[0]), ['inputs', 'outputs', 'error', 'attributes'])

    def test_raw_text_is_never_selected_and_only_the_final_span_output_is_read(self):
        script = redact.render(30, True)
        _meta, sql = statements(script)
        for statement in sql:
            outer = statement.rsplit('\nSELECT ', 1)[-1] if statement.startswith('WITH ') else statement
            if outer.startswith('SELECT '):
                self.assertRegex(outer, r"^SELECT ('[a-z_]+', (count\(\*\)|to_char\(now\(\) - interval '30 days', "
                                        r"'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'\)|[01])|NOT EXISTS )")
        # Parsing a raw output happens only after the CASE has reached the final validation component span.
        outputs = script[script.index('outputs = CASE'):script.index('error = CASE')]
        self.assertLess(outputs.index("sp.name <> 'Jeju Final Validation' THEN '{\"redacted\":true}'::json"),
                        outputs.index("sp.outputs -> 'message' ->> 'text'"))
        self.assertEqual(script.count("sp.outputs -> 'message' ->> 'text'"), 2)
        self.assertEqual(script.count("->> 'text' LIKE '{\"status\":\"blocked\"%'"), 1)
        # Raw inputs and error texts are only tested for presence or compared with the fixed marker.
        for column in ('inputs', 'error'):
            for use in re.findall(rf'sp\.{column}\b.{{0,16}}', script):
                self.assertRegex(use, rf"^sp\.{column} (IS NOT NULL|IS NULL|<> 'redacted')")

    def test_commit_happens_only_after_the_same_transaction_finds_no_raw_text(self):
        script = redact.render(30, True)
        meta, sql = statements(script)
        self.assertEqual(meta, ['\\set ON_ERROR_STOP on', '\\set VERBOSITY sqlstate', '\\if :clean', '\\else', '\\endif'])
        self.assertEqual(sql[:4], ["SET statement_timeout = '60s'", "SET lock_timeout = '5s'", "SET TIME ZONE 'UTC'", 'BEGIN'])
        self.assertEqual(script.count('COMMIT;'), 1)
        self.assertEqual(script.count('BEGIN'), 1)
        tail = script[script.index('AS clean \\gset'):]
        self.assertEqual(tail, "AS clean \\gset\n\\if :clean\nCOMMIT;\nSELECT 'committed', 1;\n\\else\nROLLBACK;\n"
                               "SELECT 'committed', 0;\n\\endif\n")
        check = script[script.rindex('SELECT NOT EXISTS'):]
        self.assertEqual(check.count('NOT EXISTS'), 4)
        # The verification repeats exactly the selection the writes used.
        raw = redact.render(30, False)
        dry_spans = raw[raw.index("SELECT 'spans_with_text'"):]
        self.assertIn(dry_spans[dry_spans.index('AND (sp.inputs'):dry_spans.index(';\n')], check)

    def test_markers_and_kept_attributes_match_what_the_metrics_sql_reads(self):
        self.assertIn('\'{"redacted":true,"finalStatus":"blocked"}\'', METRICS)
        self.assertIn("LIKE '{\"redacted\":true,\"finalStatus\":\"%'", METRICS)
        self.assertIn("LIKE '{\"redacted\":true%'", METRICS)
        self.assertEqual(redact.MARKER, '{"redacted":true}')
        self.assertEqual(redact.FINAL_MARKERS, {'accepted': '{"redacted":true,"finalStatus":"accepted"}',
                                                'blocked': '{"redacted":true,"finalStatus":"blocked"}'})
        self.assertEqual(METRICS.count(f"sp.name = '{redact.FINAL_SPAN}'"), 2)
        self.assertIn(f"get('display_name') == '{redact.FINAL_SPAN}'", GATEWAY)
        read = set(re.findall(r"a ->> '(gen_ai\.[a-z_.]+)'", METRICS))
        self.assertEqual(read, {'gen_ai.response.model', 'gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens'})
        self.assertTrue(read <= set(redact.KEEP_ATTRIBUTES))

    def test_command_runs_psql_in_the_langflow_db_container_with_its_own_password_variable(self):
        with patch.dict('os.environ', {'COMPOSE_PROJECT_NAME': 'project-x'}):
            command = redact.compose_command()
        self.assertEqual(command[:4], ['docker', 'compose', '-p', 'project-x'])
        self.assertEqual([Path(command[i + 1]).name for i, value in enumerate(command) if value == '-f'],
                         ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml'])
        self.assertEqual(command[-7:-1], ['exec', '-T', 'langflow-db', 'sh', '-eu', '-c'])
        self.assertEqual(command[-1], 'PGPASSWORD="$LANGFLOW_DB_PASSWORD" psql -h 127.0.0.1 -U langflow_app -d langflow '
                                      '-X -q -A -t -F "|" -v ON_ERROR_STOP=1')

    def test_only_fixed_label_lines_are_accepted_and_summarized(self):
        runner = Mock(return_value=completed(DRY_OUTPUT))
        result = redact.redact(30, False, runner=runner)
        self.assertEqual(result, {'status': 'dry-run', 'olderThanDays': 30, 'cutoffUtc': '2026-10-25T16:01:16Z',
                                  'gatewayTraces': {'older': 8, 'keptNewer': 12}, 'modelExecuted': False,
                                  'productionReady': False, 'toRedact': {'spansWithText': 114, 'messages': 11,
                                  'transactions': 44, 'vertexBuilds': 42}, 'changed': False})
        self.assertEqual(runner.call_args.kwargs['input'], redact.render(30, False).encode())
        self.assertEqual(runner.call_args.kwargs['timeout'], 600)
        executed = redact.redact(30, True, runner=Mock(return_value=completed(DRY_OUTPUT + 'committed|1\n')))
        self.assertEqual((executed['status'], executed['changed'], executed['redacted']['messages']), ('redacted', True, 11))
        nothing = DRY_OUTPUT.replace('|114', '|0').replace('|11\n', '|0\n').replace('|44', '|0').replace('|42', '|0')
        self.assertIs(redact.redact(30, True, runner=Mock(return_value=completed(nothing + 'committed|1\n')))['changed'], False)
        with self.assertRaisesRegex(ValueError, '^REDACTION_VERIFY_FAILED$'):
            redact.redact(30, True, runner=Mock(return_value=completed(DRY_OUTPUT + 'committed|0\n')))
        for stdout in (DRY_OUTPUT + 'SURVEYMARK private text\n', DRY_OUTPUT + 'messages|3\n', DRY_OUTPUT.replace('|8\n', '|x\n'),
                       DRY_OUTPUT.replace('vertex_builds|42\n', ''), DRY_OUTPUT + 'committed|1\n'):
            with self.subTest(stdout=stdout[-30:]), self.assertRaisesRegex(ValueError, '^REDACTION_OUTPUT_UNEXPECTED$'):
                redact.redact(30, False, runner=Mock(return_value=completed(stdout)))

    def test_failures_print_one_fixed_json_line_with_at_most_a_sqlstate(self):
        # (outcome, code, sqlstate, changed after --execute, changed after a dry run, exit code)
        cases = [
            (completed('', 3, b'psql:<stdin>:12: ERROR:  57014\n'), 'REDACTION_SQL_FAILED', '57014', False, False, 1),
            (completed('', 3, b'psql:<stdin>:9: ERROR:  55P03\n'), 'REDACTION_SQL_FAILED', '55P03', False, False, 1),
            (completed('', 1, b'service "langflow-db" is not running\nSURVEYMARK private\n'), 'REDACTION_SQL_FAILED', None,
             False, False, 1),
            # psql finished the whole script, so an --execute may have committed before the output was rejected.
            (completed('SURVEYMARK private text\n'), 'REDACTION_OUTPUT_UNEXPECTED', None, 'unknown', False, 1),
            (completed(DRY_OUTPUT + 'committed|0\n'), 'REDACTION_VERIFY_FAILED', None, False, None, 1),
            (subprocess.TimeoutExpired('docker', 600), 'REDACTION_TIMEOUT', None, 'unknown', False, 1),
            # Ctrl+C is not an Exception: it still ends in the one fixed JSON line instead of a traceback.
            (KeyboardInterrupt(), 'REDACTION_INTERRUPTED', None, 'unknown', False, 130),
        ]
        for outcome, code, sqlstate, after_execute, after_dry_run, exit_code in cases:
            for argv, changed in ((['--execute'], after_execute), ([], after_dry_run)):
                if changed is None:
                    continue
                runner = Mock(side_effect=outcome) if isinstance(outcome, BaseException) else Mock(return_value=outcome)
                output = io.StringIO()
                with self.subTest(code=code, argv=argv), patch.object(redact.subprocess, 'run', runner), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(redact.main(argv), exit_code)
                self.assertEqual(json.loads(output.getvalue()), {'status': 'failed', 'code': code, 'sqlstate': sqlstate,
                                                                'changed': changed, 'modelExecuted': False})
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertEqual(errors.getvalue(), '')
                self.assertNotIn('SURVEYMARK', output.getvalue())
        output = io.StringIO()
        with patch.object(redact.subprocess, 'run', Mock(return_value=completed(DRY_OUTPUT))), contextlib.redirect_stdout(output):
            self.assertEqual(redact.main([]), 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry-run')


if __name__ == '__main__':
    unittest.main()
