import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { collectBaselineCapture } from '../src/baseline-runner.mjs';
import { createBaselinePlan } from '../src/baseline-evaluation.mjs';
import { VALIDATED_BASELINE_ADAPTER_VERSION } from '../src/validated-capture-binding.mjs';
import { renameReplacing, WINDOWS_RENAME_ATTEMPTS } from '../scripts/collect-baseline-responses.mjs';
import { LOCAL_OLLAMA_EVALUATION, parseChainArguments, runLocalChain } from '../scripts/run-local-ollama-collection.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const API_KEY = `sk-${'k'.repeat(40)}`;
const FLOW = { name: 'fixture local evaluation flow', data: { nodes: [{ id: 'Agent-fixture', data: { value: 0.5 } }], edges: [] } };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const output = (value) => ({ outputs: [{ outputs: [{ component_id: 'ChatOutput-jejuV2',
  results: { message: { sender: 'Machine', text: JSON.stringify(value),
    properties: { source: { id: 'JejuFinalValidation-v2', source: 'Jeju Final Validation' } } } } }] }] });
const requestId = (options) => JSON.parse(JSON.parse(options.body).input_value).requestId;
const RESPONSES = {
  ok: (options) => json(output({ schemaVersion: '1.0', requestId: requestId(options), action: 'ask_clarification', placeIds: [],
    requirementClaims: [], confirmationNotice: false, noticeTopics: [] })),
  blocked: () => json(output({ status: 'blocked', code: 'RECOMMENDATION_BLOCKED', message: '합성 차단' })),
  // The exact HTTP 500 bodies Langflow 1.13.3 returned with the Ollama server stopped and with the MCP server stopped.
  agentError: () => json({ detail: JSON.stringify({ message: 'Error running graph: Error building Component Agent: \n\nAGENT_EXECUTION_FAILED',
    traceback: null, description: null, code: null, suggestion: null }) }, 500),
  agentTimeout: () => json({ detail: JSON.stringify({ message: 'Error running graph: Error building Component Agent: \n\nAGENT_EXECUTION_TIMEOUT' }) }, 500),
  mcpToolsError: () => json({ detail: JSON.stringify({ message: "Error running graph: Error building Component MCP Tools: \n\nError updating tool list: MCP server 'jeju-mcp-v2' at http://127.0.0.1:8789/mcp failed: ",
    traceback: null, description: null, code: null, suggestion: null }) }, 500),
  plainError: () => new Response('Internal Server Error', { status: 500 }),
  unauthorized: () => json({ detail: 'synthetic' }, 401),
};

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jeju-ollama-chain-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const local = path.join(root, '.local');
  await mkdir(local);
  for (const [name, value] of [['catalog', inputs.catalog], ['set', inputs.set], ['plan', plan]]) {
    await writeFile(path.join(local, `${name}.json`), JSON.stringify(value));
  }
  await writeFile(path.join(local, 'langflow-api-key.env'), `LANGFLOW_API_KEY=${API_KEY}\n`);
  const flowPath = path.join(root, 'flow.json');
  await writeFile(flowPath, JSON.stringify(FLOW));
  const state = { flowId: 'fixture-flow-1', storedData: structuredClone(FLOW.data), flows: null, down: new Set(), serviceCalls: 0 };
  const serviceFetch = async (url, options) => {
    state.serviceCalls += 1;
    const { origin, pathname } = new URL(url);
    if (origin === LOCAL_OLLAMA_EVALUATION.ollamaUrl && pathname === '/api/version') return state.down.has('ollama') ? json({}, 503) : json({ version: '0.0.0' });
    if (origin === LOCAL_OLLAMA_EVALUATION.mcpUrl && pathname === '/healthz') return state.down.has('mcp') ? json({}, 503) : json({ ok: true });
    if (origin !== LOCAL_OLLAMA_EVALUATION.langflowUrl) throw new TypeError('unexpected destination');
    if (pathname === '/health_check') return state.down.has('langflow') ? json({ status: 'nok' }) : json({ status: 'ok' });
    if (options.headers['x-api-key'] !== API_KEY) return json({ detail: 'unauthorized' }, 401);
    if (pathname === '/api/v1/flows/') return json(state.flows ?? [{ id: state.flowId, name: FLOW.name }, { id: 'other', name: 'other flow' }]);
    if (pathname === `/api/v1/flows/${state.flowId}`) return json({ id: state.flowId, name: FLOW.name, data: state.storedData });
    return json({ detail: 'not found' }, 404);
  };
  const config = { ...LOCAL_OLLAMA_EVALUATION, localDir: local, flowPath, catalog: 'catalog.json', set: 'set.json', plan: 'plan.json',
    series: 'ollama', apiKeyFile: 'langflow-api-key.env', check: false };
  const run = async (target, script, extra = {}) => {
    const lines = [];
    const calls = [];
    const runFetch = async (url, options) => {
      calls.push(url);
      const kind = script[calls.length - 1];
      if (kind === undefined) throw new Error('unscripted request');
      return typeof kind === 'function' ? kind(options) : RESPONSES[kind](options);
    };
    const result = await runLocalChain({ ...config, target, ...extra.config },
      { serviceFetch, runFetch, log: (line) => lines.push(line), isAlive: () => true, ...extra.deps });
    return { result, lines: lines.map((line) => JSON.parse(line)), raw: lines.join('\n'), calls };
  };
  const file = (name) => path.join(local, name);
  const files = async () => (await readdir(local)).filter((name) => name.startsWith('ollama-')).sort();
  return { local, state, run, file, files };
}

