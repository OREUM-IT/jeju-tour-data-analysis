import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { collectBaselineCapture } from '../src/baseline-runner.mjs';
import { createBaselinePlan } from '../src/baseline-evaluation.mjs';
import { canonicalSha256 } from '../src/evaluation-rng.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const plan = createBaselinePlan(baselineInputs());
const system = { id: 'gemini', modelId: 'test-gemini', promptVersion: 'context-v1', toolVersion: 'pinned-test', adapterVersion: '1.0.0' };
const selector = { outputIndex: 0, componentId: 'ChatOutput-test' };
const rawGemini = () => ({ success: true, data: { engine: 'gemini', model: 'test-gemini', reply: '모의 관광 응답입니다.', places: [], action: { type: 'none', placeIds: [] } } });
const rawLangflow = () => ({ outputs: [{ outputs: [{ component_id: selector.componentId, results: { message: { sender: 'Machine', text: '모의 관광 응답입니다.' } } }] }] });
const jsonResponse = (value, init = {}) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' }, ...init });
const defaults = { plan, system, endpoint: 'http://127.0.0.1:9999/api/assistant', maxCases: 1, fetchImpl: async () => jsonResponse(rawGemini()) };
const collect = (overrides = {}) => collectBaselineCapture({ ...defaults, ...overrides });

test('collection makes sequential POSTs with only shared request information and explicit unexecuted coverage', async () => {
  const requests = [];
  let active = 0;
  let maxActive = 0;
  const result = await collect({ maxCases: 3, fetchImpl: async (url, options) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    requests.push({ url, ...options, body: JSON.parse(options.body) });
    await new Promise((resolve) => setTimeout(resolve, 2));
    active -= 1;
    return jsonResponse(rawGemini());
  } });
  assert.equal(maxActive, 1);
  assert.equal(requests.length, 3);
  assert.equal(result.capture.records.length, 96);
  assert.equal(result.capture.records.filter((record) => record.error?.code === 'not_executed').length, 93);
  assert.equal(result.telemetry.attemptedCases, 3);
  assert.equal(result.telemetry.stoppedReason, 'case_limit');
  for (let i = 0; i < requests.length; i += 1) {
    const request = requests[i];
    assert.equal(request.method, 'POST');
    assert.equal(request.redirect, 'manual');
    assert.equal(request.credentials, 'omit');
    assert.equal(request.body.messages.length, 1);
    assert.ok(request.body.messages[0].text.endsWith(plan.cases[i].request.query));
    assert.deepEqual(request.body.course.placeIds, []);
    for (const field of ['gold', 'personaId', 'allowedIds', 'requirementClaims', 'caseId', 'itemId']) assert.ok(!JSON.stringify(request.body).includes(`"${field}"`));
    assert.equal(result.capture.records[i].requestSha256, plan.cases[i].requestSha256);
    assert.deepEqual(result.capture.records[i].raw, rawGemini().data);
    assert.equal(result.capture.records[i].measurements.costUsd, null);
    assert.ok(result.capture.records[i].measurements.latencyMs >= 0);
  }
});

test('Langflow uses isolated sessions, declared output selection, and the same query context', async () => {
  const bodies = [];
  const result = await collect({ system: { ...system, id: 'langflow' }, endpoint: 'http://127.0.0.1:7860/api/v1/run/test-flow?stream=false',
    outputSelector: selector, maxCases: 2, fetchImpl: async (_url, options) => { bodies.push(JSON.parse(options.body)); return jsonResponse(rawLangflow()); } });
  assert.notEqual(bodies[0].session_id, bodies[1].session_id);
  assert.ok(bodies.every((body) => /^baseline-[a-z0-9-]+$/u.test(body.session_id)));
  assert.ok(bodies[0].input_value.endsWith(plan.cases[0].request.query));
  assert.ok(!Object.hasOwn(bodies[0], 'tweaks'));
  assert.equal(bodies[0].input_type, 'chat');
  assert.equal(bodies[0].output_type, 'chat');
  assert.deepEqual(result.capture.records[0].outputSelector, selector);
  assert.deepEqual(result.capture.records[0].raw, rawLangflow());
});

