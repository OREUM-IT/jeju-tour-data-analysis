import test from 'node:test';
import assert from 'node:assert/strict';
import { createBaselinePlan, scoreBaselineRun } from '../src/baseline-evaluation.mjs';
import { adaptGatewayResponse } from '../src/baseline-adapters.mjs';
import { importCapture } from '../scripts/evaluate-baselines.mjs';
import { buildCapture, buildRequests, gatewayPayload, parseArguments, parseResults, referenceResults } from '../scripts/gateway-eval.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const VERSION = `sha256:${'d'.repeat(64)}`;
let counter = 0;
const requests = buildRequests(plan, inputs.catalog, { id: () => `baseline-gw-${String(counter++).padStart(8, '0')}` });
const decision = { action: 'ask_clarification', placeIds: [], requirementClaims: [], confirmationNotice: false, noticeTopics: [] };

function result(index, overrides = {}) {
  const gw = `gw-${index.toString(16).padStart(32, '0')}`;
  return { caseId: requests[index].caseId, runtimeRequestId: requests[index].runtimeRequest.requestId, final: 'accepted',
    submittedAt: '2026-11-25T00:00:00.000Z', finishedAt: '2026-11-25T00:00:20.000Z', totalSeconds: 20, runSeconds: 18, queueWaitSeconds: 2,
    gateway: { status: 'accepted', requestId: gw, catalogVersion: VERSION, policyVersion: 'recommendation-validation-v1' },
    response: { schemaVersion: '1.0', requestId: gw, ...decision }, ...overrides };
}
const build = (results) => buildCapture({ plan, catalog: inputs.catalog, requests, results, model: 'fixture-model', flow: 'rule-action',
  startedAt: '2026-11-25T00:00:00.000Z', completedAt: '2026-11-25T01:00:00.000Z' });

test('Gateway payloads carry only the fields the Gateway accepts; the runtime id stays with the harness', () => {
  assert.equal(requests.length, plan.cases.length);
  for (const entry of requests) {
    assert.deepEqual(Object.keys(entry.payload).sort(), ['excludedIds', 'filters', 'query', 'requirements', 'schemaVersion', 'timezone', 'visitDate']);
    assert.ok(!JSON.stringify(entry.payload).includes(entry.runtimeRequest.requestId));
  }
  assert.throws(() => gatewayPayload({ ...requests[0].payload, timezone: 'UTC' }), /contract/);
});

test('collected Gateway results import with failures kept and score as the gateway system', () => {
  const results = new Map(plan.cases.map((entry, index) => [entry.caseId, result(index)]));
  results.set(plan.cases[1].caseId, { caseId: plan.cases[1].caseId, runtimeRequestId: requests[1].runtimeRequest.requestId, final: 'failed', code: 'RECOMMENDATION_BLOCKED' });
  results.set(plan.cases[2].caseId, { caseId: plan.cases[2].caseId, runtimeRequestId: requests[2].runtimeRequest.requestId, final: 'unknown', code: 'POLL_DEADLINE_REACHED' });
  results.delete(plan.cases[3].caseId);
  const { capture, collection } = build(results);
  const run = importCapture(plan, capture, { catalog: inputs.catalog });
  assert.equal(run.system.id, 'gateway');
  assert.equal(run.records[0].normalization.adapter, 'gateway');
  assert.equal(run.records[0].normalization.runtimeRequestId, requests[0].runtimeRequest.requestId);
  assert.deepEqual([1, 2, 3].map((i) => run.records[i].error.code), ['invalid_output', 'timeout', 'not_executed']);
  const { summary } = scoreBaselineRun({ ...inputs, plan, run });
  assert.equal(summary.system, 'gateway');
  assert.equal(summary.groups[0].failedCases, 3);
  assert.equal(collection.outcomes['failed:RECOMMENDATION_BLOCKED'], 1);
  assert.equal(collection.outcomes.not_executed, 1);
  assert.equal(collection.timingSeconds.totalSeconds.count, plan.cases.length - 3);
  assert.ok(!JSON.stringify(collection).includes('gw-'));
});

test('Gateway import refuses mismatched identities, unversioned results, other models, reviews and foreign runs', () => {
  const fails = (overrides, pattern) => {
    const results = new Map(plan.cases.map((entry, index) => [entry.caseId, result(index, index === 0 ? overrides : {})]));
    assert.throws(() => importCapture(plan, build(results).capture, { catalog: inputs.catalog }), pattern);
  };
  fails({ response: { schemaVersion: '1.0', requestId: `gw-${'f'.repeat(32)}`, ...decision } }, /different Gateway request/);
  fails({ gateway: { status: 'accepted', requestId: `gw-${'0'.repeat(32)}`, catalogVersion: 'v1', policyVersion: 'recommendation-validation-v1' } }, /versioned/);
  fails({ response: { schemaVersion: '1.0', requestId: `gw-${'0'.repeat(32)}`, ...decision, extra: true } }, /schema/);
  assert.throws(() => build(new Map([[plan.cases[0].caseId, result(0, { runtimeRequestId: 'baseline-other-00000000' })]])), /different runtime request/);
  const { capture } = build(new Map(plan.cases.map((entry, index) => [entry.caseId, result(index)])));
  assert.throws(() => importCapture(plan, { ...capture, system: { ...capture.system, modelId: 'other' } }, { catalog: inputs.catalog }), /different model/);
  assert.throws(() => adaptGatewayResponse({ itemId: plan.cases[0].itemId, raw: capture.records[0].raw, requestId: 'baseline-x-00000000', review: {} }), /review override/);
});

test('result lines: the last line for a case wins; arguments keep data inside .local', () => {
  const lines = parseResults(`${JSON.stringify({ caseId: 'a', final: 'unknown' })}\n\n${JSON.stringify({ caseId: 'a', final: 'accepted' })}\n`);
  assert.equal(lines.get('a').final, 'accepted');
  assert.throws(() => parseArguments(['prepare', '--catalog', 'x/catalog.json', '--dir', '.local/x']), /inside .local/);
  assert.throws(() => parseArguments(['score', '--catalog', '.local/c.json', '--dir', '.local/x', '--label', 'gpt', '--model', 'm', '--flow', 'f', '--reports', '.local/r']), /outside .local/);
  assert.equal(parseArguments(['requests', '--catalog', '.local/c.json', '--dir', '.local/x', '--label', 'gpt']).label, 'gpt');
});

test('no-model reference: the rule-action server composes every answer offline and it scores as a gateway run', () => {
  const lines = referenceResults(requests, inputs.catalog);
  assert.equal(lines.length, plan.cases.length);
  assert.ok(lines.every((line) => ['accepted', 'failed'].includes(line.final)));
  const results = new Map(lines.map((line) => [line.caseId, line]));
  const { capture } = buildCapture({ plan, catalog: inputs.catalog, requests, results, model: 'no-model-first-3', flow: 'rule-action',
    startedAt: '2026-11-25T00:00:00.000Z', completedAt: '2026-11-25T00:00:00.000Z' });
  const run = importCapture(plan, capture, { catalog: inputs.catalog });
  assert.ok(run.records.filter((record) => record.response).every((record) => record.response.action !== 'ask_clarification'));
  assert.equal(scoreBaselineRun({ ...inputs, plan, run }).summary.system, 'gateway');
});
