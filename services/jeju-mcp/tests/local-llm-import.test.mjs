import test from 'node:test';
import assert from 'node:assert/strict';
import { createBaselinePlan, scoreBaselineRun } from '../src/baseline-evaluation.mjs';
import { adaptLocalLlmResponse } from '../src/baseline-adapters.mjs';
import { buildBoundValidatedRequest, LOCAL_LLM_ADAPTER_VERSION, prepareValidatedCollection } from '../src/validated-capture-binding.mjs';
import { importCapture } from '../scripts/evaluate-baselines.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const system = { id: 'local_llm', modelId: 'fixture/local-model', promptVersion: 'local-v1', toolVersion: 'fixed-retrieval-v1', adapterVersion: LOCAL_LLM_ADAPTER_VERSION };
const decision = { action: 'ask_clarification', placeIds: [], requirementClaims: [], confirmationNotice: false, noticeTopics: [] };
const raw = (extra = {}) => ({ kind: 'jeju-local-llm-output', model: system.modelId, output: JSON.stringify(decision),
  gate: { status: 'accepted', policyVersion: 'recommendation-validation-v1' }, ...extra });

function capture(records = {}) {
  return {
    schemaVersion: '1.0', kind: 'jeju-baseline-capture', planSha256: plan.planSha256, system,
    requestBinding: prepareValidatedCollection(plan, inputs.catalog),
    startedAt: '2026-11-21T00:00:00.000Z', completedAt: '2026-11-21T01:00:00.000Z',
    records: plan.cases.map((entry, index) => {
      const { runtimeRequest } = buildBoundValidatedRequest(entry, inputs.catalog, `baseline-local-${String(index).padStart(8, '0')}`);
      const base = { caseId: entry.caseId, requestSha256: entry.requestSha256, runtimeRequest };
      if (records[index] === 'error') return { ...base, error: { code: 'invalid_output' } };
      return { ...base, raw: records[index] ?? raw(), measurements: { latencyMs: 1000 } };
    }),
  };
}

test('local LLM captures import as structured, model-bound responses and score with failures kept', () => {
  const run = importCapture(plan, capture({ 1: 'error' }), { catalog: inputs.catalog });
  assert.equal(run.system.id, 'local_llm');
  assert.equal(run.records[0].normalization.adapter, 'local-llm');
  assert.equal(run.records[0].normalization.mode, 'structured');
  assert.equal(run.records[0].response.action, 'ask_clarification');
  assert.deepEqual(run.records[1].error, { code: 'invalid_output' });
  const { summary } = scoreBaselineRun({ ...inputs, plan, run });
  assert.equal(summary.system, 'local_llm');
  assert.equal(summary.groups[0].failedCases, 1);
  assert.equal(summary.annotationReview.structured, plan.cases.length - 1);
  assert.equal(summary.annotationReview.modelReviewed, 0);
});

test('local LLM import refuses rejected gates, own envelopes, other models, reviews and missing provenance', () => {
  const fails = (records, pattern) => assert.throws(() => importCapture(plan, capture(records), { catalog: inputs.catalog }), pattern);
  fails({ 0: raw({ gate: { status: 'blocked', policyVersion: 'x' } }) }, /final validation/);
  fails({ 0: raw({ output: JSON.stringify({ ...decision, requestId: 'baseline-other-0000' }) }) }, /own envelope/);
  fails({ 0: raw({ output: '```json\n{}\n```' }) }, /exact JSON/);
  fails({ 0: raw({ output: JSON.stringify({ ...decision, extra: true }) }) }, /schema/);
  fails({ 0: raw({ model: 'other/model' }) }, /different model/);
  const withoutBinding = capture();
  delete withoutBinding.requestBinding;
  assert.throws(() => importCapture(plan, withoutBinding, { catalog: inputs.catalog }), /provenance/);
  assert.throws(() => adaptLocalLlmResponse({ itemId: plan.cases[0].itemId, raw: raw(), requestId: 'baseline-x-00000000', review: {} }), /review override/);
});
