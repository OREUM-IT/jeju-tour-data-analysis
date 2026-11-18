// Offline baseline preparation and scoring. No network, clock, model, or free-text judge.
// Plans, runs and item-level details are local-only. Only summary is safe to publish.
import { validateEvaluationSet } from './evaluation-set.mjs';
import { aggregateScores, createScorer, validateResponse } from './evaluation-scoring.mjs';
import { canonicalSha256 } from './evaluation-rng.mjs';

export const BASELINE_SCHEMA_VERSION = '1.0';
export const BASELINE_PLAN_KIND = 'jeju-baseline-plan';
export const BASELINE_RUN_KIND = 'jeju-baseline-run';
export const BASELINE_SUMMARY_KIND = 'jeju-baseline-summary';
export const BASELINE_SYSTEMS = Object.freeze(['gemini', 'langflow', 'oracle_self_check']);
export const BASELINE_VARIANTS = Object.freeze(['template', 'written']);
export const BASELINE_ERROR_CODES = Object.freeze(['timeout', 'transport_error', 'invalid_output', 'not_executed']);
const SHA256 = /^[a-f0-9]{64}$/u;
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const ensure = (condition, message) => { if (!condition) throw new Error(`Invalid baseline: ${message}`); };
const text = (value) => typeof value === 'string' && value.trim().length > 0 && value.length <= 240 && !/[\p{Cc}\p{Cf}]/u.test(value);
const keysAre = (record, keys) => isRecord(record) && Object.keys(record).every((key) => keys.includes(key));
const isoTimestamp = (value) => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/u.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value.replace(/(?<!\.\d{3})Z$/u, '.000Z');
const ownHash = (value, key) => canonicalSha256(Object.fromEntries(Object.entries(value).filter(([name]) => name !== key)));

/**
 * Create a pinned evaluation plan. Send only each case.request to a system under test: item IDs, variants and hashes
 * are harness routing data, and the evaluation set and catalog are not model inputs. A system uses its own tools.
 */
export function createBaselinePlan({ set, catalog, personas, themeMap, spec, variants = ['template'] }) {
  validateEvaluationSet(set, { catalog, personas, themeMap, spec });
  ensure(set.items.length === set.diagnostics.requestedItems && set.diagnostics.dropped.length === 0,
    'a baseline requires the complete evaluation set; dropped items cannot silently change the denominator');
  ensure(Array.isArray(variants) && variants.length > 0 && new Set(variants).size === variants.length
    && variants.every((variant) => BASELINE_VARIANTS.includes(variant)), 'variants must be distinct template/written values');
  const orderedVariants = BASELINE_VARIANTS.filter((variant) => variants.includes(variant));
  const written = set.items.filter((item) => item.query.source === 'authored' && item.query.text !== null);
  ensure(!variants.includes('written') || written.length === set.items.length,
    'written variant is unavailable unless every item has an authored query; template text is never substituted');
  const identical = written.filter((item) => item.query.text === item.query.template).length;
  const cases = orderedVariants.flatMap((variant) => set.items.map((item, index) => {
    const request = { query: variant === 'template' ? item.query.template : item.query.text, context: structuredClone(item.context) };
    return { caseId: `CASE-${String(index + 1).padStart(3, '0')}-${variant}`, itemId: item.id, variant,
      requestSha256: canonicalSha256(request), request };
  }));
  const plan = {
    schemaVersion: BASELINE_SCHEMA_VERSION, kind: BASELINE_PLAN_KIND,
    binding: {
      goldSha256: set.contentSha256.gold, queriesSha256: set.contentSha256.queries,
      specSha256: canonicalSha256(spec), catalogSha256: canonicalSha256(catalog),
      personasSha256: canonicalSha256(personas), themeMapSha256: canonicalSha256(themeMap),
    },
    variants: orderedVariants,
    wording: { templateItems: set.items.length, writtenItems: written.length, identicalWrittenItems: identical, distinctWrittenItems: written.length - identical },
    cases,
  };
  return { ...plan, planSha256: canonicalSha256(plan) };
}

