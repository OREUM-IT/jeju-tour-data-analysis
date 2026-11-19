import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { makeCiCatalog } from '../../deploy/tests/make-ci-catalog.mjs';
import { validateAgentServerCatalog } from '../jeju-mcp/src/catalog.mjs';
import { createMcpHttpServer } from '../jeju-mcp/src/server.mjs';
import { boundedJson, createUpstream, digest, selectFinal, UpstreamError } from './upstream.mjs';
import { createGatewayServer } from './server.mjs';
import { createGatewayState } from './state.mjs';
import { signRequest } from './auth.mjs';

const NOW = Date.parse('2026-11-19T00:00:00Z');
const KEY = 'a'.repeat(64), MCP_TOKEN = 'm'.repeat(64), CONTROL = 'c'.repeat(64), API_KEY = 'sk-test-only-no-provider';
const FLOW_ID = '20e11883-f42e-4d91-9e52-9a6a40c393fd';
const ID = 'gw-' + 'b'.repeat(32);
const FLOW_DATA = { nodes: [{ id: 'approved-mock-flow' }], edges: [] };
const fixtureCatalog = validateAgentServerCatalog(structuredClone(makeCiCatalog(
  JSON.parse(await readFile(new URL('../../data/theme-category-map.json', import.meta.url), 'utf8')),
  JSON.parse(await readFile(new URL('../../data/persona-prototypes.json', import.meta.url), 'utf8')),
)));
const request = (extra = {}) => ({ schemaVersion: '1.0', requestId: ID, query: '실내 조건을 확인할 합성 장소 후보를 최대 세 곳 보여 주세요.',
  timezone: 'Asia/Seoul', filters: {}, excludedIds: [], requirements: [{ type: 'indoor_required', value: true }], ...extra });
const context = (extra = {}) => ({ subjectHash: 'd'.repeat(64), deadlineMs: NOW + 110_000,
  signal: new AbortController().signal, ...extra });
