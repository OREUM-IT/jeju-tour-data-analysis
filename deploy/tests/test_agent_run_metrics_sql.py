"""Static contracts of the read-only run-metrics SQL; no database, AWS, network or model calls.

These tests read the SQL text only. They pin what the file may read and return and how each
statistic is named, but they never execute it: that the numbers are right was compared against
scratch PostgreSQL 16 data written by Langflow 1.13.3 (docs/32) and is not re-run by this suite.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / 'deploy/agent-run-metrics.sql').read_text(encoding='utf-8')
DOCUMENT = (ROOT / 'docs/32-agent-gateway-tunnel.md').read_text(encoding='utf-8')
COMMAND = ("cd ~/oreumit-agent && sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml "
           "-f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow-db sh -eu -c "
           "'PGPASSWORD=\"$LANGFLOW_DB_PASSWORD\" psql -h 127.0.0.1 -U langflow_app -d langflow -P pager=off "
           "-v ON_ERROR_STOP=1' <<'SQL'\n")
OUTPUT_COLUMNS = {
    'traces', 'sessions', 'first_start_utc', 'last_start_utc', 'unfinished', 'without_spans', 'redacted',
    'sessions_without_trace',
    'kst_day', 'model', 'runs', 'accepted', 'blocked', 'agent_error', 'no_final', 'error_traces',
    'p50_ms', 'p95_ms', 'max_ms', 'model_calls', 'avg_model_calls', 'max_model_calls', 'failed_model_calls',
    'avg_tool_calls', 'input_tokens', 'output_tokens', 'avg_input_per_run', 'avg_output_per_run',
    'calls_without_usage', 'call_type', 'calls', 'failed'}


def statements():
    lines = [line for line in SQL.splitlines() if not line.startswith(('--', '\\echo'))]
    return [part.strip() for part in '\n'.join(lines).split(';') if part.strip()]


MODEL_LABEL = "coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')"
CALL_LABEL = ("CASE WHEN lower(sp.span_type::text) = 'tool' THEN 'tool'\n    ELSE 'model ' || coalesce(nullif("
              "sp.attributes::jsonb ->> 'gen_ai.response.model', ''), '(' || sp.name || ')') END AS call_type")
# Sessions that left messages but no trace row: a count, correlated to prefix-filtered messages only.
UNTRACED = ("(SELECT count(DISTINCT m.session_id) FROM message m WHERE m.session_id LIKE 'gw-%'\n"
            "    AND NOT EXISTS (SELECT 1 FROM trace t2 WHERE t2.session_id = m.session_id)) AS sessions_without_trace")
# Traces whose raw text deploy/redact-agent-history.py replaced: a fixed-prefix test, never a selected text.
REDACTED = ("count(*) FILTER (WHERE EXISTS (SELECT 1 FROM span s WHERE s.trace_id = t.id "
            "AND s.outputs::text LIKE '{\"redacted\":true%')) AS redacted")
# The only reads of a span output: the final validation component's fixed status prefixes or redaction marker.
FINAL = "CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce("
OUTPUT_TESTS = (
    """sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'""",
    "sp.outputs -> 'message' ->> 'text' IS NOT NULL",
    """sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'""",
    """sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%'""",
    """s.outputs::text LIKE '{"redacted":true%'""")
# Every shape a returned column may have. Arguments are named so that an aggregate cannot carry an id or a text.
RESULT_ITEMS = (
    r'kst_day', r'model', re.escape(CALL_LABEL), re.escape(UNTRACED), re.escape(REDACTED),
    r"count\(\*\) AS (?:traces|runs|calls)",
    r"count\(DISTINCT t\.session_id\) AS sessions",
    r"count\(\*\) FILTER \(WHERE t\.end_time IS NULL\) AS unfinished",
    r"count\(\*\) FILTER \(WHERE NOT EXISTS \(SELECT 1 FROM span s WHERE s\.trace_id = t\.id\)\) AS without_spans",
    r"count\(\*\) FILTER \(WHERE outcome = '(accepted|blocked|agent_error|no_final)'\) AS \1",
    r"count\(\*\) FILTER \(WHERE trace_status = 'error'\) AS error_traces",
    r"count\(\*\) FILTER \(WHERE lower\(sp\.status::text\) = 'error'\) AS failed",
    r"min\(t\.start_time\) AS first_start_utc", r"max\(t\.start_time\) AS last_start_utc",
    r"max\((?:ms|sp\.latency_ms)\) AS max_ms", r"max\(model_calls\) AS max_model_calls",
    r"sum\((model_calls|failed_model_calls|input_tokens|output_tokens|calls_without_usage)\) AS \1",
    r"round\(avg\((model|tool)_calls\), 2\) AS avg_\1_calls",
    r"round\(avg\((input|output)_tokens\)\) AS avg_\1_per_run",
    r"round\(percentile_cont\(0\.5\) WITHIN GROUP \(ORDER BY (?:ms|sp\.latency_ms)\)\) AS p50_ms",
    r"round\(percentile_cont\(0\.95\) WITHIN GROUP \(ORDER BY (?:ms|sp\.latency_ms)\)\) AS p95_ms")