test('chain argument parsing accepts one mode, bounded targets and safe names only', () => {
  assert.deepEqual(parseChainArguments(['--target', '10']), { series: 'ollama', apiKeyFile: 'langflow-api-key.env', check: false, target: 10 });
  assert.equal(parseChainArguments(['--check', '--series', 'ollama-r2', '--api-key-file', 'langflow-api-key-2.env']).check, true);
  for (const args of [[], ['--target', '10', '--check'], ['--target', '0'], ['--target', '010'], ['--target', '10', '--target', '11'],
    ['--target'], ['--series', 'Ollama', '--target', '1'], ['--series', '../x', '--target', '1'], ['--api-key-file', '..\\key.env', '--check'],
    ['--api-key-file', 'key.txt', '--check'], ['--endpoint', 'http://127.0.0.1:7860', '--check']]) {
    assert.throws(() => parseChainArguments(args), undefined, args.join(' '));
  }
});

test('chain continues after model results and a single Agent error, resuming one pair per segment, up to the target', async (t) => {
  const { run, file, files } = await fixture(t);
  const { result, lines, raw, calls } = await run(5, ['ok', 'blocked', 'ok', 'agentError', 'ok']);
  assert.equal(result.result, 'target_reached');
  assert.equal(result.segments, 3);
  assert.equal(calls.length, 5);
  assert.ok(calls.every((url) => url === 'http://127.0.0.1:7860/api/v1/run/fixture-flow-1?stream=false'));
  assert.deepEqual(await files(), ['ollama-capture-1.json', 'ollama-capture-2.json', 'ollama-capture-3.json',
    'ollama-telemetry-1.json', 'ollama-telemetry-2.json', 'ollama-telemetry-3.json']);
  const ends = lines.filter((line) => line.status === 'segment-end');
  assert.deepEqual(ends.map((line) => [line.stoppedReason, line.decision]),
    [['final_validation_blocked', 'continue'], ['http_error', 'agent_error'], ['case_limit', 'done']]);
  assert.equal(ends[1].lastHttpStatus, 500);
  assert.deepEqual(lines.filter((line) => line.status === 'collecting').map((line) => line.outcome),
    ['completed', 'final_validation_blocked', 'completed', 'http_error', 'completed']);
  assert.ok(lines.filter((line) => line.status === 'collecting').every((line) => Number.isSafeInteger(line.caseSeconds)));
  const telemetry = JSON.parse(await readFile(file('ollama-telemetry-3.json'), 'utf8'));
  const capture = JSON.parse(await readFile(file('ollama-capture-3.json'), 'utf8'));
  assert.equal(telemetry.attemptedCases, 5);
  assert.equal(telemetry.localModelTimeout, true);
  assert.equal(telemetry.timeoutMs, 960000);
  assert.equal(telemetry.resume.integrity, 'event_hashes_verified');
  assert.deepEqual(capture.system, { id: 'langflow', modelId: 'qwen3:4b', promptVersion: 'jeju-v2-ollama-7857ef60',
    toolVersion: 'jeju-mcp-v2-agent-tools-v2.0', adapterVersion: VALIDATED_BASELINE_ADAPTER_VERSION });
  assert.deepEqual(capture.records.slice(0, 5).map((record) => record.error?.code ?? 'raw'), ['raw', 'invalid_output', 'raw', 'transport_error', 'raw']);
  assert.equal(capture.records.filter((record) => record.error?.code === 'not_executed').length, plan.cases.length - 5);
  const finished = lines.at(-1);
  assert.deepEqual([finished.status, finished.latestCapture, finished.successfulResponses], ['chain-finished', 'ollama-capture-3.json', 3]);
  assert.ok(!raw.includes(API_KEY) && /^[\x20-\x7e\n]*$/u.test(raw));
  assert.equal(await stat(file('ollama.lock')).then(() => true, () => false), false);
});

