import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, readdir, rm, stat, symlink, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { main, parseArguments } from '../scripts/collect-baseline-responses.mjs';
import { createBaselinePlan } from '../src/baseline-evaluation.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const json = (value) => JSON.stringify(value) + '\n';
const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
const geminiReply = { success: true, data: { reply: '합성 테스트 응답', engine: 'gemini', model: 'fixture-gemini', places: [], action: { type: 'none', placeIds: [] } } };
const langflowReply = { outputs: [{ outputs: [{ component_id: 'Output-fixture', results: { message: { sender: 'Machine', text: '합성 응답' } } }] }] };
const CLI = fileURLToPath(new URL('../scripts/collect-baseline-responses.mjs', import.meta.url));

async function files(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jeju-baseline-live-cli-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const local = path.join(root, '.local');
  await mkdir(local);
  const p = { root, local, ...Object.fromEntries(['catalog', 'set', 'plan', 'capture', 'telemetry'].map((key) => [key, path.join(local, key + '.json')])) };
  for (const [key, value] of [['catalog', inputs.catalog], ['set', inputs.set], ['plan', plan]]) await writeFile(p[key], json(value));
  return p;
}
function args(p, system = 'gemini', endpoint = 'http://127.0.0.1:12345/api/assistant') {
  return ['--execute', '--catalog', p.catalog, '--set', p.set, '--plan', p.plan, '--system', system,
    '--model', 'fixture-gemini', '--prompt-version', 'fixture-v1', '--tool-version', 'fixture-v1', '--endpoint', endpoint,
    '--output', p.capture, '--telemetry', p.telemetry,
    ...(system === 'langflow' ? ['--output-index', '0', '--component-id', 'Output-fixture'] : [])];
}

test('live CLI is opt-in with one-case default and rejects ambiguous or unbounded options', () => {
  assert.deepEqual(parseArguments(['--help']), { help: true });
  assert.throws(() => parseArguments([]), /--execute/);
  const p = { catalog: 'a', set: 'b', plan: 'c', capture: 'd', telemetry: 'e' };
  const valid = args(p);
  assert.equal(parseArguments(valid).maxCases, 1);
  assert.equal(parseArguments(valid).timeoutMs, 60000);
  assert.throws(() => parseArguments([...valid, '--execute']), /repeated/);
  assert.throws(() => parseArguments([...valid, '--max-cases', '0']), /integer/);
  assert.throws(() => parseArguments([...valid, '--max-cases', '193']), /integer/);
  assert.throws(() => parseArguments([...valid, '--timeout-ms', '120001']), /integer/);
  assert.throws(() => parseArguments([...valid, '--max-response-bytes', 'NaN']), /integer/);
  assert.throws(() => parseArguments([...valid, '--api-key', 'not-a-real-key']), /Unknown/);
  assert.throws(() => parseArguments([...valid, '--output-index', '0']), /does not use/);
  assert.throws(() => parseArguments([...valid, '--resume-capture', 'prior.json']), /supplied together/);
  assert.throws(() => parseArguments([...valid, '--resume-telemetry', 'prior.json']), /supplied together/);
  assert.equal(parseArguments([...valid, '--continue-on-model-mismatch'])['continue-on-model-mismatch'], true);
  assert.throws(() => parseArguments([...valid, '--continue-on-model-mismatch', '--continue-on-model-mismatch']), /repeated/);
  assert.throws(() => parseArguments([...args(p, 'langflow'), '--continue-on-model-mismatch']), /only supported for Gemini/);
  assert.throws(() => parseArguments(args(p, 'langflow').slice(0, -4)), /requires/);
});

