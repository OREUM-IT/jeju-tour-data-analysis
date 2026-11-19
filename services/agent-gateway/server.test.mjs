import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { signRequest, GatewayAuthError } from './auth.mjs';
import { createGatewayState } from './state.mjs';
import { createGatewayServer } from './server.mjs';

const KEY = 'a'.repeat(64);
const NOW = Date.parse('2026-11-19T00:00:00Z');
const input = extra => ({ schemaVersion: '1.0', query: '합성 장소 후보를 보여 주세요.',
  timezone: 'Asia/Seoul', filters: {}, excludedIds: [], requirements: [], ...extra });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), 'jeju-gateway-server-'));
  const clock = { value: NOW };
  const state = createGatewayState({ filename: path.join(directory, 'gateway.sqlite'), now: () => clock.value });
  const calls = [];
  const upstream = options.upstream ?? {
    async ready() { calls.push({ kind: 'ready' }); return { ready: true, flowVerified: true, catalogPlaces: 981, productionReady: false }; },
    async execute(request, context) {
      calls.push({ kind: 'execute', request, context }); context.onSubmitted();
      return { schemaVersion: '1.0', status: 'accepted', requestId: request.requestId,
        response: { action: 'ask_clarification', placeIds: [] }, productionReady: false };
    },
  };
  const server = createGatewayServer({ authKey: KEY, state, upstream, now: () => clock.value, ...options });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    state.close(); await rm(directory, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  let counter = 0;
  const signed = (route = '/v1/recommend', body = input(), opts = {}) => {
    const method = opts.method ?? (route === '/readyz' ? 'GET' : 'POST');
    const bytes = opts.bytes ?? (body === undefined ? '' : JSON.stringify(body));
    const nonce = opts.nonce ?? (++counter).toString(16).padStart(32, '0');
    const headers = { ...signRequest({ key: KEY, method, path: route, body: bytes,
      subject: opts.subject ?? 'test-member', nonce, timestamp: Math.floor(clock.value / 1000) }),
      ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}), ...opts.headers };
    return { method, headers, ...(method === 'GET' ? {} : { body: bytes }) };
  };
  const send = (body = input(), opts = {}) => {
    const route = opts.route ?? '/v1/recommend';
    return fetch(base + route, signed(route, body, opts));
  };
  return { base, server, state, clock, calls, signed, send };
}

async function rawRequest(base, { method = 'POST', route = '/v1/recommend', headers, chunks = [] }) {
  return new Promise((resolve, reject) => {
    if (Array.isArray(headers) && !headers.some((value, index) => index % 2 === 0 && value.toLowerCase() === 'host')) {
      headers = ['Host', new URL(base).host, ...headers];
    }
    const req = http.request(base + route, { method, headers }, res => {
      const parts = [];
      res.on('data', chunk => parts.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers,
        text: Buffer.concat(parts).toString(), value: JSON.parse(Buffer.concat(parts).toString()) }));
    });
    req.on('error', reject);
    for (const chunk of chunks) req.write(chunk);
    req.end();
  });
}

test('only liveness is anonymous; signed readiness and recommendation do not expose credentials or permit arbitrary routes', async t => {
  const { base, calls, send, signed } = await fixture(t);
  const health = await fetch(base + '/healthz');
  assert.deepEqual(await health.json(), { ok: true, check: 'liveness_only' });
  assert.equal((await fetch(base + '/readyz')).status, 401);
  assert.equal((await fetch(base + '/v1/recommend', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input()) })).status, 401);
  assert.equal(calls.length, 0);
  const ready = await fetch(base + '/readyz', signed('/readyz', undefined, { bytes: '' }));
  assert.equal(ready.status, 200);
  assert.equal((await ready.json()).mode, 'private');
  for (const route of ['/api/v1/flows', '/api/v1/run/unknown-flow', '/v1/recommend?flowId=unknown', '/v1/recommend/']) {
    assert.equal((await send(input(), { route })).status, 404);
  }
  assert.equal((await send(input(), { method: 'PUT' })).status, 405);
  const response = await send();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const output = await response.json();
  assert.match(output.requestId, /^gw-[a-f0-9]{32}$/u);
  const execution = calls.find(call => call.kind === 'execute');
  assert.equal(execution.request.requestId, output.requestId);
  assert.match(execution.context.subjectHash, /^[a-f0-9]{64}$/u);
  assert.notEqual(execution.context.subjectHash, 'test-member');
  assert.equal(execution.context.deadlineMs, NOW + 105_000);
  assert.equal(JSON.stringify(output).includes(KEY), false);
});

