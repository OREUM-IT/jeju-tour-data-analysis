import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { collectBaselineCapture, loopbackHttpFetch } from '../src/baseline-runner.mjs';
import { createBaselinePlan, scoreBaselineRun } from '../src/baseline-evaluation.mjs';
import { canonicalSha256 } from '../src/evaluation-rng.mjs';
import { adaptValidatedLangflowResponse, BASELINE_ADAPTER_VERSION } from '../src/baseline-adapters.mjs';
import { VALIDATED_BASELINE_ADAPTER_VERSION } from '../src/validated-capture-binding.mjs';
import { importCapture } from '../scripts/evaluate-baselines.mjs';
import { main, parseArguments } from '../scripts/collect-baseline-responses.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const selector = { outputIndex: 0, componentId: 'ChatOutput-jejuV2' };
const system = { id: 'langflow', modelId: 'fixture-model', promptVersion: 'fixture-v2', toolVersion: 'fixture-mcp-v2', adapterVersion: VALIDATED_BASELINE_ADAPTER_VERSION };
const output = (value) => ({ outputs: [{ outputs: [{ component_id: selector.componentId,
  results: { message: { sender: 'Machine', text: typeof value === 'string' ? value : JSON.stringify(value),
    properties: { source: { id: 'JejuFinalValidation-v2', source: 'Jeju Final Validation' } } } } }] }] });
const accepted = (requestId, extra = {}) => ({ schemaVersion: '1.0', requestId, action: 'ask_clarification', placeIds: [],
  requirementClaims: [], confirmationNotice: false, noticeTopics: [], ...extra });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const goodFetch = async (_url, options) => json(output(accepted(JSON.parse(JSON.parse(options.body).input_value).requestId)));
const defaults = { plan, system, catalog: inputs.catalog, requestMode: 'langflow-v2', outputSelector: selector,
  endpoint: 'http://127.0.0.1:7860/api/v1/run/fixture-v2?stream=false', maxCases: 1, fetchImpl: goodFetch };
const collect = (options = {}) => collectBaselineCapture({ ...defaults, ...options });

test('v2 collector binds exact transformed requests, exposes no gold, and imports by mechanical normalization', async () => {
  const bodies = [];
  const result = await collect({ maxCases: 2, fetchImpl: async (url, options) => {
    bodies.push(JSON.parse(options.body)); return goodFetch(url, options);
  } });
  assert.equal(result.telemetry.requestMode, 'langflow-v2');
  assert.deepEqual(result.telemetry.requestBinding, result.capture.requestBinding);
  assert.equal(result.capture.requestBinding.catalogSha256, plan.binding.catalogSha256);
  assert.equal(result.capture.requestBinding.preparedRequestsSha256.length, 64);
  assert.equal(result.capture.records.filter(record => record.error?.code === 'not_executed').length, 94);
  for (let i = 0; i < 2; i++) {
    const record = result.capture.records[i];
    assert.equal(record.runtimeRequest.bodySha256, canonicalSha256(bodies[i]));
    assert.equal(result.telemetry.events[i].sessionId, bodies[i].session_id);
    assert.equal(result.telemetry.events[i].runtimeRequestSha256, canonicalSha256(record.runtimeRequest));
    assert.equal(result.telemetry.events[i].captureRecordSha256, canonicalSha256(record));
    assert.equal(JSON.parse(bodies[i].input_value).requestId, bodies[i].session_id);
    for (const field of ['gold', 'personaId', 'allowedIds', 'caseId', 'itemId', 'expectedAction']) assert.ok(!JSON.stringify(bodies[i]).includes(`\\"${field}\\"`));
  }
  assert.notEqual(bodies[0].session_id, bodies[1].session_id);
  const run = importCapture(plan, result.capture, { catalog: inputs.catalog });
  assert.equal(run.records[0].normalization.mode, 'structured');
  assert.equal(run.records[0].normalization.runtimeRequestId, bodies[0].session_id);
  assert.equal(run.records[0].response.itemId, plan.cases[0].itemId);
  assert.equal(run.records[0].response.requestId, undefined);
  assert.equal(run.records[0].response.costUsd, null);
  const scored = scoreBaselineRun({ ...inputs, plan, run });
  assert.equal(scored.summary.groups[0].successfulResponses, 2);
  assert.equal(scored.summary.groups[0].errors.not_executed, 94);
});

