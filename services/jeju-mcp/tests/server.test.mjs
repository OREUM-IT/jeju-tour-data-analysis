import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { validateCatalog } from '../src/catalog.mjs';
import { createMcpHttpServer } from '../src/server.mjs';

const TOKEN = 'test-only-mcp-service-token-0123456789abcdef';
const ORIGIN = 'https://jeju-tour-six.vercel.app';
const CATALOG = () => validateCatalog({ schemaVersion: '1.0', dataMode: 'fixture', places: [
  { id: 'fixture-a', name: 'Synthetic Coast', category: 'demo', lat: 33, lon: 126,
    evidence: [], constraints: {} },
] });
const AUTH = { Authorization: `Bearer ${TOKEN}` };
const HEADERS = { ...AUTH, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
  'MCP-Protocol-Version': '2025-11-25' };
const LIST = { jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} };

async function fixture(t, options = {}) {
  const server = createMcpHttpServer({ catalog: CATALOG(), token: TOKEN, allowedOrigins: [ORIGIN], ...options });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const post = (body = LIST, headers = {}, endpoint = '/mcp') => fetch(base + endpoint, {
    method: 'POST', headers: { ...HEADERS, ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
  return { base, post };
}

test('configuration rejects weak tokens and wildcard/non-origin allowlists', () => {
  assert.throws(() => createMcpHttpServer({ catalog: CATALOG(), token: 'short' }));
  for (const origin of ['*', 'https://example.com/path', 'null']) {
    assert.throws(() => createMcpHttpServer({ catalog: CATALOG(), token: TOKEN, allowedOrigins: [origin] }));
  }
});

test('real SDK client initializes, discovers and calls tools over stateless JSON HTTP', async t => {
  const { base } = await fixture(t);
  const observed = [];
  const transport = new StreamableHTTPClientTransport(new URL('/mcp', base), {
    requestInit: { headers: AUTH },
    fetch: async (url, init) => {
      const response = await fetch(url, init);
      observed.push({ method: init?.method, request: init?.body ? JSON.parse(init.body).method : null,
        sessionId: response.headers.get('mcp-session-id'), type: response.headers.get('content-type') });
      return response;
    },
  });
  const client = new Client({ name: 'loopback-test', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(transport);
  const listed = await client.listTools();
  assert.deepEqual(listed.tools.map(tool => tool.name).sort(), ['constraints.check', 'places.get', 'places.search']);
  assert.ok(listed.tools.every(tool => tool.annotations?.readOnlyHint === true));
  const found = await client.callTool({ name: 'places.search', arguments: { query: 'Synthetic', maxResults: 1 } });
  assert.notEqual(found.isError, true);
  assert.equal(found.structuredContent.dataMode, 'fixture');
  assert.match(JSON.stringify(found.structuredContent.data), /fixture-a/);
  const place = await client.callTool({ name: 'places.get', arguments: { ids: ['fixture-a'] } });
  assert.match(JSON.stringify(place.structuredContent.data), /Synthetic Coast/);
  const constraints = await client.callTool({ name: 'constraints.check', arguments: {
    ids: ['fixture-a'], requirements: { wheelchairRequired: true },
  } });
  assert.equal(constraints.structuredContent.status, 'partial');
  assert.equal(constraints.structuredContent.data.results[0].checks[0].status, 'unknown');
  assert.equal(constraints.structuredContent.data.results[0].checks[0].reason, 'value_not_provided');
  assert.ok(observed.some(entry => entry.request === 'initialize'));
  assert.ok(observed.some(entry => entry.request === 'notifications/initialized'));
  assert.ok(observed.filter(entry => entry.request === 'tools/call').length >= 3);
  assert.ok(observed.every(entry => entry.sessionId === null));
  assert.ok(observed.filter(entry => entry.request === 'tools/call')
    .every(entry => entry.type?.includes('application/json')));
});

test('liveness reveals no catalog and readiness is authenticated and explicitly non-production', async t => {
  const { base } = await fixture(t);
  assert.deepEqual(await (await fetch(base + '/healthz')).json(), { ok: true, check: 'liveness_only' });
  assert.equal((await fetch(base + '/readyz')).status, 401);
  const response = await fetch(base + '/readyz', { headers: AUTH });
  assert.deepEqual(await response.json(), { ready: true, dataMode: 'fixture', placeCount: 1,
    readOnly: true, productionReady: false });
  assert.equal(response.headers.get('access-control-allow-origin'), null);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('every MCP request requires authentication; unknown origins are rejected', async t => {
  const { post } = await fixture(t);
  assert.equal((await post(LIST, { Authorization: '' })).status, 401);
  assert.equal((await post(LIST, { Authorization: 'Bearer wrong' })).status, 401);
  assert.equal((await post(LIST, { Origin: 'https://attacker.example' })).status, 403);
  assert.equal((await post(LIST, { Origin: 'null' })).status, 403);
  assert.equal((await post(LIST, { Origin: ORIGIN })).status, 200);
  const valid = await post();
  assert.equal(valid.status, 200);
  assert.equal(valid.headers.get('cache-control'), 'no-store');
  assert.equal(valid.headers.get('x-content-type-options'), 'nosniff');
});

test('routes, methods, content types, Accept and protocol versions are restricted', async t => {
  const { post, base } = await fixture(t);
  for (const method of ['GET', 'DELETE', 'OPTIONS', 'PUT']) {
    assert.equal((await fetch(base + '/mcp', { method, headers: AUTH })).status, 405);
  }
  assert.equal((await post(LIST, {}, '/mcp?tool=anything')).status, 404);
  assert.equal((await post(LIST, {}, '/api/pull')).status, 404);
  assert.equal((await post(LIST, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post(LIST, { Accept: 'application/json' })).status, 406);
  assert.equal((await post(LIST, { 'MCP-Protocol-Version': '2099-01-01' })).status, 400);
  assert.equal((await post('{not-json')).status, 400);
});

test('fixed-length and chunked bodies above 128 KiB are rejected before tool execution', async t => {
  const { post, base } = await fixture(t);
  assert.equal((await post('x'.repeat(128 * 1024 + 1))).status, 413);
  const status = await new Promise((resolve, reject) => {
    const req = http.request(base + '/mcp', { method: 'POST', headers: {
      ...HEADERS, 'Transfer-Encoding': 'chunked',
    } }, res => { res.resume(); resolve(res.statusCode); });
    req.on('error', reject);
    req.write('x'.repeat(70_000));
    req.end('x'.repeat(70_000));
  });
  assert.equal(status, 413);
});

test('invalid tool arguments and nonexistent tool calls cannot bypass registered schemas', async t => {
  const { base } = await fixture(t);
  const client = new Client({ name: 'invalid-input-test', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(new URL('/mcp', base), { requestInit: { headers: AUTH } }));
  const invalid = await client.callTool({ name: 'places.search', arguments: { maxResults: 10000 } });
  assert.equal(invalid.isError, true);
  const unknown = await client.callTool({ name: 'shell.exec', arguments: { command: 'anything' } });
  assert.equal(unknown.isError, true);
});
