import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import { BASIS_POLICY } from '../src/agent-catalog.mjs';
import { validateAgentServerCatalog } from '../src/catalog.mjs';
import { createGatewayScopes, GatewayScopeError } from '../src/gateway-scopes.mjs';
import { createMcpHttpServer } from '../src/server.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const NOW = Date.parse('2026-11-19T00:00:00.000Z');
const TOKEN = 'm'.repeat(64);
const CONTROL = 'c'.repeat(64);
const ID = 'gw-' + 'a'.repeat(32);
const OTHER_ID = 'gw-' + 'b'.repeat(32);
const verified = value => ({ value, basis: 'verified', source: 'synthetic',
  verifiedAt: '2026-11-18T00:00:00Z', validUntil: '2026-11-21T15:00:00Z' });
const place = (id, name, extra = {}) => ({ id, name, category: 'art', kind: null,
  lat: 33.4, lon: 126.5, zone: 'JC', origin: 'raw_poi', web: null,
  themes: { art: 1 }, themeRule: 'synthetic', attributes: {}, ...extra });
const catalog = (extra = []) => validateAgentServerCatalog({
  schemaVersion: '2.0', kind: 'jeju-agent-catalog', dataMode: 'app-export',
  source: { repository: 'test/synthetic', commit: 'a'.repeat(40), placesContentSha256: 'b'.repeat(64),
    rawSha256: 'c'.repeat(64), themeMapVersion: 'synthetic-v1' },
  themeIds: ['art', 'food'], basisPolicy: structuredClone(BASIS_POLICY),
  sources: { synthetic: { name: 'Synthetic test evidence only' } },
  places: [place('p1', '합성 미술관', { attributes: { indoor: verified(true),
    hours: verified({ openMin: 540, closeMin: 1080, text: '매일 09:00~18:00' }) } }),
  place('p2', '합성 공원'), place('food', '합성 음식점', { category: 'food' }),
  place('east', '합성 동쪽 전시관', { zone: 'E' }), place('excluded', '합성 제외 전시관'),
  place('other-text', '별도 미술관'), ...extra], diagnostics: {},
});
const request = (extra = {}) => ({ schemaVersion: '1.0', requestId: ID, query: '합성 장소 후보를 보여 주세요.',
  timezone: 'Asia/Seoul', visitDate: '2026-11-20', filters: { category: 'art', theme: 'art', zone: 'JC', text: '합성' },
  excludedIds: ['excluded'], requirements: [], ...extra });
const registration = (value = request(), extra = {}) => ({ request: value, principal: '1'.repeat(64), deadlineMs: NOW + 110_000, ...extra });
const tool = (name, args, requestId = ID) => ({ requestId, name, arguments: args });
const throwsCode = (callback, code, status) => assert.throws(callback, error => error instanceof GatewayScopeError
  && error.code === code && (status === undefined || error.status === status));

test('registration preserves immutable input, exact request binding, one active scope and tombstones', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  const original = request();
  const prepared = scopes.register(registration(original));
  assert.equal(prepared.candidateCount, 2);
  assert.deepEqual(prepared.request, original);
  original.filters.category = 'food';
  prepared.request.filters.zone = 'E';
  const reversedKeys = Object.fromEntries(Object.entries(request()).reverse());
  assert.deepEqual(scopes.prepare(reversedKeys).request, request());
  throwsCode(() => scopes.prepare({ ...request(), requirements: [{ type: 'indoor_required', value: true }] }), 'GATEWAY_REQUEST_MISMATCH', 409);
  throwsCode(() => scopes.register(registration()), 'GATEWAY_SCOPE_DUPLICATE', 409);
  throwsCode(() => scopes.register(registration(request({ requestId: OTHER_ID }))), 'GATEWAY_SCOPE_BUSY', 429);
  assert.deepEqual(scopes.release(ID), { status: 'released' });
  assert.equal(scopes.ready().activeScopes, 0);
  throwsCode(() => scopes.register(registration()), 'GATEWAY_SCOPE_DUPLICATE', 409);
  throwsCode(() => scopes.prepare(request()), 'GATEWAY_SCOPE_UNAVAILABLE', 404);
  scopes.register(registration(request({ requestId: OTHER_ID })));
  assert.equal(scopes.ready().activeScopes, 1);
});

