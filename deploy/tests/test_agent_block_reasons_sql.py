"""Static contracts of the read-only block-reasons SQL; no database, AWS, network or model calls.

These tests read the SQL text only. They pin what the file may read and return, that its run outcomes and model
names are the metrics SQL's CTEs character for character, that every label it returns is a fixed string (or a
recorded model name behind a strict pattern), and that JSON text is parsed only behind pg_input_is_valid (an answer
also only up to 10000 opening brackets). That the counts are right is checked by
test_agent_block_reasons_sql_runtime.py on a scratch PostgreSQL 16+ cluster.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / 'deploy/agent-block-reasons.sql').read_text(encoding='utf-8')
METRICS = (ROOT / 'deploy/agent-run-metrics.sql').read_text(encoding='utf-8')
DOCUMENT = (ROOT / 'docs/32-agent-gateway-tunnel.md').read_text(encoding='utf-8')
COMMAND = ("cd ~/oreumit-agent && sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml "
           "-f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow-db sh -eu -c "
           "'PGPASSWORD=\"$LANGFLOW_DB_PASSWORD\" psql -h 127.0.0.1 -U langflow_app -d langflow -P pager=off "
           "-v ON_ERROR_STOP=1' <<'SQL'\n")
# Reused from deploy/agent-run-metrics.sql: the model label CTEs of its [2] and its per-run outcome.
S_C_CTE = re.search(r"WITH (s AS \(\n  SELECT sp\.trace_id, lower\(sp\.span_type::text\) AS kind, sp\.attributes::jsonb "
                    r"AS a, sp\.name\n.*?\n  FROM s GROUP BY 1\),\n)", METRICS, re.S).group(1)
F_CTE = re.search(r"\nf AS \(\n  SELECT sp\.trace_id,\n.*?GROUP BY 1\),\n", METRICS, re.S).group(0)[1:]
OUTCOME = ("CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'\n"
           "      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome")
KST = "(t.start_time + interval '9 hours')::date AS kst_day"
# This file's own shared CTEs: the block code and the run row in all four queries, the answer reads in [3] and [4].
V_CTE = re.search(r"\nv AS \(\n.*?GROUP BY 1\),\n", SQL, re.S).group(0)[1:]
RUN_CTE = re.search(r"\nrun AS \(\n.*?\n  WHERE t\.session_id LIKE 'gw-%'\)", SQL, re.S).group(0)[1:]
A_CTE = re.search(r"\na AS MATERIALIZED \(\n.*?\n  ORDER BY sp\.trace_id, sp\.start_time DESC, sp\.id\),\n", SQL, re.S).group(0)[1:]
B_CTE = re.search(r"\nb AS \(\n.*?\n  FROM a\),\n", SQL, re.S).group(0)[1:]
MODEL = ("CASE WHEN c.model IS NULL THEN '-' WHEN c.model ~ '^[A-Za-z0-9 ._:+()/-]{1,120}$' THEN c.model "
         "ELSE '(other)' END AS model")
RESULT = "CASE WHEN run.outcome = 'blocked' THEN run.code ELSE run.outcome END AS result"
# [4] splits answers whose only key is placeIds (gateway-scopes.mjs selection()) from answers with other keys.
ANSWER_KEYS = "CASE WHEN l.ids_only THEN 'placeIds_only' ELSE 'with_other_keys' END AS answer_keys"
IDS_ONLY = "(SELECT count(*) FROM jsonb_object_keys(j)) = 1 AS ids_only"
# The answer is parsed only below the bracket bound: PostgreSQL's stack depth error is not a soft input error.
ANSWER_PARSE = ("CASE WHEN length(answer) - length(translate(answer, '[{', '')) > 10000 THEN NULL\n"
                "      WHEN pg_input_is_valid(answer, 'jsonb') THEN answer::jsonb END AS j")
CODES = {'blocked_redacted', 'RECOMMENDATION_BLOCKED', 'VALIDATION_FAILED', 'blocked_other'}
SHAPES = {
    'redacted', 'unreadable', 'no_answer_text', 'empty_text', 'not_json_code_fence', 'not_json_call_limit_notice',
    'not_json_mentions_placeIds', 'not_json', 'json_not_object', 'duplicate_keys', 'json_without_placeIds',
    'json_with_other_keys', 'placeIds_not_array', 'placeIds_empty', 'placeIds_over_50', 'non_string_ids',
    'bad_id_strings', 'placeIds_1_to_5', 'placeIds_6_to_50'}
LABELS = CODES | SHAPES | {'placeIds_only', 'with_other_keys', 'accepted', 'blocked', 'agent_error', 'no_final', '-',
                            '(other)'}
# Every shape a returned column may have: group keys, the guarded labels above, and counts of runs.
RESULT_ITEMS = (
    r'(?:run\.)?kst_day', r'(?:run\.)?model', re.escape(RESULT), r'x\.answer_shape', re.escape(ANSWER_KEYS),
    r'count\(\*\) AS runs',
    r"count\(\*\) FILTER \(WHERE outcome = '(accepted|blocked|agent_error|no_final)'\) AS \1",
    r"count\(\*\) FILTER \(WHERE outcome = 'blocked' AND code = 'RECOMMENDATION_BLOCKED'\) AS recommendation_blocked",
    r"count\(\*\) FILTER \(WHERE outcome = 'blocked' AND code = 'VALIDATION_FAILED'\) AS validation_failed",
    r"count\(\*\) FILTER \(WHERE outcome = 'blocked' AND code = '(blocked_redacted|blocked_other)'\) AS \1",
    r"count\(\*\) FILTER \(WHERE coalesce\(k\.(model|tool)_calls, 0\) = 0\) AS \1_calls_0",
    r"count\(\*\) FILTER \(WHERE k\.(model|tool)_calls = ([12])\) AS \1_calls_\2",
    r"count\(\*\) FILTER \(WHERE k\.model_calls BETWEEN 3 AND 7\) AS model_calls_3_7",
    r"count\(\*\) FILTER \(WHERE k\.model_calls >= 8\) AS model_calls_8_plus",
    r"count\(\*\) FILTER \(WHERE k\.tool_calls >= 3\) AS tool_calls_3_plus",
    r"count\(\*\) FILTER \(WHERE k\.tool_errors > 0\) AS runs_with_tool_error",
    r"count\(\*\) FILTER \(WHERE r\.seen = r\.ids\) AS all_seen",
    r"count\(\*\) FILTER \(WHERE r\.seen > 0 AND r\.seen < r\.ids\) AS some_unseen",
    r"count\(\*\) FILTER \(WHERE r\.seen = 0\) AS none_seen",
    r"count\(\*\) FILTER \(WHERE r\.seen = 0 AND r\.tools = 0\) AS none_seen_without_tool_call",
    r"count\(\*\) FILTER \(WHERE r\.excluded\) AS with_excluded_id",
    r"count\(\*\) FILTER \(WHERE CASE WHEN jsonb_typeof\(l\.request -> 'requirements'\) = 'array'\n"
    r"    THEN jsonb_array_length\(l\.request -> 'requirements'\) > 0 ELSE false END\) AS with_requirement")
OUTPUT_COLUMNS = (
    ['kst_day', 'model', 'runs', 'accepted', 'blocked', 'recommendation_blocked', 'validation_failed',
     'blocked_redacted', 'blocked_other', 'agent_error', 'no_final'],
    ['kst_day', 'model', 'result', 'runs', 'model_calls_0', 'model_calls_1', 'model_calls_2', 'model_calls_3_7',
     'model_calls_8_plus', 'tool_calls_0', 'tool_calls_1', 'tool_calls_2', 'tool_calls_3_plus', 'runs_with_tool_error'],
    ['kst_day', 'model', 'result', 'answer_shape', 'runs'],
    ['kst_day', 'model', 'result', 'answer_keys', 'runs', 'all_seen', 'some_unseen', 'none_seen',
     'none_seen_without_tool_call', 'with_excluded_id', 'with_requirement'])
# Langflow table columns the file may touch: trace (t) and span (sp) only. Span inputs are reached only through the
# guarded parse in A_CTE, span outputs only through fixed tests and the id needles.
TABLE_COLUMNS = {'t': {'id', 'session_id', 'start_time'},
                 'sp': {'trace_id', 'span_type', 'attributes', 'name', 'outputs', 'status', 'parent_span_id', 'inputs',
                        'start_time', 'id'}}
# The only reads of a span output outside F_CTE: the final validation's fixed codes and redaction markers, the
# redaction prefix of A_CTE, a tool error marker, and the stored tool output text that the id needles search.
OUTPUT_READS = (
    """bool_or(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}') THEN 'blocked_redacted'""",
    """starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"RECOMMENDATION_BLOCKED",')""",
    """starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"VALIDATION_FAILED",')""",
    """sp.outputs::text LIKE '{"redacted":true%' AS redacted""",
    "strpos(sp.outputs::text, ' status=''error''') > 0",
    "SELECT sp.trace_id, sp.outputs::text AS output")