test('Origin, Cookie, duplicate signed headers and unknown top-level execution controls are rejected before upstream', async t => {
  const { base, calls, signed, send } = await fixture(t);
  for (const headers of [{ Origin: 'https://www.oreumit.com' }, { Cookie: 'session=private' }]) {
    assert.equal((await send(input(), { headers })).status, 403);
  }
  for (const extra of [{ flowId: 'attacker-flow' }, { requestId: 'attacker-id' }, { tweaks: {} },
    { model: 'unreviewed' }, { output_component: 'Agent' }, { principal: 'admin' }]) {
    const response = await send(input(extra));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, 'INVALID_RECOMMENDATION_REQUEST');
  }
  const request = signed();
  const rawHeaders = Object.entries(request.headers).flat();
  rawHeaders.push('X-Jeju-Nonce', request.headers['x-jeju-nonce']);
  const duplicate = await rawRequest(base, { headers: rawHeaders, chunks: [request.body] });
  assert.equal(duplicate.status, 400);
  assert.equal(duplicate.value.error.code, 'INVALID_HEADERS');
  assert.equal(calls.length, 0);
});

test('Origin rejection reports only bounded boolean header diagnostics while every cookie remains forbidden', async t => {
  const jwt = 'eyJ0eXAiOiJKV1QifQ.eyJzdWIiOiJjYW5hcnktc2VjcmV0In0.c2VjcmV0LXNpZ25hdHVyZQ';
  const otherJwt = 'eyJ0eXAiOiJKV1QifQ.eyJzdWIiOiJvdGhlci1jYW5hcnkifQ.b3RoZXItc2lnbmF0dXJl';
  const origin = 'https://private-canary.example.invalid';
  const cases = [
    [{ Origin: origin }, [true, false, false, false, false]],
    [{ Cookie: '' }, [false, true, true, false, false]],
    [{ Cookie: 'session=private-canary-cookie' }, [false, true, false, false, false]],
    [{ Cookie: `CF_Authorization=${jwt}` }, [false, true, false, false, false]],
    [{ Cookie: `CF_Authorization=${jwt}`, 'cf-access-jwt-assertion': '' }, [false, true, false, false, false]],
    [{ Cookie: `CF_Authorization=${jwt}`, 'cf-access-jwt-assertion': jwt }, [false, true, false, true, true]],
    [{ Cookie: `CF_Authorization=${otherJwt}`, 'cf-access-jwt-assertion': jwt }, [false, true, false, true, false]],
    [{ Cookie: `CF_Authorization=${jwt}; session=private-canary-cookie`, 'cf-access-jwt-assertion': jwt }, [false, true, false, false, false]],
    [{ Cookie: `CF_Authorization=${jwt}; CF_Authorization=${jwt}`, 'cf-access-jwt-assertion': jwt }, [false, true, false, false, false]],
    [{ Cookie: `CF_Authorization="${jwt}"`, 'cf-access-jwt-assertion': jwt }, [false, true, false, false, false]],
    [{ Cookie: 'CF_Authorization=', 'cf-access-jwt-assertion': jwt }, [false, true, false, false, false]],
    [{ Origin: origin, Cookie: `CF_Authorization=${jwt}`, 'cf-access-jwt-assertion': jwt }, [true, true, false, true, true]],
  ];
  const keys = ['originPresent', 'cookiePresent', 'cookieEmpty', 'singleCfAuthorizationCookie', 'cookieMatchesAssertion'];
  let accessCalls = 0;
  for (const mode of ['private', 'access']) {
    const app = await fixture(t, mode === 'access' ? { mode, publicHost: 'agent.example.invalid',
      accessVerifier: async () => { accessCalls++; } } : {});
    for (const [headers, expected] of cases) {
      const signed = app.signed('/v1/recommend', input(), { headers: { Host: 'agent.example.invalid', ...headers } });
      const result = await rawRequest(app.base, { headers: signed.headers, chunks: [signed.body] });
      assert.equal(result.status, 403);
      assert.deepEqual(result.value, { error: { code: 'ORIGIN_REJECTED',
        headerContext: Object.fromEntries(keys.map((key, index) => [key, expected[index]])) } });
      assert.ok(Object.values(result.value.error.headerContext).every(value => typeof value === 'boolean'));
      for (const secret of [jwt, otherJwt, origin, 'private-canary-cookie', KEY]) assert.equal(result.text.includes(secret), false);
    }
    // An Access-shaped cookie still cannot pass the anonymous liveness route.
    const health = await rawRequest(app.base, { method: 'GET', route: '/healthz',
      headers: { Cookie: `CF_Authorization=${jwt}`, 'cf-access-jwt-assertion': jwt } });
    assert.equal(health.status, 403);
    assert.equal(health.value.error.headerContext.cookieMatchesAssertion, true);
    assert.equal(app.calls.length, 0);
  }
  assert.equal(accessCalls, 0);
});