test('three Agent errors in a row stop the chain, while a completed case in between ends the streak', async (t) => {
  const first = await fixture(t);
  const stopped = await first.run(10, ['agentError', 'agentError', 'agentError', 'ok']);
  assert.deepEqual([stopped.result.result, stopped.result.reason, stopped.calls.length], ['stopped', 'repeated_agent_errors', 3]);
  assert.equal(stopped.lines.at(-1).status, 'chain-stopped');
  const second = await fixture(t);
  const continued = await second.run(5, ['agentError', 'agentTimeout', 'ok', 'agentError', 'agentError']);
  assert.deepEqual([continued.result.result, continued.calls.length], ['target_reached', 5]);
});

test('an HTTP 500 from any other component, or without the Agent code, stops the chain at once', async (t) => {
  for (const kind of ['mcpToolsError', 'plainError']) {
    const { run } = await fixture(t);
    const stopped = await run(10, ['ok', kind, 'ok']);
    assert.deepEqual([stopped.result.reason, stopped.calls.length], ['http_error', 2], kind);
    assert.equal(stopped.lines.find((line) => line.status === 'segment-end').decision, 'stop');
  }
  const { state, run } = await fixture(t);
  const mcpDown = (options) => { state.down.add('mcp'); return RESPONSES.mcpToolsError(options); };
  const named = await run(10, [mcpDown]);
  assert.deepEqual([named.result.reason, named.result.servicesDown, named.lines.at(-1).advice.includes('windows 1 and 2')], ['http_error', ['mcp'], true]);
});

test('a service that went down during a failed case is named together with that outcome', async (t) => {
  const { state, run } = await fixture(t);
  const failing = (options) => { state.down.add('ollama'); return RESPONSES.agentError(options); };
  const stopped = await run(10, ['ok', failing]);
  assert.deepEqual([stopped.result.reason, stopped.result.services, stopped.result.lastCaseOutcome, stopped.calls.length],
    ['service_unavailable', ['ollama'], 'http_error', 2]);
});

test('an unexpected status or an unavailable service stops the chain; a down service is caught before any request', async (t) => {
  const first = await fixture(t);
  const refused = await first.run(10, ['ok', 'unauthorized']);
  assert.deepEqual([refused.result.reason, refused.calls.length, refused.result.attemptedCases], ['http_error', 2, 2]);
  const second = await fixture(t);
  second.state.down.add('ollama');
  second.state.down.add('langflow');
  const down = await second.run(10, []);
  assert.deepEqual([down.result.reason, down.result.services, down.calls.length], ['service_unavailable', ['ollama', 'langflow'], 0]);
  assert.deepEqual(await second.files(), []);
});