test('Langflow reported model must match exactly and mismatch is preserved without retry', async () => {
  const flowOptions = { system: { ...system, id: 'langflow' }, endpoint: 'http://127.0.0.1:7860/api/v1/run/test-flow?stream=false', outputSelector: selector };
  for (const reportedModel of ['different-model', null, '', `${system.modelId} `]) {
    let calls = 0;
    const raw = rawLangflow();
    raw.outputs[0].outputs[0].results.message.properties = { source: { source: reportedModel } };
    const result = await collect({ ...flowOptions, maxCases: 2, fetchImpl: async () => { calls += 1; return jsonResponse(raw); } });
    assert.equal(calls, 1);
    assert.equal(result.capture.records[0].error.code, 'invalid_output');
    assert.equal(result.telemetry.events[0].outcome, 'model_or_engine_mismatch');
    assert.deepEqual(result.telemetry.events[0].raw, raw);
    assert.equal(result.capture.records[1].error.code, 'not_executed');
  }
  const matching = rawLangflow();
  matching.outputs[0].outputs[0].results.message.properties = { source: { source: system.modelId } };
  const accepted = await collect({ ...flowOptions, fetchImpl: async () => jsonResponse(matching) });
  assert.deepEqual(accepted.capture.records[0].raw, matching);
});

test('credential headers reach only transport and exception text cannot enter telemetry or progress', async () => {
  const secret = 'SYNTHETIC_HEADER_SECRET';
  const progress = [];
  const result = await collect({ headers: { Authorization: `Bearer ${secret}` }, onProgress: (event) => progress.push(event), fetchImpl: async (_url, options) => {
    assert.equal(options.headers.authorization, `Bearer ${secret}`);
    throw new Error(`mock server failure containing ${secret}`);
  } });
  assert.equal(result.capture.records[0].error.code, 'transport_error');
  assert.equal(result.telemetry.events[0].outcome, 'transport_failure');
  assert.ok(!JSON.stringify(result).includes(secret));
  assert.ok(!JSON.stringify(progress).includes(secret));
  assert.deepEqual(Object.keys(progress[0]).sort(), ['attempted', 'outcome', 'total']);
});

test('fallback/model mismatches are preserved as rejected local evidence and stop further spending', async () => {
  let calls = 0;
  const raw = rawGemini(); raw.data.engine = 'keyword'; delete raw.data.model;
  const result = await collect({ maxCases: 3, fetchImpl: async () => { calls += 1; return jsonResponse(raw); } });
  assert.equal(calls, 1);
  assert.equal(result.capture.records[0].error.code, 'invalid_output');
  assert.equal(result.telemetry.events[0].outcome, 'model_or_engine_mismatch');
  assert.deepEqual(result.telemetry.events[0].raw, raw);
  assert.equal(result.capture.records[1].error.code, 'not_executed');
  assert.ok(!Object.hasOwn(result.capture.records[0], 'raw'));
});

test('HTTP failure bodies remain local telemetry, with no retries or automatic provider fallback', async () => {
  let calls = 0;
  const raw = { success: false, message: 'synthetic server error' };
  const result = await collect({ maxCases: 2, fetchImpl: async () => { calls += 1; return jsonResponse(raw, { status: 429 }); } });
  assert.equal(calls, 1);
  assert.equal(result.capture.records[0].error.code, 'transport_error');
  assert.equal(result.telemetry.events[0].httpStatus, 429);
  assert.equal(result.telemetry.events[0].outcome, 'http_error');
  assert.deepEqual(result.telemetry.events[0].raw, raw);
  assert.ok(!JSON.stringify(result.capture).includes(raw.message));
});

test('HTTP 503 HTML is a transport failure even though its body is not JSON', async () => {
  const html = '<html><body>synthetic service unavailable</body></html>';
  const result = await collect({ fetchImpl: async () => new Response(html, { status: 503, headers: { 'content-type': 'text/html' } }) });
  assert.equal(result.capture.records[0].error.code, 'transport_error');
  assert.equal(result.telemetry.events[0].outcome, 'http_error');
  assert.equal(result.telemetry.events[0].httpStatus, 503);
  assert.equal(result.telemetry.events[0].raw, html);
  assert.equal(result.telemetry.events[0].responseBytes, Buffer.byteLength(html));
  assert.ok(!JSON.stringify(result.capture).includes(html));
});

