import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import { BASIS_POLICY } from '../src/agent-catalog.mjs';
import { validateAgentServerCatalog } from '../src/catalog.mjs';
import { createGatewayScopes, GatewayScopeError } from '../src/gateway-scopes.mjs';
import { createRuntimeValidator } from '../src/recommendation-validation.mjs';
import { createMcpHttpServer } from '../src/server.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const NOW = Date.parse('2026-11-19T00:00:00.000Z');
const TOKEN = 'm'.repeat(64);
const CONTROL = 'c'.repeat(64);
const ID = 'gw-' + 'a'.repeat(32);
const OTHER_ID = 'gw-' + 'b'.repeat(32);
const THIRD_ID = 'gw-' + 'c'.repeat(32);
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
const recommendation = (placeIds = ['p2'], extra = {}) => ({ schemaVersion: '1.0', requestId: ID,
  action: 'recommend', placeIds, requirementClaims: [], confirmationNotice: false, noticeTopics: [], ...extra });
function validationFixture({ original = request(), extraPlaces = [] } = {}) {
  const input = catalog(extraPlaces), scopes = createGatewayScopes({ catalog: input, now: NOW });
  const prepared = scopes.register(registration(original));
  const body = response => ({ request: original, response, expectedCatalogVersion: prepared.catalogVersion });
  return { scopes, prepared, body, strict: createRuntimeValidator({ catalog: input, now: NOW }),
    validate: response => scopes.validate(body(response)) };
}

test('configured single-slot registration preserves immutable input, exact binding and tombstones', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW, maxConcurrent: 1 });
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

