// Explicit live collection only. No model-selected URLs, retries, provider switching, or automatic answer repair.
// Return values contain third-party output and belong in .local; never print raw captures or headers.
import { randomUUID } from 'node:crypto';
import { canonicalSha256 } from './evaluation-rng.mjs';
import { buildGeminiRequest, buildLangflowRequest } from './baseline-request-adapters.mjs';
import { validateValidatedLangflowResponse } from './baseline-adapters.mjs';
import { prepareValidatedCollection, buildBoundValidatedRequest, validateValidatedCapture, VALIDATED_BASELINE_ADAPTER_VERSION } from './validated-capture-binding.mjs';

export const BASELINE_RUNNER_VERSION = '1.0.0';
const MAX_CASES = 192;
const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
const SHA256 = /^[a-f0-9]{64}$/u;
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const ensure = (condition, message) => { if (!condition) throw new Error(`Invalid baseline collection: ${message}`); };
const nonempty = (value) => typeof value === 'string' && value.trim().length > 0 && value.length <= 240 && !/[\p{Cc}\p{Cf}]/u.test(value);
const ownHash = (value, key) => canonicalSha256(Object.fromEntries(Object.entries(value).filter(([name]) => name !== key)));

class CollectionFailure extends Error {
  constructor(code, reason, extra = {}) {
    super(reason);
    this.code = code;
    this.reason = reason;
    Object.assign(this, extra);
  }
}

/** The caller explicitly selects a known API endpoint. Queries and model outputs never select a destination. */
function endpointUrl(endpoint, systemId) {
  let url;
  try { url = new URL(endpoint); } catch { throw new Error('Invalid baseline collection: endpoint must be an absolute URL'); }
  ensure(!url.username && !url.password && !url.hash, 'endpoint cannot contain credentials or a fragment');
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  ensure(url.protocol === 'https:' || (url.protocol === 'http:' && loopback), 'endpoint needs HTTPS except explicit loopback HTTP');
  if (systemId === 'gemini') ensure(url.pathname === '/api/assistant' && !url.search, 'Gemini endpoint must be /api/assistant without a query');
  else ensure(/^\/api\/v1\/run\/[A-Za-z0-9_-]{1,120}$/u.test(url.pathname) && url.search === '?stream=false',
    'Langflow endpoint must be /api/v1/run/<flowId>?stream=false');
  return url;
}

function requestHeaders(headers) {
  ensure(isRecord(headers), 'headers must be a record');
  const selected = { 'content-type': 'application/json', accept: 'application/json' };
  const names = new Set();
  for (const [name, value] of Object.entries(headers)) {
    const normalized = name.toLowerCase();
    ensure(['authorization', 'x-api-key', 'x-vercel-protection-bypass'].includes(normalized) && !names.has(normalized), 'unsupported or duplicated credential header');
    ensure(typeof value === 'string' && value.length > 0 && value.length <= 16_384 && !/[\r\n]/u.test(value), 'credential header has an invalid value');
    names.add(normalized);
    selected[normalized] = value;
  }
  return selected;
}

