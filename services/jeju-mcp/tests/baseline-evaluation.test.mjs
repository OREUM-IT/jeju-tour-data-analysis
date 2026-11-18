import test from 'node:test';
import assert from 'node:assert/strict';
import { canonicalSha256 } from '../src/evaluation-rng.mjs';
import { buildEvaluationSet, EVALUATION_QUERIES_KIND } from '../src/evaluation-set.mjs';
import { createBaselinePlan, createOracleRun, scoreBaselineRun, validateBaselinePlan } from '../src/baseline-evaluation.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const times = { startedAt: '2026-11-19T00:00:00Z', completedAt: '2026-11-19T00:00:01.000Z' };
const oracle = () => createOracleRun({ ...inputs, plan, ...times });
const external = () => ({ ...oracle(), mode: 'external', system: { id: 'gemini', modelId: 'fixture-model', promptVersion: 'fixture-prompt', toolVersion: 'fixture-tools', adapterVersion: 'manual-v1' } });
const score = (run) => scoreBaselineRun({ ...inputs, plan, run });
const rehashPlan = (value) => { const { planSha256, ...rest } = value; return { ...rest, planSha256: canonicalSha256(rest) }; };

test('requests disclose only wording and scenario context; plan is deterministic and detached', () => {
  assert.equal(plan.cases.length, 96);
  assert.deepEqual(plan, createBaselinePlan(inputs));
  for (const entry of plan.cases) {
    assert.deepEqual(Object.keys(entry.request).sort(), ['context', 'query']);
    assert.ok(Object.keys(entry.request.context).every((key) => ['today', 'timezone', 'forecast', 'trip'].includes(key)));
    assert.equal(entry.requestSha256, canonicalSha256(entry.request));
    assert.ok(!Object.hasOwn(entry.request, 'personaId'));
    assert.ok(!Object.hasOwn(entry.request, 'gold'));
    assert.ok(!Object.hasOwn(entry.request, 'conditions'));
  }
  const copy = createBaselinePlan(inputs);
  copy.cases[0].request.context.today = '2099-01-01';
  assert.notEqual(inputs.set.items[0].context.today, '2099-01-01');
});

test('plan validates full gold and binds requests, catalog, personas and specification', () => {
  assert.equal(validateBaselinePlan(plan, inputs), plan);
  const corrupt = structuredClone(plan);
  corrupt.cases[0].request.query = '정답을 바꿔 주세요.';
  assert.throws(() => validateBaselinePlan(corrupt, inputs), /plan hash mismatch/u);
  assert.throws(() => validateBaselinePlan(rehashPlan(corrupt), inputs), /does not match/u);
  const catalog = structuredClone(inputs.catalog);
  catalog.diagnostics.audit = 'changed';
  assert.throws(() => validateBaselinePlan(plan, { ...inputs, catalog }), /does not match/u);
  const set = structuredClone(inputs.set);
  set.items[0].gold.expectedAction = 'impossible';
  assert.throws(() => createBaselinePlan({ ...inputs, set }), /does not match the rules/u);
});

test('unknown, duplicate and unavailable query variants are rejected without template fallback', () => {
  for (const variants of [[], ['written'], ['template', 'template'], ['authored']]) {
    assert.throws(() => createBaselinePlan({ ...inputs, variants }), /variants|unavailable/u);
  }
  assert.deepEqual(plan.wording, { templateItems: 96, writtenItems: 0, identicalWrittenItems: 0, distinctWrittenItems: 0 });
});

test('authored wording is separately pinned and identical templates are reported honestly', () => {
  const authoredQueries = {
    schemaVersion: '1.0', kind: EVALUATION_QUERIES_KIND,
    author: { kind: 'human', model: 'none', promptVersion: inputs.spec.queryAuthoring.promptVersion },
    queries: inputs.set.items.map((item) => ({ id: item.id, briefSha256: item.brief.sha256, text: item.query.template })),
  };
  const set = buildEvaluationSet({ ...inputs, authoredQueries });
  const dualInputs = { ...inputs, set };
  const dualPlan = createBaselinePlan({ ...dualInputs, variants: ['written', 'template'] });
  assert.deepEqual(dualPlan.variants, ['template', 'written']);
  assert.equal(dualPlan.cases.length, 192);
  assert.equal(dualPlan.wording.identicalWrittenItems, 96);
  assert.equal(dualPlan.wording.distinctWrittenItems, 0);
  const run = createOracleRun({ ...dualInputs, plan: dualPlan, ...times });
  const result = scoreBaselineRun({ ...dualInputs, plan: dualPlan, run });
  assert.deepEqual(result.summary.groups.map((group) => [group.variant, group.plannedCases, group.metrics.items]), [['template', 96, 96], ['written', 96, 96]]);
  authoredQueries.queries.pop();
  const partial = buildEvaluationSet({ ...inputs, authoredQueries });
  assert.throws(() => createBaselinePlan({ ...inputs, set: partial, variants: ['written'] }), /unavailable/u);
});