test('stopOnError false continues only planned cases and records each failure explicitly', async () => {
  let calls = 0;
  const result = await collect({ maxCases: 3, stopOnError: false, fetchImpl: async () => {
    calls += 1;
    if (calls === 1) throw new Error('mock transport failure');
    return jsonResponse(rawGemini());
  } });
  assert.equal(calls, 3);
  assert.equal(result.capture.records[0].error.code, 'transport_error');
  assert.ok(result.capture.records[1].raw);
  assert.ok(result.capture.records[2].raw);
});

test('per-request timeout bounds an uncooperative fetch without retrying it', async () => {
  let calls = 0;
  let signal;
  const started = performance.now();
  const result = await collect({ maxCases: 2, timeoutMs: 10, fetchImpl: (_url, options) => { calls += 1; signal = options.signal; return new Promise(() => {}); } });
  assert.ok(performance.now() - started < 1000);
  assert.equal(calls, 1);
  assert.equal(signal.aborted, true);
  assert.equal(result.capture.records[0].error.code, 'timeout');
  assert.equal(result.telemetry.events[0].outcome, 'timeout');
});

test('timeout also covers a stalled response body and cancels its stream', async () => {
  let cancelled = false;
  const result = await collect({ timeoutMs: 10, fetchImpl: async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{')); },
    cancel() { cancelled = true; },
  })) });
  assert.equal(result.capture.records[0].error.code, 'timeout');
  assert.equal(result.telemetry.events[0].responseBytes, 1);
  assert.equal(cancelled, true);
});

test('abort before execution makes zero calls; abort in flight retains evidence and remaining coverage', async () => {
  let calls = 0;
  const already = new AbortController(); already.abort();
  let result = await collect({ signal: already.signal, fetchImpl: async () => { calls += 1; return jsonResponse(rawGemini()); } });
  assert.equal(calls, 0);
  assert.equal(result.telemetry.stoppedReason, 'aborted');
  assert.ok(result.capture.records.every((record) => record.error.code === 'not_executed'));
  const controller = new AbortController();
  result = await collect({ signal: controller.signal, maxCases: 2, fetchImpl: async () => {
    calls += 1; controller.abort(); return new Promise(() => {});
  } });
  assert.equal(calls, 1);
  assert.equal(result.capture.records[0].error.code, 'transport_error');
  assert.equal(result.telemetry.events[0].outcome, 'aborted');
  assert.equal(result.capture.records[1].error.code, 'not_executed');
});

test('body limits apply to declared lengths, actual streams, and the total run budget', async () => {
  let result = await collect({ maxResponseBytes: 32, fetchImpl: async () => new Response('tiny', { headers: { 'content-length': '999999' } }) });
  assert.equal(result.telemetry.events[0].outcome, 'response_too_large');
  result = await collect({ maxResponseBytes: 32, fetchImpl: async () => new Response('x'.repeat(64)) });
  assert.equal(result.telemetry.events[0].outcome, 'response_too_large');
  assert.ok(!Object.hasOwn(result.telemetry.events[0], 'raw'));
  const bytes = Buffer.byteLength(JSON.stringify(rawGemini()));
  result = await collect({ maxCases: 3, maxResponseBytes: bytes, maxTotalResponseBytes: bytes });
  assert.equal(result.telemetry.attemptedCases, 1);
  assert.equal(result.telemetry.totalResponseBytes, bytes);
  assert.equal(result.telemetry.stoppedReason, 'total_response_budget');
  assert.equal(result.capture.records[1].error.code, 'not_executed');
});

test('malformed JSON/envelopes and wrong Langflow components are failures, never successful empty answers', async () => {
  let result = await collect({ fetchImpl: async () => new Response('not JSON') });
  assert.equal(result.capture.records[0].error.code, 'invalid_output');
  assert.equal(result.telemetry.events[0].raw, 'not JSON');
  result = await collect({ fetchImpl: async () => jsonResponse({ success: false }) });
  assert.equal(result.telemetry.events[0].outcome, 'invalid_response_envelope');
  result = await collect({ system: { ...system, id: 'langflow' }, endpoint: 'http://localhost:7860/api/v1/run/test-flow?stream=false',
    outputSelector: { ...selector, componentId: 'missing' }, fetchImpl: async () => jsonResponse(rawLangflow()) });
  assert.equal(result.telemetry.events[0].outcome, 'missing_selected_machine_output');
});

