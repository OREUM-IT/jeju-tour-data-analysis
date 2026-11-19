import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptGeminiResponse, adaptLangflowResponse, baselineRawOutputHash } from '../src/baseline-adapters.mjs';

const itemId = 'EV-FIXTURE';
const annotation = (overrides = {}) => ({
  action: 'cannot_confirm', placeIds: ['FIXTURE_A'],
  requirementClaims: [{ requirement: 'wheelchair', verdict: 'unknown' }],
  confirmationNotice: true, noticeTopics: ['wheelchair_unverified'], ...overrides,
});
const assistant = (overrides = {}) => ({
  reply: '시설 정보를 확인할 수 없습니다.', action: { type: 'none', placeIds: [] },
  places: [{ id: 'FIXTURE_A', name: '합성 장소' }], engine: 'gemini', model: 'fixture-model', ...overrides,
});
const reviewOf = (raw, response, extras = {}) => ({
  method: 'human', reviewerId: 'fixture-reviewer', reviewedAt: '2026-11-19T09:30:00+09:00',
  rubricVersion: 'manual-v1', rawOutputHash: baselineRawOutputHash(raw), response, ...extras,
});
const modelReviewOf = (raw, response, extras = {}) => reviewOf(raw, response, {
  method: 'in_session_llm', reviewerId: 'fixture-codex-session', reviewerProvider: 'openai',
  reviewerModel: 'OpenAI Codex; exact model version unavailable', rubricVersion: 'llm-review-v1', ...extras,
});
const outputSelector = { outputIndex: 0, componentId: 'ChatOutput-Fixture' };
const flow = (messageText, { sender = 'Machine' } = {}) => ({
  outputs: [{
    inputs: { input_value: 'Never select this user prompt' },
    outputs: [
      { component_id: 'ChatInput-Fixture', results: { message: { text: 'prompt echo', sender: 'User' } } },
      { component_id: 'ChatOutput-Fixture', results: { message: { text: messageText, sender } } },
    ],
  }],
});

test('Gemini uses explicit reviewed meaning and preserves unapproved recommendation IDs', () => {
  const raw = assistant({
    reply: '정보가 없지만 휠체어 이용 가능으로 확정합니다.',
    places: [{ id: 'FIXTURE_OUTSIDE' }, { id: 'FIXTURE_A' }],
    action: { type: 'add', placeIds: ['FIXTURE_A', 'FIXTURE_OTHER_OUTSIDE'] },
  });
  const responseAnnotation = annotation({
    action: 'recommend', placeIds: ['FIXTURE_OUTSIDE', 'FIXTURE_A', 'FIXTURE_OTHER_OUTSIDE'],
    requirementClaims: [{ requirement: 'wheelchair', verdict: 'pass' }],
    confirmationNotice: false, noticeTopics: [],
  });
  const result = adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, responseAnnotation) });
  assert.deepEqual(result.response.placeIds, responseAnnotation.placeIds);
  assert.equal(result.response.requirementClaims[0].verdict, 'pass'); // Preserve the error for scoring.
  assert.equal(result.response.action, 'recommend');
  assert.equal(result.normalization.duplicateReferences, 1);
  assert.equal(result.normalization.sourceEngine, 'gemini');
  assert.equal(result.normalization.reportedModel, 'fixture-model');
  assert.equal(result.response.latencyMs, null);
  assert.equal(result.response.costUsd, null);
});

test('Gemini review cannot omit, reorder, or repair returned IDs and is bound to the raw output', () => {
  const raw = assistant({ places: [{ id: 'FIXTURE_A' }, { id: 'FIXTURE_OUTSIDE' }] });
  for (const ids of [['FIXTURE_A'], ['FIXTURE_OUTSIDE', 'FIXTURE_A'], ['FIXTURE_A', 'FIXTURE_REPAIRED']]) {
    assert.throws(() => adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation({ placeIds: ids })) }), /preserve/);
  }
  const review = reviewOf(raw, annotation({ placeIds: ['FIXTURE_A', 'FIXTURE_OUTSIDE'] }));
  assert.throws(() => adaptGeminiResponse({ itemId, raw: { ...raw, reply: 'edited response' }, review }), /different raw output/);
});

test('Gemini course removal and course action do not imply recommendation semantics', () => {
  const raw = assistant({ action: { type: 'remove', placeIds: ['FIXTURE_REMOVED'] } });
  const result = adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation()) });
  assert.deepEqual(result.response.placeIds, ['FIXTURE_A']);
  assert.equal(result.response.action, 'cannot_confirm');
  assert.equal(result.normalization.removalTargetCount, 1);
});

test('keyword fallback is retained and cannot be labelled as a Gemini model by normalization', () => {
  const raw = assistant({ engine: 'keyword' });
  delete raw.model;
  const result = adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation()) });
  assert.equal(result.normalization.sourceEngine, 'keyword');
  assert.equal(result.normalization.reportedModel, null);
  assert.throws(() => adaptGeminiResponse({ itemId, raw: assistant({ engine: 'invented' }), review: reviewOf(raw, annotation()) }), /known engine/);
});