test('strict registration rejects untrusted identity, timestamps, extra policy and invalid requests', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  for (const body of [registration(request({ requestId: 'client-session' })), registration(undefined, { principal: 'user@example.invalid' }),
    registration(undefined, { deadlineMs: NOW }), registration(undefined, { deadlineMs: NOW + 110_001 }),
    registration(undefined, { deadlineMs: NOW + 1.5 }), registration(undefined, { allowedIds: ['p1'] }),
    registration({ ...request(), now: '2099-01-01' }), registration(request({ filters: { zone: 'missing' } }))]) {
    assert.throws(() => scopes.register(body), GatewayScopeError);
    assert.equal(scopes.ready().activeScopes, 0);
  }
});

test('search can only narrow original candidates, preserving omitted filters and excluded IDs before limits', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  scopes.register(registration());
  const all = scopes.call(tool('places.search', {}));
  assert.deepEqual(all.data.items.map(item => item.id), ['p1', 'p2']);
  assert.equal(all.data.totalMatches, 2);
  assert.equal(all.data.category, 'art');
  assert.equal(all.data.theme, 'art');
  assert.equal(all.data.zone, 'JC');
  assert.deepEqual(all.data.excludedIds, ['excluded']);
  const narrowed = scopes.call(tool('places.search', { query: '미술관', maxResults: 1 }));
  assert.deepEqual(narrowed.data.items.map(item => item.id), ['p1']);
  assert.equal(narrowed.data.totalMatches, 1);
  assert.deepEqual(scopes.call(tool('places.search', { excludedIds: ['p1'] })).data.items.map(item => item.id), ['p2']);
  for (const args of [{ category: 'food' }, { theme: 'food' }, { zone: 'E' }]) {
    throwsCode(() => scopes.call(tool('places.search', args)), 'GATEWAY_FILTER_CONFLICT', 400);
  }
  throwsCode(() => scopes.call(tool('places.search', { maxResults: 11 })), 'GATEWAY_SEARCH_LIMIT_EXCEEDED', 400);
});

test('original exclusions above tool input limit remain enforced without rejecting a valid request', () => {
  const extras = Array.from({ length: 205 }, (_, index) => place(`x${index}`, '합성 제외 장소'));
  const scopes = createGatewayScopes({ catalog: catalog(extras), now: NOW });
  scopes.register(registration(request({ excludedIds: ['excluded', ...extras.map(item => item.id)] })));
  const found = scopes.call(tool('places.search', {}));
  assert.deepEqual(found.data.items.map(item => item.id), ['p1', 'p2']);
  assert.equal(found.data.excludedIds.length, 206);
});

test('get and check reject IDs outside registered candidates and invalid calls consume the same budget', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  scopes.register(registration(request({ requirements: [{ type: 'indoor_required', value: true }] })));
  for (const name of ['places.get', 'constraints.check']) {
    for (const id of ['food', 'east', 'excluded', 'missing']) {
      throwsCode(() => scopes.call(tool(name, { ids: [id] })), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
    }
  }
  throwsCode(() => scopes.call(tool('places.get', { ids: ['p1'] })), 'GATEWAY_TOOL_BUDGET_EXHAUSTED', 429);
});

test('unknown names, extra envelope fields and malformed tool arguments cannot bypass the eight-call budget', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  scopes.register(registration());
  const calls = [tool('shell.exec', {}), { ...tool('places.search', {}), budget: 1000 },
    tool('places.search', { url: 'http://169.254.169.254' }), tool('places.get', { ids: [] }),
    tool('places.get', { ids: ['p1', 'p1'] }), tool('constraints.check', { ids: ['p1'], requirements: { indoorRequired: 'true' } }),
    tool('places.search', { maxResults: 100 }), tool('constraints.check', { ids: ['p1'] })];
  for (const call of calls) assert.throws(() => scopes.call(call), GatewayScopeError);
  throwsCode(() => scopes.call(tool('places.search', {})), 'GATEWAY_TOOL_BUDGET_EXHAUSTED', 429);
});