test('--local-model-timeout raises only the timeout ceiling, for explicit v2 Langflow on 127.0.0.1 only', () => {
  const p = { catalog: 'a', set: 'b', plan: 'c', capture: 'd', telemetry: 'e' };
  const loopback = 'http://127.0.0.1:7860/api/v1/run/fixture?stream=false';
  const v2 = (endpoint = loopback) => [...args(p, 'langflow', endpoint), '--request-mode', 'langflow-v2'];
  const flag = '--local-model-timeout';
  // Without the flag, v2 on 127.0.0.1 keeps the 60000 default and the 120000 ceiling.
  assert.equal(parseArguments(v2()).timeoutMs, 60000);
  assert.equal(parseArguments(v2())['local-model-timeout'], undefined);
  assert.equal(parseArguments([...v2(), '--timeout-ms', '120000']).timeoutMs, 120000);
  assert.throws(() => parseArguments([...v2(), '--timeout-ms', '120001']), /^Error: --timeout-ms must be an integer from 1 to 120000\.$/);
  // The flag changes neither the default nor the floor; it accepts up to 1200000 in either argument order.
  assert.equal(parseArguments([...v2(), flag]).timeoutMs, 60000);
  assert.equal(parseArguments([...v2(), flag])['local-model-timeout'], true);
  assert.equal(parseArguments([...v2(), flag, '--timeout-ms', '120001']).timeoutMs, 120001);
  assert.equal(parseArguments([...v2(), '--timeout-ms', '1200000', flag]).timeoutMs, 1200000);
  for (const value of ['1200001', '0', '600000.5']) {
    assert.throws(() => parseArguments([...v2(), flag, '--timeout-ms', value]), /^Error: --timeout-ms must be an integer from 1 to 1200000\.$/);
  }
  assert.throws(() => parseArguments([...v2(), flag, flag]), /repeated/);
  assert.throws(() => parseArguments([...v2(), flag, 'true']), /Unknown/);
  for (const other of [args(p), args(p, 'langflow', loopback), [...args(p, 'langflow', loopback), '--request-mode', 'legacy']]) {
    assert.throws(() => parseArguments([...other, flag, '--timeout-ms', '600000']),
      /^Error: --local-model-timeout is only supported for Langflow with --request-mode langflow-v2\.$/);
  }
  for (const endpoint of ['http://localhost:7860/api/v1/run/fixture?stream=false', 'http://LOCALHOST:7860/api/v1/run/fixture?stream=false',
    'http://[::1]:7860/api/v1/run/fixture?stream=false', 'http://127.0.0.2:7860/api/v1/run/fixture?stream=false',
    'http://127.0.0.1.nip.io:7860/api/v1/run/fixture?stream=false', 'http://127.0.0.1/api/v1/run/fixture?stream=false',
    'https://127.0.0.1:7860/api/v1/run/fixture?stream=false', 'https://langflow.example.com/api/v1/run/fixture?stream=false',
    'http://example.com:7860/api/v1/run/fixture?stream=false', 'not-a-url']) {
    assert.throws(() => parseArguments([...v2(endpoint), flag, '--timeout-ms', '600000']),
      /^Error: --local-model-timeout requires an --endpoint origin of exactly http:\/\/127\.0\.0\.1:<port>\.$/);
  }
});

test('live CLI executes one loopback Gemini case, reserves private files and preserves full coverage', async (t) => {
  const p = await files(t);
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    requests.push({ url: req.url, headers: req.headers, body: JSON.parse(body) });
    assert.equal((await stat(p.capture)).size, 0);
    assert.equal((await stat(p.telemetry)).size, 0);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(geminiReply));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const logs = [];
  const marker = 'SYNTHETIC_CREDENTIAL_NOT_A_KEY';
  const status = await main(args(p, 'gemini', `http://127.0.0.1:${server.address().port}/api/assistant`),
    { env: { BASELINE_APP_BEARER_TOKEN: marker, GEMINI_API_KEY: 'SYNTHETIC_PROVIDER_KEY_MUST_NOT_BE_SENT' }, log: (value) => logs.push(value) });
  assert.equal(status.successfulResponses, 1);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].headers.authorization, `Bearer ${marker}`);
  assert.equal(requests[0].headers['x-api-key'], undefined);
  assert.ok(requests[0].body.messages[0].text.includes(plan.cases[0].request.query));
  assert.ok(!JSON.stringify(requests[0].body).includes('SYNTHETIC_PROVIDER_KEY'));
  const capture = await readJson(p.capture);
  const telemetry = await readJson(p.telemetry);
  assert.equal(capture.records.length, 96);
  assert.equal(capture.records.filter((entry) => entry.error?.code === 'not_executed').length, 95);
  assert.deepEqual(capture.records[0].raw, geminiReply.data);
  assert.equal(capture.records[0].measurements.costUsd, null);
  assert.ok(capture.records[0].measurements.latencyMs >= 0);
  assert.equal(telemetry.attemptedCases, 1);
  assert.equal((await stat(p.capture)).mode & 0o777, 0o600);
  assert.ok(!JSON.stringify({ capture, telemetry, logs }).includes(marker));
  assert.ok(!logs.join('\n').includes(geminiReply.data.reply));
});

