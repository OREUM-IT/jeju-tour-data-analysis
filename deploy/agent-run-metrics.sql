-- Read-only aggregates of Gateway runs (session ids starting with gw-) from the Langflow tracing tables.
-- Output is counts, latency percentiles and token sums only: no session ids, texts or member data.
-- docs/32-agent-gateway-tunnel.md holds the same text inside the AWS command; a deploy test keeps them equal.
SET default_transaction_read_only = on;
SET statement_timeout = '15s';
\echo '[0] database time zone (kst_day assumes trace times are stored in UTC)'
SHOW TimeZone;
\echo '[1] Gateway traces covered (times in UTC); sessions_without_trace are runs missing from every table below'
SELECT count(*) AS traces, count(DISTINCT t.session_id) AS sessions,
  min(t.start_time) AS first_start_utc, max(t.start_time) AS last_start_utc,
  count(*) FILTER (WHERE t.end_time IS NULL) AS unfinished,
  count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM span s WHERE s.trace_id = t.id)) AS without_spans,
  (SELECT count(DISTINCT m.session_id) FROM message m WHERE m.session_id LIKE 'gw-%'
    AND NOT EXISTS (SELECT 1 FROM trace t2 WHERE t2.session_id = m.session_id)) AS sessions_without_trace
FROM trace t WHERE t.session_id LIKE 'gw-%';
\echo '[2] runs, outcome and Langflow run latency (ms) by KST day and recorded model'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) = 'llm'),
c AS (
  SELECT trace_id, string_agg(DISTINCT coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')'), '+'
    ORDER BY coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')')) AS model
  FROM s GROUP BY 1),
f AS (
  SELECT m.session_id,
    bool_or(m.sender = 'Machine' AND m.properties::jsonb -> 'source' ->> 'id' = 'JejuFinalValidation-v2') AS has_final,
    bool_or(m.sender = 'Machine' AND m.properties::jsonb -> 'source' ->> 'id' = 'JejuFinalValidation-v2'
      AND coalesce(m."text" LIKE '{"status":"blocked"%', false)) AS blocked,
    bool_or(m.category = 'error') AS agent_error
  FROM message m WHERE m.session_id LIKE 'gw-%' GROUP BY 1),
run AS (
  SELECT (t.start_time + interval '9 hours')::date AS kst_day, coalesce(c.model, '-') AS model,
    lower(t.status::text) AS trace_status, t.total_latency_ms AS ms,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome
  FROM trace t LEFT JOIN c ON c.trace_id = t.id LEFT JOIN f ON f.session_id = t.session_id
  WHERE t.session_id LIKE 'gw-%')
SELECT kst_day, model, count(*) AS runs,
  count(*) FILTER (WHERE outcome = 'accepted') AS accepted,
  count(*) FILTER (WHERE outcome = 'blocked') AS blocked,
  count(*) FILTER (WHERE outcome = 'agent_error') AS agent_error,
  count(*) FILTER (WHERE outcome = 'no_final') AS no_final,
  count(*) FILTER (WHERE trace_status = 'error') AS error_traces,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms)) AS p50_ms,
  round(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)) AS p95_ms,
  max(ms) AS max_ms
FROM run GROUP BY 1, 2 ORDER BY 1, 2;
\echo '[3] model calls, tool calls and tokens by KST day and recorded model'
WITH s AS (
  SELECT sp.trace_id, lower(sp.span_type::text) AS kind, lower(sp.status::text) = 'error' AS failed,
    sp.attributes::jsonb AS a, sp.name
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) IN ('llm', 'tool')),
u AS (
  SELECT trace_id, kind, failed,
    CASE WHEN a ->> 'gen_ai.usage.input_tokens' ~ '^[0-9]{1,15}$'
      THEN (a ->> 'gen_ai.usage.input_tokens')::bigint END AS input_tokens,
    CASE WHEN a ->> 'gen_ai.usage.output_tokens' ~ '^[0-9]{1,15}$'
      THEN (a ->> 'gen_ai.usage.output_tokens')::bigint END AS output_tokens,
    coalesce(nullif(a ->> 'gen_ai.response.model', ''), '(' || name || ')') AS model
  FROM s),
c AS (
  SELECT trace_id,
    count(*) FILTER (WHERE kind = 'llm') AS model_calls,
    count(*) FILTER (WHERE kind = 'llm' AND failed) AS failed_model_calls,
    count(*) FILTER (WHERE kind = 'tool') AS tool_calls,
    coalesce(sum(input_tokens) FILTER (WHERE kind = 'llm'), 0) AS input_tokens,
    coalesce(sum(output_tokens) FILTER (WHERE kind = 'llm'), 0) AS output_tokens,
    count(*) FILTER (WHERE kind = 'llm' AND (input_tokens IS NULL OR output_tokens IS NULL)) AS calls_without_usage,
    string_agg(DISTINCT model, '+' ORDER BY model) FILTER (WHERE kind = 'llm') AS model
  FROM u GROUP BY 1),
run AS (
  SELECT (t.start_time + interval '9 hours')::date AS kst_day, coalesce(c.model, '-') AS model,
    coalesce(c.model_calls, 0) AS model_calls, coalesce(c.failed_model_calls, 0) AS failed_model_calls,
    coalesce(c.tool_calls, 0) AS tool_calls, coalesce(c.input_tokens, 0) AS input_tokens,
    coalesce(c.output_tokens, 0) AS output_tokens, coalesce(c.calls_without_usage, 0) AS calls_without_usage
  FROM trace t LEFT JOIN c ON c.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%')
SELECT kst_day, model, count(*) AS runs,
  sum(model_calls) AS model_calls, round(avg(model_calls), 2) AS avg_model_calls, max(model_calls) AS max_model_calls,
  sum(failed_model_calls) AS failed_model_calls, round(avg(tool_calls), 2) AS avg_tool_calls,
  sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
  round(avg(input_tokens)) AS avg_input_per_run, round(avg(output_tokens)) AS avg_output_per_run,
  sum(calls_without_usage) AS calls_without_usage
FROM run GROUP BY 1, 2 ORDER BY 1, 2;
\echo '[4] latency per call (ms): model calls by recorded model, tool calls together'
SELECT CASE WHEN lower(sp.span_type::text) = 'tool' THEN 'tool'
    ELSE 'model ' || coalesce(nullif(sp.attributes::jsonb ->> 'gen_ai.response.model', ''), '(' || sp.name || ')') END AS call_type,
  count(*) AS calls, count(*) FILTER (WHERE lower(sp.status::text) = 'error') AS failed,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY sp.latency_ms)) AS p50_ms,
  round(percentile_cont(0.95) WITHIN GROUP (ORDER BY sp.latency_ms)) AS p95_ms,
  max(sp.latency_ms) AS max_ms
FROM span sp JOIN trace t ON t.id = sp.trace_id
WHERE t.session_id LIKE 'gw-%' AND lower(sp.span_type::text) IN ('llm', 'tool')
GROUP BY 1 ORDER BY 1;
