import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { BASIS_POLICY, SOURCES } from '../src/agent-catalog.mjs';
import { loadCatalog, validateAgentServerCatalog, CatalogValidationError } from '../src/catalog.mjs';
import { createMcpHttpServer } from '../src/server.mjs';

const TOKEN = 'test-only-v2-mcp-service-token-0123456789abcdef';
const AUTH = { Authorization: `Bearer ${TOKEN}` };
const NOW = '2026-11-19T00:00:00.000Z';
const inputCatalog = () => ({
  schemaVersion: '2.0', kind: 'jeju-agent-catalog', dataMode: 'app-export',
  source: { repository: 'test/synthetic', commit: 'a'.repeat(40), placesContentSha256: 'b'.repeat(64),
    rawSha256: 'c'.repeat(64), themeMapVersion: 'synthetic-v1' },
  themeIds: ['coast'], basisPolicy: structuredClone(BASIS_POLICY), sources: structuredClone(SOURCES),
  places: [{ id: 'fixture-v2', name: '합성 해변', category: 'sea', kind: null, lat: 33.4, lon: 126.5,
    zone: 'JC', origin: 'raw_poi', web: null, themes: { coast: 1 }, themeRule: 'synthetic',
    attributes: { indoor: { value: false, basis: 'inferred', rule: 'synthetic category guess' } } }],
  diagnostics: {},
});
const request = () => ({ schemaVersion: '1.0', requestId: 'integration-v2', query: '해변을 추천해 주세요.',
  filters: { category: 'sea' }, excludedIds: [], requirements: [], timezone: 'Asia/Seoul', visitDate: '2026-12-07' });
const response = () => ({ schemaVersion: '1.0', requestId: 'integration-v2', action: 'recommend',
  placeIds: ['fixture-v2'], requirementClaims: [], confirmationNotice: false, noticeTopics: [] });

async function fixture(t) {
  const catalog = validateAgentServerCatalog(inputCatalog());
  const server = createMcpHttpServer({ catalog, token: TOKEN, now: NOW });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (route, body, headers = {}) => fetch(base + route, { method: 'POST',
    headers: { ...AUTH, 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  return { catalog, base, post };
}

test('bounded catalog loader recognizes v2 without changing evidence grades or source bytes', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'jeju-agent-server-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, 'catalog.json');
  const original = inputCatalog();
  const bytes = JSON.stringify(original, null, 2);
  await writeFile(file, bytes);
  const catalog = await loadCatalog(file);
  assert.equal(catalog.dataVersion, `sha256:${createHash('sha256').update(bytes).digest('hex')}`);
  assert.equal(catalog.dataMode, 'app-export');
  assert.deepEqual(catalog.places, original.places);
  assert.ok(Object.isFrozen(catalog.places[0].attributes));
  assert.throws(() => validateAgentServerCatalog({ ...original, secretExtra: 'not allowed' }), CatalogValidationError);
  assert.throws(() => validateAgentServerCatalog({ ...original, places: [...original.places, ...original.places] }), CatalogValidationError);
  await writeFile(file, JSON.stringify({ ...original, schemaVersion: '3.0' }));
  await assert.rejects(loadCatalog(file), CatalogValidationError);
});

test('v2 SDK discovery and legacy tool calls use the operator selected catalog', async t => {
  const { base, catalog } = await fixture(t);
  const client = new Client({ name: 'v2-offline-test', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(new URL('/mcp', base), { requestInit: { headers: AUTH } }));
  assert.deepEqual((await client.listTools()).tools.map(x => x.name).sort(), ['constraints.check', 'places.get', 'places.search']);
  const found = await client.callTool({ name: 'places.search', arguments: { theme: 'coast', zone: 'JC' } });
  assert.equal(found.isError, undefined);
  assert.deepEqual(found.structuredContent.data.items.map(x => x.id), ['fixture-v2']);
  assert.equal(found.structuredContent.dataVersion, catalog.dataVersion);
  const check = await client.callTool({ name: 'constraints.check', arguments: { ids: ['fixture-v2'], requirements: { indoorRequired: true } } });
  assert.equal(check.structuredContent.data.results[0].status, 'unknown');
  const ready = await (await fetch(base + '/readyz', { headers: AUTH })).json();
  assert.equal(ready.placeCount, 1);
  assert.equal(ready.catalogSchemaVersion, '2.0');
  assert.equal(ready.productionReady, false);
});

test('prepare and final validation bind the original request and catalog, blocking unsafe output without echo', async t => {
  const { post } = await fixture(t);
  const prepared = await (await post('/prepare-request', request())).json();
  assert.equal(prepared.status, 'prepared');
  assert.equal(prepared.candidateCount, 1);
  assert.equal(prepared.evaluatedAt, NOW);
  const accepted = await (await post('/validate-response', { request: prepared.request, response: response(), expectedCatalogVersion: prepared.catalogVersion })).json();
  assert.equal(accepted.status, 'accepted');
  assert.deepEqual(accepted.response, response());
  const unsafe = { ...response(), placeIds: ['injected-do-not-echo'] };
  const blocked = await (await post('/validate-response', { request: prepared.request, response: unsafe, expectedCatalogVersion: prepared.catalogVersion })).json();
  assert.equal(blocked.status, 'blocked');
  assert.equal(Object.hasOwn(blocked, 'response'), false);
  const changed = await (await post('/validate-response', { request: prepared.request, response: response(), expectedCatalogVersion: `sha256:${'0'.repeat(64)}` })).json();
  assert.equal(changed.status, 'blocked');
  assert.ok(changed.reasons.some(x => x.code === 'catalog_version_mismatch'));
});

test('validation routes reject unauthenticated, malformed, oversized and model-controlled policy inputs', async t => {
  const { post, base } = await fixture(t);
  assert.equal((await post('/prepare-request', request(), { Authorization: '' })).status, 401);
  assert.equal((await post('/prepare-request', request(), { Origin: 'https://attacker.example' })).status, 403);
  assert.equal((await fetch(base + '/validate-response', { headers: AUTH })).status, 405);
  assert.equal((await post('/prepare-request', request(), { 'Content-Type': 'text/plain' })).status, 415);
  const extra = await post('/prepare-request', { ...request(), allowedIds: ['fixture-v2'], now: '2099-01-01' });
  assert.equal(extra.status, 400);
  const missing = await post('/validate-response', { request: request(), response: response() });
  assert.equal(missing.status, 400);
  const oversized = await post('/prepare-request', { ...request(), query: 'x'.repeat(129 * 1024) });
  assert.equal(oversized.status, 413);
});
