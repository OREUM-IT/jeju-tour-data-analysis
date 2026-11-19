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
function fixture(t, options = { maxConcurrent: 1, globalLimit: 30, subjectLimit: 6 }) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'jeju-gateway-state-'));
  const filename = path.join(directory, 'private', 'state.sqlite');
  const open = () => createGatewayState({ filename, now: () => START, ...options });
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
    const state = createGatewayState({filename:workerData.filename, now:workerData.at,maxConcurrent:1});
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


test('defaults admit two different owners, but only one queued or executing request per owner', (t) => {
  const { state } = fixture(t, {});
  assert.equal(state.policy.globalLimit, 60);
  assert.equal(state.policy.subjectLimit, 12);
  assert.equal(state.policy.maxConcurrent, 2);
  state.reserve(request(1, 'one'));
  state.reserve(request(2, 'two'));
  assert.deepEqual(state.activeCounts(), { queued: 0, running: 2, uncertain: 0 });
  assert.throws(() => state.reserve(request(3, 'three')), error('BUSY'));
  state.enqueue(request(3, 'three'));
  assert.throws(() => state.enqueue(request(4, 'three')), error('BUSY'));
  assert.throws(() => state.enqueue(request(4, 'one')), error('BUSY'));
  state.finish({ requestId: 'request-1', now: START + 10 });
  state.claim({ requestId: 'request-3', now: START + 10 });
  assert.deepEqual(state.activeCounts({ now: START + 10 }), { queued: 0, running: 2, uncertain: 0 });
});

test('claim is FIFO and consumes the original reservation without charging quota twice', (t) => {
  const { state, filename } = fixture(t, { maxConcurrent: 1, globalLimit: 3, subjectLimit: 3 });
  state.reserve(request(1, 'one'));
  state.enqueue(request(2, 'two'));
  state.enqueue(request(3, 'three'));
  state.finish({ requestId: 'request-1' });
  assert.throws(() => state.claim({ requestId: 'request-3' }), error('BUSY'));
  state.claim({ requestId: 'request-2' });
  assert.equal(rows(filename).length, 3);
  state.finish({ requestId: 'request-2' });
  state.claim({ requestId: 'request-3' });
  state.finish({ requestId: 'request-3' });
  assert.throws(() => state.enqueue(request(4, 'four')), error('RATE_LIMITED'));
});

test('explicit startup abandons queued rows while retaining replay, quota, and uncertain leases', (t) => {
  const { state, filename, reopen } = fixture(t, {});
  state.reserve(request(1, 'one'));
  state.finish({ requestId: 'request-1', uncertain: true, now: START + 1000 });
  state.enqueue(request(2, 'two'));
  const reader = reopen();
  assert.equal(reader.activeCounts().queued, 1, 'opening a state reader must not recover a live queue');
  state.close();
  reader.abandonQueued({ now: START + 2000 });
  assert.deepEqual(reader.activeCounts({ now: START + 2000 }), { queued: 0, running: 0, uncertain: 1 });
  assert.equal(rows(filename).find(row => row.request_id === 'request-2').status, 'abandoned');
  assert.throws(() => reader.enqueue(request(2, 'different', START + 2000)), error('REPLAY_DETECTED'));
  reader.enqueue(request(3, 'two', START + 2000));
});

test('queued expiration, cancellation, zero waiting capacity, and Retry-After are bounded', (t) => {
  const { state } = fixture(t, { maxConcurrent: 1, maxQueue: 1 });
  state.reserve(request(1, 'one'));
  assert.throws(() => state.reserve(request(2, 'two', START + 9000)), value => value.code === 'BUSY' && value.retryAfterSeconds === 111);
  state.enqueue(request(2, 'two'));
  assert.throws(() => state.enqueue(request(3, 'three')), value => value.code === 'QUEUE_FULL');
  state.cancel({ requestId: 'request-2', now: START + 10 });
  state.enqueue(request(3, 'three', START + 10));
  assert.throws(() => state.claim({ requestId: 'request-3', now: START + 600_010 }), value => value.code === 'JOB_EXPIRED');
  assert.deepEqual(state.activeCounts({ now: START + 600_010 }), { queued: 0, running: 0, uncertain: 0 });
  assert.throws(() => state.enqueue(request(3, 'four', START + 600_010)), error('REPLAY_DETECTED'));
});

test('migration from schema one preserves replay hashes and uncertain execution leases', (t) => {
  const { state, filename, reopen } = fixture(t);
  state.reserve(request(1));
  state.finish({ requestId: 'request-1', uncertain: true, now: START + 90_000 });
  state.close();
  const db = new DatabaseSync(filename);
  db.exec(`ALTER TABLE requests RENAME TO requests_new;
    CREATE TABLE requests (nonce_hash TEXT PRIMARY KEY NOT NULL,subject_hash TEXT NOT NULL,
      request_id TEXT UNIQUE NOT NULL,reserved_at INTEGER NOT NULL,lease_until INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('inflight','finished','uncertain'))) STRICT;
    INSERT INTO requests SELECT * FROM requests_new; DROP TABLE requests_new;
    CREATE INDEX requests_subject_time ON requests(subject_hash,reserved_at);
    CREATE INDEX requests_time ON requests(reserved_at); PRAGMA user_version=1;`);
  const before = db.prepare('SELECT * FROM requests').all(); db.close();
  const resumed = reopen();
  assert.deepEqual(rows(filename), before);
  assert.throws(() => resumed.reserve(request(2, 'other', START + 209_999)), error('BUSY'));
  assert.throws(() => resumed.reserve(request(1, 'other', START + 209_999)), error('REPLAY_DETECTED'));
  resumed.reserve(request(2, 'other', START + 210_000));
});

test('deployment capacity and quota configuration are strictly bounded', (t) => {
  const { filename, state } = fixture(t); state.close();
  for (const options of [{ maxConcurrent: 0 }, { maxConcurrent: 5 }, { globalLimit: 241 },
    { subjectLimit: 49 }, { globalLimit: 2, subjectLimit: 3 }, { maxQueue: 9 }, { maxQueue: -1 }, { maxConcurrent: '2' }]) {
    assert.throws(() => createGatewayState({ filename, now: START, ...options }), error('STATE_UNAVAILABLE'));
  }
});


test('queue insertion order stays FIFO when the wall clock moves backwards', (t) => {
  const { state } = fixture(t, { maxConcurrent: 1 });
  state.reserve(request(1, 'one', START));
  state.enqueue(request(2, 'two', START + 2000));
  state.enqueue(request(3, 'three', START + 1000));
  state.finish({ requestId: 'request-1', now: START + 3000 });
  state.claim({ requestId: 'request-2', now: START + 3000 });
  state.finish({ requestId: 'request-2', now: START + 3001 });
  state.claim({ requestId: 'request-3', now: START + 3001 });
});