test('Langflow uses only its server credential and persists transport failure without retry', async (t) => {
  const p = await files(t);
  let calls = 0;
  const marker = 'SYNTHETIC_LANGFLOW_KEY';
  const status = await main([...args(p, 'langflow', 'http://127.0.0.1:7860/api/v1/run/fixture?stream=false'), '--max-cases', '3'], {
    env: { LANGFLOW_API_KEY: marker, BASELINE_APP_BEARER_TOKEN: 'SYNTHETIC_OTHER_CREDENTIAL' }, log: () => {},
    fetchImpl: async (_url, request) => {
      calls += 1;
      assert.equal(request.headers['x-api-key'], marker);
      assert.equal(request.headers.authorization, undefined);
      return new Response(JSON.stringify({ error: 'SYNTHETIC_PRIVATE_FAILURE' }), { status: 503, headers: { 'content-type': 'application/json' } });
    },
  });
  assert.equal(calls, 1);
  assert.equal(status.successfulResponses, 0);
  assert.equal(status.notExecutedCases, 95);
  assert.equal((await readJson(p.capture)).records[0].error.code, 'transport_error');
  assert.ok(!JSON.stringify(await readJson(p.telemetry)).includes(marker));
});

test('invalid plans, public paths, symlink escapes and existing outputs stop before any request', async (t) => {
  const p = await files(t);
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return new Response(JSON.stringify(geminiReply)); };
  const runtime = { env: {}, fetchImpl, log: () => {} };
  const alteredPlan = structuredClone(plan); alteredPlan.cases[0].request.query = '잘못된 평가 요청입니다';
  await writeFile(p.plan, json(alteredPlan));
  await assert.rejects(main(args(p), runtime), /plan hash/);
  await writeFile(p.plan, json(plan));
  await assert.rejects(main(args({ ...p, capture: path.join(p.root, 'public.json') }), runtime), /inside .local/);
  await symlink(p.root, path.join(p.local, 'escape'));
  await assert.rejects(main(args({ ...p, capture: path.join(p.local, 'escape', 'public.json') }), runtime), /outside .local/);
  await writeFile(p.telemetry, 'preserved');
  await assert.rejects(main(args(p), runtime), /already exists/);
  assert.equal(await readFile(p.telemetry, 'utf8'), 'preserved');
  assert.equal(await stat(p.capture).then(() => true, () => false), false);
  assert.equal(calls, 0);
});

test('missing Langflow key or invalid endpoint leaves no output reservations or network effects', async (t) => {
  const p = await files(t);
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return new Response(JSON.stringify(langflowReply)); };
  await assert.rejects(main(args(p, 'langflow', 'http://127.0.0.1:7860/api/v1/run/fixture?stream=false'), { env: {}, fetchImpl, log: () => {} }), /LANGFLOW_API_KEY/);
  await assert.rejects(main(args(p, 'gemini', 'http://example.invalid/api/assistant'), { env: {}, fetchImpl, log: () => {} }), /HTTPS/);
  assert.equal(calls, 0);
  assert.equal(await stat(p.capture).then(() => true, () => false), false);
  assert.equal(await stat(p.telemetry).then(() => true, () => false), false);
});