/** Rebuild from verified inputs, not just a supplied hash; modified prompts/context or extra fields are rejected. */
export function validateBaselinePlan(plan, inputs) {
  ensure(isRecord(plan) && plan.schemaVersion === BASELINE_SCHEMA_VERSION && plan.kind === BASELINE_PLAN_KIND, 'unsupported plan schema');
  ensure(SHA256.test(plan.planSha256 ?? '') && ownHash(plan, 'planSha256') === plan.planSha256, 'plan hash mismatch');
  const expected = createBaselinePlan({ ...inputs, variants: plan.variants });
  ensure(canonicalSha256(plan) === canonicalSha256(expected), 'plan does not match the evaluation set, requests, catalog or versions');
  return plan;
}

function validateRun(run, plan) {
  ensure(keysAre(run, ['schemaVersion', 'kind', 'planSha256', 'system', 'mode', 'startedAt', 'completedAt', 'records'])
    && run.schemaVersion === BASELINE_SCHEMA_VERSION && run.kind === BASELINE_RUN_KIND, 'unsupported run schema or fields');
  ensure(run.planSha256 === plan.planSha256, 'run was recorded for a different plan');
  ensure(['external', 'oracle_self_check'].includes(run.mode), 'mode must be external or oracle_self_check');
  const system = run.system;
  ensure(keysAre(system, ['id', 'modelId', 'promptVersion', 'toolVersion', 'adapterVersion'])
    && BASELINE_SYSTEMS.includes(system.id)
    && ['modelId', 'promptVersion', 'toolVersion', 'adapterVersion'].every((key) => text(system[key])), 'system needs id, modelId, promptVersion, toolVersion and adapterVersion');
  const oracle = run.mode === 'oracle_self_check';
  ensure((system.id === 'oracle_self_check') === oracle, 'oracle self-check cannot be labelled as an external baseline');
  ensure(!oracle || system.modelId === 'none', 'oracle does not execute a model');
  ensure(oracle || system.modelId !== 'none', 'external baseline must identify its model');
  ensure(isoTimestamp(run.startedAt) && isoTimestamp(run.completedAt) && Date.parse(run.completedAt) >= Date.parse(run.startedAt),
    'run needs valid ordered UTC startedAt/completedAt timestamps');
  ensure(Array.isArray(run.records) && run.records.length === plan.cases.length, 'run must cover every planned case exactly once, including failures');
  const cases = new Map(plan.cases.map((entry) => [entry.caseId, entry]));
  const seen = new Set();
  for (const record of run.records) {
    ensure(keysAre(record, ['caseId', 'requestSha256', 'response', 'error', 'normalization']) && cases.has(record.caseId) && !seen.has(record.caseId),
      'records must identify unique planned cases');
    seen.add(record.caseId);
    const entry = cases.get(record.caseId);
    ensure(record.requestSha256 === entry.requestSha256, 'record request hash does not match the actual query/context');
    ensure((record.response !== undefined) !== (record.error !== undefined), 'record needs exactly one response or error');
    if (record.error !== undefined) {
      ensure(record.normalization === undefined, 'failed records cannot also carry response normalization');
      ensure(keysAre(record.error, ['code']) && BASELINE_ERROR_CODES.includes(record.error.code), 'error must use a known code without raw provider text');
    } else {
      const response = validateResponse(record.response);
      ensure(response.itemId === entry.itemId, 'response item does not match its planned case');
      if (record.normalization !== undefined) {
        const audit = record.normalization;
        ensure(!oracle && isRecord(audit) && audit.adapter === (system.id === 'gemini' ? 'gemini-assistant' : 'langflow')
          && audit.adapterVersion === system.adapterVersion && SHA256.test(audit.rawOutputHash ?? '')
          && ['structured', 'human_reviewed'].includes(audit.mode), 'normalization must match the declared system and adapter version');
        ensure(system.id !== 'gemini' || (audit.sourceEngine === 'gemini' && audit.reportedModel === system.modelId && audit.mode === 'human_reviewed'),
          'Gemini normalization must name the actual declared model, not a keyword fallback');
        ensure(audit.mode !== 'human_reviewed' || (isRecord(audit.review) && audit.review.method === 'human'
          && audit.review.rawOutputHash === audit.rawOutputHash && text(audit.review.reviewerId) && text(audit.review.rubricVersion)
          && typeof audit.review.reviewedAt === 'string' && Number.isFinite(Date.parse(audit.review.reviewedAt))),
        'human normalization must preserve a hash-bound review audit');
      }
    }
  }
  return run;
}