function validateOptions({ plan, system, maxCases, timeoutMs, maxResponseBytes, maxTotalResponseBytes, stopOnError, continueOnModelMismatch, signal, fetchImpl, onProgress, onCheckpoint, outputSelector }) {
  ensure(isRecord(plan) && plan.kind === 'jeju-baseline-plan' && plan.schemaVersion === '1.0'
    && SHA256.test(plan.planSha256 ?? '') && ownHash(plan, 'planSha256') === plan.planSha256, 'a hash-verified baseline plan is required');
  ensure(Array.isArray(plan.cases) && plan.cases.length > 0 && plan.cases.length <= MAX_CASES
    && new Set(plan.cases.map((entry) => entry.caseId)).size === plan.cases.length, 'plan cases must be unique and bounded');
  for (const entry of plan.cases) {
    ensure(isRecord(entry) && nonempty(entry.caseId) && isRecord(entry.request)
      && Object.keys(entry.request).length === 2 && Object.hasOwn(entry.request, 'query') && Object.hasOwn(entry.request, 'context')
      && canonicalSha256(entry.request) === entry.requestSha256, 'every case needs an intact query/context-only request');
  }
  ensure(isRecord(system) && ['gemini', 'langflow'].includes(system.id) && Object.keys(system).length === 5
    && ['modelId', 'promptVersion', 'toolVersion', 'adapterVersion'].every((key) => nonempty(system[key])) && system.modelId !== 'none', 'system needs explicit model and version provenance');
  ensure(Number.isSafeInteger(maxCases) && maxCases >= 0 && maxCases <= plan.cases.length, 'maxCases must be between zero and the plan size');
  ensure(Number.isSafeInteger(timeoutMs) && timeoutMs >= 1 && timeoutMs <= 120_000, 'timeoutMs must be 1..120000');
  ensure(Number.isSafeInteger(maxResponseBytes) && maxResponseBytes >= 1 && maxResponseBytes <= 4 * 1024 * 1024, 'maxResponseBytes must be 1..4194304');
  ensure(Number.isSafeInteger(maxTotalResponseBytes) && maxTotalResponseBytes >= maxResponseBytes && maxTotalResponseBytes <= MAX_TOTAL_BYTES,
    'maxTotalResponseBytes must cover one response and be at most 32 MiB');
  ensure(typeof stopOnError === 'boolean' && typeof fetchImpl === 'function' && (onProgress === undefined || typeof onProgress === 'function'), 'invalid collection control');
  ensure(typeof continueOnModelMismatch === 'boolean' && (!continueOnModelMismatch || system.id === 'gemini'),
    'continueOnModelMismatch must be a boolean and can only be enabled for Gemini');
  ensure(onCheckpoint === undefined || typeof onCheckpoint === 'function', 'onCheckpoint must be a function');
  ensure(signal === undefined || signal instanceof AbortSignal, 'signal must be an AbortSignal');
  if (system.id === 'langflow') {
    ensure(isRecord(outputSelector) && Object.keys(outputSelector).length === 2 && Number.isSafeInteger(outputSelector.outputIndex)
      && outputSelector.outputIndex >= 0 && nonempty(outputSelector.componentId), 'Langflow needs an explicit outputIndex and componentId');
  } else ensure(outputSelector === undefined, 'Gemini does not use an output selector');
}

/** Race even a stalled response body against cancellation; abort listeners are removed on every path. */
function withAbort(promise, signal) {
  if (signal.aborted) return Promise.reject(new CollectionFailure(signal.reason === 'timeout' ? 'timeout' : 'transport_error', signal.reason === 'timeout' ? 'timeout' : 'aborted'));
  return new Promise((resolve, reject) => {
    const aborted = () => reject(new CollectionFailure(signal.reason === 'timeout' ? 'timeout' : 'transport_error', signal.reason === 'timeout' ? 'timeout' : 'aborted'));
    signal.addEventListener('abort', aborted, { once: true });
    Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted));
  });
}

async function limitedJson(response, limit, signal, allowNonJson = false) {
  const declared = response.headers.get('content-length');
  if (declared !== null && /^\d+$/u.test(declared) && Number(declared) > limit) {
    void response.body?.cancel().catch(() => {});
    throw new CollectionFailure('invalid_output', 'response_too_large', { responseBytes: 0 });
  }
  if (!response.body || typeof response.body.getReader !== 'function') throw new CollectionFailure('invalid_output', 'missing_response_body');
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await withAbort(reader.read(), signal);
      if (done) break;
      length += value.byteLength;
      if (length > limit) throw new CollectionFailure('invalid_output', 'response_too_large', { responseBytes: length });
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let body;
    try { body = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
    catch { throw new CollectionFailure('invalid_output', 'invalid_utf8', { responseBytes: length }); }
    try { return { raw: JSON.parse(body), responseBytes: length }; }
    catch {
      if (allowNonJson) return { raw: body, responseBytes: length };
      throw new CollectionFailure('invalid_output', 'invalid_json', { raw: body, responseBytes: length });
    }
  } catch (error) {
    void reader.cancel().catch(() => {});
    if (error instanceof CollectionFailure && error.responseBytes === undefined) error.responseBytes = length;
    throw error;
  } finally {
    reader.releaseLock();
  }
}