test('free text is never classified by keywords and requires a full human annotation', () => {
  const raw = assistant();
  assert.throws(() => adaptGeminiResponse({ itemId, raw }), /human review/);
  assert.throws(() => adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation(), { method: 'llm' }) }), /human review/);
  for (const field of ['requirementClaims', 'confirmationNotice', 'noticeTopics']) {
    const response = annotation();
    delete response[field];
    assert.throws(() => adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, response) }), /explicit claims/);
  }
  assert.throws(() => adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation(), { reviewedAt: 'yesterday' }) }), /ISO reviewedAt/);
  assert.throws(() => adaptGeminiResponse({ itemId, raw, review: reviewOf(raw, annotation(), { rubricVersion: 'unreviewed-v0' }) }), /review rubric/);
});

test('Langflow selects the named output in the named run and retains invalid IDs', () => {
  const raw = flow(JSON.stringify(annotation({ placeIds: ['FIXTURE_OUTSIDE'], latencyMs: 7, costUsd: 1 })));
  const result = adaptLangflowResponse({ itemId, raw, outputSelector });
  assert.deepEqual(result.response.placeIds, ['FIXTURE_OUTSIDE']);
  assert.equal(result.normalization.mode, 'structured');
  assert.equal(result.normalization.review, null);
  assert.equal(result.response.latencyMs, null);
  assert.equal(result.response.costUsd, null);
  assert.equal(result.response.itemId, itemId);
});

test('Langflow rejects missing, duplicate, wrong-index and user-message selectors without fallback', () => {
  const raw = flow(JSON.stringify(annotation()));
  for (const selector of [undefined, {}, { outputIndex: 1, componentId: outputSelector.componentId },
    { outputIndex: 0, componentId: 'missing' }, { outputIndex: 0, componentId: 'ChatInput-Fixture' }]) {
    assert.throws(() => adaptLangflowResponse({ itemId, raw, outputSelector: selector }));
  }
  const duplicate = structuredClone(raw);
  duplicate.outputs[0].outputs.push(duplicate.outputs[0].outputs[1]);
  assert.throws(() => adaptLangflowResponse({ itemId, raw: duplicate, outputSelector }), /exactly one/);
  const wrongSender = flow(JSON.stringify(annotation()), { sender: 'User' });
  assert.throws(() => adaptLangflowResponse({ itemId, raw: wrongSender, outputSelector }), /Machine message/);
});

test('Langflow free text and fenced JSON require a review bound to both capture and output selection', () => {
  for (const reply of ['추천합니다. 시설은 확인이 필요합니다.', `\`\`\`json\n${JSON.stringify(annotation())}\n\`\`\``]) {
    const raw = flow(reply);
    assert.throws(() => adaptLangflowResponse({ itemId, raw, outputSelector }), /human review/);
    const review = reviewOf(raw, annotation(), { outputSelector });
    const result = adaptLangflowResponse({ itemId, raw, outputSelector, review });
    assert.equal(result.normalization.mode, 'human_reviewed');
    assert.deepEqual(result.response.placeIds, ['FIXTURE_A']);
    assert.throws(() => adaptLangflowResponse({ itemId, raw, outputSelector, review: { ...review, outputSelector: { ...outputSelector, outputIndex: 1 } } }), /different Langflow/);
  }
});

test('malformed structured output cannot silently default semantic fields or rewrite item identity', () => {
  for (const value of [null, [], { action: 'recommend', placeIds: [] }, annotation({ itemId: 'EV-DIFFERENT' }),
    annotation({ action: 'none' }), annotation({ placeIds: ['FIXTURE_A', 'FIXTURE_A'] }),
    annotation({ requirementClaims: [{ requirement: 'wheelchair', verdict: 'maybe' }] })]) {
    assert.throws(() => adaptLangflowResponse({ itemId, raw: flow(JSON.stringify(value)), outputSelector }));
  }
});

test('human review cannot repair structured Langflow IDs or requirement claims', () => {
  const raw = flow(JSON.stringify(annotation({
    placeIds: ['FIXTURE_OUTSIDE'], requirementClaims: [{ requirement: 'wheelchair', verdict: 'pass' }],
  })));
  assert.throws(() => adaptLangflowResponse({
    itemId, raw, outputSelector, review: reviewOf(raw, annotation(), { outputSelector }),
  }), /scored unchanged/);
  const result = adaptLangflowResponse({ itemId, raw, outputSelector });
  assert.deepEqual(result.response.placeIds, ['FIXTURE_OUTSIDE']);
  assert.equal(result.response.requirementClaims[0].verdict, 'pass');
});