test('default two scopes isolate candidates, original requests, validation and each eight-call budget', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  const first = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const second = request({ requestId: OTHER_ID, filters: { category: 'food' } });
  const a = scopes.register(registration(first));
  const b = scopes.register(registration(second, { principal: '2'.repeat(64) }));
  assert.equal(scopes.ready().activeScopes, 2);
  assert.equal(scopes.ready().maxConcurrentScopes, 2);
  throwsCode(() => scopes.register(registration(request({ requestId: THIRD_ID }))), 'GATEWAY_SCOPE_BUSY', 429);
  assert.deepEqual(scopes.call(tool('places.search', {})).data.items.map(item => item.id), ['p1', 'p2']);
  assert.deepEqual(scopes.call(tool('places.search', {}, OTHER_ID)).data.items.map(item => item.id), ['food']);
  throwsCode(() => scopes.call(tool('places.get', { ids: ['food'] })), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
  throwsCode(() => scopes.call(tool('places.get', { ids: ['p1'] }, OTHER_ID)), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
  throwsCode(() => scopes.prepare({ ...first, requestId: OTHER_ID }), 'GATEWAY_REQUEST_MISMATCH', 409);
  const cross = scopes.validate({ request: second, expectedCatalogVersion: b.catalogVersion,
    response: recommendation(['p1'], { requestId: OTHER_ID }) });
  assert.equal(cross.status, 'blocked');
  const accepted = scopes.validate({ request: second, expectedCatalogVersion: b.catalogVersion,
    response: recommendation(['food'], { requestId: OTHER_ID }) });
  assert.equal(accepted.status, 'accepted');
  assert.deepEqual(accepted.response.placeIds, ['food']);
  const recovered = scopes.validate({ request: first, expectedCatalogVersion: a.catalogVersion,
    response: recommendation(['p2']) });
  assert.equal(recovered.status, 'accepted');
  assert.equal(recovered.response.action, 'cannot_confirm');
  for (let i = 0; i < 6; i += 1) scopes.call(tool('places.get', { ids: ['p1'] }));
  throwsCode(() => scopes.call(tool('places.search', {})), 'GATEWAY_TOOL_BUDGET_EXHAUSTED', 429);
  for (let i = 0; i < 6; i += 1) scopes.call(tool('places.get', { ids: ['food'] }, OTHER_ID));
  throwsCode(() => scopes.call(tool('places.search', {}, OTHER_ID)), 'GATEWAY_TOOL_BUDGET_EXHAUSTED', 429);
  scopes.release(ID);
  assert.deepEqual(scopes.prepare(second), b);
  assert.equal(scopes.ready().activeScopes, 1);
  scopes.register(registration(request({ requestId: THIRD_ID })));
  assert.equal(scopes.ready().activeScopes, 2);
  throwsCode(() => scopes.register(registration(first)), 'GATEWAY_SCOPE_DUPLICATE', 409);
});

test('expiring one of two scopes frees only its slot and preserves the other request and budget', () => {
  let now = NOW;
  const scopes = createGatewayScopes({ catalog: catalog(), now: () => now });
  scopes.register(registration(undefined, { deadlineMs: NOW + 1000 }));
  const second = request({ requestId: OTHER_ID });
  scopes.register(registration(second, { principal: '2'.repeat(64) }));
  scopes.call(tool('places.search', {}, OTHER_ID));
  now += 1000;
  throwsCode(() => scopes.call(tool('places.search', {})), 'GATEWAY_SCOPE_EXPIRED', 410);
  scopes.register(registration(request({ requestId: THIRD_ID })));
  assert.equal(scopes.ready().activeScopes, 2);
  assert.deepEqual(scopes.prepare(second).request, second);
  for (let i = 0; i < 7; i += 1) scopes.call(tool('places.search', {}, OTHER_ID));
  throwsCode(() => scopes.call(tool('places.search', {}, OTHER_ID)), 'GATEWAY_TOOL_BUDGET_EXHAUSTED', 429);
  assert.deepEqual(scopes.call(tool('places.search', {}, THIRD_ID)).data.items.map(item => item.id), ['p1', 'p2']);
});

test('concurrency configuration is an integer from one through four', () => {
  for (const maxConcurrent of [0, 5, -1, 2.5, NaN, Infinity, '2', null, false]) {
    throwsCode(() => createGatewayScopes({ catalog: catalog(), now: NOW, maxConcurrent }), 'INVALID_GATEWAY_CONCURRENCY', 503);
    assert.throws(() => createMcpHttpServer({ catalog: catalog(), token: TOKEN,
      gatewayControlToken: CONTROL, gatewayMaxConcurrent: maxConcurrent }));
  }
  for (const maxConcurrent of [1, 2, 3, 4]) {
    const scopes = createGatewayScopes({ catalog: catalog(), now: NOW, maxConcurrent });
    for (let i = 0; i < maxConcurrent; i += 1) {
      scopes.register(registration(request({ requestId: 'gw-' + String(i).repeat(32) })));
    }
    assert.equal(scopes.ready().maxConcurrentScopes, maxConcurrent);
    assert.equal(scopes.ready().activeScopes, maxConcurrent);
    throwsCode(() => scopes.register(registration()), 'GATEWAY_SCOPE_BUSY', 429);
  }
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

test('one in-scope ID never makes an out-of-scope ID readable in the same get or check call', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  scopes.register(registration(request({ requirements: [{ type: 'indoor_required', value: true }] })));
  for (const name of ['places.get', 'constraints.check']) {
    for (const ids of [['p1', 'food'], ['east', 'p1'], ['p2', 'excluded']]) {
      throwsCode(() => scopes.call(tool(name, { ids })), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
    }
  }
  throwsCode(() => scopes.call(tool('places.get', { ids: ['p1', 'missing'] })), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
  assert.deepEqual(scopes.call(tool('places.get', { ids: ['p1', 'p2'] })).data.items.map(item => item.id), ['p1', 'p2']);
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

test('final validation recomputes false claims but rejects changed request and catalog binding', () => {
  const scopes = createGatewayScopes({ catalog: catalog(), now: NOW });
  const original = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const prepared = scopes.register(registration(original));
  const body = { request: original, expectedCatalogVersion: prepared.catalogVersion,
    response: { schemaVersion: '1.0', requestId: ID, action: 'recommend', placeIds: ['p2'],
      requirementClaims: [{ placeId: 'p2', requirement: 'indoor_required', verdict: 'pass' }], confirmationNotice: false, noticeTopics: [] } };
  const result = scopes.validate(body);
  assert.equal(result.status, 'accepted');
  assert.equal(result.response.action, 'cannot_confirm');
  assert.deepEqual(result.response.requirementClaims, [{ placeId: 'p2', requirement: 'indoor_required', verdict: 'unknown' }]);
  assert.equal(result.response.confirmationNotice, true);
  assert.deepEqual(result.response.noticeTopics, ['indoor_unverified']);
  throwsCode(() => scopes.validate({ ...body, request: request() }), 'GATEWAY_REQUEST_MISMATCH', 409);
  throwsCode(() => scopes.validate({ ...body, expectedCatalogVersion: 'sha256:' + '0'.repeat(64) }), 'GATEWAY_CATALOG_MISMATCH', 409);
});

test('already accepted responses up to five places are unchanged and recovery is idempotent', () => {
  const { validate, strict, body } = validationFixture();
  const response = recommendation(['p2', 'p1']);
  const original = structuredClone(response);
  assert.deepEqual(validate(response), strict.validate(body(response)));
  assert.deepEqual(response, original);
  const recovered = validate({ ...response, requirementClaims: null });
  assert.equal(recovered.status, 'accepted');
  assert.deepEqual(recovered.response, response);
  assert.deepEqual(validate(recovered.response), recovered);
});

test('malformed or missing claims and missing notices recover selected IDs using every registered requirement', () => {
  const original = request({ requirements: [
    { type: 'indoor_required', value: true, date: '2026-11-22' },
    { type: 'open_at', value: '10:00', date: '2026-11-20' },
    { type: 'wheelchair', value: true },
    { type: 'budget_cap', value: 30_000, unit: 'KRW_per_person_per_day' },
  ] });
  const { validate, strict, body, prepared } = validationFixture({ original });
  const responses = [
    recommendation(['p1']),
    recommendation(['p1'], { requirementClaims: null }),
    recommendation(['p1'], { requirementClaims: [{ placeId: 'p1', requirement: 'indoor_required', verdict: ['pass'] }] }),
    recommendation(['p1'], { requirementClaims: original.requirements.map(entry => ({ placeId: 'p1', requirement: entry.type, verdict: 'pass' })) }),
    { schemaVersion: '1.0', requestId: ID, action: 'cannot_confirm', placeIds: ['p1'], privateModelProse: 'Must never be returned.' },
  ];
  for (const response of responses) {
    const unchanged = structuredClone(response);
    assert.equal(strict.validate(body(response)).status, 'blocked');
    const recovered = validate(response);
    assert.equal(recovered.status, 'accepted');
    assert.deepEqual(recovered.response, recommendation(['p1'], { action: 'cannot_confirm', confirmationNotice: true,
      requirementClaims: [
        { placeId: 'p1', requirement: 'indoor_required', verdict: 'unknown' },
        { placeId: 'p1', requirement: 'open_at', verdict: 'pass' },
        { placeId: 'p1', requirement: 'wheelchair', verdict: 'unknown' },
        { placeId: 'p1', requirement: 'budget_cap', verdict: 'unknown' },
      ], noticeTopics: ['indoor_unverified', 'wheelchair_unverified', 'cost_unknown'] }));
    assert.deepEqual(recovered.reasons, []);
    for (const key of ['requestSha256', 'catalogSha256', 'catalogVersion', 'policyVersion', 'candidateCount']) assert.equal(recovered[key], prepared[key]);
    assert.deepEqual(validate(recovered.response), recovered);
    assert.equal(strict.validate(body(recovered.response)).status, 'accepted');
    assert.deepEqual(response, unchanged);
  }
});

test('recovery drops unknown, excluded, out-of-filter, duplicate and verified failing selections without inventing places', () => {
  const original = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const { validate } = validationFixture({ original,
    extraPlaces: [place('failed', '합성 야외 전시관', { attributes: { indoor: verified(false) } })] });
  const response = recommendation(['food', 'p2', 'failed', 'p2', 'excluded', 'p1', 'missing', 'east', 'other-text']);
  const recovered = validate(response);
  assert.equal(recovered.status, 'accepted');
  assert.deepEqual(recovered.response.placeIds, ['p2', 'p1']);
  assert.deepEqual(recovered.response.requirementClaims, [
    { placeId: 'p2', requirement: 'indoor_required', verdict: 'unknown' },
    { placeId: 'p1', requirement: 'indoor_required', verdict: 'pass' },
  ]);
  assert.equal(recovered.response.action, 'cannot_confirm');
  assert.deepEqual(recovered.response.noticeTopics, ['indoor_unverified']);
  assert.deepEqual(validate(recovered.response), recovered);
});

test('recovery requires an exact response identity and bounded explicit ID strings, never prose extraction', () => {
  const { validate } = validationFixture({ original: request({ requirements: [{ type: 'indoor_required', value: true }] }) });
  for (const response of [null, JSON.stringify(recommendation()), [recommendation()], {},
    recommendation(['p2'], { schemaVersion: '2.0' }), recommendation(['p2'], { requestId: OTHER_ID }),
    recommendation(['p2'], { action: ['recommend'] }), recommendation(['p2'], { action: 'ask_clarification' }),
    recommendation(['p2'], { action: 'impossible' }), recommendation('p2'), recommendation([]),
    recommendation(Array(51).fill('p2')), recommendation(['p2', 1]), recommendation([['p2']]),
    recommendation([null]), recommendation(['']), recommendation([' p2']), recommendation(['x'.repeat(161)]),
    { schemaVersion: '1.0', requestId: ID, action: 'recommend', text: 'Choose p2.' }]) {
    const blocked = validate(response);
    assert.equal(blocked.status, 'blocked');
    assert.equal('response' in blocked, false);
  }
});

test('no eligible model selection remains blocked even when other catalog candidates are available', () => {
  const { validate, strict, body, prepared } = validationFixture({
    original: request({ requirements: [{ type: 'indoor_required', value: true }] }),
    extraPlaces: [place('failed', '합성 야외 전시관', { attributes: { indoor: verified(false) } })],
  });
  assert.ok(prepared.candidateCount > 0);
  for (const ids of [['missing'], ['excluded', 'food', 'east'], ['failed']]) {
    const response = recommendation(ids);
    const blocked = validate(response);
    assert.equal(blocked.status, 'blocked');
    assert.deepEqual(blocked, strict.validate(body(response)));
    assert.equal('response' in blocked, false);
  }
});

test('more than five valid selections are capped in model order and no requirements produce ordinary recommend', () => {
  const extraPlaces = Array.from({ length: 7 }, (_, index) => place(`extra-${index}`, `합성 추가 ${index}`));
  const { validate, strict, body } = validationFixture({ extraPlaces });
  const ordered = ['extra-6', 'extra-2', 'p2', 'extra-4', 'p1', 'extra-0', 'extra-1'];
  const response = recommendation(ordered);
  assert.equal(strict.validate(body(response)).status, 'accepted');
  const recovered = validate(response);
  assert.deepEqual(recovered.response, recommendation(ordered.slice(0, 5)));
  assert.deepEqual(validate(recovered.response), recovered);
  const malformed = validate({ ...response, requirementClaims: 'untrusted', noticeTopics: ['cost_unknown'] });
  assert.deepEqual(malformed, recovered);
});

test('rule-action selection: the server composes action, claims and notices from model-chosen IDs only', () => {
  const original = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const { validate, strict, body, prepared } = validationFixture({ original,
    extraPlaces: [place('failed', '합성 야외 전시관', { attributes: { indoor: verified(false) } })] });
  const selection = { placeIds: ['food', 'p2', 'failed', 'p2', 'excluded', 'p1', 'missing'] };
  const unchanged = structuredClone(selection);
  assert.equal(strict.validate(body(selection)).status, 'blocked');
  const composed = validate(selection);
  assert.equal(composed.status, 'accepted');
  assert.deepEqual(composed.reasons, []);
  assert.deepEqual(composed.response, recommendation(['p2', 'p1'], { action: 'cannot_confirm', confirmationNotice: true,
    requirementClaims: [
      { placeId: 'p2', requirement: 'indoor_required', verdict: 'unknown' },
      { placeId: 'p1', requirement: 'indoor_required', verdict: 'pass' },
    ], noticeTopics: ['indoor_unverified'] }));
  for (const key of ['requestSha256', 'catalogSha256', 'catalogVersion', 'policyVersion', 'candidateCount']) assert.equal(composed[key], prepared[key]);
  // The Gateway re-validates the composed response; it must come back unchanged.
  assert.deepEqual(validate(composed.response), composed);
  assert.equal(strict.validate(body(composed.response)).status, 'accepted');
  assert.deepEqual(selection, unchanged);
  const only = validate({ placeIds: ['p1'] });
  assert.deepEqual(only.response, recommendation(['p1'], {
    requirementClaims: [{ placeId: 'p1', requirement: 'indoor_required', verdict: 'pass' }] }));
});

test('rule-action selection caps five in model order and ignores any model action when IDs are the only key', () => {
  const extraPlaces = Array.from({ length: 7 }, (_, index) => place(`extra-${index}`, `합성 추가 ${index}`));
  const { validate } = validationFixture({ extraPlaces });
  const ordered = ['extra-6', 'extra-2', 'p2', 'extra-4', 'p1', 'extra-0', 'extra-1'];
  assert.deepEqual(validate({ placeIds: ordered }).response, recommendation(ordered.slice(0, 5)));
  // Extra keys are not a selection: they follow the existing strict/recovery path.
  const legacy = validate({ placeIds: ['p2'], action: 'impossible' });
  assert.equal(legacy.status, 'blocked');
});

test('rule-action selection returns impossible only when no registered candidate survives verified failures', () => {
  const closed = { attributes: { hours: verified({ openMin: 540, closeMin: 1080, text: '매일 09:00~18:00' }) } };
  const lateOnly = request({ filters: { category: 'art', zone: 'JC', text: '합성 늦은' },
    requirements: [{ type: 'open_at', value: '20:00' }] });
  const fixture = validationFixture({ original: lateOnly, extraPlaces: [place('late', '합성 늦은 전시관', closed)] });
  assert.equal(fixture.prepared.candidateCount, 1);
  for (const placeIds of [[], ['late'], ['p2', 'missing']]) {
    const result = fixture.validate({ placeIds });
    assert.equal(result.status, 'accepted');
    assert.deepEqual(result.response, recommendation([], { action: 'impossible' }));
    assert.deepEqual(fixture.validate(result.response), result);
  }
  const empty = validationFixture({ original: request({ filters: { category: 'art', text: '존재하지 않는 이름' } }) });
  assert.equal(empty.prepared.candidateCount, 0);
  assert.equal(empty.validate({ placeIds: [] }).response.action, 'impossible');
});

test('rule-action selection without an eligible ID stays blocked while viable candidates exist', () => {
  const { validate, strict, body } = validationFixture({
    original: request({ requirements: [{ type: 'indoor_required', value: true }] }),
    extraPlaces: [place('failed', '합성 야외 전시관', { attributes: { indoor: verified(false) } })],
  });
  for (const selection of [{ placeIds: [] }, { placeIds: ['missing', 'excluded', 'food'] }, { placeIds: ['failed'] },
    { placeIds: 'p2' }, { placeIds: ['p2', 1] }, { placeIds: [' p2'] }, { placeIds: Array(51).fill('p2') }]) {
    const blocked = validate(selection);
    assert.equal(blocked.status, 'blocked');
    assert.deepEqual(blocked, strict.validate(body(selection)));
    assert.equal('response' in blocked, false);
  }
});

// Minimal walking: route places are withheld like exclusions; prepared metadata and the strict check do not change.
const WALK = '합성 장소 후보를 보여 주세요. 거의 걷지 않으려고 합니다.';
const WITHHELD = 'Route places (trails, Olle courses, bike routes) are left out: the original request asks for minimal walking.';
const route = { value: { km: 9.6, level: null, summary: null }, basis: 'source_reported', source: 'synthetic' };
const routes = [place('olle', '합성 올레 코스', { category: 'hike', attributes: { route } }),
  place('trail', '합성 탐방로', { category: 'hike', attributes: { route } })];
const walking = (extra = {}) => request({ query: WALK, filters: { zone: 'JC', text: '합성' }, ...extra });

test('minimal walking withholds route places from search, get and check while binding and the strict view stay unchanged', () => {
  const input = catalog(routes);
  const strict = createRuntimeValidator({ catalog: input, now: NOW });
  const scopes = createGatewayScopes({ catalog: input, now: NOW });
  const prepared = scopes.register(registration(walking()));
  assert.deepEqual(prepared, strict.prepare(walking()));
  assert.equal(prepared.candidateCount, 5);
  assert.deepEqual(scopes.prepare(walking()), prepared);
  const found = scopes.call(tool('places.search', {}));
  assert.deepEqual(found.data.items.map(item => item.id), ['food', 'p1', 'p2']);
  assert.equal(found.data.totalMatches, 3);
  assert.deepEqual(found.data.excludedIds, ['excluded']);
  assert.equal(found.warnings.filter(warning => warning === WITHHELD).length, 1);
  assert.deepEqual(scopes.call(tool('places.search', { query: '올레' })).data.items, []);
  for (const name of ['places.get', 'constraints.check']) {
    for (const ids of [['olle'], ['p1', 'trail']]) throwsCode(() => scopes.call(tool(name, { ids })), 'GATEWAY_PLACE_OUTSIDE_SCOPE', 403);
  }
  assert.deepEqual(scopes.call(tool('places.get', { ids: ['p1', 'p2'] })).data.items.map(item => item.id), ['p1', 'p2']);

  // The same request without the sentence keeps every candidate and adds no warning.
  const plain = request({ requestId: OTHER_ID, filters: { zone: 'JC', text: '합성' } });
  scopes.register(registration(plain));
  const all = scopes.call(tool('places.search', {}, OTHER_ID));
  assert.deepEqual(all.data.items.map(item => item.id), ['food', 'olle', 'p1', 'p2', 'trail']);
  assert.ok(!all.warnings.includes(WITHHELD));
  assert.deepEqual(scopes.call(tool('places.get', { ids: ['olle'] }, OTHER_ID)).data.items.map(item => item.id), ['olle']);
  // A walking request whose candidates hold no route place is not told about one.
  scopes.release(OTHER_ID);
  scopes.register(registration(walking({ requestId: THIRD_ID, filters: { category: 'art', zone: 'JC', text: '합성' } })));
  const art = scopes.call(tool('places.search', {}, THIRD_ID));
  assert.deepEqual(art.data.items.map(item => item.id), ['p1', 'p2']);
  assert.ok(!art.warnings.includes(WITHHELD));
});

test('rule-action selection drops withheld route IDs in model order; only routes stay blocked as the strict check decides', () => {
  const { validate, strict, body } = validationFixture({ original: walking(), extraPlaces: routes });
  const selection = { placeIds: ['olle', 'p2', 'trail', 'missing', 'p1'] };
  const composed = validate(selection);
  assert.equal(composed.status, 'accepted');
  assert.deepEqual(composed.response, recommendation(['p2', 'p1']));
  assert.deepEqual(validate(composed.response), composed);
  assert.equal(strict.validate(body(composed.response)).status, 'accepted');
  for (const placeIds of [['olle'], ['trail', 'olle'], []]) {
    const blocked = validate({ placeIds });
    assert.equal(blocked.status, 'blocked');
    assert.deepEqual(blocked, strict.validate(body({ placeIds })));
  }
  // Without the sentence the same selection keeps the route places.
  const plain = validationFixture({ original: request({ filters: { zone: 'JC', text: '합성' } }), extraPlaces: routes });
  assert.deepEqual(plain.validate(selection).response.placeIds, ['olle', 'p2', 'trail', 'p1']);
});

test('when only route places could be recommended, the walking selection stays blocked instead of impossible', () => {
  const original = walking({ filters: { zone: 'JC', text: '합성 올레' } });
  const { validate, strict, body, prepared } = validationFixture({ original, extraPlaces: routes });
  assert.equal(prepared.candidateCount, 1);
  for (const placeIds of [[], ['olle']]) {
    const blocked = validate({ placeIds });
    assert.equal(blocked.status, 'blocked');
    assert.deepEqual(blocked, strict.validate(body({ placeIds })));
  }
  // The strict check does not apply the walking rule, so it cannot establish impossibility here.
  assert.equal(strict.validate(body(recommendation([], { action: 'impossible' }))).status, 'blocked');
  const plain = validationFixture({ original: request({ filters: { zone: 'JC', text: '합성 올레' } }), extraPlaces: routes });
  assert.deepEqual(plain.validate({ placeIds: ['olle'] }).response, recommendation(['olle']));
  // The same holds when verified failures leave only an unfailed route place.
  const closed = verified({ openMin: 540, closeMin: 1080, text: '매일 09:00~18:00' });
  const late = [place('late', '합성 늦은 전시관', { attributes: { hours: closed } }),
    place('late-route', '합성 늦은 산책로', { category: 'hike', attributes: { route } })];
  const requirements = [{ type: 'open_at', value: '20:00' }];
  const failing = validationFixture({ original: walking({ filters: { zone: 'JC', text: '합성 늦은' }, requirements }), extraPlaces: late });
  for (const placeIds of [[], ['late', 'late-route']]) assert.equal(failing.validate({ placeIds }).status, 'blocked');
  const open = validationFixture({ original: request({ filters: { zone: 'JC', text: '합성 늦은' }, requirements }), extraPlaces: late });
  assert.deepEqual(open.validate({ placeIds: ['late', 'late-route'] }).response.placeIds, ['late-route']);
});

test('a walking request still returns impossible when every registered candidate, route places included, fails', () => {
  const closed = verified({ openMin: 540, closeMin: 1080, text: '매일 09:00~18:00' });
  const original = walking({ filters: { zone: 'JC', text: '합성 늦은' }, requirements: [{ type: 'open_at', value: '20:00' }] });
  const { validate } = validationFixture({ original, extraPlaces: [
    place('late', '합성 늦은 전시관', { attributes: { hours: closed } }),
    place('late-route', '합성 늦은 산책로', { category: 'hike', attributes: { hours: closed, route } }),
  ] });
  for (const placeIds of [[], ['late-route'], ['late']]) {
    const result = validate({ placeIds });
    assert.equal(result.status, 'accepted');
    assert.deepEqual(result.response, recommendation([], { action: 'impossible' }));
  }
});

test('the strict path is unchanged for walking requests; recovery composes without withheld route places', () => {
  const { validate, strict, body } = validationFixture({ original: walking(), extraPlaces: routes });
  // A response the strict check accepts with up to five places is returned as it is.
  const named = recommendation(['p2', 'olle']);
  assert.deepEqual(validate(named), strict.validate(body(named)));
  assert.deepEqual(validate(named).response.placeIds, ['p2', 'olle']);
  // Recovery of a strict-blocked response uses only places the scope offers.
  const recovered = validate(recommendation(['olle', 'p2'], { requirementClaims: null }));
  assert.equal(recovered.status, 'accepted');
  assert.deepEqual(recovered.response, recommendation(['p2']));
  assert.equal(validate(recommendation(['olle'], { requirementClaims: null })).status, 'blocked');
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

async function serverFixture(t, { enabled = true, now = NOW, maxConcurrent = 2 } = {}) {
  const input = catalog();
  const server = createMcpHttpServer({ catalog: input, token: TOKEN,
    ...(enabled ? { gatewayControlToken: CONTROL } : {}), gatewayMaxConcurrent: maxConcurrent, now });
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
  assert.deepEqual(ready, { ready: true, activeScopes: 1, maxConcurrentScopes: 2, toolCallLimit: 8, catalogSchemaVersion: '2.0',
    dataMode: 'app-export', placeCount: 6, dataVersion: input.dataVersion, readOnly: true, productionReady: false });
  assert.equal((await call('/gateway/prepare-request', request())).status, 200);
  assert.equal((await call('/gateway/scopes/' + ID, undefined, { method: 'DELETE', token: CONTROL })).status, 200);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}))).status, 404);
  assert.throws(() => createMcpHttpServer({ catalog: input, token: TOKEN, gatewayControlToken: TOKEN }));
});

test('HTTP admits exactly two simultaneous registrations and keeps independent scope budgets', async t => {
  const { call } = await serverFixture(t);
  const registrations = [ID, OTHER_ID, THIRD_ID].map(requestId => registration(request({ requestId })));
  const responses = await Promise.all(registrations.map(body => call('/gateway/scopes', body, { token: CONTROL })));
  assert.equal(responses.filter(response => response.status === 200).length, 2);
  assert.equal(responses.filter(response => response.status === 429).length, 1);
  const admitted = registrations.filter((_body, index) => responses[index].status === 200).map(body => body.request.requestId);
  const rejected = registrations.find((_body, index) => responses[index].status === 429).request.requestId;
  const toolResponses = await Promise.all(admitted.flatMap(requestId => Array.from({ length: 10 },
    () => call('/gateway/tools/call', tool('places.get', { ids: ['p1'] }, requestId)))));
  assert.equal(toolResponses.filter(response => response.status === 200).length, 16);
  assert.equal(toolResponses.filter(response => response.status === 429).length, 4);
  assert.equal((await call('/gateway/scopes/' + admitted[0], undefined, { method: 'DELETE', token: CONTROL })).status, 200);
  assert.equal((await call('/gateway/scopes', registration(request({ requestId: rejected })), { token: CONTROL })).status, 200);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}, admitted[1]))).status, 429);
  assert.equal((await call('/gateway/tools/call', tool('places.search', {}, rejected))).status, 200);
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