test('cancellation persists full not-executed coverage without a model request', async (t) => {
  const p = await files(t);
  const controller = new AbortController(); controller.abort();
  let calls = 0;
  const status = await main(args(p), { env: {}, signal: controller.signal, log: () => {},
    fetchImpl: async () => { calls += 1; return new Response(JSON.stringify(geminiReply)); } });
  assert.equal(calls, 0);
  assert.equal(status.stoppedReason, 'aborted');
  assert.equal(status.notExecutedCases, 96);
  assert.equal((await readJson(p.capture)).records.length, 96);
});

test('a completed response survives process death during the following request', { timeout: 10_000 }, async (t) => {
  const p = await files(t);
  let child;
  let calls = 0;
  let observed;
  const server = http.createServer(async (req, res) => {
    for await (const _chunk of req) { /* consume synthetic request */ }
    calls += 1;
    if (calls === 1) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(geminiReply));
    } else {
      observed = { capture: await readJson(p.capture), telemetry: await readJson(p.telemetry) };
      child.kill('SIGKILL');
      res.destroy();
    }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { child?.kill('SIGKILL'); server.closeAllConnections(); server.close(); });
  child = spawn(process.execPath, [CLI, ...args(p, 'gemini', `http://127.0.0.1:${server.address().port}/api/assistant`), '--max-cases', '3'],
    { stdio: 'ignore', env: { ...process.env, BASELINE_APP_BEARER_TOKEN: '' } });
  await once(child, 'exit');
  assert.equal(calls, 2);
  assert.equal(observed.capture.records.length, 96);
  assert.equal(observed.capture.records.filter((entry) => entry.raw !== undefined).length, 1);
  assert.deepEqual(observed.capture.records[0].raw, geminiReply.data);
  assert.equal(observed.telemetry.attemptedCases, 1);
  assert.equal(observed.telemetry.notExecutedCases, 95);
  assert.deepEqual(await readJson(p.capture), observed.capture);
  assert.deepEqual(await readJson(p.telemetry), observed.telemetry);
  assert.equal((await stat(p.capture)).mode & 0o777, 0o600);
  assert.ok(!(await readdir(p.local)).some((name) => name.endsWith('.checkpoint')));
});

test('checkpoint persistence refuses a changed destination without following its symlink', async (t) => {
  const p = await files(t);
  const external = path.join(p.root, 'untouched.json');
  await writeFile(external, 'preserved external contents');
  let calls = 0;
  let before;
  await assert.rejects(main([...args(p), '--max-cases', '3'], { env: {}, log: () => {}, fetchImpl: async () => {
    calls += 1;
    if (calls === 2) {
      before = await readJson(p.capture);
      await unlink(p.telemetry);
      await symlink(external, p.telemetry);
    }
    return new Response(JSON.stringify(geminiReply), { headers: { 'content-type': 'application/json' } });
  } }), /destination changed/);
  assert.equal(calls, 2);
  assert.equal(before.records.filter((entry) => entry.raw !== undefined).length, 1);
  assert.equal((await readJson(p.capture)).records.filter((entry) => entry.raw !== undefined).length, 2);
  assert.equal(await readFile(external, 'utf8'), 'preserved external contents');
  assert.ok(!(await readdir(p.local)).some((name) => name.endsWith('.checkpoint')));
});

test('CLI resume fills only unexecuted cases into new files and preserves previous failures', async (t) => {
  const p = await files(t);
  let firstCalls = 0;
  await main([...args(p), '--max-cases', '3'], { env: {}, log: () => {}, fetchImpl: async () => {
    firstCalls += 1;
    const reply = structuredClone(geminiReply);
    if (firstCalls === 2) reply.data.model = 'different-fixture-model';
    return new Response(JSON.stringify(reply), { headers: { 'content-type': 'application/json' } });
  } });
  assert.equal(firstCalls, 2);
  const originalCapture = await readFile(p.capture, 'utf8');
  const originalTelemetry = await readFile(p.telemetry, 'utf8');
  const resumed = { ...p, capture: path.join(p.local, 'capture-resumed.json'), telemetry: path.join(p.local, 'telemetry-resumed.json') };
  let nextCalls = 0;
  await main([...args(resumed), '--resume-capture', p.capture, '--resume-telemetry', p.telemetry], { env: {}, log: () => {},
    fetchImpl: async (_url, request) => {
      nextCalls += 1;
      assert.ok(JSON.parse(request.body).messages[0].text.includes(plan.cases[2].request.query));
      return new Response(JSON.stringify(geminiReply), { headers: { 'content-type': 'application/json' } });
    } });
  assert.equal(nextCalls, 1);
  const capture = await readJson(resumed.capture);
  assert.deepEqual(capture.records.slice(0, 2), JSON.parse(originalCapture).records.slice(0, 2));
  assert.equal(capture.records[1].error.code, 'invalid_output');
  assert.deepEqual(capture.records[2].raw, geminiReply.data);
  assert.equal(capture.records.filter((entry) => entry.error?.code === 'not_executed').length, 93);
  assert.equal(await readFile(p.capture, 'utf8'), originalCapture);
  assert.equal(await readFile(p.telemetry, 'utf8'), originalTelemetry);
});