test('v2 preflights the last planned input before the first paid request despite a one-case limit', async () => {
  const changed = structuredClone(plan);
  changed.cases.at(-1).request.query += ' 숨은 조건을 추가합니다.';
  changed.cases.at(-1).requestSha256 = canonicalSha256(changed.cases.at(-1).request);
  delete changed.planSha256; changed.planSha256 = canonicalSha256(changed);
  let calls = 0;
  await assert.rejects(collect({ plan: changed, fetchImpl: async () => { calls++; return json({}); } }), /unsupported/);
  assert.equal(calls, 0);
});

test('v2 rejects blocked final gates, prose, mismatched identity and extra fields without repairing output', async () => {
  const variants = [
    [() => ({ status: 'blocked', code: 'RECOMMENDATION_BLOCKED', message: '합성 차단' }), 'final_validation_blocked'],
    [() => ({ status: 'blocked', code: 'VALIDATION_FAILED', message: '합성 오류' }), 'final_validation_failed'],
    [() => '일반 문장 응답', 'invalid_validated_output'],
    [() => accepted('different-request'), 'invalid_validated_output'],
    [(id) => accepted(id, { gold: [] }), 'invalid_validated_output'],
    [(id) => accepted(id, { requirementClaims: [{ requirement: 'wheelchair', verdict: 'unknown' }] }), 'invalid_validated_output'],
    [(id) => accepted(id, { placeIds: ['DUPLICATE', 'DUPLICATE'] }), 'invalid_validated_output'],
    [(id) => accepted(id, { noticeTopics: ['hours_unverified', 'hours_unverified'] }), 'invalid_validated_output'],
  ];
  for (const [make, outcome] of variants) {
    let calls = 0;
    const result = await collect({ maxCases: 2, fetchImpl: async (_url, options) => {
      calls++; return json(output(make(JSON.parse(JSON.parse(options.body).input_value).requestId)));
    } });
    assert.equal(calls, 1);
    assert.equal(result.capture.records[0].error.code, 'invalid_output');
    assert.equal(result.capture.records[1].error.code, 'not_executed');
    assert.equal(result.telemetry.events[0].outcome, outcome);
    assert.ok(result.telemetry.events[0].raw);
    assert.equal(result.telemetry.events[0].sessionId, result.capture.records[0].runtimeRequest.requestId);
    const run = importCapture(plan, result.capture, { catalog: inputs.catalog });
    assert.equal(run.records[0].error.code, 'invalid_output');
  }
});

test('v2 resume preserves failed attempts and checks mode, catalog, payload and failed-session bindings', async () => {
  const first = await collect({ fetchImpl: async () => json({ detail: 'synthetic provider failure' }, 500) });
  const next = await collect({ resumeCapture: first.capture, resumeTelemetry: first.telemetry });
  assert.deepEqual(next.capture.records[0], first.capture.records[0]);
  assert.equal(next.capture.records[1].raw.outputs.length, 1);
  assert.equal(next.telemetry.attemptedCases, 2);
  assert.notEqual(next.telemetry.events[0].sessionId, next.telemetry.events[1].sessionId);
  for (const mutate of [
    (c) => { delete c.requestBinding; },
    (c) => { c.requestBinding.requestMode = 'legacy'; },
    (c) => { c.requestBinding.catalogSha256 = 'a'.repeat(64); },
    (c) => { c.records[0].runtimeRequest.bodySha256 = 'a'.repeat(64); },
    (_c, t) => { t.events[0].sessionId = 'baseline-tampered123'; },
    (_c, t) => { t.requestBinding.preparedRequestsSha256 = 'a'.repeat(64); },
  ]) {
    const c = structuredClone(first.capture), t = structuredClone(first.telemetry); mutate(c, t);
    let calls = 0;
    await assert.rejects(collect({ resumeCapture: c, resumeTelemetry: t, fetchImpl: async () => { calls++; return json({}); } }));
    assert.equal(calls, 0);
  }
  await assert.rejects(collect({ catalog: { ...inputs.catalog, diagnostics: { changed: true } }, resumeCapture: first.capture, resumeTelemetry: first.telemetry }), /catalog/);
  await assert.rejects(collect({ requestMode: 'legacy', resumeCapture: first.capture, resumeTelemetry: first.telemetry }), /mode/);
  // An all-failed capture has no successful outputSelector field to compare. The pinned v2 selector still applies.
  for (const outputSelector of [{ ...selector, outputIndex: 1 }, { ...selector, componentId: 'Agent-jejuV2' }]) {
    let calls = 0;
    await assert.rejects(collect({ outputSelector, resumeCapture: first.capture, resumeTelemetry: first.telemetry,
      fetchImpl: async () => { calls++; return json({}); } }), /requires outputIndex 0 and ChatOutput-jejuV2/);
    assert.equal(calls, 0);
  }
});

