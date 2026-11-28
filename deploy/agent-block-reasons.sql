-- Read-only breakdown of the Gateway runs (session ids starting with gw-) that final validation blocked, from
-- the Langflow tracing tables. Output is counts and fixed labels only: no session ids, query or answer texts, place
-- ids or member data. A label taken from data is a fixed string or a recorded model name that passes a strict pattern.
-- Run outcomes and model names use the CTEs of deploy/agent-run-metrics.sql, character for character.
-- [3] and [4] read the agent answer as the final validation span received it (inputs.agent_response.text), parsed
-- only when it holds at most 10000 opening brackets (deeper JSON raises a stack depth error that pg_input_is_valid
-- does not catch) and passes pg_input_is_valid, and search the stored tool call outputs of the same run for fixed id
-- needles; neither text is returned. The MCP's own rejection reasons are not stored anywhere: [3] and [4] approximate
-- them.
-- docs/32-agent-gateway-tunnel.md holds the same text inside the AWS command; a deploy test keeps them equal.
SET default_transaction_read_only = on;
SET statement_timeout = '15s';
SET TIME ZONE 'UTC';
\echo '[0] session time zone (UTC)'
SHOW TimeZone;
\echo '[1] Gateway runs by KST day and recorded model; blocked runs split by the fixed code in the final validation output'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'llm'),
c AS (
  SELECT trace_id, string_agg(DISTINCT coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')'), '+'
    ORDER BY coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')) AS model
  FROM s GROUP BY 1),
f AS (
  SELECT sp.trace_id,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%', false) END) AS blocked,
    bool_or(lower(sp.status::text) = 'error') AS agent_error
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1),
v AS (
  SELECT sp.trace_id,
    CASE WHEN bool_or(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}') THEN 'blocked_redacted'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"RECOMMENDATION_BLOCKED",'))
        THEN 'RECOMMENDATION_BLOCKED'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"VALIDATION_FAILED",'))
        THEN 'VALIDATION_FAILED'
      ELSE 'blocked_other' END AS code
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation' GROUP BY 1),
run AS (
  SELECT t.id AS trace_id, (t.start_time + interval '9 hours')::date AS kst_day,
    CASE WHEN c.model IS NULL THEN '-' WHEN c.model ~ '^[A-Za-z0-9 ._:+()/-]{1,120}$' THEN c.model ELSE '(other)' END AS model,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome, v.code
  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.trace_id = t.id LEFT JOIN v ON v.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%')
SELECT kst_day, model, count(*) AS runs,
  count(*) FILTER (WHERE outcome = 'accepted') AS accepted,
  count(*) FILTER (WHERE outcome = 'blocked') AS blocked,
  count(*) FILTER (WHERE outcome = 'blocked' AND code = 'RECOMMENDATION_BLOCKED') AS recommendation_blocked,
  count(*) FILTER (WHERE outcome = 'blocked' AND code = 'VALIDATION_FAILED') AS validation_failed,
  count(*) FILTER (WHERE outcome = 'blocked' AND code = 'blocked_redacted') AS blocked_redacted,
  count(*) FILTER (WHERE outcome = 'blocked' AND code = 'blocked_other') AS blocked_other,
  count(*) FILTER (WHERE outcome = 'agent_error') AS agent_error,
  count(*) FILTER (WHERE outcome = 'no_final') AS no_final
FROM run GROUP BY 1, 2 ORDER BY 1, 2;
\echo '[2] accepted and blocked runs by model calls and tool calls per run; runs_with_tool_error = a tool call returned an error'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'llm'),
c AS (
  SELECT trace_id, string_agg(DISTINCT coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')'), '+'
    ORDER BY coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')) AS model
  FROM s GROUP BY 1),
