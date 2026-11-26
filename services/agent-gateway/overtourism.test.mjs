import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { makeCiCatalog } from '../../deploy/tests/make-ci-catalog.mjs';
import { validateAgentServerCatalog } from '../jeju-mcp/src/catalog.mjs';
import { createMcpHttpServer } from '../jeju-mcp/src/server.mjs';
import { JEJU_REGIONS } from '../jeju-mcp/src/overtourism.mjs';
import { signRequest } from './auth.mjs';
import { createOvertourismService, createOvertourismUpstream } from './overtourism.mjs';
import { createGatewayServer, overtourismFromEnv } from './server.mjs';
import { createGatewayState } from './state.mjs';
import { digest, UpstreamError } from './upstream.mjs';

const NOW = Date.parse('2026-11-24T03:00:00Z');
const KEY = 'a'.repeat(64), MCP_TOKEN = 'm'.repeat(64), CONTROL = 'c'.repeat(64), API_KEY = 'sk-test-only-no-provider';
const FLOW_ID = '4b7e4a0e-6f8e-4d2c-9a43-1f0d5f6b2c11';
const FLOW_DATA = { nodes: [{ id: 'approved-overtourism-mock-flow' }], edges: [] };
const fixtureCatalog = validateAgentServerCatalog(structuredClone(makeCiCatalog(
  JSON.parse(await readFile(new URL('../../data/theme-category-map.json', import.meta.url), 'utf8')),
  JSON.parse(await readFile(new URL('../../data/persona-prototypes.json', import.meta.url), 'utf8')),
)));
// Fictional snapshot: no app, KTO or regulation data.
const snapshot = (extra = {}) => ({
  schemaVersion: '1.0', today: '2026-11-24', dataVersion: '5559-synthetic',
  crowd: { ok: true, stale: false, at: '2026-11-24T01:00:00.000Z', total: 4, spots: [
    ['합성 오름 A', '제주시', 'udo', 'name', '2026-11-24', [70, 65, 50]],
    ['합성 해변 B', '제주시', 'udo', 'hub', '2026-11-24', [61, 20, 30]],
    ['합성 마을 C', '제주시', 'udo', 'near', '2026-11-25', [10, 80]],
    ['합성 미배정 D', '서귀포시', null, null, '2026-11-24', [90]],
  ] },
  regions: JEJU_REGIONS.map(region => ({ id: region.id, places: 3, lodging: { businesses: 1, rooms: 10 },
    regulation: { places: 0, lifted: 0, cards: [] } })),
  sources: [], ...extra,
});
const answer = { overview: '우도면의 혼잡 예측이 두드러져요.', regions: [{ id: 'udo', level: 'high', summary: '섬 안 혼잡이 커요.',
  evidence: ['crowd.highShare'], policy: '이름으로 적힌 규제 장소는 없어요.', suggestions: [], missing: ['실제 방문객 수'] }] };
