// Overtourism diagnosis for the Gateway: one synchronous run per app snapshot.
// The MCP computes the indicators from the snapshot and owns the validated
// result; the pinned Langflow flow only runs the Agent between MCP prepare and
// MCP validation. Results are reused per identical snapshot (RAM only), runs are
// limited per KST day, and every new run takes a normal state reservation
// (replay, hourly limits, shared concurrency, uncertain-run protection).
import { randomUUID } from 'node:crypto';
import { boundedJson, digest, UpstreamError } from './upstream.mjs';

export const OVERTOURISM_FINAL_OUTPUT = 'ChatOutput-jejuOvertourism';
export const OVERTOURISM_VALIDATOR_ID = 'JejuOvertourismValidation-v1';
export const OVERTOURISM_POLICY_VERSION = 'overtourism-diagnosis-v1';
export const OVERTOURISM_MODEL = 'gpt-5.4-mini';
export const OVERTOURISM_POLICY = Object.freeze({ dailyLimit: 8, resultTtlMs: 12 * 3_600_000, maxResults: 16,
  flowTimeoutMs: 95_000, toolCallLimit: 6 });
const DIAGNOSIS_ID = /^ot-[a-f0-9]{32}$/u;
const HEX = /^[a-f0-9]{64}$/u;
const KEYS = ['schemaVersion', 'today', 'dataVersion', 'crowd', 'regions', 'sources'];
const require = (ok, code, status) => { if (!ok) throw new UpstreamError(code, status); };
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Cheap envelope check; the MCP parses every field strictly. */
export function overtourismInputShape(value) {
  return record(value) && Object.keys(value).length === KEYS.length && KEYS.every(key => Object.hasOwn(value, key))
    && value.schemaVersion === '1.0';
}