f AS (
  SELECT sp.trace_id,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%', false) END) AS blocked,
    bool_or(lower(sp.status::text) = 'error') AS agent_error
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1),
v AS (
  SELECT sp.trace_id,
    CASE WHEN bool_or(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}') THEN 'blocked_redacted'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"RECOMMENDATION_BLOCKED",'))
        THEN 'RECOMMENDATION_BLOCKED'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"VALIDATION_FAILED",'))
        THEN 'VALIDATION_FAILED'
      ELSE 'blocked_other' END AS code
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation' GROUP BY 1),
run AS (
  SELECT t.id AS trace_id, (t.start_time + interval '9 hours')::date AS kst_day,
    CASE WHEN c.model IS NULL THEN '-' WHEN c.model ~ '^[A-Za-z0-9 ._:+()/-]{1,120}$' THEN c.model ELSE '(other)' END AS model,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome, v.code
  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.trace_id = t.id LEFT JOIN v ON v.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%'),
k AS (
  SELECT sp.trace_id, count(*) FILTER (WHERE lower(sp.span_type::text) = 'llm') AS model_calls,
    count(*) FILTER (WHERE lower(sp.span_type::text) = 'tool') AS tool_calls,
    count(*) FILTER (WHERE lower(sp.span_type::text) = 'tool' AND (lower(sp.status::text) = 'error'
      OR strpos(sp.outputs::text, ' status=''error''') > 0)) AS tool_errors
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) IN ('llm', 'tool') GROUP BY 1)
SELECT run.kst_day, run.model, CASE WHEN run.outcome = 'blocked' THEN run.code ELSE run.outcome END AS result,
  count(*) AS runs,
  count(*) FILTER (WHERE coalesce(k.model_calls, 0) = 0) AS model_calls_0,
  count(*) FILTER (WHERE k.model_calls = 1) AS model_calls_1,
  count(*) FILTER (WHERE k.model_calls = 2) AS model_calls_2,
  count(*) FILTER (WHERE k.model_calls BETWEEN 3 AND 7) AS model_calls_3_7,
  count(*) FILTER (WHERE k.model_calls >= 8) AS model_calls_8_plus,
  count(*) FILTER (WHERE coalesce(k.tool_calls, 0) = 0) AS tool_calls_0,
  count(*) FILTER (WHERE k.tool_calls = 1) AS tool_calls_1,
  count(*) FILTER (WHERE k.tool_calls = 2) AS tool_calls_2,
  count(*) FILTER (WHERE k.tool_calls >= 3) AS tool_calls_3_plus,
  count(*) FILTER (WHERE k.tool_errors > 0) AS runs_with_tool_error
FROM run LEFT JOIN k ON k.trace_id = run.trace_id
WHERE run.outcome IN ('accepted', 'blocked')
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo '[3] accepted and blocked runs by the shape of the agent answer that final validation received (no answer text is printed)'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'llm'),
c AS (
  SELECT trace_id, string_agg(DISTINCT coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')'), '+'
    ORDER BY coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')) AS model
  FROM s GROUP BY 1),