test('configuration, plan hashes and destination checks fail before any transport call', async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return jsonResponse(rawGemini()); };
  for (const overrides of [
    { endpoint: 'http://example.com/api/assistant' }, { endpoint: 'https://example.com/other' },
    { endpoint: 'https://user:password@example.com/api/assistant' }, { endpoint: 'https://example.com/api/assistant?token=secret' },
    { endpoint: 'https://example.com/api/assistant#part' }, { maxCases: 97 }, { timeoutMs: 0 },
    { maxResponseBytes: 0 }, { headers: { Host: 'other.example' } }, { headers: { Authorization: 'a', authorization: 'b' } },
    { outputSelector: selector }, { plan: { ...plan, planSha256: 'f'.repeat(64) } },
  ]) await assert.rejects(() => collect({ ...overrides, fetchImpl }), /Invalid baseline collection/u);
  const tampered = structuredClone(plan);
  tampered.cases[0].request.gold = { answer: 'should never be sent' };
  tampered.cases[0].requestSha256 = canonicalSha256(tampered.cases[0].request);
  const { planSha256, ...rest } = tampered; tampered.planSha256 = canonicalSha256(rest);
  await assert.rejects(() => collect({ plan: tampered, fetchImpl }), /query\/context-only/u);
  assert.equal(calls, 0);
});

