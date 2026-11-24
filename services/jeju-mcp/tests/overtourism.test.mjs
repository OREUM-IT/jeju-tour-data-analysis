import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import { BASIS_POLICY } from '../src/agent-catalog.mjs';
import { validateAgentServerCatalog } from '../src/catalog.mjs';
import { createMcpHttpServer } from '../src/server.mjs';
import {
  computeIndicators, createOvertourismScopes, JEJU_REGIONS, OVERTOURISM_TOOL_CALL_LIMIT, OvertourismError,
  parseOvertourismInput, ruleLevel, sanitizeDiagnosis, sha256,
} from '../src/overtourism.mjs';

const NOW = Date.parse('2026-11-24T03:00:00.000Z');
const TOKEN = 'm'.repeat(64);
const CONTROL = 'c'.repeat(64);
const ID = 'ot-' + 'a'.repeat(32);
const OTHER = 'ot-' + 'b'.repeat(32);
const ids = JEJU_REGIONS.map(region => region.id);
const region = (id, extra = {}) => ({ id, places: 10, lodging: { businesses: 2, rooms: 40 },
  regulation: { places: 0, lifted: 0, cards: [] }, ...extra });
// Synthetic snapshot only: fictional spot names and cards, never app or KTO data.
const input = (extra = {}) => ({
  schemaVersion: '1.0', today: '2026-11-24', dataVersion: '5559-synthetic',
  crowd: { ok: true, stale: false, at: '2026-11-24T01:00:00.000Z', total: 9, spots: [
    ['합성 오름 A', '제주시', 'udo', 'name', '2026-11-24', [70, 65, null, 50]],
    ['합성 해변 B', '제주시', 'udo', 'hub', '2026-11-24', [61, 20, 30]],
    ['합성 마을 C', '제주시', 'udo', 'near', '2026-11-25', [10, 80]],
    ['합성 시장 D', '제주시', 'jeju-dong', 'name', '2026-11-24', [40]],
    ['합성 숲 E', '서귀포시', 'seongsan', 'name', '2026-11-24', [59.9, 60, 12.5]],
    ['합성 폭포 F', '서귀포시', 'seongsan', 'name', '2026-11-24', [5, 5]],
    ['합성 동굴 G', '서귀포시', 'seongsan', 'hub', '2026-11-24', [61]],
    ['합성 미배정 H', '서귀포시', null, null, '2026-11-20', [90]],
  ] },
  regions: ids.map(id => region(id, id === 'udo' ? { regulation: { places: 1, lifted: 0, cards: [
    { id: 'synthetic-vehicle-limit', title: '합성 차량 운행 제한', n: 1, validUntil: '2026-07-31', until: null },
    { id: 'synthetic-open-card', title: '합성 상시 안내', n: 1, validUntil: null, until: '상시' }] } } : {})),
  sources: [{ id: 'visit-jeju-places', title: '합성 여행장소', url: 'https://example.org/places', asOf: '2025-09-18', use: '합성 시험' }],
  ...extra,
});
const throwsCode = (callback, code, status) => assert.throws(callback, error => error instanceof OvertourismError
  && error.code === code && (status === undefined || error.status === status));

