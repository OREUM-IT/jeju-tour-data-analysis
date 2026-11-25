-- Read-only check, after deploy/apply-agent-message-store-off.sh, that Gateway runs (session ids starting
-- with gw-) no longer leave stored Chat Input/Output messages in the Langflow message table.
-- Output is counts, booleans and one time (the last Gateway flow change): no session ids, texts or member data.
-- The boundary is the last change of the flow with the Gateway flow's exact name; any later flow change moves it.
-- Without exactly one such flow, with a time, nothing can be judged: [2] and [3] return no rows, can_judge is false.
-- Run outcomes use the final-validation span logic of deploy/agent-run-metrics.sql, character for character.
-- A run counts as redacted when deploy/redact-agent-history.py replaced its span text (the metrics SQL's marker
-- test). That tool also deleted the run's messages, so a redacted run never makes the check judgeable.
-- passed is false on any stored message after the boundary, true only when judgeable and none, else empty.
-- docs/32-agent-gateway-tunnel.md holds the same text inside the AWS command; a deploy test keeps them equal.
SET default_transaction_read_only = on;
SET statement_timeout = '15s';
SET TIME ZONE 'UTC';
\echo '[0] session time zone (UTC)'
SHOW TimeZone;
\echo '[1] Gateway flows with this exact name (must be 1) and their last change (UTC), the boundary below'
SELECT count(*) AS flows, max(updated_at) AS flow_changed_at_utc
FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행';
\echo '[2] Gateway runs (traces) started after (t) and before (f) the change, by outcome; redacted = text removed by the redaction tool; no rows unless flows is 1'
WITH b AS (
  SELECT max(updated_at) AS changed_at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행'
  HAVING count(*) = 1 AND max(updated_at) IS NOT NULL),
f AS (
  SELECT sp.trace_id,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text LIKE '{"redacted":true,"finalStatus":"%'
      OR sp.outputs -> 'message' ->> 'text' IS NOT NULL, false) END) AS has_final,
    bool_or(CASE WHEN sp.name = 'Jeju Final Validation' THEN coalesce(sp.outputs::text = '{"redacted":true,"finalStatus":"blocked"}'
      OR sp.outputs -> 'message' ->> 'text' LIKE '{"status":"blocked"%', false) END) AS blocked,
    bool_or(lower(sp.status::text) = 'error') AS agent_error
  FROM span sp JOIN trace t ON t.id = sp.trace_id
  WHERE t.session_id LIKE 'gw-%' AND sp.parent_span_id IS NULL GROUP BY 1),
run AS (
  SELECT t.start_time > b.changed_at AS after_flow_change,
    CASE WHEN f.has_final AND NOT f.blocked THEN 'accepted' WHEN f.has_final THEN 'blocked'
      WHEN f.agent_error THEN 'agent_error' ELSE 'no_final' END AS outcome,
    EXISTS (SELECT 1 FROM span sp WHERE sp.trace_id = t.id AND sp.outputs::text LIKE '{"redacted":true%') AS redacted
  FROM trace t CROSS JOIN b LEFT JOIN f ON f.trace_id = t.id
  WHERE t.session_id LIKE 'gw-%')
SELECT p.after_flow_change, count(r.outcome) AS runs,
  count(*) FILTER (WHERE r.outcome = 'accepted') AS accepted,
  count(*) FILTER (WHERE r.outcome = 'blocked') AS blocked,
  count(*) FILTER (WHERE r.outcome = 'agent_error') AS agent_error,
  count(*) FILTER (WHERE r.outcome = 'no_final') AS no_final,
  count(*) FILTER (WHERE r.redacted) AS redacted
FROM b CROSS JOIN (VALUES (true), (false)) AS p (after_flow_change)
  LEFT JOIN run r ON r.after_flow_change = p.after_flow_change
GROUP BY 1 ORDER BY 1 DESC;
\echo '[3] Gateway messages written after (t) and before (f) the change; stored = every message but an lfx error message'
WITH b AS (
  SELECT max(updated_at) AS changed_at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행'
  HAVING count(*) = 1 AND max(updated_at) IS NOT NULL),
msg AS (
  SELECT m."timestamp" > b.changed_at AS after_flow_change, m.category IS DISTINCT FROM 'error' AS stored, m.session_id
  FROM message m CROSS JOIN b
  WHERE m.session_id LIKE 'gw-%')
SELECT p.after_flow_change, count(x.session_id) AS messages,
  count(*) FILTER (WHERE x.stored) AS stored_messages,
  count(*) FILTER (WHERE NOT x.stored) AS error_messages,
  count(DISTINCT x.session_id) FILTER (WHERE x.stored) AS sessions_with_stored_message
FROM b CROSS JOIN (VALUES (true), (false)) AS p (after_flow_change)
  LEFT JOIN msg x ON x.after_flow_change = p.after_flow_change
GROUP BY 1 ORDER BY 1 DESC;
\echo '[4] can_judge = flows is 1 and an unredacted run after the change reached final validation; passed = f on a stored message after it, else t when can_judge'
WITH b AS (
  SELECT max(updated_at) AS changed_at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행'
  HAVING count(*) = 1 AND max(updated_at) IS NOT NULL),
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
  SELECT EXISTS (SELECT 1 FROM trace t JOIN b ON t.start_time > b.changed_at JOIN f ON f.trace_id = t.id
      WHERE t.session_id LIKE 'gw-%' AND f.has_final
        AND NOT EXISTS (SELECT 1 FROM span sp WHERE sp.trace_id = t.id AND sp.outputs::text LIKE '{"redacted":true%')) AS can_judge,
    NOT EXISTS (SELECT 1 FROM message m JOIN b ON m."timestamp" > b.changed_at
      WHERE m.session_id LIKE 'gw-%' AND m.category IS DISTINCT FROM 'error') AS no_stored_message)
SELECT can_judge, CASE WHEN NOT no_stored_message THEN false WHEN can_judge THEN true END AS passed
FROM v;
