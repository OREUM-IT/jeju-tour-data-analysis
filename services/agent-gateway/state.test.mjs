import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Worker } from 'node:worker_threads';
import { createGatewayState, GatewayStateError, STATE_POLICY } from './state.mjs';

const START = 1_795_046_400_000;
const nonce = (number) => number.toString(16).padStart(32, '0');
const request = (number, subject = 'opaque-account-A', now = START) => ({ subject, nonce: nonce(number), requestId: `request-${number}`, now });
function fixture(t) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'jeju-gateway-state-'));
  const filename = path.join(directory, 'private', 'state.sqlite');
  const open = () => createGatewayState({ filename, now: () => START });
  const state = open();
  const handles = [state];
  t.after(() => { handles.forEach((handle) => handle.close()); rmSync(directory, { recursive: true, force: true }); });
  return { filename, directory, state, reopen() { const next = open(); handles.push(next); return next; } };
}
const error = (code) => (value) => value instanceof GatewayStateError && value.code === code
  && value.status === ({ REPLAY_DETECTED: 409, RATE_LIMITED: 429, BUSY: 429, STATE_UNAVAILABLE: 503 })[code]
  && value.message === 'Gateway request unavailable.';
function rows(filename) {
  const database = new DatabaseSync(filename, { readOnly: true });
  try { return database.prepare('SELECT * FROM requests ORDER BY reserved_at, request_id').all(); }
  finally { database.close(); }
}

test('a committed reservation stores only bounded hashes and metadata with private file permissions', (t) => {
  const { state, filename } = fixture(t);
  assert.deepEqual(state.reserve(request(1)), { requestId: 'request-1', leaseUntil: START + 120_000 });
  const saved = rows(filename);
  assert.equal(saved.length, 1);
  assert.deepEqual(Object.keys(saved[0]).sort(), ['lease_until', 'nonce_hash', 'request_id', 'reserved_at', 'status', 'subject_hash']);
  assert.match(saved[0].subject_hash, /^[a-f0-9]{64}$/u);
  assert.match(saved[0].nonce_hash, /^[a-f0-9]{64}$/u);
  assert.equal(JSON.stringify(saved).includes('opaque-account-A'), false);
  assert.equal(JSON.stringify(saved).includes(nonce(1)), false);
  assert.equal(readFileSync(filename).includes(Buffer.from('opaque-account-A')), false);
  assert.equal(statSync(filename).mode & 0o777, 0o600);
  assert.deepEqual(state.finish({ requestId: 'request-1', now: START + 1000 }), { requestId: 'request-1', status: 'finished', leaseUntil: START + 1000 });
});

test('nonce replay is global across subjects and request IDs also cannot be reused', (t) => {
  const { state } = fixture(t);
  state.reserve(request(1));
  state.finish({ requestId: 'request-1', now: START + 1000 });
  assert.throws(() => state.reserve({ ...request(2, 'different-account', START + 2000), nonce: nonce(1) }), error('REPLAY_DETECTED'));
  assert.throws(() => state.reserve({ ...request(2, 'different-account', START + 2000), requestId: 'request-1' }), error('REPLAY_DETECTED'));
  assert.deepEqual(state.finish({ requestId: 'request-1', now: START + 2000, uncertain: true }), { requestId: 'request-1', status: 'finished', leaseUntil: START + 1000 });
  // The nonce outlives the longest signed-request acceptance window (120 seconds), including after completion.
  assert.throws(() => state.reserve({ ...request(1, 'different-account', START + 180_000) }), error('REPLAY_DETECTED'));
});

test('concurrency is one across subjects and rejected busy attempts consume no reservation or quota', (t) => {
  const { state, filename } = fixture(t);
  state.reserve(request(1));
  for (let i = 2; i <= 12; i += 1) assert.throws(() => state.reserve(request(i, `account-${i}`, START + 1)), error('BUSY'));
  assert.equal(rows(filename).length, 1);
  state.finish({ requestId: 'request-1', now: START + 10 });
  state.reserve(request(2, 'account-2', START + 10));
  assert.equal(rows(filename).length, 2);
});

test('restart preserves inflight leases and a late old completion cannot release the new request', (t) => {
  const { state, reopen } = fixture(t);
  state.reserve(request(1));
  state.close();
  const resumed = reopen();
  assert.throws(() => resumed.reserve(request(2, 'account-B', START + 119_999)), error('BUSY'));
  resumed.reserve(request(2, 'account-B', START + 120_000));
  resumed.finish({ requestId: 'request-1', now: START + 120_001 });
  assert.throws(() => resumed.reserve(request(3, 'account-C', START + 120_002)), error('BUSY'));
});

test('uncertain remote outcomes hold a fresh 120-second cooldown through restarts and late callbacks', (t) => {
  const { state, reopen } = fixture(t);
  state.reserve(request(1));
  assert.deepEqual(state.finish({ requestId: 'request-1', now: START + 90_000, uncertain: true }), {
    requestId: 'request-1', status: 'uncertain', leaseUntil: START + 210_000,
  });
  state.close();
  const resumed = reopen();
  assert.equal(resumed.finish({ requestId: 'request-1', now: START + 100_000 }).status, 'uncertain');
  assert.throws(() => resumed.reserve(request(2, 'account-B', START + 209_999)), error('BUSY'));
  resumed.reserve(request(2, 'account-B', START + 210_000));
});

