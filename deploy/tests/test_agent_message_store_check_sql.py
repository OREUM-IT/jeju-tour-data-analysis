"""Static contracts of the read-only post-apply message-store check SQL; no database, AWS, network or model calls.

These tests read the SQL text only. They pin what the file may read and return, that its run outcomes and
its redaction marker are the metrics SQL's text, how rows are put in the after (t) and before (f) periods,
and how the boundary, the "cannot judge" state and the verdict are formed, but they never execute it: that
the numbers are right was compared against a scratch PostgreSQL 16 copy of rows written by Langflow 1.13.3
(docs/32) and is not re-run by this suite.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / 'deploy/agent-message-store-check.sql').read_text(encoding='utf-8')
METRICS = (ROOT / 'deploy/agent-run-metrics.sql').read_text(encoding='utf-8')
DOCUMENT = (ROOT / 'docs/32-agent-gateway-tunnel.md').read_text(encoding='utf-8')
GATEWAY = (ROOT / 'deploy/langflow/gateway-bootstrap.py').read_text(encoding='utf-8')
COMMAND = ("cd ~/oreumit-agent && sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml "
           "-f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow-db sh -eu -c "
           "'PGPASSWORD=\"$LANGFLOW_DB_PASSWORD\" psql -h 127.0.0.1 -U langflow_app -d langflow -P pager=off "
           "-v ON_ERROR_STOP=1' <<'SQL'\n")
NAME = '제주 관광 Agent v2 — Gateway 제한 실행'
FLOW_COUNT = f"SELECT count(*) AS flows, max(updated_at) AS flow_changed_at_utc\nFROM flow WHERE name = '{NAME}';"
# The boundary: a row only when exactly one flow has the name and it has a time; otherwise no row at all.
BOUNDARY = (f"WITH b AS (\n  SELECT max(updated_at) AS changed_at FROM flow WHERE name = '{NAME}'\n"
            "  HAVING count(*) = 1 AND max(updated_at) IS NOT NULL),\n")
PERIODS = "FROM b CROSS JOIN (VALUES (true), (false)) AS p (after_flow_change)\n"
# The per-run outcome of deploy/agent-run-metrics.sql: the component-span CTE and the CASE over it.
F_CTE = re.search(r"\nf AS \(\n  SELECT sp\.trace_id,\n.*?GROUP BY 1\),\n", METRICS, re.S).group(0)[1:]
OUTCOME = ("CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'\n"
           "      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome")
# The reads of a span output inside F_CTE: the final validation's fixed status prefixes. REDACTED below
# is the only other read of a span output.
OUTPUT_TESTS = (
    """sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'""",
    "sp.outputs -> 'message' ->> 'text' IS NOT NULL",
    """sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'""",
    """sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%'""")
STORED = "m.category IS DISTINCT FROM 'error'"
# A run whose span text deploy/redact-agent-history.py replaced: the metrics SQL's [1] marker test, alias sp.
REDACTED = """EXISTS (SELECT 1 FROM span sp WHERE sp.trace_id = t.id AND sp.outputs::text LIKE '{"redacted":true%')"""
# The verdict: f on any stored message after the boundary, t only when judgeable and none, else empty.
VERDICT = 'SELECT can_judge, CASE WHEN NOT no_stored_message THEN false WHEN can_judge THEN true END AS passed\nFROM v;'
# Rows reach their period only through these joins; [2] keeps runs without spans (no_final).
PERIOD_JOINS = (
    '  FROM trace t CROSS JOIN b LEFT JOIN f ON f.trace_id = t.id\n',
    '  LEFT JOIN run r ON r.after_flow_change = p.after_flow_change\n',
    '  FROM message m CROSS JOIN b\n',
    '  LEFT JOIN msg x ON x.after_flow_change = p.after_flow_change\n')