test('header diagnostic context is absent for authentication, routing and upstream failures', async t => {
  const app = await fixture(t);
  const anonymous = await fetch(app.base + '/readyz');
  assert.equal(anonymous.status, 401);
  assert.deepEqual(await anonymous.json(), { error: { code: 'AUTH_FAILED' } });
  const unknown = await app.send(input(), { route: '/unknown' });
  assert.deepEqual(await unknown.json(), { error: { code: 'NOT_FOUND' } });
  // Even an upstream exception cannot attach arbitrary diagnostic contents to the public response.
  const forged = await fixture(t, { upstream: { execute: async () => {
    throw Object.assign(new Error('private-canary-error'), { code: 'ORIGIN_REJECTED', status: 403,
      headerContext: { cookiePresent: 'private-canary-cookie' } });
  } } });
  const response = await forged.send();
  const value = await response.json();
  assert.equal(value.error.code, 'ORIGIN_REJECTED');
  assert.equal('headerContext' in value.error, false);
  assert.equal(JSON.stringify(value).includes('private-canary'), false);
});

test('access mode requires exact origin host and a verified Access JWT in addition to application HMAC', async t => {
  const verified = [];
  const accessVerifier = async token => {
    verified.push(token);
    if (token !== 'valid-access-test-token') throw new GatewayAuthError();
  };
  const { base, signed, calls } = await fixture(t, { mode: 'access', publicHost: 'agent.example.invalid', accessVerifier });
  const send = headers => {
    const request = signed('/v1/recommend', input(), { headers });
    return rawRequest(base, { headers: request.headers, chunks: [request.body] });
  };
  assert.equal((await send({ Host: 'wrong.example.invalid', 'cf-access-jwt-assertion': 'valid-access-test-token' })).status, 401);
  assert.equal(verified.length, 0);
  assert.equal((await send({ Host: 'agent.example.invalid' })).status, 401);
  assert.equal((await send({ Host: 'agent.example.invalid', 'cf-access-jwt-assertion': 'invalid' })).status, 401);
  assert.equal(calls.length, 0);
  assert.equal((await send({ Host: 'agent.example.invalid', 'cf-access-jwt-assertion': 'valid-access-test-token' })).status, 200);
  assert.equal(calls.filter(call => call.kind === 'execute').length, 1);
});

test('Access verification time consumes the original HTTP deadline instead of granting a fresh execution window', async t => {
  let clock;
  const accessVerifier = async token => {
    assert.equal(token, 'valid-access-test-token');
    // Simulate five seconds spent waiting for Access certificates without sleeping.
    clock.value += 5000;
    await Promise.resolve();
  };
  const app = await fixture(t, { mode: 'access', publicHost: 'agent.example.invalid', accessVerifier });
  clock = app.clock;
  const request = app.signed('/v1/recommend', input(), { headers: {
    Host: 'agent.example.invalid', 'cf-access-jwt-assertion': 'valid-access-test-token',
  } });
  const response = await rawRequest(app.base, { headers: request.headers, chunks: [request.body] });
  assert.equal(response.status, 200);
  const execution = app.calls.find(call => call.kind === 'execute');
  assert.equal(clock.value, NOW + 5000);
  assert.equal(execution.context.deadlineMs, NOW + 105_000);
  assert.equal(execution.context.deadlineMs - clock.value, 100_000);
  assert.equal(execution.context.signal.aborted, false);
});

test('fixed length and chunked oversized bodies, invalid UTF-8 and malformed JSON never reach upstream', async t => {
  const { base, signed, send, calls } = await fixture(t);
  const huge = JSON.stringify(input({ query: 'x'.repeat(33 * 1024) }));
  assert.equal((await send(input(), { bytes: huge })).status, 413);
  const streamRequest = signed('/v1/recommend', undefined, { bytes: huge });
  const chunked = await rawRequest(base, { headers: streamRequest.headers, chunks: [huge.slice(0, 12000), huge.slice(12000)] });
  assert.equal(chunked.status, 413);
  assert.equal(chunked.value.error.code, 'BODY_TOO_LARGE');
  for (const bytes of ['{', Buffer.from([0xff, 0xfe, 0xff])]) {
    const response = await send(input(), { bytes });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, 'INVALID_JSON');
  }
  assert.equal((await send(input(), { headers: { 'Content-Type': 'text/plain' } })).status, 415);
  assert.equal(calls.length, 0);
});

