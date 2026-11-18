// Offline import only. These adapters neither call a model nor infer meaning from reply keywords.
import { canonicalSha256 } from './evaluation-rng.mjs';
import { EvaluationSetError } from './evaluation-set.mjs';
import { validateResponse } from './evaluation-scoring.mjs';

export const BASELINE_ADAPTER_VERSION = '1.0.0';
export const MANUAL_REVIEW_RUBRIC_VERSION = 'manual-v1';
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const ensure = (condition, message) => { if (!condition) throw new EvaluationSetError(message); };
const same = (a, b) => canonicalSha256(a) === canonicalSha256(b);
const isoDate = (value) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value));
const text = (value) => typeof value === 'string' && value.trim().length > 0;

/** Hash the original local capture, not a truncated or filtered version of the response. */
export function baselineRawOutputHash(raw) {
  ensure(isRecord(raw), 'raw output must be a JSON object');
  return canonicalSha256(raw);
}

function normalizeResponse(itemId, annotation, measurements = {}) {
  ensure(text(itemId) && itemId.length <= 160, 'adapter needs an itemId');
  ensure(isRecord(annotation), 'response annotation must be an object');
  ensure(annotation.itemId === undefined || annotation.itemId === itemId, 'response itemId does not match the request');
  // Missing annotations are different from an observed absence of claims or notices.
  ensure(Array.isArray(annotation.requirementClaims) && typeof annotation.confirmationNotice === 'boolean'
    && Array.isArray(annotation.noticeTopics), 'response needs explicit claims, notice flag, and notice topics');
  ensure(isRecord(measurements) && Object.keys(measurements).every((key) => ['latencyMs', 'costUsd'].includes(key)),
    'measurements may only contain latencyMs and costUsd');
  return validateResponse({
    itemId, action: annotation.action, placeIds: annotation.placeIds,
    requirementClaims: annotation.requirementClaims, confirmationNotice: annotation.confirmationNotice,
    noticeTopics: annotation.noticeTopics,
    // Model-generated measurement claims are not measurements. Use the caller's observation only.
    latencyMs: measurements.latencyMs ?? null, costUsd: measurements.costUsd ?? null,
  });
}

function humanAnnotation(review, rawOutputHash, selector) {
  ensure(isRecord(review) && review.method === 'human', 'free-text output requires an explicit human review');
  ensure(text(review.reviewerId) && review.reviewerId.length <= 160 && isoDate(review.reviewedAt),
    'human review needs a reviewer alias and ISO reviewedAt');
  ensure(review.rubricVersion === MANUAL_REVIEW_RUBRIC_VERSION, 'unsupported manual review rubric');
  ensure(review.rawOutputHash === rawOutputHash, 'human review belongs to a different raw output');
  if (selector !== undefined) ensure(isRecord(review.outputSelector) && same(review.outputSelector, selector),
    'human review belongs to a different Langflow output selection');
  ensure(isRecord(review.response), 'human review needs a response annotation');
  return {
    annotation: review.response,
    audit: {
      method: review.method, reviewerId: review.reviewerId, reviewedAt: review.reviewedAt,
      rubricVersion: review.rubricVersion, rawOutputHash,
      ...(selector === undefined ? {} : { outputSelector: { ...selector } }),
    },
  };
}

/**
 * Pinned source: jeju-tour@b0602a1 prototype/src/assistant.ts runAssistant -> AssistantReply.
 * Recommendation order: displayed places first, then unseen add/replace action IDs. Remove-action IDs
 * are removal targets, not recommendations. Do not filter against the catalog or evaluation answer.
 * Semantic fields always come from a hash-bound human annotation, never course action.type or reply words.
 */