export function createOvertourismUpstream({ flowId, flowSha256, langflowApiKey, mcpToken, controlToken,
  langflowUrl = 'http://langflow:7860', mcpUrl = 'http://jeju-mcp:8788', fetchImpl = fetch }) {
  require(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/u.test(flowId) && HEX.test(flowSha256)
    && /^sk-[A-Za-z0-9_-]{10,256}$/u.test(langflowApiKey) && /^[a-f0-9]{64}$/u.test(controlToken)
    && /^[\x21-\x7e]{32,256}$/u.test(mcpToken) && controlToken !== mcpToken, 'CONFIG_INVALID', 503);
  for (const value of [langflowUrl, mcpUrl]) {
    const url = new URL(value);
    require(url.protocol === 'http:' && !url.username && !url.password && url.pathname === '/'
      && !url.search && !url.hash && ['langflow', 'jeju-mcp', '127.0.0.1', 'localhost'].includes(url.hostname), 'CONFIG_INVALID', 503);
  }
  const controlAuth = { Authorization: `Bearer ${controlToken}` };
  async function call(base, route, { method = 'GET', headers, body, signal, timeout = 5000, terminal = false } = {}) {
    const response = await fetchImpl(base + route, { method, redirect: 'error',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
      headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!response.ok || (terminal && response.status !== 200)) {
      await response.body?.cancel();
      if (base === mcpUrl && response.status === 400) throw new UpstreamError('INVALID_OVERTOURISM_REQUEST', 400);
      if (base === mcpUrl && response.status === 429) throw new UpstreamError('BUSY', 429);
      throw new UpstreamError('UPSTREAM_REJECTED');
    }
    return boundedJson(response);
  }
  async function ready(signal) {
    const [flow, mcp] = await Promise.all([
      call(langflowUrl, `/api/v1/flows/${flowId}`, { headers: { 'x-api-key': langflowApiKey }, signal }),
      call(mcpUrl, '/gateway/overtourism/readyz', { headers: controlAuth, signal }),
    ]);
    require(flow.id === flowId && digest(flow.data) === flowSha256, 'OVERTOURISM_FLOW_CHANGED', 503);
    require(mcp.ready === true && mcp.policyVersion === OVERTOURISM_POLICY_VERSION
      && mcp.toolCallLimit === OVERTOURISM_POLICY.toolCallLimit && mcp.productionReady === false, 'OVERTOURISM_MCP_NOT_READY', 503);
    return { ready: true, flowVerified: true, policyVersion: OVERTOURISM_POLICY_VERSION, model: OVERTOURISM_MODEL, productionReady: false };
  }
  function finalMessage(raw, diagnosisId) {
    require(raw?.session_id === diagnosisId, 'SESSION_MISMATCH');
    require(Array.isArray(raw.outputs) && raw.outputs.length === 1 && Array.isArray(raw.outputs[0]?.outputs), 'OUTPUT_MISMATCH');
    const selected = raw.outputs[0].outputs.filter(item => item.component_id === OVERTOURISM_FINAL_OUTPUT);
    require(selected.length === 1, 'OUTPUT_MISMATCH');
    const message = selected[0]?.results?.message;
    require(typeof message?.text === 'string' && Buffer.byteLength(message.text) <= 16 * 1024, 'FINAL_INVALID');
    return message;
  }
  async function execute(input, { deadlineMs, signal, onSubmitted = () => {}, onSettled = () => {} }) {
    const diagnosisId = 'ot-' + randomUUID().replaceAll('-', '');
    let registered = false, primaryError;
    try {
      await ready(signal);
      const prepared = await call(mcpUrl, '/gateway/overtourism/scopes', { method: 'POST', headers: controlAuth,
        body: { diagnosisId, input, deadlineMs }, signal });
      registered = true;
      require(prepared?.status === 'prepared' && prepared.diagnosisId === diagnosisId && prepared.today === input.today
        && prepared.policyVersion === OVERTOURISM_POLICY_VERSION && HEX.test(prepared.indicatorsSha256), 'OVERTOURISM_PREPARATION_MISMATCH');
      onSubmitted();
      const raw = await call(langflowUrl, `/api/v1/run/${flowId}?stream=false`, {
        method: 'POST', headers: { 'x-api-key': langflowApiKey }, signal, timeout: OVERTOURISM_POLICY.flowTimeoutMs, terminal: true,
        body: { input_value: JSON.stringify({ diagnosisId }), input_type: 'chat', output_type: 'chat',
          output_component: OVERTOURISM_FINAL_OUTPUT, session_id: diagnosisId },
      });
      // Only a complete HTTP 200 body naming this run and the expected output
      // ends the execution lease as settled; anything else stays uncertain.
      const message = finalMessage(raw, diagnosisId);
      onSettled();
      require(message.sender === 'Machine' && message.properties?.source?.id === OVERTOURISM_VALIDATOR_ID
        && message.properties?.source?.source === 'Jeju Overtourism Validation', 'VALIDATOR_SOURCE_MISMATCH');
      let final;
      try { final = JSON.parse(message.text); } catch { throw new UpstreamError('FINAL_INVALID'); }
      require(final?.status !== 'blocked', 'DIAGNOSIS_BLOCKED', 422);
      require(final?.status === 'accepted' && final.diagnosisId === diagnosisId
        && final.indicatorsSha256 === prepared.indicatorsSha256 && HEX.test(final.diagnosisSha256), 'FINAL_INVALID');
      // The flow reports only digests; the result itself comes from the MCP scope.
      const result = await call(mcpUrl, '/gateway/overtourism/result', { method: 'POST', headers: controlAuth,
        body: { diagnosisId }, signal });
      require(result?.status === 'validated' && result.diagnosisId === diagnosisId
        && result.policyVersion === OVERTOURISM_POLICY_VERSION
        && result.indicatorsSha256 === prepared.indicatorsSha256 && result.diagnosisSha256 === final.diagnosisSha256
        && digest(result.indicators) === result.indicatorsSha256 && digest(result.diagnosis) === result.diagnosisSha256
        && Array.isArray(result.diagnosis?.regions) && result.diagnosis.regions.length === 14, 'OVERTOURISM_RESULT_MISMATCH', 502);
      return { schemaVersion: '1.0', status: 'accepted', diagnosisId, policyVersion: OVERTOURISM_POLICY_VERSION,
        model: OVERTOURISM_MODEL, validatedAt: result.validatedAt, toolCalls: result.toolCalls,
        indicatorsSha256: result.indicatorsSha256, diagnosisSha256: result.diagnosisSha256,
        indicators: result.indicators, diagnosis: result.diagnosis, productionReady: false };
    } catch (error) {
      primaryError = error;
      throw error;
    } finally {
      if (registered) {
        try {
          await call(mcpUrl, `/gateway/overtourism/scopes/${diagnosisId}`, { method: 'DELETE', headers: controlAuth, timeout: 3000 });
        } catch {
          if (!primaryError) throw new UpstreamError('SCOPE_CLEANUP_FAILED', 503);
        }
      }
    }
  }
  return { ready, execute };
}

const kstDay = at => new Date(at + 9 * 3_600_000).toISOString().slice(0, 10);
const untilKstMidnight = at => {
  const day = Date.parse(`${kstDay(at)}T00:00:00Z`) - 9 * 3_600_000;
  return Math.max(1, Math.ceil((day + 86_400_000 - at) / 1000));
};

/**
 * One diagnosis per identical snapshot: a cached result or a run already in
 * progress is shared; a new run needs the daily budget and a state reservation.
 */
export function createOvertourismService({ state, upstream, now = Date.now, dailyLimit = OVERTOURISM_POLICY.dailyLimit }) {
  require(state && typeof state.reserve === 'function' && Number.isInteger(dailyLimit) && dailyLimit >= 1 && dailyLimit <= 48,
    'CONFIG_INVALID', 503);
  const results = new Map(), running = new Map();
  const runs = { day: '', count: 0 };
  const prune = at => {
    for (const [key, entry] of results) if (at >= entry.at + OVERTOURISM_POLICY.resultTtlMs) results.delete(key);
    while (results.size > OVERTOURISM_POLICY.maxResults) results.delete(results.keys().next().value);
  };
  async function diagnose({ subject, nonce, input, deadlineMs, signal }) {
    require(overtourismInputShape(input), 'INVALID_OVERTOURISM_REQUEST', 400);
    const key = digest(input);
    const at = now();
    prune(at);
    const cached = results.get(key);
    if (cached) return { ...cached.value, reused: true };
    const shared = running.get(key);
    if (shared) return { ...(await shared), reused: true };
    require(upstream !== null && upstream !== undefined, 'OVERTOURISM_UNAVAILABLE', 503);
    const day = kstDay(at);
    if (runs.day !== day) { runs.day = day; runs.count = 0; }
    if (runs.count >= dailyLimit) {
      const error = new UpstreamError('RATE_LIMITED', 429);
      error.retryAfterSeconds = untilKstMidnight(at);
      throw error;
    }
    const requestId = 'gw-' + randomUUID().replaceAll('-', '');
    state.reserve({ subject, nonce, requestId, now: at });
    runs.count += 1;
    let submitted = false, settled = false;
    const run = (async () => {
      try {
        const value = await upstream.execute(input, { deadlineMs, signal,
          onSubmitted: () => { submitted = true; }, onSettled: () => { settled = true; } });
        state.finish({ requestId, now: now() });
        results.set(key, { at: now(), value });
        prune(now());
        return value;
      } catch (error) {
        try { state.finish({ requestId, now: now(), uncertain: submitted && !settled }); }
        catch { throw new UpstreamError('STATE_UNAVAILABLE', 503); }
        throw error;
      }
    })();
    running.set(key, run);
    try { return { ...(await run), reused: false }; } finally { running.delete(key); }
  }
  async function ready(signal) {
    require(upstream !== null && upstream !== undefined, 'OVERTOURISM_UNAVAILABLE', 503);
    const at = now();
    const used = runs.day === kstDay(at) ? runs.count : 0;
    return { ...await upstream.ready(signal), dailyLimit, runsToday: used, cachedResults: results.size };
  }
  return Object.freeze({ diagnose, ready });
}