test('v2 importer requires bound inputs and refuses identity changes or human overrides', async () => {
  const result = await collect();
  assert.throws(() => importCapture(plan, result.capture), /catalog/);
  for (const mutate of [
    (c) => { c.records[0].review = { method: 'human' }; },
    (c) => { c.records[0].runtimeRequest.bodySha256 = 'b'.repeat(64); },
    (c) => { c.records[0].raw.outputs[0].outputs[0].results.message.text = JSON.stringify(accepted('wrong-request')); },
    (c) => { c.records[0].raw.outputs[0].outputs[0].results.message.properties.source.id = 'Agent-jejuV2'; },
    (c) => { delete c.records[0].raw.outputs[0].outputs[0].results.message.properties; },
  ]) {
    const changed = structuredClone(result.capture); mutate(changed);
    assert.throws(() => importCapture(plan, changed, { catalog: inputs.catalog }));
  }
  const requestId = 'baseline-synthetic123';
  const value = accepted(requestId, { action: 'recommend', placeIds: ['OUTSIDE_CATALOG'],
    requirementClaims: [{ placeId: 'OUTSIDE_CATALOG', requirement: 'wheelchair', verdict: 'pass' }] });
  const normalized = adaptValidatedLangflowResponse({ itemId: 'fixture-item', requestId, raw: output(value), outputSelector: selector });
  assert.deepEqual(normalized.response.placeIds, value.placeIds);
  assert.deepEqual(normalized.response.requirementClaims, value.requirementClaims); // Scorer must see errors, never repairs.
});

test('v2 timeout retains the attempted session for independent runtime tracing', async () => {
  const result = await collect({ timeoutMs: 5, fetchImpl: () => new Promise(() => {}) });
  assert.equal(result.capture.records[0].error.code, 'timeout');
  assert.equal(result.telemetry.events[0].outcome, 'timeout');
  assert.equal(result.telemetry.events[0].sessionId, result.capture.records[0].runtimeRequest.requestId);
  const resumed = await collect({ resumeCapture: result.capture, resumeTelemetry: result.telemetry });
  assert.equal(resumed.capture.records[0].error.code, 'timeout');
  assert.ok(resumed.capture.records[1].raw);
});

test('CLI opts into v2, stores its binding, and rejects v2 for Gemini', async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jeju-v2-collection-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const local = path.join(root, '.local'); await mkdir(local);
  const paths = Object.fromEntries(['catalog', 'set', 'plan', 'capture', 'telemetry'].map(key => [key, path.join(local, `${key}.json`)]));
  for (const [key, value] of [['catalog', inputs.catalog], ['set', inputs.set], ['plan', plan]]) await writeFile(paths[key], JSON.stringify(value));
  const args = ['--execute', '--catalog', paths.catalog, '--set', paths.set, '--plan', paths.plan,
    '--system', 'langflow', '--model', system.modelId, '--prompt-version', system.promptVersion, '--tool-version', system.toolVersion,
    '--endpoint', defaults.endpoint, '--output', paths.capture, '--telemetry', paths.telemetry,
    '--output-index', '0', '--component-id', selector.componentId, '--request-mode', 'langflow-v2'];
  assert.equal(parseArguments(args).requestMode, 'langflow-v2');
  const changed = [...args]; changed[changed.indexOf('langflow')] = 'gemini';
  assert.throws(() => parseArguments(changed), /Langflow-only/);
  const logs = [];
  const result = await main(args, { env: { LANGFLOW_API_KEY: 'synthetic' }, fetchImpl: goodFetch, log: message => logs.push(message) });
  assert.equal(result.successfulResponses, 1);
  const capture = JSON.parse(await readFile(paths.capture, 'utf8'));
  assert.equal(capture.system.adapterVersion, VALIDATED_BASELINE_ADAPTER_VERSION);
  assert.equal(capture.requestBinding.requestMode, 'langflow-v2');
  assert.ok(!logs.join('').includes(capture.records[0].runtimeRequest.requestId));
});