test('resume sources cannot alias outputs or leave .local, and invalid resume metadata sends no requests', async (t) => {
  const p = await files(t);
  await main(args(p), { env: {}, log: () => {}, fetchImpl: async () => new Response(JSON.stringify(geminiReply)) });
  const output = { ...p, capture: path.join(p.local, 'next-capture.json'), telemetry: path.join(p.local, 'next-telemetry.json') };
  const resumeArgs = ['--resume-capture', p.capture, '--resume-telemetry', p.telemetry];
  let calls = 0;
  const runtime = { env: {}, log: () => {}, fetchImpl: async () => { calls += 1; return new Response(JSON.stringify(geminiReply)); } };
  await assert.rejects(main([...args({ ...output, capture: p.capture }), ...resumeArgs], runtime), /distinct/);
  const publicCapture = path.join(p.root, 'public-prior.json');
  await writeFile(publicCapture, await readFile(p.capture));
  await assert.rejects(main([...args(output), '--resume-capture', publicCapture, '--resume-telemetry', p.telemetry], runtime), /inside .local/);
  const linked = path.join(p.local, 'linked-prior.json');
  await symlink(publicCapture, linked);
  await assert.rejects(main([...args(output), '--resume-capture', linked, '--resume-telemetry', p.telemetry], runtime), /inside .local/);
  const corrupted = await readJson(p.telemetry); corrupted.planSha256 = '0'.repeat(64);
  const corruptedPath = path.join(p.local, 'corrupted-telemetry.json');
  await writeFile(corruptedPath, json(corrupted));
  await assert.rejects(main([...args(output), '--resume-capture', p.capture, '--resume-telemetry', corruptedPath], runtime));
  assert.equal(calls, 0);
  assert.equal(await stat(output.capture).then(() => true, () => false), false);
  assert.equal(await stat(output.telemetry).then(() => true, () => false), false);
});

test('explicit Gemini mismatch continuation retains fallback failure but still stops on HTTP failure', async (t) => {
  const p = await files(t);
  let calls = 0;
  const status = await main([...args(p), '--max-cases', '4', '--continue-on-model-mismatch'], { env: {}, log: () => {}, fetchImpl: async () => {
    calls += 1;
    const reply = structuredClone(geminiReply);
    if (calls === 1) reply.data.model = 'different-fixture-model';
    if (calls === 3) return new Response('<html>synthetic outage</html>', { status: 503 });
    return new Response(JSON.stringify(reply));
  } });
  assert.equal(calls, 3);
  assert.equal(status.successfulResponses, 1);
  const capture = await readJson(p.capture);
  assert.equal(capture.records[0].error.code, 'invalid_output');
  assert.deepEqual(capture.records[1].raw, geminiReply.data);
  assert.equal(capture.records[2].error.code, 'transport_error');
  assert.equal(capture.records[3].error.code, 'not_executed');
  const telemetry = await readJson(p.telemetry);
  assert.equal(telemetry.continueOnModelMismatch, true);
  assert.equal(telemetry.events[0].outcome, 'model_or_engine_mismatch');
  assert.equal(telemetry.events[0].raw.data.model, 'different-fixture-model');
  assert.equal(telemetry.stoppedReason, 'http_error');
});