test('parse rejects malformed snapshots without echoing values', () => {
  assert.equal(parseOvertourismInput(input()).crowd.spots.length, 8);
  const spots = input().crowd.spots;
  const bad = [
    [input({ schemaVersion: '2.0' }), 'INVALID_OVERTOURISM_INPUT'],
    [input({ today: '2026-02-30' }), 'INVALID_OVERTOURISM_INPUT'],
    [input({ extra: true }), 'INVALID_OVERTOURISM_INPUT'],
    [input({ crowd: { ...input().crowd, ok: false } }), 'INVALID_OVERTOURISM_CROWD'],
    [input({ crowd: { ...input().crowd, at: '2026-11-24' } }), 'INVALID_OVERTOURISM_CROWD'],
    [input({ crowd: { ...input().crowd, total: 3 } }), 'INVALID_OVERTOURISM_CROWD'],
    [input({ crowd: { ...input().crowd, spots: [] } }), 'INVALID_OVERTOURISM_CROWD'],
    [input({ crowd: { ...input().crowd, spots: [['A', '제주시', 'seongsan', 'name', '2026-11-24', [1]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [['A', '제주시', 'udo', null, '2026-11-24', [1]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [['A', '제주시', null, 'name', '2026-11-24', [1]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [['A', '제주시', 'udo', 'name', '2026-11-24', [null, 1]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [['A', '제주시', 'udo', 'name', '2026-11-24', [101]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [['A\u0007', '제주시', 'udo', 'name', '2026-11-24', [1]]] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ crowd: { ...input().crowd, spots: [spots[0].slice(0, 5)] } }), 'INVALID_OVERTOURISM_SPOT'],
    [input({ regions: input().regions.slice(1) }), 'INVALID_OVERTOURISM_REGION'],
    [input({ regions: [...input().regions.slice(1), input().regions[0]] }), 'INVALID_OVERTOURISM_REGION'],
    [input({ regions: input().regions.map((r, i) => (i ? r : { ...r, places: -1 })) }), 'INVALID_OVERTOURISM_REGION'],
    [input({ sources: [{ ...input().sources[0], url: 'http://example.org/places' }] }), 'INVALID_OVERTOURISM_SOURCE'],
    [input({ sources: [{ ...input().sources[0], id: 'kto-crowd' }] }), 'INVALID_OVERTOURISM_SOURCE'],
  ];
  for (const [value, code] of bad) throwsCode(() => parseOvertourismInput(value), code, 400);
});

test('indicators follow the app arithmetic: shares, means, peaks, top three and ended cards', () => {
  const ind = computeIndicators(parseOvertourismInput(input()));
  assert.deepEqual(ind.window, { from: '2026-11-20', to: '2026-11-27' });
  assert.deepEqual(ind.crowd, { ok: true, stale: false, at: '2026-11-24T01:00:00.000Z', spots: 9, mapped: 7,
    byMethod: { name: 4, hub: 2, near: 1 }, unmapped: ['서귀포시 합성 미배정 H'] });
  const udo = ind.regions.find(r => r.id === 'udo');
  // 9 spot-days: 70 65 50 | 61 20 30 | 10 80 → four at or above 60.
  assert.deepEqual(udo.crowd, { spots: 3, spotDays: 8, highDays: 4, highShare: 0.5, mean: 48.3,
    peak: { spot: '합성 마을 C', rate: 80, date: '2026-11-26' },
    top: [{ spot: '합성 오름 A', peak: 70, date: '2026-11-24', highDays: 2 },
      { spot: '합성 마을 C', peak: 80, date: '2026-11-26', highDays: 1 },
      { spot: '합성 해변 B', peak: 61, date: '2026-11-24', highDays: 1 }] });
  assert.deepEqual(udo.regulation.cards.map(card => card.ended), [true, false]);
  const seongsan = ind.regions.find(r => r.id === 'seongsan');
  // 59.9 stays below the 60 threshold; 60 counts.
  assert.equal(seongsan.crowd.highDays, 2);
  assert.equal(seongsan.crowd.highShare, 0.333);
  assert.equal(seongsan.crowd.mean, 33.9);
  assert.equal(ruleLevel(udo), 'high');
  assert.equal(ruleLevel(seongsan), 'high');
  assert.equal(ruleLevel(ind.regions.find(r => r.id === 'jeju-dong')), 'insufficient');
  assert.equal(ind.sources[0].id, 'kto-crowd');
  assert.equal(ind.sources[0].asOf, '2026-11-24');
  assert.equal(ind.regions.length, 14);
  // Deterministic: same snapshot, same digest.
  assert.equal(sha256(ind), sha256(computeIndicators(parseOvertourismInput(input()))));
});

test('validation keeps 14 regions, drops digits, unknown evidence and level jumps', () => {
  const ind = computeIndicators(parseOvertourismInput(input()));
  assert.equal(sanitizeDiagnosis(null, ind), null);
  assert.equal(sanitizeDiagnosis({ regions: [{ id: 'nowhere' }] }, ind), null);
  const out = sanitizeDiagnosis({ overview: '제주 전역 3곳이 혼잡해요.', regions: [
    { id: 'udo', level: 'normal', summary: '섬 안 혼잡이 커요.', evidence: ['crowd.highShare', 'made.up', 'crowd.highShare'],
      policy: '운행 제한 기간이 끝났어요.', suggestions: ['차량 대신 도보·자전거 이용을 권해요.', '20% 줄이기', '셋째'], missing: ['주민 인구'] },
    { id: 'jeju-dong', level: 'high', summary: '도심이 붐벼요.', evidence: [], policy: '없음', suggestions: [], missing: [] },
    { id: 'seongsan', level: 'watch', summary: '2곳이 혼잡해요.', evidence: ['crowd.peak'], policy: '규제 장소가 없어요.', suggestions: [], missing: [] },
    { id: 'udo', level: 'high', summary: '두 번째 우도', evidence: [], policy: '', suggestions: [], missing: [] },
  ] }, ind);
  assert.equal(out.regions.length, 14);
  assert.equal(out.overview, '혼잡 예측이 높은 지역: 우도면 · 성산읍.');
  const udo = out.regions.find(r => r.id === 'udo');
  // normal is two steps from high → back to the rule level.
  assert.equal(udo.level, 'high');
  assert.equal(udo.adjusted, true);
  assert.deepEqual(udo.evidence, ['crowd.highShare']);
  assert.deepEqual(udo.suggestions, ['차량 대신 도보·자전거 이용을 권해요.', '셋째']);
  const dong = out.regions.find(r => r.id === 'jeju-dong');
  assert.equal(dong.level, 'insufficient');
  assert.equal(dong.adjusted, true);
  const seongsan = out.regions.find(r => r.id === 'seongsan');
  assert.equal(seongsan.level, 'watch');
  assert.equal(seongsan.summary, '앞으로 한 달 혼잡으로 예측된 관광지·날이 많아요.');
  assert.equal(seongsan.adjusted, true);
  const missing = out.regions.find(r => r.id === 'hallim');
  assert.equal(missing.adjusted, true);
  assert.equal(missing.level, 'insufficient');
});

test('scopes bind tools, budget, validation and result to one registration', () => {
  let now = NOW;
  const scopes = createOvertourismScopes({ now: () => now });
  const prepared = scopes.register({ diagnosisId: ID, input: input(), deadlineMs: NOW + 100_000 });
  assert.deepEqual(Object.keys(prepared).sort(), ['diagnosisId', 'indicatorsSha256', 'policyVersion', 'regionIds',
    'schemaVersion', 'status', 'today', 'toolCallLimit']);
  assert.equal(prepared.status, 'prepared');
  assert.deepEqual(scopes.prepare({ diagnosisId: ID }), prepared);
  throwsCode(() => scopes.register({ diagnosisId: ID, input: input(), deadlineMs: NOW + 100_000 }), 'OVERTOURISM_SCOPE_DUPLICATE', 409);
  throwsCode(() => scopes.register({ diagnosisId: OTHER, input: input(), deadlineMs: NOW + 100_000 }), 'OVERTOURISM_SCOPE_BUSY', 429);
  throwsCode(() => scopes.result({ diagnosisId: ID }), 'OVERTOURISM_NOT_VALIDATED', 409);

  const overview = scopes.call({ diagnosisId: ID, name: 'overtourism_overview', arguments: {} });
  assert.equal(overview.data.regions.length, 14);
  assert.equal(overview.data.regions.find(r => r.id === 'udo').ruleLevel, 'high');
  assert.equal(overview.data.regions.find(r => r.id === 'udo').regulation.ended, 1);
  const detail = scopes.call({ diagnosisId: ID, name: 'overtourism_regions', arguments: { ids: ['udo'] } });
  assert.equal(detail.data.regions[0].crowd.top.length, 3);
  assert.deepEqual(detail.data.regions[0].regulation.cards[0], { title: '합성 차량 운행 제한', places: 1, validUntil: '2026-07-31', ended: true });
  const ranked = scopes.call({ diagnosisId: ID, name: 'overtourism_rank', arguments: { metric: 'crowd.highShare', limit: 3 } });
  assert.deepEqual(ranked.data.ranking.map(row => row.id), ['udo', 'seongsan', 'jeju-dong']);
  // Invalid calls spend budget too.
  throwsCode(() => scopes.call({ diagnosisId: ID, name: 'overtourism_regions', arguments: { ids: ['udo', 'udo'] } }), 'INVALID_OVERTOURISM_TOOL_ARGUMENTS');
  throwsCode(() => scopes.call({ diagnosisId: ID, name: 'places.search', arguments: {} }), 'INVALID_OVERTOURISM_TOOL_CALL');
  throwsCode(() => scopes.call({ diagnosisId: ID, name: 'overtourism_overview', arguments: { extra: 1 } }), 'INVALID_OVERTOURISM_TOOL_ARGUMENTS');
  assert.equal(OVERTOURISM_TOOL_CALL_LIMIT, 6);
  throwsCode(() => scopes.call({ diagnosisId: ID, name: 'overtourism_overview', arguments: {} }), 'OVERTOURISM_TOOL_BUDGET_EXHAUSTED', 429);

  assert.equal(scopes.validate({ diagnosisId: ID, response: { regions: [] } }).status, 'blocked');
  const answer = { overview: '우도면이 가장 붐벼요.', regions: [{ id: 'udo', level: 'high', summary: '섬 안 혼잡이 커요.',
    evidence: ['crowd.highShare', 'regulation.ended'], policy: '운행 제한 공고 기간이 끝나 연장 여부를 확인해야 해요.',
    suggestions: ['연장 공고를 확인하도록 안내해요.'], missing: ['실제 방문객 수'] }] };
  const accepted = scopes.validate({ diagnosisId: ID, response: answer });
  assert.equal(accepted.status, 'accepted');
  assert.equal(accepted.indicatorsSha256, prepared.indicatorsSha256);
  assert.equal(accepted.adjustedRegions, 13);
  assert.deepEqual(scopes.validate({ diagnosisId: ID, response: structuredClone(answer) }), accepted);
  throwsCode(() => scopes.validate({ diagnosisId: ID, response: { ...answer, overview: '다른 답' } }), 'OVERTOURISM_ALREADY_VALIDATED', 409);
  const result = scopes.result({ diagnosisId: ID });
  assert.equal(result.status, 'validated');
  assert.equal(result.diagnosisSha256, sha256(result.diagnosis));
  assert.equal(result.indicatorsSha256, sha256(result.indicators));
  assert.equal(result.toolCalls, 6);
  assert.equal(result.diagnosis.regions.find(r => r.id === 'udo').summary, '섬 안 혼잡이 커요.');
  assert.deepEqual(scopes.release(ID), { status: 'released' });
  throwsCode(() => scopes.prepare({ diagnosisId: ID }), 'OVERTOURISM_SCOPE_UNAVAILABLE', 404);
  // The released id stays a tombstone until its deadline; a new id can register.
  throwsCode(() => scopes.register({ diagnosisId: ID, input: input(), deadlineMs: NOW + 100_000 }), 'OVERTOURISM_SCOPE_DUPLICATE', 409);
  scopes.register({ diagnosisId: OTHER, input: input(), deadlineMs: NOW + 10_000 });
  now = NOW + 10_000;
  throwsCode(() => scopes.prepare({ diagnosisId: OTHER }), 'OVERTOURISM_SCOPE_EXPIRED', 410);
  throwsCode(() => scopes.register({ diagnosisId: 'ot-' + 'c'.repeat(32), input: input(), deadlineMs: now + 120_000 }), 'INVALID_OVERTOURISM_DEADLINE');
  assert.equal(scopes.ready().activeScopes, 0);
});

const verified = value => ({ value, basis: 'verified', source: 'synthetic', verifiedAt: '2026-11-18T00:00:00Z', validUntil: '2026-11-30T15:00:00Z' });
const catalog = () => validateAgentServerCatalog({
  schemaVersion: '2.0', kind: 'jeju-agent-catalog', dataMode: 'app-export',
  source: { repository: 'test/synthetic', commit: 'a'.repeat(40), placesContentSha256: 'b'.repeat(64),
    rawSha256: 'c'.repeat(64), themeMapVersion: 'synthetic-v1' },
  themeIds: ['art'], basisPolicy: structuredClone(BASIS_POLICY), sources: { synthetic: { name: 'Synthetic test evidence only' } },
  places: [{ id: 'p1', name: '합성 미술관', category: 'art', kind: null, lat: 33.4, lon: 126.5, zone: 'JC', origin: 'raw_poi',
    web: null, themes: { art: 1 }, themeRule: 'synthetic', attributes: { indoor: verified(true) } }], diagnostics: {},
});

test('HTTP routes separate Gateway control from Langflow execution and bound bodies', async t => {
  assert.throws(() => createMcpHttpServer({ catalog: catalog(), token: TOKEN, gatewayControlToken: TOKEN }));
  const server = createMcpHttpServer({ catalog: catalog(), token: TOKEN, gatewayControlToken: CONTROL, now: () => NOW });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (path, body, secret, method = 'POST', type = 'application/json') => {
    const response = await fetch(base + path, { method, headers: { Authorization: `Bearer ${secret}`,
      ...(body === undefined ? {} : { 'Content-Type': type }) }, ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
  assert.equal((await call('/gateway/overtourism/readyz', undefined, CONTROL, 'GET')).body.policyVersion, 'overtourism-diagnosis-v1');
  assert.equal((await call('/gateway/overtourism/readyz', undefined, TOKEN, 'GET')).status, 401);
  // Langflow's MCP token cannot register, read results or release.
  assert.equal((await call('/gateway/overtourism/scopes', { diagnosisId: ID, input: input(), deadlineMs: NOW + 100_000 }, TOKEN)).status, 401);
  const registered = await call('/gateway/overtourism/scopes', { diagnosisId: ID, input: input(), deadlineMs: NOW + 100_000 }, CONTROL);
  assert.equal(registered.status, 200);
  assert.equal(registered.body.status, 'prepared');
  // And the control token cannot act as the Agent.
  assert.equal((await call('/gateway/overtourism/prepare', { diagnosisId: ID }, CONTROL)).status, 401);
  assert.deepEqual((await call('/gateway/overtourism/prepare', { diagnosisId: ID }, TOKEN)).body, registered.body);
  assert.equal((await call('/gateway/overtourism/tools/call', { diagnosisId: ID, name: 'overtourism_overview', arguments: {} }, TOKEN)).body.data.regions.length, 14);
  assert.equal((await call('/gateway/overtourism/tools/call', { diagnosisId: ID, name: 'nope', arguments: {} }, TOKEN)).body.error.code, 'INVALID_OVERTOURISM_TOOL_CALL');
  assert.equal((await call('/gateway/overtourism/prepare', 'x', TOKEN, 'POST', 'text/plain')).status, 415);
  assert.equal((await call('/gateway/overtourism/prepare', { diagnosisId: ID }, TOKEN, 'PUT')).status, 405);
  assert.equal((await call('/gateway/overtourism/unknown', {}, TOKEN)).status, 404);
  // 128 KiB for Agent bodies, 256 KiB only for registration.
  assert.equal((await call('/gateway/overtourism/validate', { diagnosisId: ID, response: { overview: 'x'.repeat(140_000) } }, TOKEN)).status, 413);
  const big = { diagnosisId: OTHER, input: { ...input(), dataVersion: 'x'.repeat(270_000) }, deadlineMs: NOW + 100_000 };
  assert.equal((await call('/gateway/overtourism/scopes', big, CONTROL)).status, 413);
  const validated = await call('/gateway/overtourism/validate', { diagnosisId: ID, response: { overview: '요약', regions: [{ id: 'udo', level: 'high' }] } }, TOKEN);
  assert.equal(validated.body.status, 'accepted');
  const result = await call('/gateway/overtourism/result', { diagnosisId: ID }, CONTROL);
  assert.equal(result.body.diagnosisSha256, validated.body.diagnosisSha256);
  assert.equal((await call('/gateway/overtourism/result', { diagnosisId: ID }, TOKEN)).status, 401);
  assert.equal((await call(`/gateway/overtourism/scopes/${ID}`, undefined, CONTROL, 'DELETE')).body.status, 'released');
  assert.equal((await call('/gateway/overtourism/prepare', { diagnosisId: ID }, TOKEN)).body.error.code, 'OVERTOURISM_SCOPE_UNAVAILABLE');
  // Existing recommendation readiness keeps its exact shape.
  const ready = await call('/gateway/readyz', undefined, CONTROL, 'GET');
  assert.equal(Object.hasOwn(ready.body, 'overtourism'), false);
});

test('without Gateway control the overtourism routes do not exist', async t => {
  const server = createMcpHttpServer({ catalog: catalog(), token: TOKEN, now: () => NOW });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const response = await fetch(`http://127.0.0.1:${server.address().port}/gateway/overtourism/readyz`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert.equal(response.status, 404);
  await response.body?.cancel();
});