const envelope = (final, id = final.requestId) => ({ session_id: id, outputs: [{ outputs: [{
  component_id: 'ChatOutput-jejuV2', results: { message: { sender: 'Machine',
    properties: { source: { id: 'JejuFinalValidation-v2', source: 'Jeju Final Validation' } }, text: JSON.stringify(final) } },
}]}] });
const json = (res, value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
const readBody = async req => { const chunks = []; for await (const chunk of req) chunks.push(chunk); return JSON.parse(Buffer.concat(chunks)); };
async function listen(t, server) {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return `http://127.0.0.1:${server.address().port}`;
}

async function fixture(t, options = {}) {
  const mcp = createMcpHttpServer({ catalog: fixtureCatalog, token: MCP_TOKEN, gatewayControlToken: CONTROL, now: NOW });
  const mcpUrl = await listen(t, mcp);
  const mcpCall = async (route, body, control = false) => {
    const response = await fetch(mcpUrl + route, { method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${control ? CONTROL : MCP_TOKEN}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    assert.equal(response.status, 200);
    return response.json();
  };
  const calls = [];
  let flowData = structuredClone(FLOW_DATA);
  const langflow = http.createServer(async (req, res) => {
    const call = { method: req.method, route: req.url, headers: req.headers }; calls.push(call);
    try {
      assert.equal(req.headers['x-api-key'], API_KEY);
      if (req.method === 'GET' && req.url === `/api/v1/flows/${FLOW_ID}`) return json(res, { id: FLOW_ID, data: flowData });
      if (req.method !== 'POST' || req.url !== `/api/v1/run/${FLOW_ID}?stream=false`) return json(res, { privateError: 'canary-route-body' }, 404);
      call.body = await readBody(req);
      const original = JSON.parse(call.body.input_value);
      if (options.run) return await options.run({ req, res, original, body: call.body, mcpCall });
      // A mock model reaches the real scoped MCP endpoints; no provider SDK or API is used.
      const prepared = await mcpCall('/gateway/prepare-request', original);
      const found = await mcpCall('/gateway/tools/call', { requestId: original.requestId, name: 'places.search', arguments: { maxResults: 3 } });
      const placeIds = found.data.items.map(item => item.id);
      const checked = await mcpCall('/gateway/tools/call', { requestId: original.requestId,
        name: 'constraints.check', arguments: { ids: placeIds } });
      assert.ok(checked.data.results.every(result => result.checks.every(check => check.status === 'unknown')));
      let final = { schemaVersion: '1.0', requestId: original.requestId, action: 'cannot_confirm', placeIds,
        requirementClaims: placeIds.map(placeId => ({ placeId, requirement: 'indoor_required', verdict: 'unknown' })),
        confirmationNotice: true, noticeTopics: ['indoor_unverified'] };
      if (options.agent) final = options.agent(final);
      const accepted = await mcpCall('/gateway/validate-response', { request: original, response: final, expectedCatalogVersion: prepared.catalogVersion });
      assert.equal(accepted.status, 'accepted');
      final = accepted.response; // Match the real Langflow Final component's publication boundary.
      if (options.final) final = options.final(final);
      let raw = envelope(final, original.requestId);
      if (options.raw) raw = options.raw(raw);
      return json(res, raw);
    } catch { if (!res.headersSent) json(res, { privateError: 'canary-mock-runtime-error' }, 500); }
  });
  const langflowUrl = await listen(t, langflow);
  const network = [];
  const fetchImpl = async (url, config) => {
    network.push({ url, config });
    assert.ok(url.startsWith(mcpUrl + '/') || url.startsWith(langflowUrl + '/'), 'Only local fixture URLs may be contacted.');
    assert.equal(config.redirect, 'error');
    if (options.fetchOverride) {
      const overridden = await options.fetchOverride(url, config, { mcpUrl, langflowUrl });
      if (overridden !== undefined) return overridden;
    }
    return fetch(url, config);
  };
  const upstream = createUpstream({ flowId: FLOW_ID, flowSha256: digest(FLOW_DATA), langflowApiKey: API_KEY,
    mcpToken: MCP_TOKEN, controlToken: CONTROL, mcpUrl, langflowUrl, fetchImpl });
  return { upstream, network, calls, mcpCall, mcpUrl, langflowUrl, changeFlow: () => { flowData.nodes.push({ id: 'unknown-edit' }); } };
}
const hasCode = code => error => error instanceof UpstreamError && error.code === code;

test('actual scoped MCP and mock Langflow enforce fixed requests and return only independently accepted final output', async t => {
  const { upstream, network, calls, mcpCall } = await fixture(t);
  assert.deepEqual(await upstream.ready(), { ready: true, flowVerified: true, catalogPlaces: 981, productionReady: false });
  let submitted = 0;
  const result = await upstream.execute(request(), context({ onSubmitted: () => { submitted++; } }));
  assert.equal(submitted, 1);
  assert.equal(result.status, 'accepted');
  assert.equal(result.response.action, 'cannot_confirm');
  assert.deepEqual(result.response.noticeTopics, ['indoor_unverified']);
  assert.equal(result.response.placeIds.length, 3);
  assert.equal(result.productionReady, false);
  const run = calls.filter(call => call.method === 'POST');
  assert.equal(run.length, 1);
  assert.equal(run[0].route, `/api/v1/run/${FLOW_ID}?stream=false`);
  assert.deepEqual(run[0].body, { input_value: JSON.stringify(request()), input_type: 'chat', output_type: 'chat',
    output_component: 'ChatOutput-jejuV2', session_id: ID });
  assert.equal('tweaks' in run[0].body, false);
  for (const secret of [KEY, MCP_TOKEN, CONTROL, API_KEY]) assert.equal(JSON.stringify(result).includes(secret), false);
  assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  const controlCalls = network.filter(call => /\/gateway\/(scopes|readyz)/u.test(call.url));
  assert.ok(controlCalls.every(call => call.config.headers.Authorization === `Bearer ${CONTROL}`));
  const finalCheck = network.find(call => call.url.endsWith('/gateway/validate-response'));
  assert.equal(finalCheck.config.headers.Authorization, `Bearer ${MCP_TOKEN}`);
  assert.deepEqual(JSON.parse(finalCheck.config.body).request, request());
  assert.equal(network.at(-1).config.method, 'DELETE');
});

test('real signed HTTP Gateway → mock Langflow → real scoped MCP completes without exposing intermediate artifacts', async t => {
  const { upstream, calls, mcpCall } = await fixture(t);
  const directory = await mkdtemp(path.join(tmpdir(), 'jeju-gateway-e2e-'));
  const state = createGatewayState({ filename: path.join(directory, 'gateway.sqlite'), now: NOW });
  const server = createGatewayServer({ authKey: KEY, state, upstream, now: () => NOW });
  const url = await listen(t, server);
  t.after(async () => { state.close(); await rm(directory, { recursive: true, force: true }); });
  const { requestId: _, ...input } = request();
  const body = JSON.stringify(input);
  const headers = { ...signRequest({ key: KEY, method: 'POST', path: '/v1/recommend', body,
    subject: 'synthetic-user', timestamp: NOW / 1000, nonce: 'e'.repeat(32) }), 'Content-Type': 'application/json' };
  const response = await fetch(url + '/v1/recommend', { method: 'POST', headers, body });
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.equal(value.status, 'accepted');
  assert.match(value.requestId, /^gw-[a-f0-9]{32}$/u);
  assert.equal(value.response.requestId, value.requestId);
  assert.equal(value.response.action, 'cannot_confirm');
  assert.equal('outputs' in value, false);
  assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  assert.equal((await fetch(url + '/v1/recommend', { method: 'POST', headers, body })).status, 409);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});

test('missing model claims and false passes become evidence-backed candidates before final Gateway publication', async t => {
  for (const claims of [[], 'malformed', [{ placeId: fixtureCatalog.places[0].id, requirement: 'indoor_required', verdict: 'pass' }]]) {
    const { upstream, calls, mcpCall } = await fixture(t, { agent: value => ({ ...value, action: 'recommend',
      requirementClaims: claims, confirmationNotice: false, noticeTopics: [], reply: 'unchecked text must not escape' }) });
    const result = await upstream.execute(request(), context());
    assert.equal(result.response.action, 'cannot_confirm');
    assert.equal(result.response.placeIds.length, 3);
    assert.ok(result.response.requirementClaims.every(claim => claim.verdict === 'unknown'));
    assert.deepEqual(result.response.noticeTopics, ['indoor_unverified']);
    assert.equal(result.response.confirmationNotice, true);
    assert.equal(JSON.stringify(result).includes('unchecked text'), false);
    assert.equal(calls.filter(call => call.method === 'POST').length, 1);
    assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  }
});

test('changed approved graph or bad readiness blocks before any model submission or scope creation', async t => {
  const first = await fixture(t); first.changeFlow();
  await assert.rejects(first.upstream.execute(request(), context()), hasCode('FLOW_CHANGED'));
  assert.equal(first.calls.filter(call => call.method === 'POST').length, 0);
  assert.equal(first.network.some(call => call.url.endsWith('/gateway/scopes')), false);
  for (const invalid of [{ placeCount: 980 }, { productionReady: true }, { dataMode: 'fixture' },
    { catalogSchemaVersion: '1.0' }, { dataVersion: 'unbound' }, { toolCallLimit: 9 }]) {
    const testCase = await fixture(t, { fetchOverride: async (url, _config, { mcpUrl }) => {
      if (url === mcpUrl + '/gateway/readyz') return Response.json({ ready: true, catalogSchemaVersion: '2.0',
        dataMode: 'app-export', placeCount: 981, toolCallLimit: 8, productionReady: false,
        dataVersion: 'sha256:' + '0'.repeat(64), ...invalid });
    } });
    await assert.rejects(testCase.upstream.execute(request(), context()), hasCode('MCP_NOT_READY'));
    assert.equal(testCase.calls.filter(call => call.method === 'POST').length, 0);
  }
});

test('untrusted final source, session, request identity, unknown places and unknown-to-pass claims are blocked and scopes released', async t => {
  const cases = [
    { raw: raw => ({ ...raw, session_id: 'gw-' + 'f'.repeat(32) }), code: 'SESSION_MISMATCH' },
    { raw: raw => { raw.outputs[0].outputs[0].results.message.properties.source.id = 'Agent'; return raw; }, code: 'VALIDATOR_SOURCE_MISMATCH' },
    { raw: raw => { raw.outputs[0].outputs.push(structuredClone(raw.outputs[0].outputs[0])); return raw; }, code: 'OUTPUT_MISMATCH' },
    { final: final => ({ ...final, requestId: 'gw-' + 'f'.repeat(32) }), code: 'REQUEST_MISMATCH' },
    { final: final => ({ ...final, placeIds: ['injected-canary-unknown-place'] }), code: 'FINAL_VALIDATION_REJECTED' },
    { final: final => ({ ...final, action: 'recommend', requirementClaims: final.requirementClaims.map(claim => ({ ...claim, verdict: 'pass' })) }), code: 'FINAL_VALIDATION_REJECTED' },
    { final: final => ({ ...final, reply: 'canary-unchecked-free-text' }), code: 'FINAL_VALIDATION_REJECTED' },
    { final: final => {
      const placeIds = fixtureCatalog.places.slice(0, 6).map(place => place.id);
      return { ...final, placeIds, requirementClaims: placeIds.map(placeId => ({ placeId, requirement: 'indoor_required', verdict: 'unknown' })) };
    }, code: 'FINAL_VALIDATION_REJECTED' },
    { final: () => ({ status: 'blocked', code: 'VALIDATION_FAILED', message: 'private-canary' }), code: 'RECOMMENDATION_BLOCKED' },
  ];
  for (const item of cases) {
    const { upstream, calls, mcpCall } = await fixture(t, item);
    await assert.rejects(upstream.execute(request(), context()), error => hasCode(item.code)(error) && !error.message.includes('canary'));
    assert.equal(calls.filter(call => call.method === 'POST').length, 1);
    assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  }
});

test('independent validator metadata and response must exactly match the prepared request binding', async t => {
  for (const field of ['requestSha256', 'catalogSha256', 'catalogVersion', 'policyVersion', 'candidateCount', 'response']) {
    const { upstream, mcpCall } = await fixture(t, { fetchOverride: async (url, config, { mcpUrl }) => {
      if (url !== mcpUrl + '/gateway/validate-response') return;
      const real = await fetch(url, config);
      const checked = await real.json();
      checked[field] = field === 'response' ? { ...checked.response, confirmationNotice: false }
        : field === 'candidateCount' ? checked[field] + 1 : 'mismatched';
      return Response.json(checked);
    } });
    await assert.rejects(upstream.execute(request(), context()), hasCode('FINAL_VALIDATION_REJECTED'));
    assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  }
});

test('preparation mismatch and aborted model request clean up the scope without using the cancelled signal or retrying', async t => {
  const malformed = await fixture(t, { fetchOverride: async (url, config, { mcpUrl }) => {
    if (url !== mcpUrl + '/gateway/scopes') return;
    const real = await fetch(url, config); const prepared = await real.json();
    prepared.request.query = 'canary-replaced-request'; return Response.json(prepared);
  } });
  await assert.rejects(malformed.upstream.execute(request(), context()), hasCode('PREPARATION_MISMATCH'));
  assert.equal(malformed.calls.filter(call => call.method === 'POST').length, 0);
  assert.equal((await malformed.mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
  const controller = new AbortController();
  const timed = await fixture(t, { fetchOverride: async (url, config, { langflowUrl }) => {
    if (!url.startsWith(langflowUrl + '/api/v1/run/')) return;
    controller.abort(new DOMException('canary-private-timeout', 'TimeoutError'));
    throw config.signal.reason;
  } });
  let submissions = 0;
  await assert.rejects(timed.upstream.execute(request(), context({ signal: controller.signal, onSubmitted: () => { submissions++; } })), { name: 'TimeoutError' });
  assert.equal(submissions, 1);
  assert.equal(timed.network.filter(call => call.url.includes('/api/v1/run/')).length, 1);
  const cleanup = timed.network.at(-1);
  assert.equal(cleanup.config.method, 'DELETE');
  assert.equal(cleanup.config.signal.aborted, false);
  assert.equal((await timed.mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
});

test('failed scope cleanup does not replace the original model timeout or trigger another model submission', async t => {
  const controller = new AbortController();
  const originalTimeout = new DOMException('canary-original-model-timeout', 'TimeoutError');
  const { upstream, network, mcpCall } = await fixture(t, { fetchOverride: async (url, config, { langflowUrl }) => {
    if (config.method === 'DELETE') return Response.json({ error: 'canary-private-cleanup-body' }, { status: 503 });
    if (url.startsWith(langflowUrl + '/api/v1/run/')) {
      controller.abort(originalTimeout);
      throw originalTimeout;
    }
  } });
  let submitted = 0;
  await assert.rejects(upstream.execute(request(), context({ signal: controller.signal,
    onSubmitted: () => { submitted++; } })), error => error === originalTimeout);
  assert.equal(submitted, 1);
  assert.equal(network.filter(call => call.url.includes('/api/v1/run/')).length, 1);
  const cleanup = network.filter(call => call.config.method === 'DELETE');
  assert.equal(cleanup.length, 1);
  assert.equal(cleanup[0].config.signal.aborted, false);
  // A failed revoke is not falsely reported as a released scope; TTL remains the fail-closed bound.
  assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 1);
});

test('otherwise accepted recommendation is withheld if scope cleanup fails', async t => {
  const { upstream, calls, network, mcpCall } = await fixture(t, { fetchOverride: async (_url, config) => {
    if (config.method === 'DELETE') throw new Error('canary-private-cleanup-exception');
  } });
  await assert.rejects(upstream.execute(request(), context()), error => hasCode('SCOPE_CLEANUP_FAILED')(error)
    && error.status === 503 && !error.message.includes('canary'));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(network.filter(call => call.url.endsWith('/gateway/validate-response')).length, 1);
  assert.equal(network.filter(call => call.config.method === 'DELETE').length, 1);
  assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 1);
});

test('HTTP upstream rejection never echoes response content and still releases its registered scope', async t => {
  const { upstream, calls, mcpCall } = await fixture(t, { run: async ({ res }) => json(res, { error: 'canary-secret-provider-detail' }, 500) });
  await assert.rejects(upstream.execute(request(), context()), error => hasCode('UPSTREAM_REJECTED')(error) && !error.message.includes('canary'));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal((await mcpCall('/gateway/readyz', undefined, true)).activeScopes, 0);
});

test('JSON reader bounds streamed bodies, rejects invalid UTF-8, and final selector never uses intermediate outputs', async () => {
  await assert.rejects(boundedJson(new Response('x'.repeat(33)), 32), hasCode('UPSTREAM_TOO_LARGE'));
  await assert.rejects(boundedJson(new Response(Buffer.from([0xff, 0xfe]))), hasCode('UPSTREAM_INVALID_JSON'));
  await assert.rejects(boundedJson(new Response('{')), hasCode('UPSTREAM_INVALID_JSON'));
  const raw = envelope({ schemaVersion: '1.0', requestId: ID, action: 'ask_clarification', placeIds: [] });
  raw.outputs[0].outputs.unshift({ component_id: 'Agent', results: { message: { text: 'canary-raw-agent' } } });
  assert.deepEqual(selectFinal(raw, ID), { schemaVersion: '1.0', requestId: ID, action: 'ask_clarification', placeIds: [] });
  assert.throws(() => selectFinal({ ...raw, outputs: [raw.outputs[0], raw.outputs[0]] }, ID), hasCode('OUTPUT_MISMATCH'));
});

test('JSON reader cancels a declared oversized body and preserves abort classification while streaming', async () => {
  let cancelled = false;
  const response = new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { 'content-length': '100' } });
  await assert.rejects(boundedJson(response, 32), hasCode('UPSTREAM_TOO_LARGE'));
  assert.equal(cancelled, true);
  const aborted = new Response(new ReadableStream({ start(controller) {
    controller.error(new DOMException('canary-timeout', 'TimeoutError'));
  } }));
  await assert.rejects(boundedJson(aborted), { name: 'TimeoutError' });
});

test('upstream rejects unapproved destinations and malformed fixed flow identity before any network access', () => {
  const config = { flowId: FLOW_ID, flowSha256: digest(FLOW_DATA), langflowApiKey: API_KEY, mcpToken: MCP_TOKEN, controlToken: CONTROL };
  for (const url of ['http://169.254.169.254', 'http://evil.invalid', 'http://jeju-mcp:8788/path', 'http://user:pass@jeju-mcp:8788', 'http://langflow:7860?target=elsewhere']) {
    assert.throws(() => createUpstream({ ...config, mcpUrl: url }), hasCode('CONFIG_INVALID'));
  }
  assert.throws(() => createUpstream({ ...config, flowId: '../different-flow' }), hasCode('CONFIG_INVALID'));
});