test('a changed, missing or duplicated flow and a rejected key stop before any evaluation request', async (t) => {
  const changes = [
    ['flow_changed', (state) => { state.storedData.nodes[0].data.value = 0.6; }],
    ['flow_missing', (state) => { state.flows = [{ id: 'other', name: 'other flow' }]; }],
    ['flow_name_conflict', (state) => { state.flows = [{ id: 'a', name: FLOW.name }, { id: 'b', name: FLOW.name }]; }],
    ['flow_id_invalid', (state) => { state.flows = [{ id: '../x', name: FLOW.name }]; }],
  ];
  for (const [reason, change] of changes) {
    const { state, run, files } = await fixture(t);
    change(state);
    const stopped = await run(10, []);
    assert.deepEqual([stopped.result.reason, stopped.calls.length], [reason, 0], reason);
    assert.deepEqual(await files(), []);
  }
  const { file, run } = await fixture(t);
  await writeFile(file('langflow-api-key.env'), `LANGFLOW_API_KEY=sk-${'x'.repeat(40)}\n`);
  assert.equal((await run(10, [])).result.reason, 'api_key_rejected');
  await writeFile(file('langflow-api-key.env'), 'LANGFLOW_API_KEY="quoted"\n');
  assert.equal((await run(10, [])).result.reason, 'api_key_missing');
});

test('a later invocation resumes the newest pair, and an endpoint change is refused before any request', async (t) => {
  const { state, run, file, files } = await fixture(t);
  assert.equal((await run(2, ['ok', 'ok'])).result.result, 'target_reached');
  const resumed = await run(5, ['ok', 'blocked', 'ok']);
  assert.equal(resumed.lines[0].resumeFrom, 'ollama-capture-1.json');
  assert.deepEqual([resumed.result.result, resumed.result.attemptedCases, resumed.result.latestCapture, resumed.calls.length],
    ['target_reached', 5, 'ollama-capture-3.json', 3]);
  const telemetry = JSON.parse(await readFile(file('ollama-telemetry-3.json'), 'utf8'));
  assert.equal(telemetry.resume.previousAttemptedCases, 4);
  const unchanged = await run(5, []);
  assert.deepEqual([unchanged.result.result, unchanged.result.segments, unchanged.calls.length], ['target_reached', 0, 0]);
  state.flowId = 'fixture-flow-2';
  const moved = await run(7, []);
  assert.deepEqual([moved.result.reason, moved.calls.length], ['endpoint_changed', 0]);
  assert.equal((await files()).length, 6);
});

test('one Ctrl+C lets the case in flight finish and starts no further case', async (t) => {
  const { run, file } = await fixture(t);
  const stop = new AbortController();
  const finishing = (options) => { stop.abort(); return RESPONSES.ok(options); };
  const stopped = await run(10, [finishing, 'ok'], { deps: { stopSignal: stop.signal } });
  assert.deepEqual([stopped.result.result, stopped.calls.length, stopped.result.attemptedCases, stopped.result.successfulResponses],
    ['operator_stop', 1, 1, 1]);
  const telemetry = JSON.parse(await readFile(file('ollama-telemetry-1.json'), 'utf8'));
  assert.deepEqual([telemetry.stoppedReason, telemetry.events[0].outcome], ['operator_stop', 'completed']);
  const before = new AbortController();
  before.abort();
  const idle = await run(10, [], { deps: { stopSignal: before.signal } });
  assert.deepEqual([idle.result.result, idle.calls.length], ['operator_stop', 0]);
});

test('--check sends no evaluation request; unreadable leftovers are skipped, kept, and never reused as names', async (t) => {
  const { run, file, files } = await fixture(t);
  const checked = await run(null, [], { config: { check: true } });
  assert.deepEqual([checked.result.result, checked.result.flowId, checked.calls.length], ['checked', 'fixture-flow-1', 0]);
  assert.deepEqual(await files(), []);
  await writeFile(file('ollama-capture-7.json'), '');
  await writeFile(file('ollama-telemetry-7.json'), '');
  await writeFile(file('ollama-capture-8.json'), '{}');
  const fresh = await run(1, ['ok']);
  assert.deepEqual(fresh.lines[0].skippedIndexes, [8, 7]);
  assert.equal(fresh.lines[0].resumeFrom, null);
  assert.equal(fresh.result.latestCapture, 'ollama-capture-9.json');
  assert.deepEqual(await files(), ['ollama-capture-7.json', 'ollama-capture-8.json', 'ollama-capture-9.json',
    'ollama-telemetry-7.json', 'ollama-telemetry-9.json']);
});

