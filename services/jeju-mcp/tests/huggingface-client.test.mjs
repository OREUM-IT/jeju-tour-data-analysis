import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { z } from 'zod';
import { HF_ENDPOINT, MAX_OUTPUT_BYTES, runHuggingFace } from '../scripts/huggingface-client.mjs';

const TOKEN = 'placeholder-for-local-test-only';
const success = data => ({ content: [{ type: 'text', text: JSON.stringify(data) }], structuredContent: data });

async function fixture(t, { result, missing = false, pagination } = {}) {
  const requests = [], calls = [], resources = new Set();
  const server = http.createServer(async (req, res) => {
    if (req.method !== 'POST') { res.writeHead(405).end(); return; }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    requests.push({ body, authorization: req.headers.authorization });
    const mcp = new McpServer({ name: 'hf-local-fixture', version: '1.0.0' });
    const handle = name => async args => {
      calls.push({ name, args });
      return typeof result === 'function' ? result(name, args) : result ?? success({ found: ['fixture-only'] });
    };
    if (!missing) mcp.registerTool('hub_repo_search', { inputSchema: {
      query: z.string(), repo_types: z.array(z.enum(['model', 'dataset'])).length(1), limit: z.literal(5),
    } }, handle('hub_repo_search'));
    mcp.registerTool('hub_repo_details', { inputSchema: { repo_ids: z.array(z.string()) } }, handle('hub_repo_details'));
    mcp.registerTool('hf_fs', { inputSchema: { operations: z.array(z.object({
      cmd: z.enum(['search', 'cat']), args: z.array(z.string()),
    }).strict()).length(1) } }, handle('hf_fs'));
    mcp.registerTool('run_paid_job', { inputSchema: {} }, handle('run_paid_job'));
    if (pagination) mcp.server.setRequestHandler(ListToolsRequestSchema, request => pagination(request.params?.cursor));
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    resources.add(mcp);
    res.once('close', () => { resources.delete(mcp); void mcp.close(); });
    await mcp.connect(transport);
    await transport.handleRequest(req, res, body);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    await Promise.all([...resources].map(item => item.close()));
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const fetchImpl = async (url, init) => {
    assert.equal(String(url), HF_ENDPOINT);
    assert.equal(init.redirect, 'error');
    return fetch(`http://127.0.0.1:${server.address().port}/mcp`, init);
  };
  return { requests, calls, fetchImpl };
}

test('real SDK handshake and discovery expose only the fixed read-only allowlist; anonymous use works', async t => {
  const f = await fixture(t);
  const result = await runHuggingFace({ token: '', fetchImpl: f.fetchImpl });
  assert.deepEqual(result.tools, ['hub_repo_search', 'hub_repo_details', 'hf_fs']);
  assert.equal(result.status, 'ok');
  assert.deepEqual(result.missingExpectedTools, []);
  assert.ok(f.requests.some(r => r.body.method === 'initialize'));
  assert.ok(f.requests.some(r => r.body.method === 'notifications/initialized'));
  assert.ok(f.requests.some(r => r.body.method === 'tools/list'));
  assert.ok(f.requests.every(r => r.authorization === undefined));
  assert.equal(f.calls.length, 0);
});

test('model/dataset commands pin the official schema and send a placeholder token only in Authorization', async t => {
  const f = await fixture(t, { result: success({ echoed: TOKEN, another: 'hf_abcdefghijk123456', bearer: 'Bearer remote-secret' }) });
  for (const command of ['models', 'datasets']) {
    const result = await runHuggingFace({ argv: [command, '제주 추천'], token: TOKEN, fetchImpl: f.fetchImpl });
    const output = JSON.stringify(result);
    assert.ok(!output.includes(TOKEN));
    assert.ok(!output.includes('hf_abcdefghijk123456'));
    assert.ok(!output.includes('remote-secret'));
  }
  assert.deepEqual(f.calls, [
    { name: 'hub_repo_search', args: { query: '제주 추천', repo_types: ['model'], limit: 5 } },
    { name: 'hub_repo_search', args: { query: '제주 추천', repo_types: ['dataset'], limit: 5 } },
  ]);
  assert.ok(f.requests.every(r => r.authorization === `Bearer ${TOKEN}`));
  assert.ok(f.requests.every(r => !JSON.stringify(r.body).includes(TOKEN)));
  const unusual = await fixture(t, { result: success({ echoed: ':"placeholder"' }) });
  const safe = await runHuggingFace({ argv: ['models', 'demo'], token: ':"placeholder"', fetchImpl: unusual.fetchImpl });
  assert.equal(safe.data.echoed, '[REDACTED]');
});

test('papers search uses one bounded hf_fs operation and paper fallback never claims complete full text', async t => {
  const content = '> **Full-text availability:** The full paper is not available in Markdown.\n> This fallback document contains metadata and summaries.';
  const f = await fixture(t, { result: success({ results: [{ index: 0, status: 'success', result: {
    uri: 'hf://papers/2504.09277/paper.md', op: 'cat', content, truncated: true, next_offset: 20000,
  } }] }) });
  await runHuggingFace({ argv: ['papers', 'tourism agents'], token: '', fetchImpl: f.fetchImpl });
  const paper = await runHuggingFace({ argv: ['paper', '2504.09277'], token: '', fetchImpl: f.fetchImpl });
  assert.deepEqual(f.calls.map(c => c.args), [
    { operations: [{ cmd: 'search', args: ['hf://papers', 'tourism agents', '--limit', '5'] }] },
    { operations: [{ cmd: 'cat', args: ['hf://papers/2504.09277/paper.md'] }] },
  ]);
  assert.equal(paper.paper.documentKind, 'summary_or_abstract_fallback');
  assert.equal(paper.paper.fullPaperVerified, false);
  assert.equal(paper.providerTruncated, true);
  assert.equal(paper.status, 'partial');
});

test('local validation rejects arbitrary tools, paths, hf_fs flag injection and whitespace-bearing tokens before fetch', async () => {
  const fetchImpl = () => { assert.fail('Invalid input must not make any request'); };
  for (const argv of [['run_paid_job'], ['spaces', 'demo'], ['paper', '../../secret'], ['paper', 'https://arxiv.org/abs/2504.09277'],
    ['papers', '--limit'], ['papers', 'topic', '--limit', '100'], ['models', ''], ['list', 'extra'],
    ['paper', '2504.09277v' + '1'.repeat(40)]]) {
    await assert.rejects(runHuggingFace({ argv, token: '', fetchImpl }), /INVALID_/);
  }
  for (const token of ['secret token', 'secret\r\nAuthorization: forged']) {
    await assert.rejects(runHuggingFace({ token, fetchImpl }), /INVALID_TOKEN_FORMAT/);
  }
});

test('tool errors, operation errors and missing tools fail without returning remote error text', async t => {
  for (const result of [{ isError: true, content: [{ type: 'text', text: `private failure ${TOKEN}` }] },
    success({ results: [{ index: 0, status: 'error', error: { message: TOKEN } }] })]) {
    const f = await fixture(t, { result });
    await assert.rejects(runHuggingFace({ argv: ['paper', '2504.09277'], token: TOKEN, fetchImpl: f.fetchImpl }),
      error => error.code === 'TOOL_REPORTED_ERROR' && !error.message.includes(TOKEN));
  }
  const f = await fixture(t, { missing: true });
  await assert.rejects(runHuggingFace({ argv: ['models', 'demo'], token: '', fetchImpl: f.fetchImpl }), /REQUIRED_TOOL_MISSING/);
  assert.equal(f.calls.length, 0);
});

test('discovery follows bounded pages and rejects cursor loops or excessive pages', async t => {
  const tool = { name: 'hub_repo_search', inputSchema: { type: 'object' } };
  const paged = await fixture(t, { pagination: cursor => cursor ? { tools: [tool] } : { tools: [], nextCursor: 'page-2' } });
  const partial = await runHuggingFace({ token: '', fetchImpl: paged.fetchImpl });
  assert.deepEqual(partial.tools, ['hub_repo_search']);
  assert.equal(partial.status, 'partial');
  assert.deepEqual(partial.missingExpectedTools, ['hub_repo_details', 'hf_fs']);
  const empty = await fixture(t, { pagination: () => ({ tools: [] }) });
  await assert.rejects(runHuggingFace({ token: '', fetchImpl: empty.fetchImpl }), /REQUIRED_TOOL_MISSING/);
  const loop = await fixture(t, { pagination: () => ({ tools: [], nextCursor: 'same' }) });
  await assert.rejects(runHuggingFace({ token: '', fetchImpl: loop.fetchImpl }), /CURSOR_LOOP/);
  let page = 0;
  const endless = await fixture(t, { pagination: () => ({ tools: [], nextCursor: `page-${++page}` }) });
  await assert.rejects(runHuggingFace({ token: '', fetchImpl: endless.fetchImpl }), /TOOL_LIST_PAGE_LIMIT/);
  assert.equal(page, 5);
});

test('response and output bounds are enforced, and truncation is explicit', async t => {
  const f = await fixture(t, { result: success({ text: '가'.repeat(30000) }) });
  const report = await runHuggingFace({ argv: ['models', 'demo'], token: '', fetchImpl: f.fetchImpl });
  assert.equal(report.outputTruncated, true);
  assert.ok(Buffer.byteLength(JSON.stringify(report)) + 1 <= MAX_OUTPUT_BYTES);
  await assert.rejects(runHuggingFace({ token: '', fetchImpl: async () => new Response('x'.repeat(2 * 1024 * 1024 + 1)) }), /RESPONSE_TOO_LARGE|REQUEST_FAILED/);
});

test('redirect and timeout failures remain generic and never disclose a provider exception', async () => {
  await assert.rejects(runHuggingFace({ token: TOKEN,
    fetchImpl: async () => new Response(null, { status: 302, headers: { location: 'https://other.invalid/' } }) }), /REDIRECT_REJECTED|REQUEST_FAILED/);
  await assert.rejects(runHuggingFace({ token: TOKEN, timeoutMs: 20, fetchImpl: (_url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error(`provider echoed ${TOKEN}`)), { once: true });
  }) }), error => error.code === 'REQUEST_FAILED' && !error.message.includes(TOKEN));
});