test('local-model timeout raises only the ceiling, for v2 Langflow on 127.0.0.1, before any request', async () => {
  let calls = 0;
  const counted = async (url, options) => { calls++; return goodFetch(url, options); };
  await assert.rejects(collect({ timeoutMs: 120_001, fetchImpl: counted }), /timeoutMs must be 1\.\.120000$/);
  await assert.rejects(collect({ localModelTimeout: true, timeoutMs: 1_200_001, fetchImpl: counted }), /timeoutMs must be 1\.\.1200000$/);
  await assert.rejects(collect({ localModelTimeout: 'true', timeoutMs: 600_000, fetchImpl: counted }), /localModelTimeout must be a boolean/);
  const legacyLangflow = { system: { ...system, adapterVersion: BASELINE_ADAPTER_VERSION }, requestMode: 'legacy' };
  const gemini = { system: { ...system, id: 'gemini', adapterVersion: BASELINE_ADAPTER_VERSION }, requestMode: 'legacy',
    outputSelector: undefined, endpoint: 'http://127.0.0.1:7860/api/assistant' };
  // Endpoints the default rules already accept; other plain-HTTP hosts are refused by the existing endpoint rule.
  for (const overrides of [legacyLangflow, gemini, ...['http://localhost:7860', 'http://[::1]:7860', 'http://127.0.0.1',
    'https://127.0.0.1:7860', 'https://langflow.example.com'].map((origin) => ({ endpoint: `${origin}/api/v1/run/fixture-v2?stream=false` }))]) {
    await assert.rejects(collect({ localModelTimeout: true, timeoutMs: 600_000, fetchImpl: counted, ...overrides }),
      /localModelTimeout requires Langflow v2 mode and an http:\/\/127\.0\.0\.1:<port> endpoint/);
  }
  await assert.rejects(loopbackHttpFetch('http://localhost:7860/api/v1/run/fixture-v2?stream=false', { method: 'POST' }), TypeError);
  assert.equal(calls, 0);
  const result = await collect({ localModelTimeout: true, timeoutMs: 1_200_000, fetchImpl: counted });
  assert.equal(calls, 1);
  assert.ok(result.capture.records[0].raw);
  assert.equal(result.telemetry.timeoutMs, 1_200_000);
  assert.equal(result.telemetry.localModelTimeout, true);
  assert.equal(Object.hasOwn(result.capture, 'localModelTimeout'), false);
  const plain = await collect({ fetchImpl: counted });
  assert.equal(plain.telemetry.timeoutMs, 60_000);
  assert.equal(Object.hasOwn(plain.telemetry, 'localModelTimeout'), false);
});