test('a live lock refuses a second chain; a dead owner\'s lock is replaced and removed afterwards', async (t) => {
  const { run, file } = await fixture(t);
  await writeFile(file('ollama.lock'), '999999\n');
  const refused = await run(1, ['ok']);
  assert.deepEqual([refused.result.reason, refused.calls.length], ['other_run_active', 0]);
  const replaced = await run(1, ['ok'], { deps: { isAlive: () => false } });
  assert.deepEqual([replaced.result.result, replaced.lines[0].staleLockReplaced], ['target_reached', true]);
  assert.equal(await stat(file('ollama.lock')).then(() => true, () => false), false);
});

test('the runner stop signal never aborts the request in flight and is validated', async () => {
  const system = { id: 'langflow', modelId: 'fixture-model', promptVersion: 'fixture-v2', toolVersion: 'fixture-mcp-v2', adapterVersion: VALIDATED_BASELINE_ADAPTER_VERSION };
  const base = { plan, system, catalog: inputs.catalog, requestMode: 'langflow-v2', outputSelector: { outputIndex: 0, componentId: 'ChatOutput-jejuV2' },
    endpoint: 'http://127.0.0.1:7860/api/v1/run/fixture-v2?stream=false', maxCases: 3 };
  const stop = new AbortController();
  let signalAtResponse;
  const result = await collectBaselineCapture({ ...base, stopSignal: stop.signal, fetchImpl: async (_url, options) => {
    stop.abort(); await new Promise((resolve) => { setTimeout(resolve, 5); }); signalAtResponse = options.signal.aborted; return RESPONSES.ok(options);
  } });
  assert.equal(signalAtResponse, false);
  assert.deepEqual([result.telemetry.attemptedCases, result.telemetry.stoppedReason, result.telemetry.events[0].outcome], [1, 'operator_stop', 'completed']);
  await assert.rejects(collectBaselineCapture({ ...base, stopSignal: { aborted: true }, fetchImpl: RESPONSES.ok }), /stopSignal must be an AbortSignal/);
});

test('checkpoint rename retries Windows sharing errors only, re-checks the destination, and gives up after a bound', async () => {
  const sharing = (code) => Object.assign(new Error(code), { code });
  const attempt = (failures) => {
    const calls = { rename: 0, check: 0, waits: [] };
    const options = { platform: 'win32', check: async () => { calls.check += 1; }, wait: async (ms) => { calls.waits.push(ms); },
      renameImpl: async () => { calls.rename += 1; if (calls.rename <= failures.length) throw sharing(failures[calls.rename - 1]); } };
    return { calls, options };
  };
  const retried = attempt(['EPERM', 'EBUSY', 'EACCES']);
  await renameReplacing('a', 'b', retried.options);
  assert.deepEqual([retried.calls.rename, retried.calls.check, retried.calls.waits], [4, 4, [50, 100, 200]]);
  for (const [platform, code] of [['linux', 'EPERM'], ['win32', 'ENOENT'], ['darwin', 'EBUSY']]) {
    const once = attempt([code]);
    await assert.rejects(renameReplacing('a', 'b', { ...once.options, platform }), { code });
    assert.equal(once.calls.rename, 1);
  }
  const forever = attempt(Array(WINDOWS_RENAME_ATTEMPTS + 5).fill('EPERM'));
  await assert.rejects(renameReplacing('a', 'b', forever.options), { code: 'EPERM' });
  assert.equal(forever.calls.rename, WINDOWS_RENAME_ATTEMPTS);
  assert.ok(forever.calls.waits.every((ms) => ms <= 1000));
  const moved = attempt(['EPERM']);
  let checks = 0;
  await assert.rejects(renameReplacing('a', 'b', { ...moved.options, check: async () => { checks += 1; if (checks === 2) throw new Error('Checkpoint destination changed while writing.'); } }),
    /destination changed/);
  assert.equal(moved.calls.rename, 1);
});
