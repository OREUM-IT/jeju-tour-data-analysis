#!/usr/bin/env bash
# Run from any directory: sudo bash deploy/check.sh
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose -f compose.yaml)
"${compose[@]}" exec -T jeju-mcp node scripts/check-client.mjs
"${compose[@]}" exec -T jeju-mcp node --input-type=module - <<'JS'
const url = 'http://127.0.0.1:8788/readyz';
const denied = await fetch(url, {signal: AbortSignal.timeout(3000)});
if (denied.status !== 401) throw new Error('Unauthenticated readiness was not denied');
const r = await fetch(url, {
  headers: {Authorization: `Bearer ${process.env.MCP_SERVICE_TOKEN}`},
  signal: AbortSignal.timeout(3000),
});
const b = await r.json();
if (!r.ok || b.dataMode !== 'fixture' || b.productionReady !== false || !b.readOnly)
  throw new Error('Unexpected bootstrap readiness');
console.log('PASS: missing token rejected; authenticated fixture readiness; productionReady=false');
JS
"${compose[@]}" exec -T postgres sh -eu -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U bootstrap_admin -d jeju_sandbox -v ON_ERROR_STOP=1' <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
    RAISE EXCEPTION 'pgvector extension missing';
  END IF;
  IF ('[1,2,3]'::vector <-> '[1,2,3]'::vector) <> 0 THEN
    RAISE EXCEPTION 'vector distance check failed';
  END IF;
END $$;
SELECT version(), extversion AS pgvector_version FROM pg_extension WHERE extname='vector';
SQL
echo 'PASS: private bootstrap checks. Real catalog, Langflow, Agent, Tunnel and Vercel are not connected.'
