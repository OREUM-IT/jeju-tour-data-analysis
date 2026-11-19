// Deterministic request provenance for the v2 experiment. No scorer, gold, or network access.
import { canonicalSha256 } from './evaluation-rng.mjs';
import { buildValidatedLangflowRequest, VALIDATED_REQUEST_ADAPTER_VERSION, VALIDATED_REQUEST_GRAMMAR_VERSION } from './validated-request-adapter.mjs';

export const VALIDATED_BASELINE_ADAPTER_VERSION = 'langflow-v2-1.0.0';
export const VALIDATED_REQUEST_MODE = 'langflow-v2';
const PREFLIGHT_ID = 'baseline-preflight';
const ensure = (condition, message) => { if (!condition) throw new Error(`Invalid validated capture: ${message}`); };
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const same = (a, b) => canonicalSha256(a) === canonicalSha256(b);

/** Parse every request before the first call, binding the exact catalog and transformed inputs. */
export function prepareValidatedCollection(plan, catalog) {
  ensure(catalog !== undefined && canonicalSha256(catalog) === plan.binding?.catalogSha256, 'catalog differs from the baseline plan');
  const prepared = plan.cases.map((entry) => {
    const body = buildValidatedLangflowRequest(entry.request, { catalog, sessionId: PREFLIGHT_ID });
    return { caseId: entry.caseId, requestSha256: entry.requestSha256, preparedRequestSha256: canonicalSha256(body) };
  });
  return {
    requestMode: VALIDATED_REQUEST_MODE,
    requestAdapterVersion: VALIDATED_REQUEST_ADAPTER_VERSION,
    grammarVersion: VALIDATED_REQUEST_GRAMMAR_VERSION,
    catalogSha256: canonicalSha256(catalog),
    preparedRequestsSha256: canonicalSha256(prepared),
  };
}

/** Opaque runtime/session identity varies per attempt; the normalized request hash does not. */
export function buildBoundValidatedRequest(entry, catalog, requestId) {
  ensure(typeof requestId === 'string' && /^baseline-[a-z0-9-]{8,100}$/u.test(requestId), 'runtime request ID must be an opaque baseline session');
  const body = buildValidatedLangflowRequest(entry.request, { catalog, sessionId: requestId });
  const prepared = buildValidatedLangflowRequest(entry.request, { catalog, sessionId: PREFLIGHT_ID });
  return { body, runtimeRequest: { requestId, bodySha256: canonicalSha256(body), preparedRequestSha256: canonicalSha256(prepared) } };
}

export function validateRuntimeRequest(entry, catalog, binding) {
  ensure(isRecord(binding) && Object.keys(binding).length === 3
    && Object.keys(binding).every((key) => ['requestId', 'bodySha256', 'preparedRequestSha256'].includes(key)), 'runtime request binding is missing or malformed');
  const expected = buildBoundValidatedRequest(entry, catalog, binding.requestId).runtimeRequest;
  ensure(same(binding, expected), 'runtime request body or prepared hash mismatch');
  return binding;
}

/** Import/resume checks retain failures and require the actual opaque identity, never a model-supplied replacement. */
export function validateValidatedCapture(plan, capture, catalog) {
  if (capture.requestBinding === undefined) {
    ensure(capture.system?.adapterVersion !== VALIDATED_BASELINE_ADAPTER_VERSION
      && capture.records.every((record) => record.runtimeRequest === undefined), 'v2 records cannot omit request provenance');
    return 'legacy';
  }
  ensure(capture.system?.id === 'langflow' && capture.system.adapterVersion === VALIDATED_BASELINE_ADAPTER_VERSION,
    'v2 request provenance requires the v2 Langflow adapter');
  ensure(same(capture.requestBinding, prepareValidatedCollection(plan, catalog)), 'request mode, catalog, grammar or prepared inputs changed');
  const cases = new Map(plan.cases.map((entry) => [entry.caseId, entry]));
  const seenIds = new Set();
  for (const record of capture.records) {
    if (record.error?.code === 'not_executed') {
      ensure(record.runtimeRequest === undefined, 'unexecuted records cannot claim a runtime request');
      continue;
    }
    ensure(cases.has(record.caseId), 'runtime request names an unknown case');
    validateRuntimeRequest(cases.get(record.caseId), catalog, record.runtimeRequest);
    ensure(!seenIds.has(record.runtimeRequest.requestId), 'runtime sessions cannot be reused across cases');
    seenIds.add(record.runtimeRequest.requestId);
  }
  return VALIDATED_REQUEST_MODE;
}