# Columns of the Langflow tables that the file may touch, by table alias. Span inputs, message texts and
# names of senders or flows are absent; span outputs are reached only by the fixed tests in OUTPUT_TESTS.
TABLE_COLUMNS = {
    't': {'id', 'session_id', 'start_time', 'end_time', 'status', 'total_latency_ms'}, 't2': {'session_id'},
    'm': {'session_id'},
    'sp': {'trace_id', 'span_type', 'status', 'attributes', 'name', 'latency_ms', 'outputs', 'parent_span_id'},
    's': {'trace_id', 'outputs'}}
SESSION_ID_USES = (
    "t.session_id LIKE 'gw-%'", "m.session_id LIKE 'gw-%'", 'count(DISTINCT t.session_id) AS sessions',
    'count(DISTINCT m.session_id) FROM message m', 't2.session_id = m.session_id')


def code():
    return '\n'.join(statements())


def final_select(statement):
    """The outermost result list: from the last SELECT at column 0 to the next FROM at column 0."""
    start = [match.start() for match in re.finditer(r'^SELECT ', statement, re.M)][-1]
    return statement[start:statement.index('\nFROM ', start)]


class RunMetricsSqlTests(unittest.TestCase):
    def test_document_command_holds_exactly_this_file(self):
        self.assertTrue(SQL.endswith('\n') and '\nSQL\n' not in SQL and not SQL.startswith('SQL\n'))
        self.assertEqual(DOCUMENT.count(COMMAND + SQL + 'SQL\n```'), 1)
        # Same container, role and psql options as the confirmation SQL the user already ran on AWS
        # (the third command holds deploy/agent-message-store-check.sql).
        self.assertEqual(DOCUMENT.count(COMMAND), 3)

    def test_session_is_read_only_with_a_timeout_before_any_query(self):
        first = statements()
        self.assertEqual(first[:3], ['SET default_transaction_read_only = on', "SET statement_timeout = '15s'",
                                     "SET TIME ZONE 'UTC'"])
        # Langflow 1.13.3 stores trace times as timestamptz; kst_day and *_utc need a UTC session.
        self.assertEqual(first[3], 'SHOW TimeZone')
        self.assertTrue(all(statement.startswith(('SELECT ', 'WITH ')) for statement in first[4:]))
        self.assertEqual(len(first), 8)

    def test_no_writing_or_session_changing_statement(self):
        words = set(re.findall(r'[A-Za-z_]+', re.sub(r"'[^']*'", "''", SQL).upper()))
        for forbidden in ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE', 'CREATE', 'ALTER', 'DROP', 'COPY', 'GRANT',
                          'REVOKE', 'CALL', 'DO', 'EXECUTE', 'PREPARE', 'RESET', 'BEGIN', 'COMMIT', 'LOCK', 'VACUUM',
                          'NOTIFY', 'LISTEN', 'INTO', 'PG_SLEEP', 'DBLINK', 'LO_IMPORT', 'PG_READ_FILE'):
            self.assertNotIn(forbidden, words)
        self.assertEqual(SQL.count('SET '), 3)
        meta = [line.split()[0] for line in SQL.splitlines() if line.startswith('\\')]
        self.assertEqual(set(meta), {'\\echo'})  # no \copy, \o, \! or \set

    def test_only_gateway_sessions_are_read(self):
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ([a-z_0-9]+)', SQL)) - {'s', 'u', 'c', 'f', 'run'},
                         {'trace', 'span', 'message'})
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ((?:trace|span|message) \w+)', SQL)),
                         {'trace t', 'trace t2', 'message m', 'span sp', 'span s'})
        for statement in statements()[4:]:
            # Every trace and message read carries the prefix filter; spans are reached only through such a trace.
            self.assertEqual(len(re.findall(r'\b(?:FROM|JOIN) (?:trace t|message m)\b', statement)),
                             len(re.findall(r"\b(?:t|m)\.session_id LIKE 'gw-%'", statement)))
            self.assertEqual(len(re.findall(r'\b(?:FROM|JOIN) span (?:sp|s)\b', statement)),
                             len(re.findall(r'\b(?:t\.id = sp\.trace_id|s\.trace_id = t\.id)\b', statement)))
        # The second trace alias exists only to ask whether a prefix-filtered message session has a trace.
        self.assertEqual(SQL.count('trace t2'), 1)
        self.assertEqual(SQL.count(UNTRACED), 1)
        self.assertNotIn('%gw', SQL)

    def test_session_ids_are_used_only_to_filter_join_and_count(self):
        rest = code()
        for use in SESSION_ID_USES:
            self.assertIn(use, rest, use)
            rest = rest.replace(use, '')
        self.assertNotIn('session_id', rest)
        # The per-run outcome CTE is grouped by trace id, every other column is a boolean over the run's
        # top-level (component) spans, and the trace id is used only to join it to the trace.
        outcome = re.findall(r"f AS \(\n  SELECT sp\.trace_id,\n(.*?)\n  FROM span sp JOIN trace t ON t\.id = sp\.trace_id\n"
                             r"  WHERE t\.session_id LIKE 'gw-%' AND sp\.parent_span_id IS NULL GROUP BY 1\)", code(), re.S)
        self.assertEqual(len(outcome), 1)
        self.assertEqual(re.findall(r' AS (\w+)', outcome[0]), ['has_final', 'blocked', 'agent_error'])
        self.assertEqual(len(re.findall(r'^    bool_or\(', outcome[0], re.M)), 3)
        self.assertEqual(code().count('f.trace_id'), 1)
        self.assertNotIn('message m WHERE m.session_id LIKE \'gw-%\' GROUP BY', code())

    def test_only_listed_table_columns_are_read_and_message_text_only_for_the_blocked_prefix(self):
        seen = {}
        for alias, column in re.findall(r'(?<![\w.])(t2|sp|t|m|s)\.("?\w+"?)', code()):
            seen.setdefault(alias, set()).add(column)
        self.assertEqual(seen, TABLE_COLUMNS)
        self.assertNotIn('*', code().replace('count(*)', ''))  # no SELECT * or alias.*
        # No message text, sender or properties: messages are read only to count untraced sessions.
        for absent in ('"text"', 'm.text', 'properties', 'sender', 'category', 'inputs', 'content_blocks'):
            self.assertNotIn(absent, SQL)
        # Span outputs occur only in five fixed tests: two status prefixes of the final validation output,
        # its presence, and the redaction marker. None of them is a selected column.
        for test in OUTPUT_TESTS:
            self.assertEqual(SQL.count(test), 1, test)
        self.assertEqual(SQL.count('outputs'), len(OUTPUT_TESTS))
        self.assertEqual(SQL.count(FINAL), 2)
        # Outside casts and those tests, the word text does not occur: no text column reaches a result.
        rest = code().replace('::text', '')
        for test in OUTPUT_TESTS:
            rest = rest.replace(test.replace('::text', ''), '')
        self.assertEqual(re.findall(r'\btext\b', rest), [])
        # Span attributes are read through three fixed keys only; 'text' is the final output's key above.
        keys = re.findall(r"->> '([^']+)'", code())
        self.assertEqual(set(keys), {'gen_ai.response.model', 'gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens', 'text'})
        self.assertEqual(len(re.findall(r'\battributes\b', code())), len(re.findall(r'\battributes::jsonb\b', code())))

    def test_result_columns_are_aggregates_and_model_names_only(self):
        columns = set()
        for statement in statements()[4:]:
            outer = final_select(statement)[len('SELECT '):]
            items, depth, current = [], 0, ''
            for character in outer:
                depth += (character == '(') - (character == ')')
                if character == ',' and depth == 0:
                    items.append(current.strip()); current = ''
                else:
                    current += character
            items.append(current.strip())
            for item in items:
                # A result column is a group key, a model label, or an aggregate over a named number or time.
                self.assertTrue(any(re.fullmatch(shape, item) for shape in RESULT_ITEMS), item)
                columns.add(item.rsplit(' AS ', 1)[1] if ' AS ' in item else item)
        self.assertEqual(columns, OUTPUT_COLUMNS)
        self.assertEqual(SQL.count('GROUP BY 1, 2 ORDER BY 1, 2;'), 2)
        self.assertNotIn('"text" AS', SQL)
        # No aggregate anywhere in the file takes another argument than these numbers and times.
        self.assertEqual(set(re.findall(r'\b(?:min|max)\(([^()]*)\)', SQL)), {'t.start_time', 'ms', 'model_calls', 'sp.latency_ms'})
        self.assertEqual(set(re.findall(r'\bsum\(([^()]*)\)', SQL)),
                         {'model_calls', 'failed_model_calls', 'input_tokens', 'output_tokens', 'calls_without_usage'})
        self.assertEqual(set(re.findall(r'\bavg\(([^()]*)\)', SQL)), {'model_calls', 'tool_calls', 'input_tokens', 'output_tokens'})

    def test_model_label_is_the_recorded_model_name_or_the_call_kind_name(self):
        # Every expression named model: the per-call label, its per-run combination, or '-' for a run without calls.
        expressions = (f"string_agg(DISTINCT {MODEL_LABEL}, '+'\n    ORDER BY {MODEL_LABEL}) AS model",
                       f'    {MODEL_LABEL} AS model\n',
                       "string_agg(DISTINCT model, '+' ORDER BY model) FILTER (WHERE kind = 'llm') AS model")
        for expression in expressions:
            self.assertEqual(SQL.count(expression), 1, expression)
        self.assertEqual(SQL.count("AS kst_day, coalesce(c.model, '-') AS model,\n"), 2)
        self.assertEqual(len(re.findall(r' AS model\b', SQL)), 5)
        self.assertEqual(SQL.count('string_agg('), 2)
        self.assertEqual(SQL.count(CALL_LABEL), 1)
        self.assertEqual(SQL.count("(t.start_time + interval '9 hours')::date AS kst_day"), 2)
        self.assertEqual(len(re.findall(r' AS kst_day\b', SQL)), 2)

    def test_statistics_keep_their_documented_definitions(self):
        # Static pins of the definitions in docs/32; they do not replace running the SQL against a database.
        shares = re.findall(r'percentile_cont\(([0-9.]+)\) WITHIN GROUP \(ORDER BY ([a-z_.]+)\)\) AS p([0-9]+)_ms', SQL)
        self.assertEqual(sorted(shares), [('0.5', 'ms', '50'), ('0.5', 'sp.latency_ms', '50'),
                                          ('0.95', 'ms', '95'), ('0.95', 'sp.latency_ms', '95')])
        self.assertEqual(SQL.count('percentile_cont('), 4)
        self.assertEqual(SQL.count("CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'\n"
                                   "      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome"), 1)
        self.assertEqual(SQL.count(f"""bool_or({FINAL}sp.outputs::text LIKE '{{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final"""), 1)
        self.assertEqual(SQL.count(f"""bool_or({FINAL}sp.outputs::text = '{{"redacted":true,"finalStatus":"blocked"}}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{{"status":"blocked"%', false) END) AS blocked"""), 1)
        # A component span that ended with an error: lfx stores the same failure as an error message.
        self.assertEqual(SQL.count("bool_or(lower(sp.status::text) = 'error') AS agent_error"), 1)
        self.assertEqual(SQL.count("WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1)"), 1)
        self.assertEqual(SQL.count("t.total_latency_ms AS ms"), 1)
        for line in ("count(*) FILTER (WHERE kind = 'llm') AS model_calls,",
                     "count(*) FILTER (WHERE kind = 'llm' AND failed) AS failed_model_calls,",
                     "count(*) FILTER (WHERE kind = 'tool') AS tool_calls,",
                     "coalesce(sum(input_tokens) FILTER (WHERE kind = 'llm'), 0) AS input_tokens,",
                     "coalesce(sum(output_tokens) FILTER (WHERE kind = 'llm'), 0) AS output_tokens,",
                     "count(*) FILTER (WHERE kind = 'llm' AND (input_tokens IS NULL OR output_tokens IS NULL)) AS calls_without_usage,",
                     "lower(sp.status::text) = 'error' AS failed,"):
            self.assertEqual(SQL.count(line), 1, line)
        # Only model and tool calls are counted: the agent component span repeats its children's tokens.
        self.assertEqual(SQL.count("lower(sp.span_type::text) IN ('llm', 'tool')"), 2)
        self.assertEqual(SQL.count("lower(sp.span_type::text) = 'llm'"), 1)
        self.assertEqual((SQL.count('span_type'), SQL.count('lower(sp.span_type::text)')), (6, 6))
        self.assertEqual(SQL.count('parent_span_id'), 1)

    def test_token_casts_are_guarded_and_enum_labels_are_case_insensitive(self):
        self.assertEqual(len(re.findall(r"~ '\^\[0-9\]\{1,15\}\$'\s+THEN \(a ->> 'gen_ai\.usage\.(?:input|output)_tokens'\)::bigint", SQL)), 2)
        self.assertEqual(SQL.count('::bigint'), 2)
        self.assertNotIn("span_type = '", SQL)
        self.assertNotIn("status = '", SQL.replace("trace_status = 'error'", ''))
        self.assertEqual(SQL.count("interval '9 hours'"), 2)


if __name__ == '__main__':
    unittest.main()