function unwrapResponse(raw, system, outputSelector, runtimeRequest) {
  if (system.id === 'gemini') {
    if (!isRecord(raw) || raw.success !== true || !isRecord(raw.data)) throw new CollectionFailure('invalid_output', 'invalid_response_envelope', { raw });
    const value = raw.data;
    if (typeof value.reply !== 'string' || !Array.isArray(value.places) || !isRecord(value.action)
      || !['gemini', 'keyword', 'none'].includes(value.engine) || (value.engine === 'gemini' && !nonempty(value.model))) {
      throw new CollectionFailure('invalid_output', 'invalid_assistant_reply', { raw });
    }
    if (value.engine !== 'gemini' || value.model !== system.modelId) throw new CollectionFailure('invalid_output', 'model_or_engine_mismatch', { raw });
    return value;
  }
  const components = raw?.outputs?.[outputSelector.outputIndex]?.outputs;
  const selected = Array.isArray(components) ? components.filter((entry) => entry?.component_id === outputSelector.componentId) : [];
  if (selected.length !== 1 || selected[0]?.results?.message?.sender !== 'Machine' || typeof selected[0]?.results?.message?.text !== 'string') {
    throw new CollectionFailure('invalid_output', 'missing_selected_machine_output', { raw });
  }
  // Older captures may omit model metadata; absence is not verification. A reported model must match exactly.
  const reportedModel = selected[0].results.message.properties?.source?.source;
  if (runtimeRequest === undefined && reportedModel !== undefined && reportedModel !== system.modelId) {
    throw new CollectionFailure('invalid_output', 'model_or_engine_mismatch', { raw });
  }
  if (runtimeRequest !== undefined) {
    try { validateValidatedLangflowResponse({ raw, outputSelector, requestId: runtimeRequest.requestId }); }
    catch (error) {
      const reason = error.code === 'RECOMMENDATION_BLOCKED' ? 'final_validation_blocked'
        : error.code === 'VALIDATION_FAILED' ? 'final_validation_failed' : 'invalid_validated_output';
      throw new CollectionFailure('invalid_output', reason, { raw });
    }
  }
  return raw;
}

