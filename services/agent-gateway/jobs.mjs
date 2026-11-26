import { createHash, randomUUID } from 'node:crypto';
import { GatewayStateError } from './state.mjs';

export const JOB_POLICY = Object.freeze({ maxQueue: 8, queueTtlMs: 600_000, lifetimeMs: 720_000,
  resultTtlMs: 300_000, maxJobs: 128, deadlineMs: 105_000, pollAfterSeconds: 2 });
const ID = /^gw-[a-f0-9]{32}$/u;
const TOKEN = /^[\x21-\x7e]{1,128}$/u;
const STATUSES = Object.freeze({ QUEUE_FULL: 429, JOB_EXPIRED: 410, JOB_CANCELLED: 410,
  JOB_NOT_FOUND: 404, JOB_ALREADY_RUNNING: 409, STATE_UNAVAILABLE: 503 });
const SAFE_CODES = new Set(['AUTH_FAILED', 'REPLAY_DETECTED', 'RATE_LIMITED', 'BUSY', 'STATE_UNAVAILABLE',
  'INVALID_RECOMMENDATION_REQUEST', 'FLOW_CHANGED', 'MCP_NOT_READY', 'RECOMMENDATION_BLOCKED',
  'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES', 'SCOPE_CLEANUP_FAILED']);
export class GatewayJobError extends Error {
  constructor(code) { super('Gateway request unavailable.'); this.name = 'GatewayJobError';
    this.code = Object.hasOwn(STATUSES, code) ? code : 'STATE_UNAVAILABLE'; this.status = STATUSES[this.code]; }
}
const fail = code => { throw new GatewayJobError(code); };
const owner = subject => {
  if (typeof subject !== 'string' || !TOKEN.test(subject)) fail('JOB_NOT_FOUND');
  return createHash('sha256').update(subject, 'ascii').digest('hex');
};
function errorResult(error) {
  const timeout = ['TimeoutError', 'AbortError'].includes(error?.name);
  const code = timeout ? 'UPSTREAM_TIMEOUT_NO_RETRY' : SAFE_CODES.has(error?.code) ? error.code : 'GATEWAY_UNAVAILABLE';
  // Cloudflare replaces the body of an origin 502 or 504 with its own error, so a code sent with 502 never
  // reaches the client; upstream failures are therefore 500. Timeouts stay 504 because the web app treats
  // that status itself as "do not resubmit".
  const statusCode = timeout ? 504 : Number.isInteger(error?.status) && error.status >= 400 && error.status <= 599 ? error.status : 500;
  return { statusCode, body: { error: { code } } };
}