test('persistent state rejects replay and seventh user request without making another upstream call', async t => {
  const { send, calls } = await fixture(t);
  const nonce = 'e'.repeat(32);
  assert.equal((await send(input(), { nonce })).status, 200);
  const replay = await send(input(), { nonce });
  assert.equal(replay.status, 409);
  assert.equal((await replay.json()).error.code, 'REPLAY_DETECTED');
  for (let index = 0; index < 5; index++) assert.equal((await send()).status, 200);
  const seventh = await send();
  assert.equal(seventh.status, 429);
  assert.equal((await seventh.json()).error.code, 'RATE_LIMITED');
  assert.equal(calls.filter(call => call.kind === 'execute').length, 6);
  assert.equal((await send(input(), { subject: 'different-member' })).status, 200);
});

test('overlapping requests reserve a single execution and return busy with bounded retry guidance', async t => {
  const started = deferred(); const release = deferred(); let submissions = 0;
  const upstream = { ready: async () => ({}), execute: async (request, context) => {
    submissions++; context.onSubmitted(); started.resolve(); await release.promise;
    return { status: 'accepted', requestId: request.requestId };
  } };
  const { send } = await fixture(t, { upstream });
  const first = send(); await started.promise;
  const second = await send(input(), { subject: 'another-member' });
  assert.equal(second.status, 429);
  assert.equal(second.headers.get('retry-after'), '120');
  assert.equal((await second.json()).error.code, 'BUSY');
  assert.equal(submissions, 1);
  release.resolve(); assert.equal((await first).status, 200);
  assert.equal((await send(input(), { subject: 'another-member' })).status, 200);
});

test('timeout after submission retains uncertain lease, has no automatic retry and sanitizes provider failures', async t => {
  let submissions = 0;
  const upstream = { ready: async () => ({}), execute: async (_request, context) => {
    submissions++; context.onSubmitted(); throw new DOMException('canary-private-provider-body', 'TimeoutError');
  } };
  const { send, clock } = await fixture(t, { upstream });
  const timedOut = await send();
  assert.equal(timedOut.status, 504);
  const text = await timedOut.text();
  assert.equal(text.includes('canary'), false);
  assert.equal(JSON.parse(text).error.code, 'UPSTREAM_TIMEOUT_NO_RETRY');
  assert.equal((await send()).status, 429);
  assert.equal(submissions, 1);
  clock.value += 120_001;
  assert.equal((await send()).status, 504);
  assert.equal(submissions, 2);
});

test('pre-submission errors and state failures are sanitized and never advertise successful results', async t => {
  const upstream = { ready: async () => ({}), execute: async () => { throw new Error('canary-secret-token'); } };
  const { send, state } = await fixture(t, { upstream });
  const response = await send();
  assert.equal(response.status, 502);
  const value = await response.json();
  assert.deepEqual(value.error, { code: 'GATEWAY_UNAVAILABLE' });
  assert.equal(JSON.stringify(value).includes('canary'), false);
  state.close();
  const unavailable = await send();
  assert.equal(unavailable.status, 503);
  assert.equal((await unavailable.json()).error.code, 'STATE_UNAVAILABLE');
});

test('scope cleanup failure after submission blocks a successful result and preserves the uncertain execution lease', async t => {
  let submissions = 0;
  const upstream = { ready: async () => ({}), execute: async (_request, context) => {
    submissions++; context.onSubmitted();
    throw Object.assign(new Error('canary-cleanup-provider-detail'), { code: 'SCOPE_CLEANUP_FAILED', status: 503 });
  } };
  const { send } = await fixture(t, { upstream });
  const response = await send();
  assert.equal(response.status, 503);
  const output = await response.json();
  assert.deepEqual(output.error, { code: 'SCOPE_CLEANUP_FAILED' });
  assert.equal('response' in output, false);
  assert.equal(JSON.stringify(output).includes('canary'), false);
  assert.equal((await send()).status, 429);
  assert.equal(submissions, 1);
});