/** Validate a full prior capture against its telemetry; old events can bind a continuation, not prove old inference. */
function priorCollection(resumeCapture, resumeTelemetry, { plan, system, url, outputSelector, requestMode, catalog, requestBinding }) {
  ensure((resumeCapture === undefined) === (resumeTelemetry === undefined), 'resumeCapture and resumeTelemetry must be supplied together');
  if (resumeCapture === undefined) return null;
  const capture = resumeCapture;
  const telemetry = resumeTelemetry;
  const timestamp = (value) => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
  const measurement = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  ensure(isRecord(capture) && capture.schemaVersion === '1.0' && capture.kind === 'jeju-baseline-capture'
    && capture.planSha256 === plan.planSha256 && canonicalSha256(capture.system) === canonicalSha256(system), 'resume capture plan/system provenance mismatch');
  ensure(timestamp(capture.startedAt) && timestamp(capture.completedAt) && capture.startedAt <= capture.completedAt, 'resume capture timestamps are invalid');
  ensure(isRecord(telemetry) && telemetry.schemaVersion === '1.0' && telemetry.kind === 'jeju-baseline-collection-telemetry'
    && telemetry.runnerVersion === BASELINE_RUNNER_VERSION && telemetry.planSha256 === plan.planSha256 && telemetry.system === system.id
    && telemetry.endpointSha256 === canonicalSha256(url.href) && telemetry.startedAt === capture.startedAt && telemetry.completedAt === capture.completedAt,
  'resume telemetry provenance, endpoint or timestamps mismatch');
  ensure(Array.isArray(capture.records) && capture.records.length === plan.cases.length && Array.isArray(telemetry.events), 'resume requires full record coverage and event telemetry');
  ensure(validateValidatedCapture(plan, capture, catalog) === requestMode, 'resume request mode changed');
  if (requestMode === 'langflow-v2') {
    ensure(telemetry.requestMode === requestMode && canonicalSha256(telemetry.requestBinding) === canonicalSha256(requestBinding), 'resume telemetry request provenance changed');
  } else ensure(telemetry.requestMode === undefined && telemetry.requestBinding === undefined, 'legacy telemetry cannot claim v2 request provenance');
  const attemptedRecords = new Map();
  for (let index = 0; index < plan.cases.length; index += 1) {
    const entry = plan.cases[index];
    const record = capture.records[index];
    ensure(isRecord(record) && record.caseId === entry.caseId && record.requestSha256 === entry.requestSha256,
      'resume records must preserve every planned case and request hash in order');
    const hasRaw = record.raw !== undefined;
    ensure(hasRaw !== (record.error !== undefined), 'resume record needs raw output or an explicit error');
    if (hasRaw) {
      ensure(Object.keys(record).every((key) => ['caseId', 'requestSha256', 'raw', 'measurements', 'outputSelector', 'runtimeRequest'].includes(key))
        && isRecord(record.measurements) && measurement(record.measurements.latencyMs) && record.measurements.costUsd === null,
      'resume raw record needs original observed measurements only');
      if (system.id === 'langflow') ensure(canonicalSha256(record.outputSelector) === canonicalSha256(outputSelector), 'resume output selection changed');
      else ensure(record.outputSelector === undefined, 'resume Gemini record cannot have an output selector');
      try { unwrapResponse(system.id === 'gemini' ? { success: true, data: record.raw } : record.raw, system, outputSelector, record.runtimeRequest); }
      catch { throw new Error('Invalid baseline collection: resume raw record does not match the declared baseline'); }
      attemptedRecords.set(record.caseId, record);
    } else {
      ensure(Object.keys(record).every((key) => ['caseId', 'requestSha256', 'error', 'runtimeRequest'].includes(key)) && isRecord(record.error)
        && Object.keys(record.error).length === 1 && ['timeout', 'transport_error', 'invalid_output', 'not_executed'].includes(record.error.code),
      'resume error record must preserve its original failure code');
      if (record.error.code !== 'not_executed') attemptedRecords.set(record.caseId, record);
    }
  }
  const reasons = {
    timeout: ['timeout'], transport_error: ['aborted', 'transport_failure', 'redirect_blocked', 'http_error', 'collector_interrupted'],
    invalid_output: ['response_too_large', 'missing_response_body', 'invalid_utf8', 'invalid_json', 'invalid_response_envelope',
      'model_or_engine_mismatch', 'invalid_assistant_reply', 'missing_selected_machine_output', 'invalid_validated_output',
      'final_validation_blocked', 'final_validation_failed'],
  };
  let bytes = 0;
  ensure(telemetry.resume === undefined || (isRecord(telemetry.resume)
    && ['legacy_event_binding_checked', 'event_hashes_verified'].includes(telemetry.resume.integrity)),
  'resume history must use a known integrity classification');
  // A previous continuation may have added hashes to legacy events. Those new bindings do not retroactively
  // establish source integrity, so retain that limitation throughout every later continuation in the chain.
  let legacy = telemetry.resume?.integrity === 'legacy_event_binding_checked';
  const seen = new Set();
  for (const event of telemetry.events) {
    const record = attemptedRecords.get(event?.caseId);
    ensure(isRecord(event) && record !== undefined && !seen.has(event.caseId), 'resume needs exactly one event for every attempted record');
    seen.add(event.caseId);
    if (requestMode === 'langflow-v2') ensure(event.sessionId === record.runtimeRequest.requestId
      && event.runtimeRequestSha256 === canonicalSha256(record.runtimeRequest), 'resume event runtime request/session binding changed');
    else ensure(event.sessionId === undefined && event.runtimeRequestSha256 === undefined, 'legacy event cannot claim a v2 runtime request');
    const interrupted = event.outcome === 'collector_interrupted';
    if (interrupted) {
      // Operator recovery from independently retained server evidence. An absent client observation is never zero latency.
      ensure(record.error?.code === 'transport_error' && event.latencyMs === null && event.httpStatus === null
        && event.responseBytes === 0 && event.raw === undefined && SHA256.test(event.interruptionEvidenceSha256 ?? '')
        && event.requestSha256 === record.requestSha256 && event.captureRecordSha256 === canonicalSha256(record),
      'resume collector interruption needs a bound evidence hash and explicitly unobserved latency/status');
    } else ensure(event.interruptionEvidenceSha256 === undefined, 'resume interruption evidence belongs only to collector_interrupted');
    ensure(Number.isSafeInteger(event.responseBytes) && event.responseBytes >= 0 && (interrupted || measurement(event.latencyMs))
      && (event.httpStatus === null || (Number.isSafeInteger(event.httpStatus) && event.httpStatus >= 100 && event.httpStatus <= 599)), 'resume event measurements are invalid');
    if (record.raw !== undefined) {
      ensure(event.outcome === 'completed' && event.httpStatus >= 200 && event.httpStatus < 300
        && event.latencyMs >= record.measurements.latencyMs, 'resume successful record/event mismatch');
    } else ensure(reasons[record.error.code].includes(event.outcome), 'resume failure code/event mismatch');
    const hashed = event.requestSha256 !== undefined || event.captureRecordSha256 !== undefined;
    if (hashed) {
      ensure(event.requestSha256 === record.requestSha256 && event.captureRecordSha256 === canonicalSha256(record), 'resume event request or record hash mismatch');
      ensure(telemetry.provenanceSha256 === canonicalSha256(system), 'resume telemetry system hash mismatch');
    } else legacy = true;
    bytes += event.responseBytes;
  }
  ensure(seen.size === attemptedRecords.size && telemetry.events.length === attemptedRecords.size,
    'resume is missing event telemetry for an attempted record');
  ensure(telemetry.plannedCases === plan.cases.length && telemetry.attemptedCases === attemptedRecords.size
    && telemetry.notExecutedCases === plan.cases.length - attemptedRecords.size && telemetry.totalResponseBytes === bytes,
  'resume telemetry counts/byte totals do not match the preserved records');
  ensure(telemetry.provenanceSha256 === undefined || telemetry.provenanceSha256 === canonicalSha256(system), 'resume telemetry system hash mismatch');
  ensure(telemetry.continueOnModelMismatch === undefined || (typeof telemetry.continueOnModelMismatch === 'boolean'
    && (!telemetry.continueOnModelMismatch || system.id === 'gemini')), 'resume model-mismatch policy is invalid');
  return {
    startedAt: capture.startedAt, records: structuredClone(capture.records),
    // Added hashes on old events describe this continuation binding; integrity explicitly preserves the limitation.
    events: telemetry.events.map((event) => ({ ...structuredClone(event), requestSha256: attemptedRecords.get(event.caseId).requestSha256,
      captureRecordSha256: canonicalSha256(attemptedRecords.get(event.caseId)) })),
    attempted: attemptedRecords.size, totalBytes: bytes,
    resume: { previousAttemptedCases: attemptedRecords.size, sourceCaptureSha256: canonicalSha256(capture),
      sourceTelemetrySha256: canonicalSha256(telemetry), integrity: legacy ? 'legacy_event_binding_checked' : 'event_hashes_verified',
      previousContinueOnModelMismatch: telemetry.continueOnModelMismatch ?? false },
  };
}