/** One scheduler owns a SQLite database. Requests/results are RAM-only and are never resumed after restart. */
export function createJobQueue({ state, upstream, now = Date.now, deadlineMs = JOB_POLICY.deadlineMs,
  maxQueue = state?.policy?.maxQueue ?? JOB_POLICY.maxQueue, setTimer = setTimeout, clearTimer = clearTimeout }) {
  if (!state?.policy || typeof upstream?.execute !== 'function' || typeof now !== 'function'
    || !Number.isInteger(deadlineMs) || deadlineMs < 1 || deadlineMs > JOB_POLICY.deadlineMs
    || !Number.isInteger(maxQueue) || maxQueue < 0 || maxQueue > 8 || maxQueue !== state.policy.maxQueue) fail();
  const jobs = new Map(), workers = new Set();
  let closed = false, timer, draining = false, running = 0, closing;
  const clock = () => {
    const value = now();
    if (!Number.isSafeInteger(value) || value < 0 || value > 8_640_000_000_000_000 - JOB_POLICY.lifetimeMs) fail();
    return value;
  };
  // This is an explicit startup recovery action, not a side effect of a state reader/constructor.
  // Previously queued rows retain their quota/replay evidence but have no recoverable raw input.
  state.abandonQueued({ now: clock() });

  function terminal(job, result, at) {
    job.status = 'terminal'; job.result = result; job.finishedAt = at;
    delete job.input;
  }
  function clean(at) {
    for (const [id, job] of jobs) {
      if (job.status === 'terminal' && at >= job.finishedAt + JOB_POLICY.resultTtlMs) jobs.delete(id);
      else if (job.status === 'queued' && at >= job.createdAt + JOB_POLICY.queueTtlMs) {
        // activeCounts atomically marks all expired durable queued reservations.
        state.activeCounts({ now: at });
        terminal(job, { statusCode: 410, body: { error: { code: 'JOB_EXPIRED' } } }, at);
      }
    }
  }
  function pending(job) {
    const waiting = [...jobs.values()].filter(item => item.status === 'queued');
    return { statusCode: 202, body: { schemaVersion: '1.0', status: job.status, requestId: job.requestId,
      position: job.status === 'running' ? 0 : waiting.indexOf(job) + 1,
      pollAfterSeconds: 2, expiresAt: new Date(job.expiresAt).toISOString(), productionReady: false } };
  }
  function schedule() {
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
    if (closed || !jobs.size) return;
    const at = clock();
    const queued = [...jobs.values()].some(job => job.status === 'queued');
    const finished = [...jobs.values()].filter(job => job.status === 'terminal').map(job => job.finishedAt + JOB_POLICY.resultTtlMs);
    if (!queued && !finished.length) return;
    const wait = queued ? 1000 : Math.max(1, Math.min(...finished) - at);
    timer = setTimer(() => {
      timer = undefined;
      try { drain(); } catch { schedule(); } // A locked/unavailable state never permits execution.
    }, wait);
    timer?.unref?.();
  }
  function start(job) {
    job.status = 'running'; running += 1;
    const controller = new AbortController();
    const absoluteDeadline = Math.min(clock() + deadlineMs, job.expiresAt);
    const deadlineTimer = setTimer(() => controller.abort(new DOMException('Execution deadline reached.', 'TimeoutError')), Math.max(1, absoluteDeadline - clock()));
    deadlineTimer?.unref?.();
    let submitted = false, settled = false;
    const worker = Promise.resolve().then(async () => {
      let result;
      try {
        const value = await upstream.execute(job.input, { subjectHash: job.ownerHash, deadlineMs: absoluteDeadline,
          signal: controller.signal, onSubmitted: () => { submitted = true; },
          onSettled: () => { if (submitted) settled = true; } });
        // A successful execute has necessarily finished its upstream validation/cleanup.
        settled = true;
        if (controller.signal.aborted) throw controller.signal.reason;
        result = { statusCode: 200, body: value };
      } catch (error) { result = errorResult(error); }
      finally { clearTimer(deadlineTimer); }
      try { state.finish({ requestId: job.requestId, now: clock(), uncertain: submitted && !settled }); }
      catch { result = { statusCode: 503, body: { error: { code: 'STATE_UNAVAILABLE' } } }; }
      terminal(job, result, clock());
    }).finally(() => {
      workers.delete(worker); running -= 1;
      if (!closed) { try { drain(); } catch { schedule(); } }
    });
    workers.add(worker);
  }
  function drain() {
    if (closed || draining) return;
    draining = true;
    try {
      clean(clock());
      for (const job of jobs.values()) {
        if (job.status !== 'queued') continue;
        if (running >= state.policy.maxConcurrent) break;
        try { state.claim({ requestId: job.requestId, now: clock() }); }
        catch (error) {
          if (error?.code === 'BUSY' || error?.code === 'STATE_UNAVAILABLE') break;
          if (error?.code === 'JOB_EXPIRED') { terminal(job, { statusCode: 410, body: { error: { code: 'JOB_EXPIRED' } } }, clock()); continue; }
          terminal(job, { statusCode: 503, body: { error: { code: 'STATE_UNAVAILABLE' } } }, clock());
          continue;
        }
        start(job);
      }
    } finally { draining = false; schedule(); }
  }
  function find({ subject, requestId }) {
    const ownerHash = owner(subject);
    if (typeof requestId !== 'string' || !ID.test(requestId)) fail('JOB_NOT_FOUND');
    clean(clock());
    const job = jobs.get(requestId);
    if (!job || job.ownerHash !== ownerHash) fail('JOB_NOT_FOUND');
    return job;
  }
  return {
    submit({ subject, nonce, input, requestId = 'gw-' + randomUUID().replaceAll('-', '') }) {
      if (closed) fail();
      const ownerHash = owner(subject), at = clock();
      if (typeof nonce !== 'string' || !/^[a-f0-9]{32}$/u.test(nonce)) fail();
      const nonceHash = createHash('sha256').update(nonce, 'ascii').digest('hex');
      if (!ID.test(requestId) || !input || typeof input !== 'object' || Array.isArray(input)) fail();
      let copied;
      try {
        const text = JSON.stringify(input);
        if (Buffer.byteLength(text) > 32 * 1024) fail();
        copied = JSON.parse(text);
      } catch { fail(); }
      clean(at);
      if (jobs.has(requestId) || [...jobs.values()].some(job => job.nonceHash === nonceHash)) {
        throw new GatewayStateError('REPLAY_DETECTED');
      }
      // An uncooperative execution cannot admit another job for the same owner merely
      // because its durable lease elapsed; the in-process worker must also finish.
      if ([...jobs.values()].some(job => job.ownerHash === ownerHash && ['queued', 'running'].includes(job.status))) {
        throw new GatewayStateError('BUSY', 120);
      }
      if (jobs.size >= JOB_POLICY.maxJobs) fail('QUEUE_FULL');
      const waiting = [...jobs.values()].filter(job => job.status === 'queued').length;
      // The database independently enforces both capacity and per-owner admission atomically.
      if (maxQueue > 0 && waiting >= maxQueue) fail('QUEUE_FULL');
      if (maxQueue === 0) state.reserve({ subject, nonce, requestId, now: at });
      else state.enqueue({ subject, nonce, requestId, now: at });
      const job = { requestId, ownerHash, nonceHash, input: { ...copied, requestId }, status: 'queued', createdAt: at,
        expiresAt: at + JOB_POLICY.lifetimeMs };
      jobs.set(requestId, job);
      if (maxQueue === 0) start(job);
      else drain();
      return pending(job);
    },
    get(options) {
      const job = find(options);
      return structuredClone(job.status === 'terminal' ? job.result : pending(job));
    },
    cancel(options) {
      const job = find(options);
      if (job.status === 'terminal') {
        if (job.result.body?.error?.code === 'JOB_CANCELLED') return { statusCode: 200, body: { status: 'cancelled' } };
        fail('JOB_ALREADY_RUNNING');
      }
      if (job.status !== 'queued') fail('JOB_ALREADY_RUNNING');
      state.cancel({ requestId: job.requestId, now: clock() });
      terminal(job, { statusCode: 410, body: { error: { code: 'JOB_CANCELLED' } } }, clock());
      drain();
      return { statusCode: 200, body: { status: 'cancelled' } };
    },
    ready() {
      clean(clock());
      const counts = state.activeCounts({ now: clock() });
      // Do not advertise idle while an in-process worker has not returned, even
      // if a broken transport ignored its deadline and outlived the durable lease.
      return { ...counts, running: Math.max(counts.running, running) };
    },
    close() {
      if (closing) return closing;
      closed = true;
      if (timer !== undefined) clearTimer(timer);
      timer = undefined;
      // Shutdown never aborts a submitted model; its original worker/deadline still owns completion.
      let shutdownError;
      try { state.abandonQueued({ now: clock() }); } catch (error) { shutdownError = error; }
      for (const job of jobs.values()) if (job.status === 'queued') {
        terminal(job, { statusCode: 410, body: { error: { code: 'JOB_CANCELLED' } } }, clock());
      }
      closing = Promise.allSettled([...workers]).then(() => { if (shutdownError) throw shutdownError; });
      return closing;
    },
  };
}