const json = (res, value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
const readBody = async req => { const chunks = []; for await (const chunk of req) chunks.push(chunk); return JSON.parse(Buffer.concat(chunks)); };
async function listen(t, server) {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return `http://127.0.0.1:${server.address().port}`;
}
const envelope = (id, final, extra = {}) => ({ session_id: id, outputs: [{ outputs: [{
  component_id: 'ChatOutput-jejuOvertourism', results: { message: { sender: 'Machine',
    properties: { source: { id: 'JejuOvertourismValidation-v1', source: 'Jeju Overtourism Validation' } },
    text: JSON.stringify(final), ...extra } } }] }] });

/** Real MCP scopes plus a mock Langflow that drives them like the pinned flow (no model). */
async function stack(t, options = {}) {
  let clock = NOW;
  const mcp = createMcpHttpServer({ catalog: fixtureCatalog, token: MCP_TOKEN, gatewayControlToken: CONTROL, now: () => clock });
  const mcpUrl = await listen(t, mcp);
  const mcpCall = async (route, body, control = false) => {
    const response = await fetch(mcpUrl + route, { method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${control ? CONTROL : MCP_TOKEN}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
  const runs = [];
  let flowData = structuredClone(FLOW_DATA);
  const langflow = http.createServer(async (req, res) => {
    try {
      assert.equal(req.headers['x-api-key'], API_KEY);
      if (req.method === 'GET' && req.url === `/api/v1/flows/${FLOW_ID}`) return json(res, { id: FLOW_ID, data: flowData });
      if (req.method !== 'POST' || req.url !== `/api/v1/run/${FLOW_ID}?stream=false`) return json(res, {}, 404);
      const body = await readBody(req);
      runs.push(body);
      const { diagnosisId } = JSON.parse(body.input_value);
      assert.deepEqual(Object.keys(JSON.parse(body.input_value)), ['diagnosisId']);
      assert.equal(body.output_component, 'ChatOutput-jejuOvertourism');
      if (options.gate) await options.gate;
      const prepared = await mcpCall('/gateway/overtourism/prepare', { diagnosisId });
      assert.equal(prepared.status, 200);
      const overview = await mcpCall('/gateway/overtourism/tools/call', { diagnosisId, name: 'overtourism_overview', arguments: {} });
      assert.equal(overview.body.data.regions.length, 14);
      const validated = await mcpCall('/gateway/overtourism/validate', { diagnosisId, response: options.answer ?? answer });
      const final = options.final ? options.final(validated.body) : validated.body;
      return json(res, envelope(options.session ?? diagnosisId, final, options.message ?? {}));
    } catch { return json(res, {}, 500); }
  });
  const langflowUrl = await listen(t, langflow);
  const upstream = createOvertourismUpstream({ flowId: FLOW_ID, flowSha256: digest(FLOW_DATA), langflowApiKey: API_KEY,
    mcpToken: MCP_TOKEN, controlToken: CONTROL, langflowUrl, mcpUrl });
  const directory = await mkdtemp(path.join(tmpdir(), 'jeju-gateway-overtourism-'));
  const state = createGatewayState({ filename: path.join(directory, 'gateway.sqlite'), now: () => clock });
  t.after(async () => { state.close(); await rm(directory, { recursive: true, force: true }); });
  return { mcpCall, runs, upstream, state, setFlow: value => { flowData = value; }, tick: ms => { clock += ms; }, now: () => clock };
}
const context = () => ({ deadlineMs: NOW + 100_000, signal: new AbortController().signal });
const rejects = (promise, code, status) => assert.rejects(promise, error => error instanceof UpstreamError
  && error.code === code && (status === undefined || error.status === status));

test('a run returns MCP-validated indicators and diagnosis and releases its scope', async t => {
  const s = await stack(t);
  const result = await s.upstream.execute(snapshot(), context());
  assert.equal(result.status, 'accepted');
  assert.match(result.diagnosisId, /^ot-[a-f0-9]{32}$/u);
  assert.equal(result.model, 'gpt-5.4-mini');
  assert.equal(result.indicatorsSha256, digest(result.indicators));
  assert.equal(result.diagnosisSha256, digest(result.diagnosis));
  assert.equal(result.diagnosis.regions.length, 14);
  assert.equal(result.diagnosis.regions.find(region => region.id === 'udo').summary, '섬 안 혼잡이 커요.');
  assert.equal(result.indicators.regions.find(region => region.id === 'udo').crowd.highDays, 4);
  assert.equal(result.toolCalls, 1);
  assert.equal(result.productionReady, false);
  assert.equal((await s.mcpCall('/gateway/overtourism/readyz', undefined, true)).body.activeScopes, 0);
});

test('flow drift, blocked answers and tampered finals fail closed', async t => {
  const drift = await stack(t);
  drift.setFlow({ nodes: [{ id: 'edited-in-langflow' }], edges: [] });
  await rejects(drift.upstream.execute(snapshot(), context()), 'OVERTOURISM_FLOW_CHANGED', 503);
  assert.equal(drift.runs.length, 0);

  const blocked = await stack(t, { answer: { regions: [{ id: 'nowhere' }] } });
  await rejects(blocked.upstream.execute(snapshot(), context()), 'DIAGNOSIS_BLOCKED', 422);
  assert.equal((await blocked.mcpCall('/gateway/overtourism/readyz', undefined, true)).body.activeScopes, 0);

  const forged = await stack(t, { final: value => ({ ...value, diagnosisSha256: 'f'.repeat(64) }) });
  await rejects(forged.upstream.execute(snapshot(), context()), 'OVERTOURISM_RESULT_MISMATCH', 500);
  assert.equal((await forged.mcpCall('/gateway/overtourism/readyz', undefined, true)).body.activeScopes, 0);

  const source = await stack(t, { message: { properties: { source: { id: 'Other', source: 'Other' } } } });
  await rejects(source.upstream.execute(snapshot(), context()), 'VALIDATOR_SOURCE_MISMATCH');

  const session = await stack(t, { session: 'ot-' + '9'.repeat(32) });
  await rejects(session.upstream.execute(snapshot(), context()), 'SESSION_MISMATCH');

  const invalid = await stack(t);
  await rejects(invalid.upstream.execute(snapshot({ today: 'not-a-date' }), context()), 'INVALID_OVERTOURISM_REQUEST', 400);
  assert.equal(invalid.runs.length, 0);
});

test('identical snapshots share one run, the daily budget and replay protection hold', async t => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const s = await stack(t, { gate });
  const service = createOvertourismService({ state: s.state, upstream: s.upstream, now: s.now, dailyLimit: 2 });
  const request = nonce => ({ subject: 'service:overtourism', nonce, input: snapshot(), ...context() });
  const first = service.diagnose(request('1'.repeat(32)));
  const second = service.diagnose(request('2'.repeat(32)));
  await new Promise(resolve => setTimeout(resolve, 50));
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(s.runs.length, 1);
  assert.equal(a.reused, false);
  assert.equal(b.reused, true);
  assert.equal(a.diagnosisId, b.diagnosisId);
  const cached = await service.diagnose(request('3'.repeat(32)));
  assert.equal(cached.reused, true);
  assert.equal(s.runs.length, 1);
  // A new snapshot is a new run; the nonce of an earlier run cannot be replayed.
  await assert.rejects(service.diagnose({ ...request('1'.repeat(32)), input: snapshot({ dataVersion: 'second' }) }),
    error => error.code === 'REPLAY_DETECTED');
  await service.diagnose({ ...request('4'.repeat(32)), input: snapshot({ dataVersion: 'second' }) });
  assert.equal(s.runs.length, 2);
  await assert.rejects(service.diagnose({ ...request('5'.repeat(32)), input: snapshot({ dataVersion: 'third' }) }),
    error => error.code === 'RATE_LIMITED' && error.status === 429 && error.retryAfterSeconds > 0);
  assert.equal(s.runs.length, 2);
  // The reservations finished; nothing is left in flight or uncertain.
  assert.deepEqual(s.state.activeCounts({ now: s.now() }), { queued: 0, running: 0, uncertain: 0 });
  const ready = await service.ready();
  assert.equal(ready.runsToday, 2);
  assert.equal(ready.cachedResults, 2);
});

test('Gateway route: signed, larger body limit, unchanged /readyz, disabled without settings', async t => {
  const s = await stack(t);
  const directory = await mkdtemp(path.join(tmpdir(), 'jeju-gateway-overtourism-server-'));
  const state = createGatewayState({ filename: path.join(directory, 'gateway.sqlite'), now: s.now });
  const upstream = { async ready() { return { ready: true, flowVerified: true, catalogPlaces: 981, productionReady: false }; },
    async execute() { throw new Error('not used'); } };
  const serve = async overtourism => {
    const server = createGatewayServer({ authKey: KEY, state, upstream, now: s.now, overtourism });
    const base = await listen(t, server);
    t.after(async () => { try { await server.closeJobs(); } catch { /* closed */ } });
    return base;
  };
  t.after(async () => { state.close(); await rm(directory, { recursive: true, force: true }); });
  const service = createOvertourismService({ state, upstream: s.upstream, now: s.now });
  const base = await serve(service);
  let counter = 0;
  const send = (target, route, body, method = 'POST') => {
    const bytes = body === undefined ? '' : JSON.stringify(body);
    const headers = signRequest({ key: KEY, method, path: route, body: bytes, subject: 'service:overtourism',
      nonce: (++counter).toString(16).padStart(32, '0'), timestamp: Math.floor(s.now() / 1000) });
    return fetch(target + route, { method, headers: { ...headers, ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}) },
      ...(method === 'POST' ? { body: bytes } : {}) });
  };
  // Above the 32 KiB recommendation limit, below 256 KiB.
  const padded = snapshot({ dataVersion: 'p'.repeat(100) });
  padded.crowd.spots.push(...Array.from({ length: 700 }, (_, i) => [`합성 추가 관광지 ${i}`, '서귀포시', null, null, '2026-11-24', [12.5, 13.5]]));
  padded.crowd.total = padded.crowd.spots.length;
  assert.ok(Buffer.byteLength(JSON.stringify(padded)) > 32 * 1024);
  const accepted = await send(base, '/v1/overtourism', padded);
  assert.equal(accepted.status, 200);
  const value = await accepted.json();
  assert.equal(value.status, 'accepted');
  assert.equal(value.reused, false);
  assert.equal(value.indicators.crowd.unmapped.length, 701);
  const big = snapshot({ dataVersion: 'x'.repeat(270_000) });
  assert.equal((await send(base, '/v1/overtourism', big)).status, 413);
  const unsigned = await fetch(base + '/v1/overtourism', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  assert.equal(unsigned.status, 401);
  await unsigned.body?.cancel();
  const ready = await send(base, '/v1/overtourism/readyz', undefined, 'GET');
  assert.equal(ready.status, 200);
  assert.equal((await ready.json()).flowVerified, true);
  const readyz = await (await send(base, '/readyz', undefined, 'GET')).json();
  assert.deepEqual(Object.keys(readyz).sort(), ['asyncJobs', 'catalogPlaces', 'flowVerified', 'jobs', 'maxConcurrent', 'mode', 'productionReady', 'ready']);
  const wrongMethod = await send(base, '/v1/overtourism', undefined, 'GET');
  assert.equal(wrongMethod.status, 405);
  await wrongMethod.body?.cancel();
  const offBase = await serve(null);
  const off = await send(offBase, '/v1/overtourism', snapshot());
  assert.equal(off.status, 503);
  assert.equal((await off.json()).error.code, 'OVERTOURISM_UNAVAILABLE');
});

test('a client disconnect does not abort the shared run; the next call reuses its result', async t => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const s = await stack(t, { gate });
  const directory = await mkdtemp(path.join(tmpdir(), 'jeju-gateway-overtourism-disconnect-'));
  const state = createGatewayState({ filename: path.join(directory, 'gateway.sqlite'), now: s.now });
  t.after(async () => { state.close(); await rm(directory, { recursive: true, force: true }); });
  const upstream = { async ready() { return { ready: true, flowVerified: true, catalogPlaces: 981, productionReady: false }; },
    async execute() { throw new Error('not used'); } };
  const server = createGatewayServer({ authKey: KEY, state, upstream, now: s.now,
    overtourism: createOvertourismService({ state, upstream: s.upstream, now: s.now }) });
  const base = await listen(t, server);
  t.after(async () => { try { await server.closeJobs(); } catch { /* closed */ } });
  let counter = 0;
  const signed = body => {
    const bytes = JSON.stringify(body);
    return { method: 'POST', body: bytes, headers: { 'Content-Type': 'application/json', ...signRequest({ key: KEY, method: 'POST',
      path: '/v1/overtourism', body: bytes, subject: 'service:overtourism', nonce: (++counter).toString(16).padStart(32, '0'),
      timestamp: Math.floor(s.now() / 1000) }) } };
  };
  const controller = new AbortController();
  const first = fetch(base + '/v1/overtourism', { ...signed(snapshot()), signal: controller.signal }).catch(error => error);
  await new Promise(resolve => setTimeout(resolve, 50));
  controller.abort();
  assert.equal((await first).name, 'AbortError');
  release();
  for (let i = 0; i < 50 && s.runs.length === 0; i++) await new Promise(resolve => setTimeout(resolve, 10));
  const second = await (await fetch(base + '/v1/overtourism', signed(snapshot()))).json();
  assert.equal(second.status, 'accepted');
  assert.equal(second.reused, true);
  assert.equal(s.runs.length, 1);
  assert.deepEqual(state.activeCounts({ now: s.now() }), { queued: 0, running: 0, uncertain: 0 });
});

test('settings: missing disables the route, invalid never stops the Gateway', () => {
  const state = { reserve() {}, finish() {} };
  const base = { LANGFLOW_GATEWAY_API_KEY: API_KEY, MCP_SERVICE_TOKEN: MCP_TOKEN, GATEWAY_CONTROL_TOKEN: CONTROL };
  assert.deepEqual(overtourismFromEnv(base, { state }), { status: 'not_configured', service: null });
  assert.equal(overtourismFromEnv({ ...base, LANGFLOW_OVERTOURISM_FLOW_ID: 'bad', LANGFLOW_OVERTOURISM_FLOW_SHA256: 'f'.repeat(64) },
    { state }).status, 'disabled_invalid_config');
  assert.equal(overtourismFromEnv({ ...base, LANGFLOW_OVERTOURISM_FLOW_ID: FLOW_ID, LANGFLOW_OVERTOURISM_FLOW_SHA256: 'f'.repeat(64),
    GATEWAY_OVERTOURISM_DAILY_LIMIT: '99' }, { state }).status, 'disabled_invalid_config');
  const configured = overtourismFromEnv({ ...base, LANGFLOW_OVERTOURISM_FLOW_ID: FLOW_ID, LANGFLOW_OVERTOURISM_FLOW_SHA256: 'f'.repeat(64),
    GATEWAY_OVERTOURISM_DAILY_LIMIT: '4' }, { state });
  assert.equal(configured.status, 'configured');
  assert.equal(typeof configured.service.diagnose, 'function');
});
