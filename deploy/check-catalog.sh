#!/usr/bin/env bash
# Run from any directory: sudo bash deploy/check-catalog.sh
# COMPOSE_PROJECT_NAME is inherited for isolated verification projects.
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose -f compose.yaml -f compose.catalog.yaml)
catalog=.local/catalog/agent-catalog-b0602a1.json
if [[ ! -f "$catalog" || -L "$catalog" ]]; then
  echo 'FAIL: prepare the private catalog file before checking the service.' >&2
  exit 1
fi
catalog_digest=$(sha256sum -- "$catalog")
catalog_digest=${catalog_digest%% *}
if [[ ! "$catalog_digest" =~ ^[a-f0-9]{64}$ ]]; then
  echo 'FAIL: could not calculate the private catalog SHA-256.' >&2
  exit 1
fi
"${compose[@]}" exec -T jeju-mcp node scripts/check-client.mjs
"${compose[@]}" exec -T -e "EXPECTED_CATALOG_VERSION=sha256:$catalog_digest" \
  jeju-mcp node --input-type=module - <<'JS'
try {
  const url = 'http://127.0.0.1:8788/readyz';
  const denied = await fetch(url, {signal: AbortSignal.timeout(3000)});
  if (denied.status !== 401) throw new Error('Unauthenticated readiness was not denied');
  const r = await fetch(url, {
    headers: {Authorization: `Bearer ${process.env.MCP_SERVICE_TOKEN}`},
    signal: AbortSignal.timeout(3000),
  });
  const b = await r.json();
  if (!r.ok || b.ready !== true || b.dataMode !== 'app-export'
    || b.catalogSchemaVersion !== '2.0' || b.placeCount !== 981
    || b.readOnly !== true || b.productionReady !== false)
    throw new Error('Unexpected catalog readiness');
  const expected = process.env.EXPECTED_CATALOG_VERSION;
  if (!/^sha256:[a-f0-9]{64}$/.test(expected ?? '') || b.dataVersion !== expected)
    throw new Error('The loaded catalog does not match the host file');
  console.log('PASS: missing token rejected; authenticated v2 catalog readiness; 981 places; host SHA-256 matches; productionReady=false');
} catch {
  console.error('FAIL: catalog readiness, authentication or host SHA-256 mismatch. No credential values were printed.');
  process.exitCode = 1;
}
JS
echo 'PASS: private catalog and three MCP tools checked. Langflow, Agent, Tunnel and Vercel are not connected by this overlay.'