f AS (
  SELECT sp.trace_id,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%', false) END) AS blocked,
    bool_or(lower(sp.status::text) = 'error') AS agent_error
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1),
v AS (
  SELECT sp.trace_id,
    CASE WHEN bool_or(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}') THEN 'blocked_redacted'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"RECOMMENDATION_BLOCKED",'))
        THEN 'RECOMMENDATION_BLOCKED'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"VALIDATION_FAILED",'))
        THEN 'VALIDATION_FAILED'
      ELSE 'blocked_other' END AS code
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation' GROUP BY 1),
run AS (
  SELECT t.id AS trace_id, (t.start_time + interval '9 hours')::date AS kst_day,
    CASE WHEN c.model IS NULL THEN '-' WHEN c.model ~ '^[A-Za-z0-9 ._:+()/-]{1,120}$' THEN c.model ELSE '(other)' END AS model,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome, v.code
  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.trace_id = t.id LEFT JOIN v ON v.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%'),
a AS MATERIALIZED (
  SELECT DISTINCT ON (sp.trace_id) sp.trace_id, sp.outputs::text LIKE '{"redacted":true%' AS redacted,
    p.doc IS NOT NULL AS readable,
    CASE WHEN jsonb_typeof(p.doc #> '{agent_response,text}') = 'string' THEN p.doc #>> '{agent_response,text}' END AS answer,
    CASE WHEN jsonb_typeof(p.doc #> '{prepared_request,text}') = 'string' THEN p.doc #>> '{prepared_request,text}' END AS prepared
  FROM span sp JOIN trace t ON t.id = sp.trace_id
    CROSS JOIN LATERAL (SELECT CASE WHEN pg_input_is_valid(sp.inputs::text, 'jsonb') THEN sp.inputs::text::jsonb END AS doc
      OFFSET 0) p
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation'
  ORDER BY sp.trace_id, sp.start_time DESC, sp.id),
b AS (
  SELECT trace_id, redacted, readable, answer,
    CASE WHEN length(answer) - length(translate(answer, '[{', '')) > 10000 THEN NULL
      WHEN pg_input_is_valid(answer, 'jsonb') THEN answer::jsonb END AS j,
    CASE WHEN pg_input_is_valid(prepared, 'jsonb') THEN prepared::jsonb -> 'request' END AS request
  FROM a),
x AS (
  SELECT trace_id,
    CASE WHEN redacted THEN 'redacted' WHEN NOT readable THEN 'unreadable'
      WHEN answer IS NULL THEN 'no_answer_text' WHEN answer ~ '^[[:space:]]*$' THEN 'empty_text'
      WHEN j IS NULL THEN CASE WHEN answer ~ '^[[:space:]]*`{3}' THEN 'not_json_code_fence'
        WHEN strpos(answer, 'Model call limits exceeded') > 0 THEN 'not_json_call_limit_notice'
        WHEN strpos(answer, 'placeIds') > 0 THEN 'not_json_mentions_placeIds' ELSE 'not_json' END
      WHEN jsonb_typeof(j) <> 'object' THEN 'json_not_object'
      WHEN (SELECT count(*) FROM json_object_keys(answer::json)) <> (SELECT count(*) FROM jsonb_object_keys(j))
        THEN 'duplicate_keys'
      WHEN NOT j ? 'placeIds' THEN 'json_without_placeIds'
      WHEN (SELECT count(*) FROM jsonb_object_keys(j)) > 1 THEN 'json_with_other_keys'
      WHEN jsonb_typeof(j -> 'placeIds') <> 'array' THEN 'placeIds_not_array'
      WHEN jsonb_array_length(j -> 'placeIds') = 0 THEN 'placeIds_empty'
      WHEN jsonb_array_length(j -> 'placeIds') > 50 THEN 'placeIds_over_50'
      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(j -> 'placeIds') e WHERE jsonb_typeof(e) <> 'string')
        THEN 'non_string_ids'
      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements_text(j -> 'placeIds') e (id)
        WHERE id = '' OR length(id) > 160 OR id ~ '^[[:space:]]|[[:space:]]$') THEN 'bad_id_strings'
      WHEN jsonb_array_length(j -> 'placeIds') <= 5 THEN 'placeIds_1_to_5' ELSE 'placeIds_6_to_50' END AS answer_shape
  FROM b)
SELECT run.kst_day, run.model, CASE WHEN run.outcome = 'blocked' THEN run.code ELSE run.outcome END AS result,
  x.answer_shape, count(*) AS runs
FROM run JOIN x ON x.trace_id = run.trace_id
WHERE run.outcome IN ('accepted', 'blocked')
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
\echo '[4] runs whose answer lists 1 to 50 place ids as strings, split by answer keys: were the ids in the tool results of the same run (seen)?'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'llm'),
c AS (
  SELECT trace_id, string_agg(DISTINCT coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')'), '+'
    ORDER BY coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')) AS model
  FROM s GROUP BY 1),