test('local-model timeout is bound on resume, and default captures resume as before only without it', async () => {
  const flagged = await collect({ localModelTimeout: true, timeoutMs: 600_000 });
  const resumed = await collect({ localModelTimeout: true, timeoutMs: 600_000, resumeCapture: flagged.capture, resumeTelemetry: flagged.telemetry });
  assert.deepEqual(resumed.capture.records[0], flagged.capture.records[0]);
  assert.equal(resumed.telemetry.attemptedCases, 2);
  assert.equal(resumed.telemetry.localModelTimeout, true);
  assert.equal(resumed.telemetry.timeoutMs, 600_000);
  // Existing behaviour: a default run may still resume with another timeout inside the default ceiling.
  const plain = await collect({ timeoutMs: 60_000 });
  const plainResumed = await collect({ timeoutMs: 120_000, resumeCapture: plain.capture, resumeTelemetry: plain.telemetry });
  assert.equal(plainResumed.telemetry.attemptedCases, 2);
  assert.equal(plainResumed.telemetry.timeoutMs, 120_000);
  assert.equal(Object.hasOwn(plainResumed.telemetry, 'localModelTimeout'), false);
  const claimed = (localModelTimeout) => ({ capture: plain.capture, telemetry: { ...plain.telemetry, localModelTimeout } });
  for (const [options, prior] of [
    [{}, flagged], [{ timeoutMs: 120_000 }, flagged],
    [{ localModelTimeout: true, timeoutMs: 600_000 }, plain], [{ localModelTimeout: true, timeoutMs: 60_000 }, plain],
    [{ localModelTimeout: true, timeoutMs: 900_000 }, flagged], [{ localModelTimeout: true, timeoutMs: 120_000 }, flagged],
    [{}, claimed(false)], [{ localModelTimeout: true, timeoutMs: 60_000 }, claimed(false)], [{}, claimed(true)],
  ]) {
    let calls = 0;
    await assert.rejects(collect({ ...options, resumeCapture: prior.capture, resumeTelemetry: prior.telemetry,
      fetchImpl: async () => { calls++; return json({}); } }), /resume local-model timeout setting changed/);
    assert.equal(calls, 0);
  }
});