test('constraint checks reconstruct every original condition and preserve individual requirement dates', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  const original = request({ requirements: [
    { type: 'indoor_required', value: true, date: '2026-11-22' },
    { type: 'open_at', value: '10:00', date: '2026-11-20' },
    { type: 'wheelchair', value: true },
    { type: 'budget_cap', value: 30_000, unit: 'KRW_per_person_per_day' },
  ] });
  scopes.register(registration(original));
  const omitted = scopes.call(tool('constraints.check', { ids: ['p1'] }));
  const weakened = scopes.call(tool('constraints.check', { ids: ['p1'], requirements: {
    indoorRequired: false, wheelchairRequired: false, maxVisitCostKRW: 999_999,
    openAt: { date: '2026-11-19', time: '12:00' },
  } }));
  assert.deepEqual(weakened, omitted);
  const checked = omitted.data.results[0];
  assert.equal(checked.checks.length, 4);
  assert.equal(checked.status, 'unknown');
  assert.deepEqual(checked.checks.map(item => [item.constraint, item.status, item.visitDate]), [
    ['indoorRequired', 'unknown', '2026-11-22'], ['openAt', 'pass', '2026-11-20'],
    ['wheelchairRequired', 'unknown', '2026-11-20'], ['maxVisitCostKRW', 'unknown', '2026-11-20'],
  ]);
  assert.equal(checked.checks[3].requestedValue, 30_000);
  assert.deepEqual(checked.checks[1].requestedValue, { date: '2026-11-20', time: '10:00' });
  const response = { schemaVersion: '1.0', requestId: ID, action: 'cannot_confirm', placeIds: ['p1'],
    requirementClaims: original.requirements.map((entry, index) => ({ placeId: 'p1', requirement: entry.type,
      verdict: checked.checks[index].status })), confirmationNotice: true,
    noticeTopics: ['indoor_unverified', 'wheelchair_unverified', 'cost_unknown'] };
  const result = scopes.validate({ request: original, response, expectedCatalogVersion: scopes.prepare(original).catalogVersion });
  assert.equal(result.status, 'accepted');
});

test('final validation rejects changed request, catalog and false claims without returning unsafe response', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  const original = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const prepared = scopes.register(registration(original));
  const body = { request: original, expectedCatalogVersion: prepared.catalogVersion,
    response: { schemaVersion: '1.0', requestId: ID, action: 'recommend', placeIds: ['p2'],
      requirementClaims: [{ placeId: 'p2', requirement: 'indoor_required', verdict: 'pass' }], confirmationNotice: false, noticeTopics: [] } };
  const blocked = scopes.validate(body);
  assert.equal(blocked.status, 'blocked');
  assert.equal('response' in blocked, false);
  throwsCode(() => scopes.validate({ ...body, request: request() }), 'GATEWAY_REQUEST_MISMATCH', 409);
  throwsCode(() => scopes.validate({ ...body, expectedCatalogVersion: 'sha256:' + '0'.repeat(64) }), 'GATEWAY_CATALOG_MISMATCH', 409);
});

test('deadline and process restart fail closed without carrying scopes into a fresh process', () => {
  let now = NOW;
  const input = catalog();
  const scopes = createGatewayScopes({ catalog: input, now: () => now });
  scopes.register(registration());
  now += 110_000;
  throwsCode(() => scopes.call(tool('places.search', {})), 'GATEWAY_SCOPE_EXPIRED', 410);
  assert.equal(scopes.ready().activeScopes, 0);
  throwsCode(() => scopes.prepare(request()), 'GATEWAY_SCOPE_UNAVAILABLE', 404);
  const fresh = createGatewayScopes({ catalog: input, now: NOW });
  throwsCode(() => fresh.call(tool('places.search', {})), 'GATEWAY_SCOPE_UNAVAILABLE', 404);
});