export function adaptGeminiResponse({ itemId, raw, review, measurements } = {}) {
  const rawOutputHash = baselineRawOutputHash(raw);
  ensure(typeof raw.reply === 'string' && Array.isArray(raw.places) && isRecord(raw.action),
    'Gemini import requires an AssistantReply capture');
  ensure(['gemini', 'keyword', 'none'].includes(raw.engine), 'AssistantReply needs a known engine');
  ensure(['none', 'replace', 'add', 'remove'].includes(raw.action.type) && Array.isArray(raw.action.placeIds),
    'AssistantReply action contract is invalid');
  ensure(raw.places.every((place) => isRecord(place) && text(place.id)) && raw.action.placeIds.every(text),
    'AssistantReply place IDs must be nonempty strings');
  ensure(raw.model === undefined || text(raw.model), 'AssistantReply model must be a nonempty string when supplied');
  const displayedIds = raw.places.map((place) => place.id);
  const courseIds = ['add', 'replace'].includes(raw.action.type) ? raw.action.placeIds : [];
  // The same place can occur in both UI picks and a proposed course. Normalize that overlap explicitly.
  const placeIds = [...new Set([...displayedIds, ...courseIds])];
  const { annotation, audit } = humanAnnotation(review, rawOutputHash);
  ensure(Array.isArray(annotation.placeIds) && same(annotation.placeIds, placeIds), 'human review must preserve all returned recommendation IDs in order');
  const response = normalizeResponse(itemId, annotation, measurements);
  return {
    response,
    normalization: {
      adapter: 'gemini-assistant', adapterVersion: BASELINE_ADAPTER_VERSION, mode: 'human_reviewed',
      rawOutputHash, review: audit, sourceEngine: raw.engine, reportedModel: raw.model ?? null,
      idPolicy: 'displayed_then_add_or_replace_unique',
      duplicateReferences: displayedIds.length + courseIds.length - placeIds.length,
      removalTargetCount: raw.action.type === 'remove' ? raw.action.placeIds.length : 0,
    },
  };
}

function selectedLangflowMessage(raw, outputSelector) {
  ensure(isRecord(outputSelector) && Object.keys(outputSelector).length === 2
    && Number.isSafeInteger(outputSelector.outputIndex) && outputSelector.outputIndex >= 0
    && text(outputSelector.componentId), 'Langflow needs outputIndex and componentId explicitly');
  ensure(Array.isArray(raw.outputs) && isRecord(raw.outputs[outputSelector.outputIndex]), 'Langflow output index does not exist');
  const outputs = raw.outputs[outputSelector.outputIndex].outputs;
  ensure(Array.isArray(outputs), 'Langflow run output needs component outputs');
  const selected = outputs.filter((entry) => isRecord(entry) && entry.component_id === outputSelector.componentId);
  ensure(selected.length === 1, 'Langflow component selection must identify exactly one output');
  const message = selected[0].results?.message;
  // In particular, do not fall back to inputs, artifacts, logs, or a different output component.
  ensure(isRecord(message) && message.sender === 'Machine' && typeof message.text === 'string',
    'selected Langflow output must contain a Machine message with text');
  return message.text;
}

/**
 * Accept one explicitly selected run response. Exact JSON may use the scoring response contract;
 * ordinary chat text requires manual-v1 annotations. JSON in markdown fences is ordinary text here.
 * This is an import contract, not evidence that a live Langflow run has been performed.
 */
export function adaptLangflowResponse({ itemId, raw, outputSelector, review, measurements } = {}) {
  const rawOutputHash = baselineRawOutputHash(raw);
  const reply = selectedLangflowMessage(raw, outputSelector);
  let annotation;
  let audit = null;
  let mode = 'structured';
  let exactJson = true;
  try { annotation = JSON.parse(reply); } catch { exactJson = false; }
  if (exactJson) {
    // A human must not repair returned IDs or constraint claims before they reach the scorer.
    ensure(review === undefined, 'structured Langflow output must be scored unchanged without a review override');
    ensure(isRecord(annotation), 'structured Langflow output must be a response object');
  } else {
    const reviewed = humanAnnotation(review, rawOutputHash, outputSelector);
    annotation = reviewed.annotation;
    audit = reviewed.audit;
    mode = 'human_reviewed';
  }
  const response = normalizeResponse(itemId, annotation, measurements);
  return {
    response,
    normalization: {
      adapter: 'langflow', adapterVersion: BASELINE_ADAPTER_VERSION, mode, rawOutputHash,
      outputSelector: { ...outputSelector }, review: audit,
    },
  };
}