f AS (
  SELECT sp.trace_id,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%', false) END) AS blocked,
    bool_or(lower(sp.status::text) = 'error') AS agent_error
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1),
v AS (
  SELECT sp.trace_id,
    CASE WHEN bool_or(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}') THEN 'blocked_redacted'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"RECOMMENDATION_BLOCKED",'))
        THEN 'RECOMMENDATION_BLOCKED'
      WHEN bool_or(starts_with(sp.outputs -> 'message' ->> 'text', '{"status":"blocked","code":"VALIDATION_FAILED",'))
        THEN 'VALIDATION_FAILED'
      ELSE 'blocked_other' END AS code
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation' GROUP BY 1),
run AS (
  SELECT t.id AS trace_id, (t.start_time + interval '9 hours')::date AS kst_day,
    CASE WHEN c.model IS NULL THEN '-' WHEN c.model ~ '^[A-Za-z0-9 ._:+()/-]{1,120}$' THEN c.model ELSE '(other)' END AS model,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome, v.code
  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.trace_id = t.id LEFT JOIN v ON v.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%'),
a AS MATERIALIZED (
  SELECT DISTINCT ON (sp.trace_id) sp.trace_id, sp.outputs::text LIKE '{"redacted":true%' AS redacted,
    p.doc IS NOT NULL AS readable,
    CASE WHEN jsonb_typeof(p.doc #> '{agent_response,text}') = 'string' THEN p.doc #>> '{agent_response,text}' END AS answer,
    CASE WHEN jsonb_typeof(p.doc #> '{prepared_request,text}') = 'string' THEN p.doc #>> '{prepared_request,text}' END AS prepared
  FROM span sp JOIN trace t ON t.id = sp.trace_id
    CROSS JOIN LATERAL (SELECT CASE WHEN pg_input_is_valid(sp.inputs::text, 'jsonb') THEN sp.inputs::text::jsonb END AS doc
      OFFSET 0) p
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL AND sp.name = 'Jeju Final Validation'
  ORDER BY sp.trace_id, sp.start_time DESC, sp.id),
b AS (
  SELECT trace_id, redacted, readable, answer,
    CASE WHEN length(answer) - length(translate(answer, '[{', '')) > 10000 THEN NULL
      WHEN pg_input_is_valid(answer, 'jsonb') THEN answer::jsonb END AS j,
    CASE WHEN pg_input_is_valid(prepared, 'jsonb') THEN prepared::jsonb -> 'request' END AS request
  FROM a),
l AS (
  SELECT trace_id, j -> 'placeIds' AS chosen, (SELECT count(*) FROM jsonb_object_keys(j)) = 1 AS ids_only, request
  FROM b
  WHERE CASE WHEN jsonb_typeof(j -> 'placeIds') = 'array' THEN jsonb_array_length(j -> 'placeIds') BETWEEN 1 AND 50
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(j -> 'placeIds') e WHERE jsonb_typeof(e) <> 'string') ELSE false END),
n AS (
  SELECT l.trace_id, e.id, bool_or(coalesce(l.request -> 'excludedIds' @> jsonb_build_array(e.id), false)) AS excluded
  FROM l CROSS JOIN LATERAL jsonb_array_elements_text(l.chosen) AS e (id) GROUP BY 1, 2),
o AS (
  SELECT sp.trace_id, sp.outputs::text AS output
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'tool'),
q AS (
  SELECT n.trace_id, n.id, n.excluded, count(o.trace_id) AS tools,
    coalesce(bool_or(n.id ~ '^[A-Za-z0-9._:-]{1,160}$' AND (strpos(o.output, E'\\"id\\":\\"' || n.id || E'\\"') > 0
      OR strpos(o.output, E'\\"id\\": \\"' || n.id || E'\\"') > 0 OR strpos(o.output, '"id":"' || n.id || '"') > 0
      OR strpos(o.output, '"id": "' || n.id || '"') > 0)), false) AS seen
  FROM n LEFT JOIN o ON o.trace_id = n.trace_id GROUP BY 1, 2, 3),
r AS (
  SELECT trace_id, count(*) AS ids, count(*) FILTER (WHERE seen) AS seen, bool_or(excluded) AS excluded,
    max(tools) AS tools
  FROM q GROUP BY 1)
SELECT run.kst_day, run.model, CASE WHEN run.outcome = 'blocked' THEN run.code ELSE run.outcome END AS result,
  CASE WHEN l.ids_only THEN 'placeIds_only' ELSE 'with_other_keys' END AS answer_keys, count(*) AS runs,
  count(*) FILTER (WHERE r.seen = r.ids) AS all_seen,
  count(*) FILTER (WHERE r.seen > 0 AND r.seen < r.ids) AS some_unseen,
  count(*) FILTER (WHERE r.seen = 0) AS none_seen,
  count(*) FILTER (WHERE r.seen = 0 AND r.tools = 0) AS none_seen_without_tool_call,
  count(*) FILTER (WHERE r.excluded) AS with_excluded_id,
  count(*) FILTER (WHERE CASE WHEN jsonb_typeof(l.request -> 'requirements') = 'array'
    THEN jsonb_array_length(l.request -> 'requirements') > 0 ELSE false END) AS with_requirement
FROM run JOIN l ON l.trace_id = run.trace_id JOIN r ON r.trace_id = run.trace_id
WHERE run.outcome IN ('accepted', 'blocked')
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
