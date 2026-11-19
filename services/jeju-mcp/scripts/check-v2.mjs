// Private catalog connection check: no model, no writes, no place names/IDs printed.
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { loadCatalog } from '../src/catalog.mjs';

let client;
try {
  const base = 'http://127.0.0.1:8788';
  const auth = { Authorization: `Bearer ${process.env.MCP_SERVICE_TOKEN}` };
  const catalog = await loadCatalog(process.env.MCP_CATALOG_PATH);
  assert.equal(catalog.schemaVersion, '2.0');
  assert.equal(catalog.dataMode, 'app-export');
  assert.equal(catalog.places.length, 981);
  const denied = await fetch(base + '/readyz', { signal: AbortSignal.timeout(5000) });
  assert.equal(denied.status, 401);
  const readyResponse = await fetch(base + '/readyz', { headers: auth, signal: AbortSignal.timeout(5000) });
  assert.equal(readyResponse.status, 200);
  const ready = await readyResponse.json();
  assert.equal(ready.catalogSchemaVersion, '2.0');
  assert.equal(ready.dataVersion, catalog.dataVersion);
  assert.equal(ready.placeCount, 981);
  assert.equal(ready.productionReady, false);
  client = new Client({ name: 'jeju-v2-private-check', version: '0.1.0' }, { capabilities: {} });
  await client.connect(new StreamableHTTPClientTransport(new URL(base + '/mcp'), {
    requestInit: { headers: auth, redirect: 'error' },
  }), { timeout: 10000 });
  const tools = await client.listTools();
  assert.deepEqual(tools.tools.map(t => t.name).sort(), ['constraints.check', 'places.get', 'places.search']);
  const call = async (name, args) => {
    const r = await client.callTool({ name, arguments: args }, undefined, { timeout: 10000 });
    assert.ok(!r.isError && r.structuredContent);
    return r.structuredContent;
  };
  const found = await call('places.search', { category: 'sea', maxResults: 2 });
  assert.equal(found.data.items.length, 2);
  const ids = found.data.items.map(p => p.id);
  const detail = await call('places.get', { ids });
  assert.equal(detail.data.items.length, 2);
  assert.ok(detail.data.items.every(p => ids.includes(p.id)));

  let unknown = 0;
  const visitDate = new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 10);
  for (let i = 0; i < catalog.places.length; i += 20) {
    const batch = catalog.places.slice(i, i + 20).map(p => p.id);
    const r = await call('constraints.check', { ids: batch, requirements: {
      wheelchairRequired: true, indoorRequired: true, maxVisitCostKRW: 100000,
      openAt: { date: visitDate, time: '12:00' },
    } });
    assert.equal(r.data.results.length, batch.length);
    for (const item of r.data.results) {
      assert.ok(item.found && batch.includes(item.id));
      assert.equal(item.checks.length, 4);
      assert.ok(item.checks.every(c => c.status === 'unknown'));
      unknown += item.checks.length;
    }
  }
  assert.equal(unknown, 3924);
  const post = async (route, body) => {
    const r = await fetch(base + route, { method: 'POST',
      headers: { ...auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    assert.equal(r.status, 200);
    return r.json();
  };
  const request = { schemaVersion: '1.0', requestId: 'private-v2-check', query: '실내 여부를 확인해 주세요.',
    timezone: 'Asia/Seoul', visitDate, filters: {}, excludedIds: [],
    requirements: [{ type: 'indoor_required', value: true }] };
  const prepared = await post('/prepare-request', request);
  assert.equal(prepared.status, 'prepared');
  const response = { schemaVersion: '1.0', requestId: request.requestId, action: 'cannot_confirm', placeIds: ids,
    requirementClaims: ids.map(placeId => ({ placeId, requirement: 'indoor_required', verdict: 'unknown' })),
    confirmationNotice: true, noticeTopics: ['indoor_unverified'] };
  const body = { request, response, expectedCatalogVersion: prepared.catalogVersion };
  assert.equal((await post('/validate-response', body)).status, 'accepted');
  const unsafe = structuredClone(body);
  unsafe.response.requirementClaims[0].verdict = 'pass';
  assert.equal((await post('/validate-response', unsafe)).status, 'blocked');
  const invented = structuredClone(body);
  invented.response.placeIds = ['synthetic-not-a-catalog-place'];
  assert.equal((await post('/validate-response', invented)).status, 'blocked');
  console.log(JSON.stringify({ status: 'passed', places: ready.placeCount, schemaVersion: '2.0',
    dataMode: 'app-export', constraintChecks: unknown, allConstraints: 'unknown',
    finalValidation: 'accepted unknown; blocked false pass and invented ID',
    productionReady: false, modelCalls: 0 }, null, 2));
  console.log('PASS: real catalog v2 connection checks. Langflow, DB search, Tunnel and Vercel remain disconnected.');
} catch {
  console.error('FAIL: v2 connection check. Check container health, catalog, token and response contract. No secret or place records printed.');
  process.exitCode = 1;
} finally {
  if (client) await client.close().catch(() => {});
}