test('HTTP recovery is confined to the registered Gateway route and its final response revalidates unchanged', async t => {
  const { call } = await serverFixture(t);
  const original = request({ requirements: [{ type: 'indoor_required', value: true }] });
  const prepared = await (await call('/gateway/scopes', registration(original), { token: CONTROL })).json();
  const body = { request: original, expectedCatalogVersion: prepared.catalogVersion,
    response: recommendation(['p2'], { requirementClaims: [{ placeId: 'p2', requirement: 'indoor_required', verdict: 'pass' }] }) };
  const unscoped = await (await call('/validate-response', body)).json();
  assert.equal(unscoped.status, 'blocked');
  assert.equal('response' in unscoped, false);
  const recovered = await (await call('/gateway/validate-response', body)).json();
  assert.equal(recovered.status, 'accepted');
  assert.equal(recovered.response.action, 'cannot_confirm');
  assert.deepEqual(recovered.response.placeIds, ['p2']);
  const rechecked = await (await call('/gateway/validate-response', { ...body, response: recovered.response })).json();
  assert.deepEqual(rechecked, recovered);
  assert.equal((await call('/gateway/validate-response', { ...body, request: request() })).status, 409);
  await call('/gateway/scopes/' + ID, undefined, { method: 'DELETE', token: CONTROL });
  assert.equal((await call('/gateway/validate-response', body)).status, 404);
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