NEEDLES = ("""strpos(o.output, E'\\\\"id\\\\":\\\\"' || n.id || E'\\\\"') > 0""",
           """strpos(o.output, E'\\\\"id\\\\": \\\\"' || n.id || E'\\\\"') > 0""",
           """strpos(o.output, '"id":"' || n.id || '"') > 0""",
           """strpos(o.output, '"id": "' || n.id || '"') > 0""")
ID_GUARD = "n.id ~ '^[A-Za-z0-9._:-]{1,160}$' AND ("


def statements():
    lines = [line for line in SQL.splitlines() if not line.startswith(('--', '\\echo'))]
    return [part.strip() for part in '\n'.join(lines).split(';') if part.strip()]


def code():
    return '\n'.join(statements())


def split_literals(text):
    """(text with every string literal replaced by '', [(is_escape_string, literal body)]) for code()."""
    rest, literals, index = [], [], 0
    while index < len(text):
        escape = text[index] in 'Ee' and text[index + 1:index + 2] == "'" and not (text[index - 1:index].isalnum()
                                                                                   or text[index - 1:index] == '_')
        if text[index] != "'" and not escape:
            rest.append(text[index]); index += 1
            continue
        end, body = index + (2 if escape else 1), ''
        while True:
            if escape and text[end] == '\\':
                body += text[end:end + 2]; end += 2
            elif text[end] == "'" and text[end + 1:end + 2] == "'":
                body += "''"; end += 2
            elif text[end] == "'":
                break
            else:
                body += text[end]; end += 1
        literals.append((escape, body))
        rest.append("''")
        index = end + 1
    return ''.join(rest), literals


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