/**
 * Score an imported run. Import is not proof that a provider was called; local metadata records the operator's
 * provenance assertion. Failed calls remain in all-case denominators, and successful-response metrics are explicit.
 */
export function scoreBaselineRun({ plan, set, catalog, personas, themeMap, spec, run }) {
  validateBaselinePlan(plan, { set, catalog, personas, themeMap, spec });
  validateRun(run, plan);
  const scorer = createScorer({ catalog, personas, spec });
  const items = new Map(set.items.map((item) => [item.id, item]));
  const records = new Map(run.records.map((record) => [record.caseId, record]));
  const details = plan.cases.map((entry) => {
    const record = records.get(entry.caseId);
    return { caseId: entry.caseId, variant: entry.variant, requestSha256: entry.requestSha256,
      ...(record.error !== undefined ? { error: { code: record.error.code } }
        : { response: validateResponse(record.response), score: scorer.score(items.get(entry.itemId), record.response) }) };
  });
  const groups = plan.variants.map((variant) => {
    const selected = details.filter((detail) => detail.variant === variant);
    const scores = selected.filter((detail) => detail.score !== undefined).map((detail) => detail.score);
    const errors = Object.fromEntries(BASELINE_ERROR_CODES.map((code) => [code, selected.filter((detail) => detail.error?.code === code).length]));
    const unexpectedImpossible = selected.filter((detail) => detail.score !== undefined
      && detail.score.expectedAction !== 'impossible' && detail.response.action === 'impossible').length;
    const nonImpossibleCases = plan.cases.filter((entry) => entry.variant === variant && items.get(entry.itemId).gold.expectedAction !== 'impossible').length;
    const measuredNonImpossible = scores.filter((entry) => entry.expectedAction !== 'impossible').length;
    return {
      system: run.system.id, variant, plannedCases: selected.length, successfulResponses: scores.length, failedCases: selected.length - scores.length,
      completionRate: scores.length / selected.length, errors,
      actionAccuracyAllCases: scores.filter((score) => score.actionCorrect).length / selected.length,
      unexpectedImpossible: { count: unexpectedImpossible, applicableCases: nonImpossibleCases, measuredCases: measuredNonImpossible,
        rate: measuredNonImpossible === 0 ? null : unexpectedImpossible / measuredNonImpossible },
      // These existing scorer metrics have successful structured responses as their population, never failed calls.
      metrics: scores.length === 0 ? null : aggregateScores(scores, scorer.k),
    };
  });
  const summary = {
    schemaVersion: BASELINE_SCHEMA_VERSION, kind: BASELINE_SUMMARY_KIND,
    mode: run.mode, resultType: run.mode === 'oracle_self_check' ? 'scorer_self_check_not_model_quality' : 'imported_responses_not_independently_verified_inference',
    system: run.system.id,
    planSha256: plan.planSha256, binding: { ...plan.binding },
    // Model and version strings are recorded exactly in the local run, hashed here to avoid publishing arbitrary text.
    provenanceSha256: canonicalSha256(run.system), runSha256: canonicalSha256(run),
    startedAt: run.startedAt, completedAt: run.completedAt,
    wording: { ...plan.wording }, groups,
    securityEvaluation: 'not_executed',
  };
  return { summary, details };
}

/** Gold-derived responses exercise scoring only. They are never a Gemini/Langflow result or a quality benchmark. */
export function createOracleRun({ plan, set, catalog, personas, themeMap, spec, startedAt, completedAt }) {
  validateBaselinePlan(plan, { set, catalog, personas, themeMap, spec });
  const scorer = createScorer({ catalog, personas, spec });
  const items = new Map(set.items.map((item) => [item.id, item]));
  const run = {
    schemaVersion: BASELINE_SCHEMA_VERSION, kind: BASELINE_RUN_KIND, planSha256: plan.planSha256,
    system: { id: 'oracle_self_check', modelId: 'none', promptVersion: 'none', toolVersion: 'none', adapterVersion: 'oracle-v1' },
    mode: 'oracle_self_check', startedAt, completedAt,
    records: plan.cases.map((entry) => ({ caseId: entry.caseId, requestSha256: entry.requestSha256, response: scorer.oracle(items.get(entry.itemId)) })),
  };
  return validateRun(run, plan);
}
