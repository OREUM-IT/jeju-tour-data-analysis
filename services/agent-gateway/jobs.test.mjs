import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { createGatewayState } from './state.mjs';
import { createJobQueue, JOB_POLICY } from './jobs.mjs';
import { UpstreamError } from './upstream.mjs';

const START = 1_795_046_400_000;
const id = n => 'gw-' + n.toString(16).padStart(32, '0');
const nonce = n => n.toString(16).padStart(32, '0');
const input = () => ({ schemaVersion: '1.0', query: 'private trip conditions', timezone: 'Asia/Seoul',
  filters: {}, excludedIds: [], requirements: [] });
const tick = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
function fixture(t, { policy = {}, execute, deadlineMs } = {}) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'jeju-gateway-jobs-'));
  const filename = path.join(directory, 'state.sqlite');
  let at = START, counter = 0;
  const timers = new Map(), calls = [];
  const now = () => at;
  const state = createGatewayState({ filename, now, ...policy });
  const upstream = { execute: execute || ((request, context) => {
    context.onSubmitted();
    return new Promise((resolve, reject) => calls.push({ request, context,
      resolve(value = { status: 'accepted', requestId: request.requestId }) { context.onSettled(); resolve(value); }, reject }));
  }) };
  const options = { state, upstream, now, ...(deadlineMs ? { deadlineMs } : {}),
    setTimer(callback, delay) { const key = ++counter; timers.set(key, { callback, when: at + delay }); return key; },
    clearTimer(key) { timers.delete(key); } };
  const jobs = createJobQueue(options);
  t.after(async () => {
    const closing = jobs.close().catch(() => {});
    for (const call of calls) call.resolve();
    await tick();
    await closing;
    state.close(); rmSync(directory, { recursive: true, force: true });
  });
  return { jobs, state, filename, calls, options, timers, now,
    submit(number, subject = `owner-${number}`, value = input()) {
      return jobs.submit({ subject, nonce: nonce(number), requestId: id(number), input: value });
    },
    async advance(ms) {
      at += ms;
      // Run each timer that existed at this advance at most once; new wakeups use the new clock.
      for (const [key, timer] of [...timers]) if (timer.when <= at && timers.has(key)) {
        timers.delete(key); timer.callback();
      }
      await tick();
    },
  };
}
const query = n => ({ subject: `owner-${n}`, requestId: id(n) });
const error = code => value => value.code === code && value.message === 'Gateway request unavailable.';

test('two requests execute once in parallel and later owners dispatch in FIFO order', async t => {
  const f = fixture(t);
  assert.equal(f.submit(1).body.status, 'running');
  assert.equal(f.submit(2).body.status, 'running');
  assert.deepEqual(f.submit(3).body, { schemaVersion: '1.0', status: 'queued', requestId: id(3), position: 1,
    pollAfterSeconds: 2, expiresAt: new Date(START + 720_000).toISOString(), productionReady: false });
  assert.equal(f.submit(4).body.position, 2);
  await tick();
  assert.deepEqual(f.calls.map(x => x.request.requestId), [id(1), id(2)]);
  for (let i = 0; i < 5; i += 1) assert.equal(f.jobs.get(query(3)).body.status, 'queued');
  f.calls[1].resolve(); await tick();
  assert.deepEqual(f.calls.map(x => x.request.requestId), [id(1), id(2), id(3)]);
  assert.equal(f.jobs.get(query(4)).body.position, 1);
  f.calls[0].resolve(); await tick();
  assert.deepEqual(f.calls.map(x => x.request.requestId), [id(1), id(2), id(3), id(4)]);
  assert.deepEqual(f.jobs.ready(), { queued: 0, running: 2, uncertain: 0 });
});

test('owner reads are indistinguishable from unknown IDs, same-owner duplicates and nonce replay never execute', async t => {
  const f = fixture(t);
  f.submit(1); f.submit(2);
  assert.throws(() => f.submit(1), error('REPLAY_DETECTED'));
  assert.throws(() => f.submit(3, 'owner-1'), error('BUSY'));
  assert.throws(() => f.jobs.submit({ subject: 'other', nonce: nonce(1), requestId: id(4), input: input() }), error('REPLAY_DETECTED'));
  for (const method of ['get', 'cancel']) {
    assert.throws(() => f.jobs[method]({ subject: 'different', requestId: id(1) }), error('JOB_NOT_FOUND'));
    assert.throws(() => f.jobs[method]({ subject: 'owner-1', requestId: id(999) }), error('JOB_NOT_FOUND'));
  }
  await tick(); assert.equal(f.calls.length, 2);
  assert.throws(() => f.jobs.cancel(query(1)), error('JOB_ALREADY_RUNNING'));
});