/**
 * The caller must also validateBaselinePlan against catalog/spec before authorizing collection. Preparation here checks
 * hashes and request shape again before the first call. maxCases enables a smoke run while preserving full coverage as
 * explicit not_executed records. A new random Langflow session per case prevents cross-question chat memory.
 * onProgress receives only counts/status; it must be a synchronous callback and is never given headers or raw output.
 * onCheckpoint is awaited after each attempted case with an isolated, local-only full-coverage snapshot. Future cases
 * are not_executed. A failed checkpoint stops new paid requests and returns the collected result without retrying it.
 * A paired resume capture/telemetry continues only not_executed cases; maxCases limits NEW attempts. Prior failed
 * attempts are never retried. Original timestamps/measurements are preserved and cumulative byte limits still apply.
 * Explicit Gemini-only continueOnModelMismatch keeps an unexpected model/engine result as a failure but permits the
 * NEXT case. It does not retry the failed case, change providers, accept the fallback, or relax any other stop policy.
 */
export async function collectBaselineCapture({
  plan, system, endpoint, headers = {}, outputSelector,
  maxCases = plan?.cases?.length, timeoutMs = 60_000, maxResponseBytes = 1024 * 1024,
  maxTotalResponseBytes = 16 * 1024 * 1024, stopOnError = true, continueOnModelMismatch = false,
  signal, fetchImpl = globalThis.fetch, onProgress, onCheckpoint,
  resumeCapture, resumeTelemetry, requestMode = 'legacy', catalog,
} = {}) {
  validateOptions({ plan, system, maxCases, timeoutMs, maxResponseBytes, maxTotalResponseBytes, stopOnError, continueOnModelMismatch, signal, fetchImpl, onProgress, onCheckpoint, outputSelector });
  const url = endpointUrl(endpoint, system.id);
  const selectedHeaders = requestHeaders(headers);
  ensure(['legacy', 'langflow-v2'].includes(requestMode) && (requestMode !== 'langflow-v2' || system.id === 'langflow'), 'requestMode must be legacy or Langflow-only langflow-v2');
  ensure((requestMode === 'langflow-v2') === (system.adapterVersion === VALIDATED_BASELINE_ADAPTER_VERSION), 'request mode and response adapter version disagree');
  // Parse ALL planned inputs before any endpoint call, including cases beyond this invocation's limit.
  const requestBinding = requestMode === 'langflow-v2' ? prepareValidatedCollection(plan, catalog) : undefined;
  const prior = priorCollection(resumeCapture, resumeTelemetry, { plan, system, url, outputSelector, requestMode, catalog, requestBinding });
  const prepared = plan.cases.map((entry) => requestMode === 'langflow-v2'
    ? buildBoundValidatedRequest(entry, catalog, `baseline-${randomUUID()}`)
    : { body: system.id === 'gemini' ? buildGeminiRequest(entry.request) : buildLangflowRequest(entry.request, { sessionId: `baseline-${randomUUID()}` }) });
  const requests = prepared.map((entry) => entry.body);
  const startedAt = prior?.startedAt ?? new Date().toISOString();
  const records = prior?.records ?? plan.cases.map((entry) => ({ caseId: entry.caseId, requestSha256: entry.requestSha256, error: { code: 'not_executed' } }));
  const events = prior?.events ?? [];
  let attempted = prior?.attempted ?? 0;
  let newAttempted = 0;
  let totalBytes = prior?.totalBytes ?? 0;
  let stoppedReason = null;
  const snapshot = () => {
    const completedAt = new Date().toISOString();
    return structuredClone({
      capture: { schemaVersion: '1.0', kind: 'jeju-baseline-capture', planSha256: plan.planSha256,
        system, startedAt, completedAt, records, ...(requestBinding === undefined ? {} : { requestBinding }) },
      telemetry: { schemaVersion: '1.0', kind: 'jeju-baseline-collection-telemetry', runnerVersion: BASELINE_RUNNER_VERSION,
        planSha256: plan.planSha256, system: system.id, provenanceSha256: canonicalSha256(system), endpointSha256: canonicalSha256(url.href),
        startedAt, completedAt, plannedCases: plan.cases.length, attemptedCases: attempted, notExecutedCases: plan.cases.length - attempted,
        newAttemptedCases: newAttempted, ...(prior === null ? {} : { resume: prior.resume }),
        totalResponseBytes: totalBytes, stoppedReason, timeoutMs, maxResponseBytes, maxTotalResponseBytes, maxCases, stopOnError, continueOnModelMismatch,
        ...(requestBinding === undefined ? {} : { requestMode, requestBinding }), events },
    });
  };
  for (let index = 0; index < plan.cases.length; index += 1) {
    const entry = plan.cases[index];
    const identity = { caseId: entry.caseId, requestSha256: entry.requestSha256 };
    if (records[index].error?.code !== 'not_executed') continue;
    if (stoppedReason !== null || newAttempted >= maxCases || signal?.aborted || totalBytes >= maxTotalResponseBytes) {
      stoppedReason ??= signal?.aborted ? 'aborted' : totalBytes >= maxTotalResponseBytes ? 'total_response_budget' : 'case_limit';
      continue;
    }
    if (prepared[index].runtimeRequest !== undefined) identity.runtimeRequest = prepared[index].runtimeRequest;
    const controller = new AbortController();
    const userAbort = () => controller.abort('aborted');
    signal?.addEventListener('abort', userAbort, { once: true });
    const timer = setTimeout(() => controller.abort('timeout'), timeoutMs);
    const started = performance.now();
    let status = null;
    let responseBytes = 0;
    let outcome = 'completed';
    let rejectedRaw;
    attempted += 1;
    newAttempted += 1;
    try {
      const response = await withAbort(fetchImpl(url.href, {
        method: 'POST', headers: selectedHeaders, body: JSON.stringify(requests[index]), signal: controller.signal,
        redirect: 'manual', credentials: 'omit', cache: 'no-store',
      }), controller.signal);
      status = response.status;
      if (response.redirected || (status >= 300 && status < 400)) {
        void response.body?.cancel().catch(() => {});
        throw new CollectionFailure('transport_error', 'redirect_blocked');
      }
      // HTTP failures may return HTML/plain text. Their status is a transport failure, not model JSON failure.
      const parsed = await limitedJson(response, Math.min(maxResponseBytes, maxTotalResponseBytes - totalBytes), controller.signal, !response.ok);
      responseBytes = parsed.responseBytes;
      if (!response.ok) throw new CollectionFailure('transport_error', 'http_error', { raw: parsed.raw });
      const raw = unwrapResponse(parsed.raw, system, outputSelector, identity.runtimeRequest);
      records[index] = { ...identity, raw, measurements: { latencyMs: Math.max(0, performance.now() - started), costUsd: null },
        ...(system.id === 'langflow' ? { outputSelector: { ...outputSelector } } : {}) };
    } catch (error) {
      const failure = error instanceof CollectionFailure ? error
        : new CollectionFailure(controller.signal.aborted && controller.signal.reason === 'timeout' ? 'timeout' : 'transport_error',
          controller.signal.aborted ? controller.signal.reason === 'timeout' ? 'timeout' : 'aborted' : 'transport_failure');
      outcome = failure.reason;
      responseBytes = failure.responseBytes ?? responseBytes;
      rejectedRaw = failure.raw;
      records[index] = { ...identity, error: { code: failure.code } };
      const continueMismatch = continueOnModelMismatch && failure.code === 'invalid_output' && outcome === 'model_or_engine_mismatch';
      if ((stopOnError && !continueMismatch) || outcome === 'aborted' || outcome === 'response_too_large') stoppedReason = outcome;
      controller.abort('finished');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', userAbort);
    }
    totalBytes += responseBytes;
    events.push({ caseId: entry.caseId, requestSha256: entry.requestSha256, captureRecordSha256: canonicalSha256(records[index]),
      outcome, httpStatus: status, responseBytes, latencyMs: Math.max(0, performance.now() - started),
      ...(identity.runtimeRequest === undefined ? {} : { sessionId: identity.runtimeRequest.requestId, runtimeRequestSha256: canonicalSha256(identity.runtimeRequest) }),
      ...(rejectedRaw === undefined ? {} : { raw: rejectedRaw }) });
    try {
      const result = onProgress?.({ attempted, total: plan.cases.length, outcome });
      if (result && typeof result.then === 'function') {
        void result.catch(() => {});
        stoppedReason = 'async_progress_callback';
      }
    } catch { stoppedReason = 'progress_callback_error'; }
    if (attempted < plan.cases.length && stoppedReason === null) {
      if (signal?.aborted) stoppedReason = 'aborted';
      else if (newAttempted >= maxCases) stoppedReason = 'case_limit';
      else if (totalBytes >= maxTotalResponseBytes) stoppedReason = 'total_response_budget';
    }
    if (onCheckpoint !== undefined) {
      try { await onCheckpoint(snapshot()); }
      catch { stoppedReason = 'checkpoint_error'; }
    }
  }
  return snapshot();
}