class BlockReasonsSqlTests(unittest.TestCase):
    def test_document_command_holds_exactly_this_file(self):
        self.assertTrue(SQL.endswith('\n') and '\nSQL\n' not in SQL and not SQL.startswith('SQL\n'))
        self.assertEqual(DOCUMENT.count(COMMAND + SQL + 'SQL\n```'), 1)
        # Same container, role and psql options as the other three read-only SQL commands in docs/32.
        self.assertEqual(DOCUMENT.count(COMMAND), 4)

    def test_session_is_read_only_with_a_timeout_before_any_query(self):
        first = statements()
        self.assertEqual(first[:4], ['SET default_transaction_read_only = on', "SET statement_timeout = '15s'",
                                     "SET TIME ZONE 'UTC'", 'SHOW TimeZone'])
        self.assertTrue(all(statement.startswith('WITH ') for statement in first[4:]))
        self.assertEqual(len(first), 8)
        labels = [line.split(']', 1)[0] + ']' for line in SQL.splitlines() if line.startswith('\\echo ')]
        self.assertEqual(labels, ["\\echo '[0]", "\\echo '[1]", "\\echo '[2]", "\\echo '[3]", "\\echo '[4]"])

    def test_no_writing_or_session_changing_statement(self):
        words = set(re.findall(r'[A-Za-z_]+', split_literals(code())[0].upper()))
        for forbidden in ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE', 'CREATE', 'ALTER', 'DROP', 'COPY', 'GRANT',
                          'REVOKE', 'CALL', 'DO', 'EXECUTE', 'PREPARE', 'RESET', 'BEGIN', 'COMMIT', 'LOCK', 'VACUUM',
                          'NOTIFY', 'LISTEN', 'INTO', 'PG_SLEEP', 'DBLINK', 'LO_IMPORT', 'PG_READ_FILE', 'SETVAL',
                          'NEXTVAL', 'SET_CONFIG', 'FOR'):
            self.assertNotIn(forbidden, words)
        self.assertEqual(len(re.findall(r'\bSET\b', code())), 3)
        meta = [line.split()[0] for line in SQL.splitlines() if line.startswith('\\')]
        self.assertEqual(set(meta), {'\\echo'})  # no \copy, \o, \! or \set

    def test_only_gateway_traces_and_their_spans_are_read(self):
        ctes = {'s', 'c', 'f', 'v', 'run', 'k', 'a', 'b', 'x', 'l', 'n', 'o', 'q', 'r'}
        functions = {'json_object_keys', 'jsonb_object_keys', 'jsonb_array_elements', 'jsonb_array_elements_text'}
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ([a-z_0-9]+)', code())) - ctes - functions, {'trace', 'span'})
        self.assertEqual(set(re.findall(r'\b(?:FROM|JOIN) ((?:trace|span) \w+)', code())), {'trace t', 'span sp'})
        for statement in statements()[4:]:
            # Every trace read carries the prefix filter; every span read is joined to such a trace.
            self.assertEqual(len(re.findall(r'\b(?:FROM|JOIN) trace t\b', statement)),
                             len(re.findall(r"\bt\.session_id LIKE 'gw-%'", statement)))
            self.assertEqual(len(re.findall(r'\bFROM span sp\b', statement)),
                             len(re.findall(r'\bFROM span sp JOIN trace t ON t\.id = sp\.trace_id\n', statement)))
        self.assertNotIn('%gw', SQL)
        self.assertNotIn('NOT LIKE', SQL)
        for absent in ('message', 'flow ', 'vertex_build', 'transaction'):
            self.assertNotIn(f'FROM {absent}', SQL)

    def test_session_ids_are_used_only_to_filter(self):
        rest = code().replace("t.session_id LIKE 'gw-%'", '')
        self.assertNotIn('session_id', rest)

    def test_shared_ctes_are_the_metrics_sql_text(self):
        self.assertEqual(METRICS.count(S_C_CTE), 1)
        self.assertEqual(METRICS.count(F_CTE), 1)
        self.assertEqual(METRICS.count(OUTCOME), 1)
        self.assertEqual(METRICS.count(KST), 2)
        self.assertEqual(SQL.count('WITH ' + S_C_CTE + F_CTE + V_CTE + RUN_CTE), 4)
        self.assertEqual(SQL.count(OUTCOME), 4)
        self.assertEqual(SQL.count(KST), 4)
        # The block code, the run row and the answer reads are one text each, repeated per query.
        self.assertEqual(SQL.count(V_CTE), 4)
        self.assertEqual(SQL.count(RUN_CTE), 4)
        self.assertEqual(SQL.count(A_CTE + B_CTE), 2)
        self.assertEqual(SQL.count('\nv AS ('), 4)
        self.assertEqual(SQL.count('\na AS MATERIALIZED ('), 2)
        self.assertEqual(SQL.count('\nb AS ('), 2)
        self.assertIn('  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.trace_id = t.id '
                      'LEFT JOIN v ON v.trace_id = t.id\n', RUN_CTE)

    def test_only_listed_table_columns_are_read(self):
        seen = {}
        for alias, column in re.findall(r'(?<![\w.])(sp|t)\.("?\w+"?)', code()):
            seen.setdefault(alias, set()).add(column)
        self.assertEqual(seen, TABLE_COLUMNS)
        self.assertNotIn('*', split_literals(code())[0].replace('count(*)', ''))  # no SELECT * or alias.*
        for absent in ('"text"', 'properties', 'sender', 'category', 'content_blocks', 'sp.error',
                       'total_latency_ms', 'total_tokens', 'flow_id', 'service_token'):
            self.assertNotIn(absent, code())

    def test_span_inputs_are_parsed_only_behind_pg_input_is_valid(self):
        # Span inputs occur only in the guarded parse of the final validation span, once per answer query.
        guarded = "CASE WHEN pg_input_is_valid(sp.inputs::text, 'jsonb') THEN sp.inputs::text::jsonb END AS doc"
        self.assertEqual(SQL.count(guarded), 2)
        self.assertEqual(code().count('sp.inputs'), 4)
        self.assertEqual(A_CTE.count("sp.name = 'Jeju Final Validation'"), 1)
        # From that document only the two message texts are read, each only when it is a JSON string.
        self.assertEqual(set(re.findall(r"#>>? '([^']+)'", SQL)), {'{agent_response,text}', '{prepared_request,text}'})
        for path in ('agent_response', 'prepared_request'):
            self.assertEqual(SQL.count(f"CASE WHEN jsonb_typeof(p.doc #> '{{{path},text}}') = 'string' "
                                       f"THEN p.doc #>> '{{{path},text}}' END"), 2)
        # Every other parse of a text is guarded the same way; attributes are the metrics SQL's own cast.
        casts = re.findall(r'([\w.]+(?:::text)?)::jsonb\b', code())
        self.assertEqual(sorted(set(casts)), ['answer', 'prepared', 'sp.attributes', 'sp.inputs::text'])
        self.assertEqual(casts.count('sp.attributes'), 4)
        self.assertEqual(SQL.count(ANSWER_PARSE), 2)
        self.assertEqual(SQL.count("CASE WHEN pg_input_is_valid(prepared, 'jsonb') THEN prepared::jsonb"), 2)
        self.assertEqual(SQL.count('pg_input_is_valid('), 2 * 3)
        for text in ('answer', 'prepared'):
            self.assertEqual(casts.count(text), 2)
        # The one json cast counts duplicate keys of an answer that already passed the jsonb check.
        self.assertEqual(re.findall(r'(\w+)::json\b', code()), ['answer'])
        shape = re.search(r'\nx AS \(\n.*?\n  FROM b\)\n', SQL, re.S).group(0)
        self.assertLess(shape.index('WHEN j IS NULL THEN'), shape.index('answer::json'))
        # Nothing from the prepared request but its exclusions and requirements; from the answer only placeIds.
        self.assertEqual(set(re.findall(r"-> '([^']+)'", code())),
                         {'message', 'request', 'placeIds', 'excludedIds', 'requirements'})
        self.assertEqual(set(re.findall(r"->> '([^']+)'", code())), {'gen_ai.response.model', 'text'})

    def test_span_outputs_are_read_only_by_fixed_tests_and_id_needles(self):
        rest = code().replace(F_CTE, '')
        for read in OUTPUT_READS:
            self.assertIn(read, rest)
        # Four per block-code CTE (four copies), one per answer CTE (two), one tool error marker, one needle source.
        self.assertEqual(rest.count('outputs'), 4 * 3 + 2 + 1 + 1)
        self.assertEqual(code().count('outputs'), 4 * 4 + 4 * 3 + 2 + 1 + 1)
        # Tool outputs are searched, never parsed or returned: four fixed needle forms, each for a guarded id.
        for needle in NEEDLES:
            self.assertEqual(SQL.count(needle), 1, needle)
        self.assertEqual(SQL.count('strpos(o.output'), 4)
        self.assertEqual(SQL.count('o.output'), 4)
        self.assertEqual(SQL.count(ID_GUARD), 1)
        self.assertLess(SQL.index(ID_GUARD), SQL.index(NEEDLES[0]))
        self.assertEqual(SQL.count("  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'tool'),\n"), 1)

    def test_result_columns_are_fixed_labels_and_counts(self):
        for statement, expected in zip(statements()[4:], OUTPUT_COLUMNS):
            columns = []
            for item in items(final_select(statement)):
                self.assertTrue(any(re.fullmatch(shape, item) for shape in RESULT_ITEMS), item)
                columns.append(item.rsplit(' AS ', 1)[1] if ' AS ' in item else item.split('.')[-1])
            self.assertEqual(columns, expected)
        # The model label and the result are guarded or fixed in every query; group keys only, no free text.
        self.assertEqual(SQL.count(MODEL), 4)
        self.assertEqual(len(re.findall(r' AS model\b', SQL)), 4 + 4)  # the c CTEs and the guarded labels
        self.assertEqual(SQL.count(RESULT), 3)
        self.assertEqual(SQL.count("WHERE run.outcome IN ('accepted', 'blocked')"), 3)
        self.assertEqual(SQL.count('GROUP BY 1, 2 ORDER BY 1, 2;'), 1)
        self.assertEqual(SQL.count('GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;'), 1)
        self.assertEqual(SQL.count('GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;'), 2)

    def test_every_label_is_a_fixed_string(self):
        labels = set(re.findall(r"\b(?:THEN|ELSE) '([^']*)'", SQL))
        self.assertEqual(labels, LABELS)
        shape = re.search(r'\nx AS \(\n.*?\n  FROM b\)\n', SQL, re.S).group(0)
        self.assertEqual(set(re.findall(r"\b(?:THEN|ELSE) '([^']*)'", shape)), SHAPES)
        self.assertEqual(set(re.findall(r"\b(?:THEN|ELSE) '([^']*)'", V_CTE)), CODES)
        self.assertTrue(V_CTE.rstrip().endswith("ELSE 'blocked_other' END AS code\n"
                                                "  FROM span sp JOIN trace t ON t.id = sp.trace_id\n"
                                                "  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL "
                                                "AND sp.name = 'Jeju Final Validation' GROUP BY 1),"))
        # No literal depends on standard_conforming_strings: only E'' strings hold a backslash.
        literals = split_literals(code())[1]
        self.assertEqual([body for escape, body in literals if escape], ['\\\\"id\\\\":\\\\"', '\\\\"',
                                                                          '\\\\"id\\\\": \\\\"', '\\\\"'])
        self.assertFalse(any('\\' in body for escape, body in literals if not escape))

    def test_answer_shapes_follow_the_final_validation_and_mcp_order(self):
        shape = re.search(r'\nx AS \(\n.*?\n  FROM b\)\n', SQL, re.S).group(0)
        order = [label for label in re.findall(r"\b(?:THEN|ELSE) '([^']*)'", shape)]
        self.assertEqual(order, [
            'redacted', 'unreadable', 'no_answer_text', 'empty_text', 'not_json_code_fence', 'not_json_call_limit_notice',
            'not_json_mentions_placeIds', 'not_json', 'json_not_object', 'duplicate_keys', 'json_without_placeIds',
            'json_with_other_keys', 'placeIds_not_array', 'placeIds_empty', 'placeIds_over_50', 'non_string_ids',
            'bad_id_strings', 'placeIds_1_to_5', 'placeIds_6_to_50'])
        # The bounds of gateway-scopes.mjs selection(): at most 50 ids, strings of 1 to 160 characters, trimmed.
        self.assertIn("WHEN jsonb_array_length(j -> 'placeIds') > 50 THEN 'placeIds_over_50'", shape)
        self.assertIn("WHEN jsonb_array_length(j -> 'placeIds') <= 5 THEN 'placeIds_1_to_5' ELSE 'placeIds_6_to_50' END", shape)
        self.assertIn("WHERE id = '' OR length(id) > 160 OR id ~ '^[[:space:]]|[[:space:]]$') THEN 'bad_id_strings'", shape)
        self.assertIn("jsonb_array_length(j -> 'placeIds') BETWEEN 1 AND 50", SQL)
        mcp = (ROOT / 'services/jeju-mcp/src/gateway-scopes.mjs').read_text(encoding='utf-8')
        self.assertIn("raw.placeIds.length > 50\n      || !raw.placeIds.every(id => typeof id === 'string' && id.length > 0 "
                      "&& id.length <= 160 && id === id.trim())) return null;", mcp)
        self.assertIn("if (exact(raw, ['placeIds'])) {", mcp)
        # [4]'s placeIds_only is that exact-keys test: one key, and l keeps only answers whose placeIds is an array.
        self.assertEqual(SQL.count(IDS_ONLY), 1)
        self.assertEqual(SQL.count(ANSWER_KEYS), 1)
        # The final validation component publishes exactly these two codes.
        component = (ROOT / 'services/langflow/gateway_components.py').read_text(encoding='utf-8')
        for code_name in ('RECOMMENDATION_BLOCKED', 'VALIDATION_FAILED'):
            self.assertIn(f'_output({{"status": "blocked", "code": "{code_name}", "message": BLOCKED_MESSAGE}}', component)


if __name__ == '__main__':
    unittest.main()