test('subject hourly quota includes completed and uncertain attempts without blocking other subjects after cooldown', (t) => {
  const { state } = fixture(t);
  for (let i = 1; i <= 6; i += 1) {
    state.reserve(request(i, 'account-A', START + i));
    state.finish({ requestId: `request-${i}`, now: START + i, uncertain: i === 6 });
  }
  assert.throws(() => state.reserve(request(7, 'account-A', START + 120_010)), error('RATE_LIMITED'));
  state.reserve(request(8, 'account-B', START + 120_010));
  state.finish({ requestId: 'request-8', now: START + 120_010 });
  state.reserve(request(9, 'account-A', START + STATE_POLICY.windowMs + 1));
});

test('global hourly quota cannot be bypassed with new opaque subjects and resets by the rolling window', (t) => {
  const { state } = fixture(t);
  for (let i = 1; i <= 30; i += 1) {
    state.reserve(request(i, `account-${i}`, START + i));
    state.finish({ requestId: `request-${i}`, now: START + i });
  }
  assert.throws(() => state.reserve(request(31, 'new-account', START + 1000)), error('RATE_LIMITED'));
  state.reserve(request(32, 'new-account', START + STATE_POLICY.windowMs + 1));
});

test('retention and table size remain bounded over many hourly budgets', (t) => {
  const { state, filename } = fixture(t);
  for (let hour = 0; hour < 8; hour += 1) {
    for (let i = 1; i <= 30; i += 1) {
      const number = hour * 30 + i;
      const at = START + hour * STATE_POLICY.windowMs + i;
      state.reserve(request(number, `account-${number}`, at));
      state.finish({ requestId: `request-${number}`, now: at });
    }
    assert.ok(rows(filename).length <= 60);
  }
  const finalRows = rows(filename);
  assert.ok(finalRows.every((row) => row.reserved_at >= START + 7 * STATE_POLICY.windowMs + 30 - STATE_POLICY.retentionMs));
});

test('multiple connections commit only one reservation for a racing pair', async (t) => {
  const { filename } = fixture(t);
  const signal = new SharedArrayBuffer(4);
  const moduleUrl = new URL('./state.mjs', import.meta.url).href;
  const code = `
    import { parentPort, workerData } from 'node:worker_threads';
    const { createGatewayState } = await import(workerData.moduleUrl);
    const state = createGatewayState({filename:workerData.filename, now:workerData.at});
    parentPort.postMessage({ready:true});
    Atomics.wait(new Int32Array(workerData.signal),0,0);
    try { state.reserve(workerData.request); parentPort.postMessage({accepted:true}); }
    catch(error) { parentPort.postMessage({accepted:false,code:error.code}); }
    state.close();
  `;
  const make = async (number) => {
    const worker = new Worker(new URL(`data:text/javascript,${encodeURIComponent(code)}`), {
      workerData: { filename, signal, moduleUrl, at: START, request: request(number, `account-${number}`) },
    });
    t.after(() => worker.terminate());
    await new Promise((resolve, reject) => { worker.once('message', resolve); worker.once('error', reject); });
    const result = new Promise((resolve, reject) => { worker.once('message', resolve); worker.once('error', reject); });
    return { worker, result };
  };
  const first = await make(1);
  const second = await make(2);
  Atomics.store(new Int32Array(signal), 0, 1);
  Atomics.notify(new Int32Array(signal), 0);
  const results = await Promise.all([first.result, second.result]);
  assert.equal(results.filter((value) => value.accepted).length, 1);
  assert.ok(['BUSY', 'STATE_UNAVAILABLE'].includes(results.find((value) => !value.accepted).code));
  assert.equal(rows(filename).length, 1);
});

test('invalid, closed, locked and corrupt persistent state fails closed with fixed errors', (t) => {
  const { state, filename, directory } = fixture(t);
  for (const input of [undefined, null, false, []]) {
    assert.throws(() => state.reserve(input), error('STATE_UNAVAILABLE'));
    assert.throws(() => state.finish(input), error('STATE_UNAVAILABLE'));
    assert.throws(() => createGatewayState(input), error('STATE_UNAVAILABLE'));
  }
  for (const override of [{ subject: '' }, { nonce: 'bad' }, { requestId: 'bad\nrequest' }, { now: NaN }]) {
    assert.throws(() => state.reserve({ ...request(1), ...override }), error('STATE_UNAVAILABLE'));
  }
  assert.throws(() => state.finish({ requestId: 'missing', now: START }), error('STATE_UNAVAILABLE'));
  const lock = new DatabaseSync(filename);
  lock.exec('BEGIN IMMEDIATE');
  assert.throws(() => state.reserve(request(1)), error('STATE_UNAVAILABLE'));
  lock.exec('ROLLBACK');
  lock.close();
  state.reserve(request(1));
  state.close();
  assert.throws(() => state.reserve(request(2)), error('STATE_UNAVAILABLE'));
  assert.throws(() => createGatewayState({ filename: ':memory:' }), error('STATE_UNAVAILABLE'));
  const corrupt = path.join(directory, 'corrupt.sqlite');
  writeFileSync(corrupt, 'not a database');
  assert.throws(() => createGatewayState({ filename: corrupt }), error('STATE_UNAVAILABLE'));
  const linked = path.join(directory, 'linked.sqlite');
  symlinkSync(filename, linked);
  assert.throws(() => createGatewayState({ filename: linked }), error('STATE_UNAVAILABLE'));
});