test('queued cancellation preserves replay and quota, clears ownership slot, and never calls upstream', async t => {
  const f = fixture(t); f.submit(1); f.submit(2); f.submit(3);
  assert.deepEqual(f.jobs.cancel(query(3)), { statusCode: 200, body: { status: 'cancelled' } });
  assert.deepEqual(f.jobs.cancel(query(3)), { statusCode: 200, body: { status: 'cancelled' } });
  assert.equal(f.jobs.get(query(3)).body.error.code, 'JOB_CANCELLED');
  assert.throws(() => f.submit(3), error('REPLAY_DETECTED'));
  f.submit(4, 'owner-3'); await tick();
  f.calls[0].resolve(); await tick();
  assert.deepEqual(f.calls.map(x => x.request.requestId), [id(1), id(2), id(4)]);
});

test('bounded waiting queue rejects without starting a model and maxQueue zero permits only immediate execution', async t => {
  const f = fixture(t);
  for (let n = 1; n <= 10; n += 1) f.submit(n);
  assert.equal(f.jobs.get(query(10)).body.position, 8);
  assert.throws(() => f.submit(11), error('QUEUE_FULL'));
  await tick(); assert.equal(f.calls.length, 2);
  const g = fixture(t, { policy: { maxQueue: 0 } });
  g.submit(1); g.submit(2);
  assert.throws(() => g.submit(3), error('BUSY'));
  assert.equal(g.jobs.ready().queued, 0);
});

test('recognized terminal validation failure releases a slot immediately; uncertain failure holds a durable lease', async t => {
  const f = fixture(t, { policy: { maxConcurrent: 1 } });
  f.submit(1); f.submit(2); await tick();
  f.calls[0].context.onSettled();
  f.calls[0].reject(Object.assign(new Error('raw upstream secret'), { code: 'FINAL_VALIDATION_REJECTED', status: 422 }));
  await tick();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.jobs.get(query(1)), { statusCode: 422, body: { error: { code: 'FINAL_VALIDATION_REJECTED' } } });
  f.submit(3);
  f.calls[1].reject(Object.assign(new Error('private provider body'), { name: 'TimeoutError' }));
  await tick();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.jobs.ready(), { queued: 1, running: 0, uncertain: 1 });
  assert.equal(f.jobs.get(query(2)).body.error.code, 'UPSTREAM_TIMEOUT_NO_RETRY');
  await f.advance(119_999); assert.equal(f.calls.length, 2);
  await f.advance(1001); assert.equal(f.calls.length, 3, 'timer wakes the queue after the persistent lease expires');
});

test('failure before submission releases capacity, and polling never exposes arbitrary exception strings', async t => {
  let executions = 0;
  const f = fixture(t, { policy: { maxConcurrent: 1 }, execute: async () => {
    executions += 1; throw new Error('credential=secret-and-private-prompt');
  } });
  f.submit(1); f.submit(2); await tick();
  assert.equal(executions, 2);
  assert.deepEqual(f.jobs.ready(), { queued: 0, running: 0, uncertain: 0 });
  assert.deepEqual(f.jobs.get(query(1)), { statusCode: 500, body: { error: { code: 'GATEWAY_UNAVAILABLE' } } });
});

test('a failed upstream run is stored as 500 GATEWAY_UNAVAILABLE, never 502, and a timeout stays 504', async t => {
  const f = fixture(t);
  f.submit(1); f.submit(2); await tick();
  // What the upstream client throws when Langflow answers the run with a non-200 status (e.g. an Agent error).
  f.calls[0].reject(new UpstreamError('UPSTREAM_REJECTED'));
  f.calls[1].reject(new DOMException('canary-private-timeout', 'TimeoutError'));
  await tick();
  assert.deepEqual(f.jobs.get(query(1)), { statusCode: 500, body: { error: { code: 'GATEWAY_UNAVAILABLE' } } });
  assert.deepEqual(f.jobs.get(query(2)), { statusCode: 504, body: { error: { code: 'UPSTREAM_TIMEOUT_NO_RETRY' } } });
});

test('queued expiry does not execute or release an unrelated running worker', async t => {
  const f = fixture(t, { policy: { maxConcurrent: 1 } });
  f.submit(1); f.submit(2); await tick();
  await f.advance(600_000);
  assert.deepEqual(f.jobs.get(query(2)), { statusCode: 410, body: { error: { code: 'JOB_EXPIRED' } } });
  f.calls[0].resolve(); await tick();
  assert.equal(f.calls.length, 1);
  await f.advance(300_000);
  assert.throws(() => f.jobs.get(query(2)), error('JOB_NOT_FOUND'));
});