test('only observed nonnegative measurements are accepted and explicit zero is preserved', () => {
  const raw = flow(JSON.stringify(annotation()));
  const result = adaptLangflowResponse({ itemId, raw, outputSelector, measurements: { latencyMs: 0, costUsd: 0 } });
  assert.equal(result.response.latencyMs, 0);
  assert.equal(result.response.costUsd, 0);
  for (const measurements of [{ latencyMs: -1 }, { costUsd: '0.01' }, { costUsd: Infinity }, { tokens: 30 }, null]) {
    assert.throws(() => adaptLangflowResponse({ itemId, raw, outputSelector, measurements }));
  }
});

test('normalization does not mutate captures or human annotations', () => {
  const raw = assistant();
  const review = reviewOf(raw, annotation());
  const before = JSON.stringify({ raw, review });
  const result = adaptGeminiResponse({ itemId, raw, review });
  result.response.placeIds.push('MUTATION');
  result.response.requirementClaims[0].verdict = 'fail';
  assert.equal(JSON.stringify({ raw, review }), before);
});

test('in-session Gemini annotation remains explicitly model-reviewed with exact metadata and wrong claims preserved', () => {
  const raw = assistant({ places: [{ id: 'FIXTURE_OUTSIDE' }] });
  const response = annotation({ placeIds: ['FIXTURE_OUTSIDE'], requirementClaims: [{ requirement: 'wheelchair', verdict: 'pass' }] });
  const review = modelReviewOf(raw, response);
  const before = JSON.stringify(review);
  const result = adaptGeminiResponse({ itemId, raw, review });
  assert.equal(result.normalization.mode, 'model_reviewed');
  assert.equal(result.normalization.review.method, 'in_session_llm');
  assert.equal(result.normalization.review.reviewerProvider, 'openai');
  assert.equal(result.normalization.review.reviewerModel, 'OpenAI Codex; exact model version unavailable');
  assert.equal(result.normalization.review.rubricVersion, 'llm-review-v1');
  assert.deepEqual(result.response.placeIds, ['FIXTURE_OUTSIDE']);
  assert.equal(result.response.requirementClaims[0].verdict, 'pass');
  assert.equal(result.response.costUsd, null);
  assert.equal(JSON.stringify(review), before);
  assert.ok(!Object.hasOwn(result.normalization.review, 'humanSpotCheck'));
});

test('model annotation requires provider, honest model label, version, time and matching raw output', () => {
  const raw = assistant();
  for (const extras of [
    { reviewerProvider: undefined }, { reviewerProvider: 'google' }, { reviewerModel: undefined },
    { reviewerModel: ' ' }, { reviewerModel: 'invalid\nlabel' }, { rubricVersion: 'manual-v1' },
    { reviewedAt: 'unrecorded' }, { rawOutputHash: 'a'.repeat(64) }, { method: 'llm' },
  ]) assert.throws(() => adaptGeminiResponse({ itemId, raw, review: modelReviewOf(raw, annotation(), extras) }));
  for (const ids of [[], ['FIXTURE_REPAIRED'], ['FIXTURE_A', 'FIXTURE_OTHER']]) {
    assert.throws(() => adaptGeminiResponse({ itemId, raw, review: modelReviewOf(raw, annotation({ placeIds: ids })) }), /preserve/u);
  }
});

test('changing a model review to human cannot retain machine metadata or manufacture a human audit', () => {
  const raw = assistant();
  const mislabeled = modelReviewOf(raw, annotation(), { method: 'human', rubricVersion: 'manual-v1' });
  assert.throws(() => adaptGeminiResponse({ itemId, raw, review: mislabeled }), /human review cannot carry model/u);
  const originalHuman = reviewOf(raw, annotation());
  const result = adaptGeminiResponse({ itemId, raw, review: originalHuman });
  assert.equal(result.normalization.mode, 'human_reviewed');
  assert.deepEqual(Object.keys(result.normalization.review).sort(), ['method', 'rawOutputHash', 'reviewedAt', 'reviewerId', 'rubricVersion']);
  assert.equal(result.normalization.review.method, 'human');
});

test('Langflow model annotation binds its selected free-text output while structured overrides remain forbidden', () => {
  const raw = flow('조건은 확인할 수 없습니다.');
  const review = modelReviewOf(raw, annotation(), { outputSelector });
  const result = adaptLangflowResponse({ itemId, raw, outputSelector, review });
  assert.equal(result.normalization.mode, 'model_reviewed');
  assert.equal(result.normalization.review.method, 'in_session_llm');
  assert.deepEqual(result.normalization.review.outputSelector, outputSelector);
  assert.throws(() => adaptLangflowResponse({ itemId, raw, outputSelector,
    review: { ...review, outputSelector: { ...outputSelector, outputIndex: 1 } } }), /different Langflow/u);
  const structured = flow(JSON.stringify(annotation({ placeIds: ['FIXTURE_OUTSIDE'] })));
  assert.throws(() => adaptLangflowResponse({ itemId, raw: structured, outputSelector,
    review: modelReviewOf(structured, annotation(), { outputSelector }) }), /scored unchanged/u);
});
