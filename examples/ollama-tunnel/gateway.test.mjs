import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';
import { createGateway } from './gateway.mjs';

const TOKEN = 'test-only-not-a-real-secret-0123456789abcdef';
const MODEL = 'test-model:small';
const BODY = { messages: [{ role: 'user', content: '제주 테스트' }] };
const RESULT = { model: MODEL, message: { role: 'assistant', content: '응답' }, done: true };

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
async function fixture(t, handler, options = {}) {
  const upstream = http.createServer(handler || ((_req, res) => res.end(JSON.stringify(RESULT))));
  const upstreamOrigin = await listen(upstream);
  const gateway = createGateway({ token: TOKEN, model: MODEL, upstreamOrigin, ...options });
  const base = await listen(gateway);
  t.after(async () => { await close(gateway); await close(upstream); });
  const call = (body = BODY, extras = {}) => fetch(base + (extras.path || '/api/chat'), {
    method: 'POST', headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json',
      ...(extras.headers || {}) }, body: typeof body === 'string' ? body : JSON.stringify(body),
    ...(extras.signal ? { signal: extras.signal } : {}),
  });
  return { call, base };
}

test('configuration rejects weak tokens, missing model, and remote upstream', () => {
  assert.throws(() => createGateway({ token: 'short', model: MODEL }));
  assert.throws(() => createGateway({ token: TOKEN }));
  assert.throws(() => createGateway({ token: TOKEN, model: MODEL, upstreamOrigin: 'https://example.com' }));
});

test('authorization is required; health reports gateway only without contacting upstream', async t => {
  let calls = 0;
  const { call, base } = await fixture(t, (_req, res) => { calls++; res.end('{}'); });
  assert.equal((await call(BODY, { headers: { Authorization: 'Bearer wrong' } })).status, 401);
  assert.equal((await fetch(base + '/healthz')).status, 401);
  const health = await fetch(base + '/healthz', { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert.deepEqual(await health.json(), { ok: true, check: 'gateway_only' });
  assert.equal(health.headers.get('access-control-allow-origin'), null);
  assert.equal(calls, 0);
});

test('routes, model, streaming, extra options, roles and message bounds are enforced', async t => {
  let calls = 0;
  const { call } = await fixture(t, (_req, res) => { calls++; res.end('{}'); });
  for (const path of ['/api/pull', '/api/create', '/api/delete', '/api/chat?x=1', '/api/chat/']) {
    assert.equal((await call(BODY, { path })).status, 404);
  }
  for (const body of [
    { ...BODY, model: 'other' }, { ...BODY, stream: true }, { ...BODY, options: {} },
    { messages: [] }, { messages: Array(17).fill(BODY.messages[0]) },
    { messages: [{ role: 'system', content: 'instructions' }] },
    { messages: [{ role: 'user', content: 'x'.repeat(4001) }] },
    { messages: [{ role: 'user', content: 'hi', images: ['data'] }] },
  ]) assert.equal((await call(body)).status, 400);
  assert.equal((await call('{bad')).status, 400);
  assert.equal((await call(BODY, { headers: { 'Content-Type': 'text/plain' } })).status, 415);
  assert.equal(calls, 0);
});

test('valid requests pin model/options and never forward client credentials', async t => {
  let observed;
  const { call } = await fixture(t, async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    observed = { url: req.url, headers: req.headers, body: JSON.parse(Buffer.concat(chunks)) };
    res.end(JSON.stringify({ ...RESULT, extra: 'not exposed' }));
  });
  const response = await call({ ...BODY, model: MODEL, stream: false },
    { headers: { 'X-Client-Secret': 'never-forward-this' } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), RESULT);
  assert.equal(observed.url, '/api/chat');
  assert.deepEqual(observed.body, { model: MODEL, messages: BODY.messages, stream: false,
    options: { num_predict: 256, num_ctx: 4096 } });
  assert.equal(observed.headers.authorization, undefined);
  assert.equal(observed.headers['x-client-secret'], undefined);
});

test('oversized fixed-length and chunked request bodies return 413', async t => {
  const { call, base } = await fixture(t);
  assert.equal((await call('x'.repeat(32769))).status, 413);
  const status = await new Promise((resolve, reject) => {
    const req = http.request(base + '/api/chat', { method: 'POST', headers: {
      Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json',
      'Transfer-Encoding': 'chunked',
    } }, res => { res.resume(); resolve(res.statusCode); });
    req.on('error', reject);
    req.write('x'.repeat(20000));
    req.end('x'.repeat(20000));
  });
  assert.equal(status, 413);
});

test('only one upstream request runs at a time and the slot is released', async t => {
  let release;
  let started;
  let calls = 0;
  const entered = new Promise(resolve => { started = resolve; });
  const { call } = await fixture(t, (_req, res) => {
    if (++calls > 1) return res.end(JSON.stringify(RESULT));
    release = () => res.end(JSON.stringify(RESULT));
    started();
  });
  const first = call();
  await entered;
  const second = await call();
  assert.equal(second.status, 429);
  assert.equal(second.headers.get('retry-after'), '1');
  release();
  assert.equal((await first).status, 200);
  assert.equal((await call()).status, 200);
  assert.equal(calls, 2);
});

test('upstream failure details are not exposed', async t => {
  const { call } = await fixture(t, (_req, res) => {
    res.writeHead(500); res.end('private path or provider secret');
  });
  const response = await call();
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: 'Model service unavailable.' });
});

test('upstream timeout aborts the request and returns 504', async t => {
  const { call } = await fixture(t, () => {}, { upstreamTimeoutMs: 40 });
  const response = await call();
  assert.equal(response.status, 504);
  assert.deepEqual(await response.json(), { error: 'Model service timed out.' });
});

test('response-size limit is enforced and malformed model output is rejected', async t => {
  let count = 0;
  const { call } = await fixture(t, (_req, res) => {
    count++;
    res.end(count === 1 ? 'x'.repeat(257) : JSON.stringify({ error: 'upstream internal detail' }));
  }, { maxResponseBytes: 256 });
  const large = await call();
  assert.equal(large.status, 502);
  assert.deepEqual(await large.json(), { error: 'Model response too large.' });
  const malformed = await call();
  assert.equal(malformed.status, 502);
  assert.deepEqual(await malformed.json(), { error: 'Invalid model response.' });
});

test('client disconnect aborts the in-flight upstream', async t => {
  let started;
  let disconnected;
  const entered = new Promise(resolve => { started = resolve; });
  const closed = new Promise(resolve => { disconnected = resolve; });
  const { call } = await fixture(t, (_req, res) => {
    res.on('close', disconnected);
    started();
  });
  const controller = new AbortController();
  const request = call(BODY, { signal: controller.signal });
  await entered;
  controller.abort();
  await assert.rejects(request);
  await Promise.race([closed, new Promise((_resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Upstream was not aborted.')), 1000);
    timer.unref();
  })]);
});