test('restart abandons unrecoverable queued payloads but preserves replay and pre-crash leases', async t => {
  const f = fixture(t, { policy: { maxConcurrent: 1 } });
  f.submit(1); f.submit(2); await tick();
  const restarted = createJobQueue(f.options);
  assert.throws(() => restarted.get(query(1)), error('JOB_NOT_FOUND'));
  assert.throws(() => restarted.get(query(2)), error('JOB_NOT_FOUND'));
  assert.deepEqual(restarted.ready(), { queued: 0, running: 1, uncertain: 0 });
  assert.throws(() => restarted.submit({ subject: 'owner-2', nonce: nonce(2), requestId: id(2), input: input() }), error('REPLAY_DETECTED'));
  const db = new DatabaseSync(f.filename, { readOnly: true });
  const rows = db.prepare('SELECT * FROM requests').all(); db.close();
  assert.equal(rows.find(row => row.request_id === id(2)).status, 'abandoned');
  assert.equal(JSON.stringify(rows).includes('private trip conditions'), false);
  assert.equal(JSON.stringify(rows).includes('owner-2'), false);
  await restarted.close();
});

test('results are isolated copies and expire after five minutes with a bounded total memory count', async t => {
  let executions = 0;
  const f = fixture(t, { policy: { globalLimit: 240, subjectLimit: 48 }, execute: async request => {
    executions += 1; return { status: 'accepted', requestId: request.requestId, response: { placeIds: ['registered'] } };
  } });
  const original = input(); f.submit(1, 'owner-1', original); original.query = 'changed after submission'; await tick();
  const result = f.jobs.get(query(1)); result.body.response.placeIds.push('tampered');
  assert.deepEqual(f.jobs.get(query(1)).body.response.placeIds, ['registered']);
  for (let n = 2; n <= JOB_POLICY.maxJobs; n += 1) { f.submit(n); await tick(); }
  assert.equal(executions, 128);
  assert.throws(() => f.submit(129), error('QUEUE_FULL'));
  await f.advance(300_000);
  assert.throws(() => f.jobs.get(query(1)), error('JOB_NOT_FOUND'));
  f.submit(129); await tick(); assert.equal(executions, 129);
});

test('shutdown stops queued dispatch, waits for the active worker, and never aborts or retries it', async t => {
  const f = fixture(t, { policy: { maxConcurrent: 1 } });
  f.submit(1); f.submit(2); await tick();
  let closed = false;
  const done = f.jobs.close().then(() => { closed = true; });
  await tick(); assert.equal(closed, false);
  assert.equal(f.calls[0].context.signal.aborted, false);
  f.calls[0].resolve(); await done;
  assert.equal(f.calls.length, 1);
  assert.throws(() => f.submit(3), error('STATE_UNAVAILABLE'));
  await f.jobs.close();
});

test('shutdown remains asynchronous and idempotent when persistent state is unavailable', async t => {
  const f = fixture(t);
  f.state.close();
  let completion;
  assert.doesNotThrow(() => { completion = f.jobs.close(); });
  await assert.rejects(completion, error('STATE_UNAVAILABLE'));
  assert.equal(f.jobs.close(), completion);
});

test('a failed claim cannot expose queue positions beyond eight or start an unreserved worker', async t => {
  const f = fixture(t);
  const originalClaim = f.state.claim.bind(f.state);
  f.state.claim = () => { throw Object.assign(new Error('locked'), { code: 'STATE_UNAVAILABLE' }); };
  for (let n = 1; n <= 8; n += 1) assert.equal(f.submit(n).body.position, n);
  assert.throws(() => f.submit(9), error('QUEUE_FULL'));
  await tick(); assert.equal(f.calls.length, 0);
  f.state.claim = originalClaim;
  await f.advance(1000);
  assert.equal(f.calls.length, 2);
});

test('an in-process worker that ignores abort still blocks another request for the same owner', async t => {
  const f = fixture(t);
  f.submit(1); await tick();
  await f.advance(121_000);
  assert.equal(f.calls[0].context.signal.aborted, true);
  assert.equal(f.jobs.ready().running, 1);
  assert.throws(() => f.submit(2, 'owner-1'), error('BUSY'));
  assert.equal(f.calls.length, 1);
});