test('native loopback transport refuses redirects without contacting their destination', async () => {
  let redirectedRequests = 0;
  const server = createServer((req, res) => {
    if (req.url === '/api/assistant') { res.writeHead(307, { location: '/forbidden' }); res.end(); }
    else { redirectedRequests += 1; res.end('should not run'); }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const result = await collect({ endpoint: `http://127.0.0.1:${server.address().port}/api/assistant`, fetchImpl: globalThis.fetch });
    assert.equal(redirectedRequests, 0);
    assert.equal(result.capture.records[0].error.code, 'transport_error');
    assert.equal(result.telemetry.events[0].outcome, 'redirect_blocked');
    assert.equal(result.telemetry.events[0].httpStatus, 307);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

test('async checkpoints are awaited after each attempt and preserve isolated full-coverage snapshots', async () => {
  let calls = 0;
  let persisted = 0;
  const snapshots = [];
  const result = await collect({ maxCases: 3,
    fetchImpl: async () => {
      assert.equal(persisted, calls, 'next paid request must wait for the previous checkpoint');
      calls += 1;
      return jsonResponse(rawGemini());
    },
    onCheckpoint: async (snapshot) => {
      await new Promise((resolve) => setTimeout(resolve, 2));
      assert.equal(snapshot.capture.records.length, 96);
      assert.equal(snapshot.telemetry.attemptedCases, calls);
      assert.equal(snapshot.telemetry.notExecutedCases, 96 - calls);
      assert.equal(snapshot.capture.records.filter((record) => record.error?.code === 'not_executed').length, 96 - calls);
      assert.equal(snapshot.capture.planSha256, plan.planSha256);
      assert.equal(snapshot.capture.completedAt, snapshot.telemetry.completedAt);
      assert.ok(Date.parse(snapshot.capture.completedAt) >= Date.parse(snapshot.capture.startedAt));
      snapshots.push(structuredClone(snapshot));
      snapshot.capture.records[0].raw.reply = 'callback mutation must not alter the run';
      snapshot.telemetry.events[0].outcome = 'callback mutation';
      persisted += 1;
    },
  });
  assert.equal(calls, 3);
  assert.equal(persisted, 3);
  assert.equal(snapshots[2].telemetry.stoppedReason, 'case_limit');
  assert.equal(result.capture.records[0].raw.reply, rawGemini().data.reply);
  assert.equal(result.telemetry.events[0].outcome, 'completed');
});

test('failed checkpoints stop further paid requests without retrying or losing the completed response', async () => {
  let calls = 0;
  let checkpoints = 0;
  const result = await collect({ maxCases: 3, stopOnError: false,
    fetchImpl: async () => { calls += 1; return jsonResponse(rawGemini()); },
    onCheckpoint: async () => { checkpoints += 1; throw new Error('SYNTHETIC_PRIVATE_STORAGE_DETAIL'); },
  });
  assert.equal(calls, 1);
  assert.equal(checkpoints, 1);
  assert.equal(result.telemetry.stoppedReason, 'checkpoint_error');
  assert.deepEqual(result.capture.records[0].raw, rawGemini().data);
  assert.equal(result.capture.records[1].error.code, 'not_executed');
  assert.equal(result.telemetry.notExecutedCases, 95);
  assert.ok(!JSON.stringify(result).includes('SYNTHETIC_PRIVATE_STORAGE_DETAIL'));
});

test('resume runs only the 48 unexecuted cases, preserving 47 completed responses and one failed attempt', async () => {
  let originalCalls = 0;
  const original = await collect({ maxCases: 96, fetchImpl: async () => {
    originalCalls += 1;
    const raw = rawGemini();
    if (originalCalls === 48) raw.data.model = 'unexpected-fallback-model';
    return jsonResponse(raw);
  } });
  assert.equal(originalCalls, 48);
  const originalBytes = JSON.stringify(original);
  const newQueries = [];
  const checkpoints = [];
  const continued = await collect({ maxCases: 96, resumeCapture: original.capture, resumeTelemetry: original.telemetry,
    fetchImpl: async (_url, options) => { newQueries.push(JSON.parse(options.body).messages[0].text); return jsonResponse(rawGemini()); },
    onCheckpoint: async (value) => { checkpoints.push(value.telemetry.attemptedCases); },
  });
  assert.equal(newQueries.length, 48);
  for (let index = 0; index < newQueries.length; index += 1) assert.ok(newQueries[index].endsWith(plan.cases[index + 48].request.query));
  assert.deepEqual(continued.capture.records.slice(0, 48), original.capture.records.slice(0, 48));
  assert.equal(continued.capture.records[47].error.code, 'invalid_output');
  assert.equal(continued.telemetry.attemptedCases, 96);
  assert.equal(continued.telemetry.newAttemptedCases, 48);
  assert.equal(continued.telemetry.notExecutedCases, 0);
  assert.equal(continued.telemetry.events.length, 96);
  assert.equal(continued.telemetry.resume.previousAttemptedCases, 48);
  assert.equal(continued.telemetry.resume.sourceCaptureSha256, canonicalSha256(original.capture));
  assert.equal(continued.telemetry.resume.sourceTelemetrySha256, canonicalSha256(original.telemetry));
  assert.equal(continued.telemetry.resume.integrity, 'event_hashes_verified');
  assert.equal(continued.capture.startedAt, original.capture.startedAt);
  assert.deepEqual(checkpoints, Array.from({ length: 48 }, (_, index) => index + 49));
  assert.equal(JSON.stringify(original), originalBytes, 'source captures and telemetry must stay immutable');
});

test('legacy telemetry can bind a continuation without claiming old response hashes were verified', async () => {
  const original = await collect({ maxCases: 1 });
  delete original.telemetry.provenanceSha256;
  for (const event of original.telemetry.events) { delete event.requestSha256; delete event.captureRecordSha256; }
  const continued = await collect({ maxCases: 2, resumeCapture: original.capture, resumeTelemetry: original.telemetry });
  assert.equal(continued.telemetry.attemptedCases, 3);
  assert.equal(continued.telemetry.newAttemptedCases, 2);
  assert.equal(continued.telemetry.resume.integrity, 'legacy_event_binding_checked');
  assert.equal(continued.telemetry.events[0].requestSha256, original.capture.records[0].requestSha256);
  assert.equal(continued.telemetry.events[0].captureRecordSha256, canonicalSha256(original.capture.records[0]));
  assert.deepEqual(continued.capture.records[0], original.capture.records[0]);
});

test('resume rejects changed plans, metadata, requests, outputs and missing/inconsistent events before any call', async () => {
  const original = await collect({ maxCases: 2 });
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return jsonResponse(rawGemini()); };
  const mutations = [
    (value) => { value.capture.planSha256 = 'f'.repeat(64); },
    (value) => { value.capture.system.modelId = 'changed'; },
    (value) => { value.capture.records[0].requestSha256 = 'f'.repeat(64); },
    (value) => { value.capture.records[0].caseId = value.capture.records[1].caseId; },
    (value) => { value.capture.records[0].raw.reply = 'changed response'; },
    (value) => { value.telemetry.endpointSha256 = 'f'.repeat(64); },
    (value) => { value.telemetry.events.pop(); },
    (value) => { value.telemetry.events[0].outcome = 'timeout'; },
    (value) => { value.telemetry.events[0].requestSha256 = 'f'.repeat(64); },
    (value) => { value.telemetry.provenanceSha256 = 'f'.repeat(64); },
    (value) => { value.telemetry.attemptedCases = 0; },
    (value) => { value.telemetry.totalResponseBytes += 1; },
    (value) => { value.telemetry.completedAt = '2026-01-01T00:00:00.000Z'; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(original); mutate(value);
    await assert.rejects(() => collect({ maxCases: 0, resumeCapture: value.capture, resumeTelemetry: value.telemetry, fetchImpl }), /resume/u);
  }
  await assert.rejects(() => collect({ resumeCapture: original.capture, fetchImpl }), /supplied together/u);
  await assert.rejects(() => collect({ resumeTelemetry: original.telemetry, fetchImpl }), /supplied together/u);
  assert.equal(calls, 0);
});

test('an aborted resume preserves prior attempts and makes no new calls', async () => {
  const original = await collect({ maxCases: 1 });
  const controller = new AbortController(); controller.abort();
  let calls = 0;
  const continued = await collect({ maxCases: 96, signal: controller.signal, resumeCapture: original.capture,
    resumeTelemetry: original.telemetry, fetchImpl: async () => { calls += 1; return jsonResponse(rawGemini()); } });
  assert.equal(calls, 0);
  assert.equal(continued.telemetry.attemptedCases, 1);
  assert.equal(continued.telemetry.newAttemptedCases, 0);
  assert.equal(continued.telemetry.notExecutedCases, 95);
  assert.equal(continued.telemetry.stoppedReason, 'aborted');
  assert.deepEqual(continued.capture.records, original.capture.records);
});

test('explicit Gemini mismatch continuation preserves failed fallback evidence and executes only the next case', async () => {
  let calls = 0;
  const sent = [];
  const checkpoints = [];
  const fallback = rawGemini(); fallback.data.model = 'unexpected-fallback-model';
  const result = await collect({ maxCases: 2, continueOnModelMismatch: true, fetchImpl: async (_url, options) => {
    sent.push(JSON.parse(options.body).messages[0].text);
    calls += 1;
    return jsonResponse(calls === 1 ? fallback : rawGemini());
  }, onCheckpoint: async (value) => { checkpoints.push(value); } });
  assert.equal(calls, 2);
  assert.ok(sent[0].endsWith(plan.cases[0].request.query));
  assert.ok(sent[1].endsWith(plan.cases[1].request.query));
  assert.equal(result.capture.records[0].error.code, 'invalid_output');
  assert.equal(result.telemetry.events[0].outcome, 'model_or_engine_mismatch');
  assert.deepEqual(result.telemetry.events[0].raw, fallback);
  assert.equal(result.capture.records[1].raw.model, system.modelId);
  assert.equal(result.telemetry.continueOnModelMismatch, true);
  assert.equal(checkpoints[0].telemetry.stoppedReason, null);
  assert.equal(result.telemetry.stoppedReason, 'case_limit');
});

test('Gemini mismatch continuation does not relax HTTP failures or malformed output stops', async () => {
  for (const response of [() => new Response('<html>unavailable</html>', { status: 503 }), () => jsonResponse({ success: true, data: {} }),
    () => jsonResponse({ success: false })]) {
    let calls = 0;
    const result = await collect({ maxCases: 3, continueOnModelMismatch: true,
      fetchImpl: async () => { calls += 1; return response(); } });
    assert.equal(calls, 1);
    assert.equal(result.capture.records[1].error.code, 'not_executed');
    assert.ok(['http_error', 'invalid_response_envelope', 'invalid_assistant_reply'].includes(result.telemetry.stoppedReason));
  }
});

test('mismatch continuation is explicit Gemini-only and can be enabled when resuming without retrying the old failure', async () => {
  let calls = 0;
  await assert.rejects(() => collect({ system: { ...system, id: 'langflow' }, continueOnModelMismatch: true,
    endpoint: 'http://localhost:7860/api/v1/run/test?stream=false', outputSelector: selector,
    fetchImpl: async () => { calls += 1; return jsonResponse(rawLangflow()); } }), /only be enabled for Gemini/u);
  await assert.rejects(() => collect({ continueOnModelMismatch: 'true' }), /must be a boolean/u);
  assert.equal(calls, 0);
  const fallback = rawGemini(); fallback.data.engine = 'keyword';
  const original = await collect({ maxCases: 2, fetchImpl: async () => jsonResponse(fallback) });
  const result = await collect({ maxCases: 1, continueOnModelMismatch: true, resumeCapture: original.capture,
    resumeTelemetry: original.telemetry, fetchImpl: async () => { calls += 1; return jsonResponse(rawGemini()); } });
  assert.equal(calls, 1);
  assert.deepEqual(result.capture.records[0], original.capture.records[0]);
  assert.ok(result.capture.records[1].raw);
  assert.equal(result.telemetry.resume.previousContinueOnModelMismatch, false);
  assert.equal(result.telemetry.continueOnModelMismatch, true);
});

test('evidence-bound collector interruption resumes after the lost response without retry or invented latency', async () => {
  const original = await collect({ maxCases: 1 });
  const interruptedRecord = { caseId: plan.cases[1].caseId, requestSha256: plan.cases[1].requestSha256, error: { code: 'transport_error' } };
  original.capture.records[1] = interruptedRecord;
  original.telemetry.events.push({ caseId: interruptedRecord.caseId, requestSha256: interruptedRecord.requestSha256,
    captureRecordSha256: canonicalSha256(interruptedRecord), outcome: 'collector_interrupted', httpStatus: null,
    responseBytes: 0, latencyMs: null, interruptionEvidenceSha256: 'a'.repeat(64) });
  original.telemetry.attemptedCases = 2;
  original.telemetry.notExecutedCases = 94;
  let calls = 0;
  const resumed = await collect({ maxCases: 1, resumeCapture: original.capture, resumeTelemetry: original.telemetry,
    fetchImpl: async (_url, options) => {
      calls += 1;
      assert.ok(JSON.parse(options.body).messages[0].text.endsWith(plan.cases[2].request.query));
      return jsonResponse(rawGemini());
    } });
  assert.equal(calls, 1);
  assert.deepEqual(resumed.capture.records[1], interruptedRecord);
  assert.equal(resumed.telemetry.events[1].latencyMs, null);
  assert.equal(resumed.telemetry.events[1].interruptionEvidenceSha256, 'a'.repeat(64));
  assert.equal(resumed.telemetry.attemptedCases, 3);
  assert.equal(resumed.telemetry.newAttemptedCases, 1);
  for (const mutate of [
    (value) => { delete value.telemetry.events[1].interruptionEvidenceSha256; },
    (value) => { value.telemetry.events[1].interruptionEvidenceSha256 = 'not-a-hash'; },
    (value) => { value.telemetry.events[1].latencyMs = 0; },
    (value) => { value.telemetry.events[1].httpStatus = 200; },
    (value) => { value.telemetry.events[1].responseBytes = 10; },
    (value) => { value.telemetry.events[1].captureRecordSha256 = 'b'.repeat(64); },
    (value) => { value.telemetry.events[1].outcome = 'transport_failure'; },
    (value) => { value.telemetry.events[0].latencyMs = null; },
  ]) {
    const modified = structuredClone(original); mutate(modified);
    await assert.rejects(() => collect({ maxCases: 0, resumeCapture: modified.capture, resumeTelemetry: modified.telemetry }), /resume/);
  }
});