async function serverFixture(t, { enabled = true, now = NOW } = {}) {
  const input = catalog();
  const server = createMcpHttpServer({ catalog: input, token: TOKEN,
    ...(enabled ? { gatewayControlToken: CONTROL } : {}), now });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = (path, body, { method = 'POST', token = TOKEN, headers = {} } = {}) => fetch(base + path,
    { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  return { call, base, input };
}

test('HTTP scopes are opt-in and control/execution tokens cannot cross authentication boundaries', async t => {
  const disabled = await serverFixture(t, { enabled: false });
  assert.equal((await disabled.call('/gateway/readyz', undefined, { method: 'GET', token: CONTROL })).status, 404);
  const { call, input } = await serverFixture(t);
  assert.equal((await call('/gateway/scopes', registration())).status, 401);
  assert.equal((await call('/gateway/scopes', registration(), { token: CONTROL, headers: { Origin: 'https://attacker.invalid' } })).status, 403);
  assert.equal((await call('/gateway/scopes', registration(), { token: CONTROL })).status, 200);
  assert.equal((await call('/gateway/prepare-request', request(), { token: CONTROL })).status, 401);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}), { token: CONTROL })).status, 401);
  assert.equal((await call('/gateway/scopes/' + ID, undefined, { method: 'DELETE' })).status, 401);
  assert.equal((await call('/gateway/readyz', undefined, { method: 'GET' })).status, 401);
  const ready = await (await call('/gateway/readyz', undefined, { method: 'GET', token: CONTROL })).json();
  assert.deepEqual(ready, { ready: true, activeScopes: 1, toolCallLimit: 8, catalogSchemaVersion: '2.0',
    dataMode: 'app-export', placeCount: 6, dataVersion: input.dataVersion, readOnly: true, productionReady: false });
  assert.equal((await call('/gateway/prepare-request', request())).status, 200);
  assert.equal((await call('/gateway/scopes/' + ID, undefined, { method: 'DELETE', token: CONTROL })).status, 200);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}))).status, 404);
  assert.throws(() => createMcpHttpServer({ catalog: input, token: TOKEN, gatewayControlToken: TOKEN }));
});

test('concurrent HTTP calls reserve at most eight tool executions and malformed requests do not reset the scope', async t => {
  const { call } = await serverFixture(t);
  await call('/gateway/scopes', registration(), { token: CONTROL });
  const responses = await Promise.all(Array.from({ length: 12 }, () => call('/gateway/tools/call', tool('places.get', { ids: ['p1'] }))));
  assert.equal(responses.filter(response => response.status === 200).length, 8);
  assert.equal(responses.filter(response => response.status === 429).length, 4);
  assert.equal((await call('/gateway/scopes', registration(), { token: CONTROL })).status, 409);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}))).status, 429);
});

test('legacy SDK and unscoped validation remain available alongside scoped routes', async t => {
  const { call, base } = await serverFixture(t);
  const client = new Client({ name: 'scoped-route-regression', version: '1.0' });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(new URL('/mcp', base), {
    requestInit: { headers: { Authorization: `Bearer ${TOKEN}` } },
  }));
  assert.deepEqual((await client.listTools()).tools.map(tool => tool.name).sort(), ['constraints.check', 'places.get', 'places.search']);
  assert.equal((await client.callTool({ name: 'places.get', arguments: { ids: ['food'] } })).structuredContent.data.items[0].id, 'food');
  assert.equal((await call('/prepare-request', request({ requestId: 'legacy' }))).status, 200);
  assert.equal((await call('/readyz', undefined, { method: 'GET' })).status, 200);
  const invalid = await call('/gateway/tools/call', tool('places.get', { ids: ['p1'] }), { headers: { 'Content-Type': 'text/plain' } });
  assert.equal(invalid.status, 415);
  assert.equal((await call('/gateway/tools/call', undefined, { method: 'GET' })).status, 405);
});