# Every shape a returned column may have: counts, a boolean key or verdict, and the one flow time.
RESULT_ITEMS = (
    r"count\(\*\) AS flows", r"max\(updated_at\) AS flow_changed_at_utc", r"p\.after_flow_change",
    r"count\(r\.outcome\) AS runs",
    r"count\(\*\) FILTER \(WHERE r\.outcome = '(accepted|blocked|agent_error|no_final)'\) AS \1",
    r"count\(\*\) FILTER \(WHERE r\.redacted\) AS redacted",
    r"count\(x\.session_id\) AS messages",
    r"count\(\*\) FILTER \(WHERE x\.stored\) AS stored_messages",
    r"count\(\*\) FILTER \(WHERE NOT x\.stored\) AS error_messages",
    r"count\(DISTINCT x\.session_id\) FILTER \(WHERE x\.stored\) AS sessions_with_stored_message",
    r"can_judge", r"CASE WHEN NOT no_stored_message THEN false WHEN can_judge THEN true END AS passed")
OUTPUT_COLUMNS = {
    'flows', 'flow_changed_at_utc', 'after_flow_change', 'runs', 'accepted', 'blocked', 'agent_error', 'no_final',
    'redacted', 'messages', 'stored_messages', 'error_messages', 'sessions_with_stored_message', 'can_judge', 'passed'}
# Columns by alias: Langflow tables (t trace, m message, sp span) and the CTE rows built from them.
# Message texts, senders and properties, span inputs and attributes, and flow data are absent.
TABLE_COLUMNS = {
    't': {'id', 'session_id', 'start_time'}, 'm': {'session_id', '"timestamp"', 'category'},
    'sp': {'trace_id', 'name', 'outputs', 'status', 'parent_span_id'},
    'b': {'changed_at'}, 'f': {'trace_id', 'has_final', 'blocked', 'agent_error'},
    'r': {'outcome', 'after_flow_change', 'redacted'}, 'x': {'session_id', 'stored', 'after_flow_change'},
    'p': {'after_flow_change'}}
SESSION_ID_USES = (
    "t.session_id LIKE 'gw-%'", "m.session_id LIKE 'gw-%'", ', m.session_id\n  FROM message m',
    'count(x.session_id) AS messages', 'count(DISTINCT x.session_id) FILTER (WHERE x.stored)')


def statements():
    lines = [line for line in SQL.splitlines() if not line.startswith(('--', '\\echo'))]
    return [part.strip() for part in '\n'.join(lines).split(';') if part.strip()]


def code():
    return '\n'.join(statements())


def final_select(statement):
    """The outermost result list: from the last SELECT at column 0 to the next FROM at column 0."""
    start = [match.start() for match in re.finditer(r'^SELECT ', statement, re.M)][-1]
    return statement[start:statement.index('\nFROM ', start)]


def items(select):
    found, depth, current = [], 0, ''
    for character in select[len('SELECT '):]:
        depth += (character == '(') - (character == ')')
        if character == ',' and depth == 0:
            found.append(current.strip()); current = ''
        else:
            current += character
    return found + [current.strip()]