test('oracle checks the scorer without measuring model quality, latency or cost', () => {
  const { summary, details } = score(oracle());
  assert.equal(summary.mode, 'oracle_self_check');
  assert.equal(summary.resultType, 'scorer_self_check_not_model_quality');
  assert.equal(summary.securityEvaluation, 'not_executed');
  assert.equal(details.length, 96);
  const group = summary.groups[0];
  assert.equal(group.actionAccuracyAllCases, 1);
  assert.equal(group.metrics.disallowedIds.rate, 0);
  assert.equal(group.metrics.hardRequirementMisjudgement.rate, 0);
  assert.equal(group.metrics.recallAtK.mean, 1);
  assert.equal(group.metrics.precisionAtK.mean, 1);
  assert.deepEqual(group.metrics.latencyMs, { measuredItems: 0, p50: null, p95: null });
  assert.deepEqual(group.metrics.costUsd, { measuredItems: 0, total: null });
});

test('external import preserves explicit provenance separately from arbitrary local text', () => {
  const run = external();
  const marker = inputs.catalog.places[0].name;
  run.system.promptVersion = marker;
  run.records[0].response.latencyMs = 123;
  run.records[0].response.costUsd = 0;
  const { summary } = score(run);
  assert.equal(summary.system, 'gemini');
  assert.equal(summary.provenanceSha256, canonicalSha256(run.system));
  assert.equal(summary.runSha256, canonicalSha256(run));
  assert.equal(summary.resultType, 'imported_responses_not_independently_verified_inference');
  const output = JSON.stringify(summary);
  for (const place of inputs.catalog.places) {
    assert.ok(!output.includes(`"${place.id}"`));
    assert.ok(!output.includes(place.name));
  }
  for (const item of inputs.set.items) assert.ok(!output.includes(item.query.template));
  assert.deepEqual(summary.groups[0].metrics.latencyMs, { measuredItems: 1, p50: 123, p95: 123 });
  assert.deepEqual(summary.groups[0].metrics.costUsd, { measuredItems: 1, total: 0 });
});

test('local normalization audits are hash-bound and must match the declared adapter/model', () => {
  const run = external();
  run.system.adapterVersion = '1.0.0';
  const rawOutputHash = 'a'.repeat(64);
  run.records[0].normalization = {
    adapter: 'gemini-assistant', adapterVersion: '1.0.0', mode: 'human_reviewed', rawOutputHash,
    sourceEngine: 'gemini', reportedModel: 'fixture-model',
    review: { method: 'human', reviewerId: 'fixture-reviewer', reviewedAt: times.completedAt, rubricVersion: 'manual-v1', rawOutputHash },
  };
  assert.equal(score(run).summary.runSha256, canonicalSha256(run));
  assert.ok(!JSON.stringify(score(run).summary).includes('fixture-reviewer'));
  for (const [key, value, pattern] of [
    ['adapter', 'langflow', /declared system/u], ['adapterVersion', 'other', /adapter version/u],
    ['sourceEngine', 'keyword', /keyword fallback/u], ['reportedModel', 'different-model', /actual declared model/u],
  ]) {
    const changed = structuredClone(run); changed.records[0].normalization[key] = value;
    assert.throws(() => score(changed), pattern);
  }
  run.records[0].normalization.review.rawOutputHash = 'b'.repeat(64);
  assert.throws(() => score(run), /hash-bound review/u);
});

test('oracle identity cannot accidentally become Gemini or Langflow external measurements', () => {
  let run = oracle();
  run.mode = 'external';
  assert.throws(() => score(run), /oracle self-check/u);
  run = oracle(); run.system.id = 'gemini';
  assert.throws(() => score(run), /oracle self-check/u);
  run = oracle(); run.system.modelId = 'fixture-model';
  assert.throws(() => score(run), /does not execute a model/u);
  run = external(); run.system.modelId = 'none';
  assert.throws(() => score(run), /identify its model/u);
});