test('local-model timeout defaults to the loopback node:http transport and keeps fetch outcome classification', async (t) => {
  const seen = [];
  const arrivals = {};
  let stalled;
  const server = http.createServer(async (req, res) => {
    const flow = new URL(req.url, 'http://127.0.0.1').pathname.split('/').at(-1);
    arrivals[flow] = (arrivals[flow] ?? 0) + 1;
    if (flow === 'stall') stalled = new Promise((resolve) => res.once('close', () => resolve(!res.writableFinished)));
    let body = '';
    try { for await (const chunk of req) body += chunk; } catch { return; }
    seen.push({ flow, method: req.method, headers: req.headers, body });
    if (flow === 'stall') return;
    if (flow === 'partial') { res.writeHead(200, { 'content-type': 'application/json' }); res.write('{'); return; }
    if (flow === 'status204') { res.writeHead(204); res.end(); return; }
    if (flow === 'redirect') { res.writeHead(307, { location: '/api/v1/run/ok?stream=false' }); res.end('moved'); return; }
    if (flow === 'failure') { res.writeHead(503, { 'content-type': 'text/plain' }); res.end('synthetic outage'); return; }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(output(accepted(JSON.parse(JSON.parse(body).input_value).requestId))));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const endpoint = (flow) => `http://127.0.0.1:${server.address().port}/api/v1/run/${flow}?stream=false`;
  const builtIn = globalThis.fetch;
  let builtInCalls = 0;
  globalThis.fetch = (...input) => { builtInCalls++; return builtIn(...input); };
  t.after(() => { globalThis.fetch = builtIn; });
  const local = (flow, extra = {}) => collect({ endpoint: endpoint(flow), headers: { 'x-api-key': 'synthetic-local-key' },
    localModelTimeout: true, timeoutMs: 600_000, fetchImpl: undefined, ...extra });
  const result = await local('ok');
  assert.equal(builtInCalls, 0);
  assert.ok(result.capture.records[0].raw);
  assert.equal(result.telemetry.events[0].outcome, 'completed');
  assert.equal(result.telemetry.events[0].httpStatus, 200);
  assert.equal(seen[0].method, 'POST');
  assert.equal(seen[0].headers['x-api-key'], 'synthetic-local-key');
  assert.equal(seen[0].headers['content-type'], 'application/json');
  assert.equal(seen[0].headers['accept-encoding'], 'identity');
  assert.equal(Number(seen[0].headers['content-length']), Buffer.byteLength(seen[0].body));
  assert.equal(JSON.parse(JSON.parse(seen[0].body).input_value).requestId, result.capture.records[0].runtimeRequest.requestId);
  // The same server outcomes classify identically through the transport and the built-in fetch used without the flag.
  for (const [flow, outcome, httpStatus] of [['ok', 'completed', 200], ['status204', 'missing_response_body', 204],
    ['redirect', 'redirect_blocked', 307], ['failure', 'http_error', 503]]) {
    for (const run of [() => local(flow), () => collect({ endpoint: endpoint(flow), fetchImpl: undefined })]) {
      const { telemetry } = await run();
      assert.deepEqual([telemetry.events[0].outcome, telemetry.events[0].httpStatus], [outcome, httpStatus]);
    }
  }
  assert.equal(builtInCalls, 4);
  // Exact per-flow counts also show that neither transport followed the 307 to the "ok" flow.
  assert.deepEqual(arrivals, { ok: 3, status204: 2, redirect: 2, failure: 2 });
  // Its own timeout still applies and closes the stalled local request without a retry.
  const timedOut = await local('stall', { timeoutMs: 50, maxCases: 2 });
  assert.equal(timedOut.telemetry.events[0].outcome, 'timeout');
  assert.equal(timedOut.capture.records[0].error.code, 'timeout');
  assert.equal(timedOut.capture.records[1].error.code, 'not_executed');
  assert.equal(await stalled, true);
  // A body that stalls after its first byte also ends as the runner's timeout through either transport.
  for (const run of [() => local('partial', { timeoutMs: 50 }), () => collect({ endpoint: endpoint('partial'), timeoutMs: 50, fetchImpl: undefined })]) {
    const { capture, telemetry } = await run();
    assert.equal(capture.records[0].error.code, 'timeout');
    assert.deepEqual([telemetry.events[0].outcome, telemetry.events[0].httpStatus, telemetry.events[0].responseBytes], ['timeout', 200, 1]);
  }
  assert.deepEqual(arrivals, { ok: 3, status204: 2, redirect: 2, failure: 2, stall: 1, partial: 2 });
  assert.equal(builtInCalls, 5);
});

test('CLI records --local-model-timeout and refuses a resume that changes the flag or its timeout before any request', async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jeju-v2-local-timeout-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const local = path.join(root, '.local'); await mkdir(local);
  const file = (name) => path.join(local, `${name}.json`);
  for (const [key, value] of [['catalog', inputs.catalog], ['set', inputs.set], ['plan', plan]]) await writeFile(file(key), JSON.stringify(value));
  const args = (output, ...extra) => ['--execute', '--catalog', file('catalog'), '--set', file('set'), '--plan', file('plan'),
    '--system', 'langflow', '--request-mode', 'langflow-v2', '--model', system.modelId, '--prompt-version', system.promptVersion,
    '--tool-version', system.toolVersion, '--endpoint', defaults.endpoint, '--output-index', '0', '--component-id', selector.componentId,
    '--output', file(`${output}-capture`), '--telemetry', file(`${output}-telemetry`), ...extra];
  let calls = 0;
  const runtime = { env: { LANGFLOW_API_KEY: 'synthetic' }, log: () => {}, fetchImpl: async (url, options) => { calls++; return goodFetch(url, options); } };
  const first = await main(args('first', '--local-model-timeout', '--timeout-ms', '600000'), runtime);
  assert.equal(first.successfulResponses, 1);
  const telemetry = JSON.parse(await readFile(file('first-telemetry'), 'utf8'));
  assert.equal(telemetry.localModelTimeout, true);
  assert.equal(telemetry.timeoutMs, 600000);
  const resume = ['--resume-capture', file('first-capture'), '--resume-telemetry', file('first-telemetry')];
  for (const [name, extra] of [['dropped', []], ['default-ceiling', ['--timeout-ms', '120000']], ['changed', ['--local-model-timeout', '--timeout-ms', '900000']]]) {
    await assert.rejects(main(args(name, ...resume, ...extra), runtime), /resume local-model timeout setting changed/);
    for (const suffix of ['capture', 'telemetry']) assert.equal(await stat(file(`${name}-${suffix}`)).then(() => true, () => false), false);
  }
  assert.equal(calls, 1);
  const next = await main(args('next', ...resume, '--local-model-timeout', '--timeout-ms', '600000'), runtime);
  assert.equal(next.attemptedCases, 2);
  assert.equal(next.newAttemptedCases, 1);
  assert.equal(calls, 2);
});