class MessageStoreCheckSqlTests(unittest.TestCase):
    def test_document_command_holds_exactly_this_file(self):
        self.assertTrue(SQL.endswith('\n') and '\nSQL\n' not in SQL and not SQL.startswith('SQL\n'))
        self.assertEqual(DOCUMENT.count(COMMAND + SQL + 'SQL\n```'), 1)
        # Same container, role and psql options as the confirmation and metrics SQL commands.
        self.assertEqual(DOCUMENT.count(COMMAND), 3)

    def test_session_is_read_only_with_a_timeout_before_any_query(self):
        first = statements()
        self.assertEqual(first[:4], ['SET default_transaction_read_only = on', "SET statement_timeout = '15s'",
                                     "SET TIME ZONE 'UTC'", 'SHOW TimeZone'])
        self.assertTrue(all(statement.startswith(('SELECT ', 'WITH ')) for statement in first[4:]))
        self.assertEqual(len(first), 8)
        # The CI parser and the documented reading rely on these section labels, in this order.
        labels = [line.split(']', 1)[0] + ']' for line in SQL.splitlines() if line.startswith('\\echo ')]
        self.assertEqual(labels, ["\\echo '[0]", "\\echo '[1]", "\\echo '[2]", "\\echo '[3]", "\\echo '[4]"])

    def test_no_writing_or_session_changing_statement(self):
        words = set(re.findall(r'[A-Za-z_]+', re.sub(r"'[^']*'", "''", SQL).upper()))
        for forbidden in ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE', 'CREATE', 'ALTER', 'DROP', 'COPY', 'GRANT',
                          'REVOKE', 'CALL', 'DO', 'EXECUTE', 'PREPARE', 'RESET', 'BEGIN', 'COMMIT', 'LOCK', 'VACUUM',
                          'NOTIFY', 'LISTEN', 'INTO', 'PG_SLEEP', 'DBLINK', 'LO_IMPORT', 'PG_READ_FILE', 'SETVAL',
                          'NEXTVAL', 'SET_CONFIG'):
            self.assertNotIn(forbidden, words)
        self.assertEqual(SQL.count('SET '), 3)
        meta = [line.split()[0] for line in SQL.splitlines() if line.startswith('\\')]
        self.assertEqual(set(meta), {'\\echo'})  # no \copy, \o, \! or \set

    def test_only_the_named_flow_and_gateway_sessions_are_read(self):
        ctes = {'b', 'f', 'run', 'msg', 'v'}
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ([a-z_0-9]+)', SQL)) - ctes, {'flow', 'trace', 'span', 'message'})
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ((?:trace|span|message) \w+)', SQL)),
                         {'trace t', 'message m', 'span sp'})
        # The flow table is read in exactly two fixed ways, both by the Gateway flow's exact name.
        self.assertEqual(SQL.count('FROM flow'), 4)
        self.assertEqual(SQL.count(FLOW_COUNT), 1)
        self.assertEqual(SQL.count(BOUNDARY), 3)
        self.assertEqual(SQL.count('WITH b AS ('), 3)
        self.assertIn(f"FLOW_NAME = '{NAME}'", GATEWAY)  # the helpers find the flow by this name
        for statement in statements()[4:]:
            # Every trace and message read carries the prefix filter; spans are reached only through such a trace:
            # joined to it in the outcome CTE, or by its id in the redaction test of that trace's own row.
            self.assertEqual(len(re.findall(r'\b(?:FROM|JOIN) (?:trace t|message m)\b', statement)),
                             len(re.findall(r"\b(?:t|m)\.session_id LIKE 'gw-%'", statement)))
            self.assertEqual(len(re.findall(r'\bFROM span sp\b', statement)),
                             len(re.findall(r'\bJOIN trace t ON t\.id = sp\.trace_id\b', statement))
                             + statement.count(REDACTED))
        self.assertNotIn('%gw', SQL)
        self.assertNotIn('NOT LIKE', SQL)

    def test_session_ids_are_used_only_to_filter_and_count(self):
        rest = code()
        for use in SESSION_ID_USES:
            self.assertIn(use, rest, use)
            rest = rest.replace(use, '')
        self.assertNotIn('session_id', rest)
        # The message CTE passes the session id on only to be counted, never grouped or returned as is.
        self.assertEqual(re.findall(r'x\.session_id\)?', code()), ['x.session_id)', 'x.session_id)'])
        self.assertNotIn('GROUP BY 1, 2', SQL)
        self.assertEqual(SQL.count('GROUP BY 1 ORDER BY 1 DESC;'), 2)

    def test_only_listed_columns_are_read_and_no_text_is_selected(self):
        seen = {}
        for alias, column in re.findall(r'(?<![\w.])(sp|t|m|b|f|r|x|p)\.("?\w+"?)', code()):
            seen.setdefault(alias, set()).add(column)
        self.assertEqual(seen, TABLE_COLUMNS)
        self.assertNotIn('*', code().replace('count(*)', ''))  # no SELECT * or alias.*
        # Flow columns appear only in the two fixed flow reads above.
        self.assertEqual(SQL.count('updated_at'), 7)
        for absent in ('"text"', 'm.text', 'properties', 'sender', 'inputs', 'content_blocks', 'attributes', 'files',
                       'data', 'user_id', 'm.error', 'is_output', 'flow_id', 'description'):
            self.assertNotIn(absent, code())
        # Span outputs occur only in the metrics SQL's four fixed tests, inside its outcome CTE copies, and in
        # the redaction marker test, which reads a fixed prefix and returns a boolean.
        for test in OUTPUT_TESTS:
            self.assertEqual(SQL.count(test), 2, test)
        self.assertEqual(SQL.count(REDACTED), 2)
        self.assertEqual(SQL.count('outputs'), 2 * len(OUTPUT_TESTS) + 2)
        rest = code().replace(F_CTE.strip().rstrip(','), '').replace(REDACTED, '')
        for absent in ('outputs', 'sp.', 'text', 'status', "'Jeju Final Validation'"):
            self.assertNotIn(absent, rest)
        # The message category is read through one fixed comparison: an lfx error message or any stored message.
        self.assertEqual(SQL.count('category'), 2)
        self.assertEqual(SQL.count(STORED), 2)
        self.assertEqual(SQL.count(f'{STORED} AS stored'), 1)
        self.assertEqual(set(re.findall(r"->> '([^']+)'", code())), {'text'})

    def test_result_columns_are_counts_booleans_or_the_flow_time(self):
        columns = set()
        for statement in statements()[4:]:
            for item in items(final_select(statement)):
                self.assertTrue(any(re.fullmatch(shape, item) for shape in RESULT_ITEMS), item)
                columns.add(item.rsplit(' AS ', 1)[1] if ' AS ' in item else item.split('.')[-1])
        self.assertEqual(columns, OUTPUT_COLUMNS)
        # The period key and the verdict inputs are booleans built from timestamptz comparisons and EXISTS.
        self.assertEqual(SQL.count('(VALUES (true), (false)) AS p (after_flow_change)'), 2)
        self.assertEqual(SQL.count('SELECT t.start_time > b.changed_at AS after_flow_change,'), 1)
        self.assertEqual(SQL.count('SELECT m."timestamp" > b.changed_at AS after_flow_change, '), 1)
        self.assertEqual(SQL.count('v AS (\n  SELECT EXISTS (SELECT 1 FROM trace t JOIN b ON t.start_time > b.changed_at '
                                   'JOIN f ON f.trace_id = t.id\n'), 1)
        self.assertEqual(SQL.count("      WHERE t.session_id LIKE 'gw-%' AND f.has_final\n"
                                   f"        AND NOT {REDACTED}) AS can_judge,\n"), 1)
        self.assertEqual(SQL.count('    NOT EXISTS (SELECT 1 FROM message m JOIN b ON m."timestamp" > b.changed_at\n'), 1)
        self.assertEqual(SQL.count(f"      WHERE m.session_id LIKE 'gw-%' AND {STORED}) AS no_stored_message)"), 1)
        # No aggregate takes anything but a row, a run outcome, a counted session id or the flow time.
        self.assertEqual(set(re.findall(r'\bcount\(([^()]*)\)', code())),
                         {'*', 'r.outcome', 'x.session_id', 'DISTINCT x.session_id'})
        self.assertEqual(set(re.findall(r'\bmax\(([^()]*)\)', code())), {'updated_at'})
        for absent in ('sum(', 'avg(', 'min(', 'string_agg(', 'array_agg(', 'json_agg(', 'percentile_cont('):
            self.assertNotIn(absent, SQL)

    def test_redacted_runs_are_counted_and_never_make_the_check_judgeable(self):
        # The same marker test as the metrics SQL's redacted count, so both files call the same runs redacted.
        self.assertEqual(METRICS.count(REDACTED.replace('span sp', 'span s').replace('sp.', 's.')), 1)
        # [2] counts them per period; [4] ignores them as evidence, since the redaction tool deleted their messages.
        self.assertEqual(SQL.count(f'    {REDACTED} AS redacted\n  FROM trace t CROSS JOIN b'), 1)
        self.assertEqual(SQL.count(f'        AND NOT {REDACTED}) AS can_judge,\n'), 1)
        self.assertEqual(SQL.count('r.redacted'), 1)
        self.assertNotIn('NOT r.redacted', SQL)

    def test_rows_reach_their_period_only_through_the_fixed_joins(self):
        for join in PERIOD_JOINS:
            self.assertEqual(SQL.count(join), 1, join)
        self.assertEqual(SQL.count('after_flow_change = p.after_flow_change'), 2)
        for absent in ('RIGHT JOIN', 'FULL JOIN', 'INNER JOIN', 'NATURAL', 'USING', '<>', '!='):
            self.assertNotIn(absent, code())
        # Each period CTE has exactly one source row per run or message: no other join in run or msg.
        self.assertEqual(code().count('CROSS JOIN b'), 2)
        self.assertEqual(code().count('LEFT JOIN f'), 1)

    def test_verdict_fails_on_any_stored_message_and_passes_only_when_judgeable(self):
        self.assertEqual(SQL.count(VERDICT), 1)
        self.assertTrue(SQL.endswith(VERDICT + '\n'))
        self.assertEqual(code().count('no_stored_message'), 2)
        self.assertEqual(code().count('can_judge'), 3)

    def test_run_outcomes_are_the_metrics_sql_text(self):
        self.assertTrue(F_CTE.startswith('f AS (\n  SELECT sp.trace_id,\n') and F_CTE.endswith('GROUP BY 1),\n'))
        self.assertEqual(METRICS.count(F_CTE), 1)
        self.assertEqual(SQL.count(F_CTE), 2)
        self.assertEqual(METRICS.count(OUTCOME), 1)
        self.assertEqual(SQL.count(OUTCOME), 1)
        # Outside those copies the outcome is used only by name, and the verdict only asks for a final.
        self.assertEqual(code().count('f.has_final'), 3)
        self.assertEqual(code().count('f.blocked'), 1)
        self.assertEqual(code().count('f.agent_error'), 1)
        self.assertEqual(code().count('f.trace_id = t.id'), 2)

    def test_boundary_is_strict_and_no_flow_means_no_rows(self):
        # Runs are split by start, messages by write time, both strictly after the last flow change.
        self.assertEqual(SQL.count('t.start_time > b.changed_at'), 2)
        self.assertEqual(SQL.count('m."timestamp" > b.changed_at'), 2)
        self.assertEqual(len(re.findall(r'changed_at\b', SQL)), 7)
        for absent in ('>= b.', '< b.', '<= b.', 'b.changed_at <', 'now()', 'current_timestamp', 'interval'):
            self.assertNotIn(absent, SQL)
        # [2] and [3] read the boundary row in their FROM: without exactly one named flow they return no rows,
        # so a missing or duplicated flow can never read as zero runs or zero stored messages.
        self.assertEqual(SQL.count(PERIODS), 2)
        self.assertEqual(SQL.count('CROSS JOIN b'), 2)
        self.assertEqual(SQL.count('JOIN b ON'), 2)
        self.assertEqual(SQL.count(VERDICT), 1)
        # The legacy confirmation SQL in docs/32 identifies the flow the same way.
        self.assertGreaterEqual(DOCUMENT.count(f"SELECT max(updated_at) AS at FROM flow WHERE name = '{NAME}'"), 1)


if __name__ == '__main__':
    unittest.main()