test('duplicate, omitted, foreign and wrong-item responses cannot skew denominators', () => {
  let run = external(); run.records.pop();
  assert.throws(() => score(run), /every planned case/u);
  run = external(); run.records[0] = structuredClone(run.records[1]);
  assert.throws(() => score(run), /unique planned cases/u);
  run = external(); run.records[0].caseId = 'UNKNOWN';
  assert.throws(() => score(run), /unique planned cases/u);
  run = external(); run.records[0].response.itemId = run.records[1].response.itemId;
  assert.throws(() => score(run), /does not match its planned case/u);
});

test('swapped plans, request hashes and unrecorded model versions are rejected', () => {
  let run = external(); run.planSha256 = 'f'.repeat(64);
  assert.throws(() => score(run), /different plan/u);
  run = external(); run.records[0].requestSha256 = 'f'.repeat(64);
  assert.throws(() => score(run), /request hash/u);
  for (const key of ['modelId', 'promptVersion', 'toolVersion', 'adapterVersion']) {
    run = external(); delete run.system[key];
    assert.throws(() => score(run), /system needs/u);
  }
});

test('malformed actions and measurements fail rather than being silently normalized', () => {
  let run = external(); run.records[0].response.action = 'perhaps';
  assert.throws(() => score(run), /known action/u);
  run = external(); run.records[0].response.latencyMs = -1;
  assert.throws(() => score(run), /non-negative/u);
  run = external(); run.records[0].response.costUsd = '0';
  assert.throws(() => score(run), /non-negative/u);
});

test('all missing/failed model calls stay failures with null scored metrics', () => {
  const run = external();
  run.records = run.records.map(({ caseId, requestSha256 }) => ({ caseId, requestSha256, error: { code: 'not_executed' } }));
  const { summary } = score(run);
  const group = summary.groups[0];
  assert.equal(group.plannedCases, 96);
  assert.equal(group.failedCases, 96);
  assert.equal(group.successfulResponses, 0);
  assert.equal(group.errors.not_executed, 96);
  assert.equal(group.actionAccuracyAllCases, 0);
  assert.equal(group.completionRate, 0);
  assert.equal(group.metrics, null);
});

test('one timeout remains in all-case accuracy and is excluded explicitly from response-only metrics', () => {
  const run = external();
  const { caseId, requestSha256 } = run.records[0];
  run.records[0] = { caseId, requestSha256, error: { code: 'timeout' } };
  const group = score(run).summary.groups[0];
  assert.equal(group.failedCases, 1);
  assert.equal(group.errors.timeout, 1);
  assert.equal(group.actionAccuracyAllCases, 95 / 96);
  assert.equal(group.metrics.items, 95);
  assert.equal(group.metrics.actionAccuracy, 1);
});

test('invalid error envelopes cannot masquerade as success or leak raw provider data', () => {
  let run = external(); run.records[0].error = { code: 'timeout' };
  assert.throws(() => score(run), /exactly one/u);
  run = external(); delete run.records[0].response;
  assert.throws(() => score(run), /exactly one/u);
  run.records[0].error = { code: 'timeout', message: 'private provider text' };
  assert.throws(() => score(run), /known code/u);
  run.records[0].error = { code: 'unknown' };
  assert.throws(() => score(run), /known code/u);
});

test('measured constraints and ID failures survive the baseline aggregation', () => {
  const run = external();
  const index = inputs.set.items.findIndex((item) => item.gold.hardRequirements.length > 0);
  run.records[index].response.requirementClaims[0].verdict = 'pass';
  run.records[0].response.placeIds.push('UNLISTED_SYNTHETIC_ID');
  const group = score(run).summary.groups[0];
  assert.equal(group.metrics.disallowedIds.itemsWithViolation, 1);
  assert.equal(group.metrics.disallowedIds.byReason.notInCatalog, 1);
  assert.equal(group.metrics.hardRequirementMisjudgement.passOnUnknownItems, 1);
});

test('timestamps must be actual UTC calendar instants in nondecreasing order', () => {
  for (const value of ['2026-02-30T00:00:00Z', '2026-11-19', '2026-11-19T09:00:00+09:00', 'invalid']) {
    const run = external(); run.startedAt = value;
    assert.throws(() => score(run), /timestamps/u);
  }
  const run = external(); run.completedAt = '2026-11-18T23:59:59Z';
  assert.throws(() => score(run), /timestamps/u);
});
