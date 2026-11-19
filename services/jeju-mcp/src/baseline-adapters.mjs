// Offline import only. These adapters neither call a model nor infer meaning from reply keywords.
import { canonicalSha256 } from './evaluation-rng.mjs';
import { EvaluationSetError } from './evaluation-set.mjs';
import { validateResponse } from './evaluation-scoring.mjs';
import { z } from 'zod';
import { VALIDATED_BASELINE_ADAPTER_VERSION } from './validated-capture-binding.mjs';

export const BASELINE_ADAPTER_VERSION = '1.0.0';
export const MANUAL_REVIEW_RUBRIC_VERSION = 'manual-v1';
export const MODEL_REVIEW_RUBRIC_VERSION = 'llm-review-v1';
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

function reviewedAnnotation(review, rawOutputHash, selector) {
  ensure(isRecord(review) && ['human', 'in_session_llm'].includes(review.method),
    'free-text output requires an explicit human review or in_session_llm review');
  const modelReviewed = review.method === 'in_session_llm';
  ensure(text(review.reviewerId) && review.reviewerId.length <= 160 && isoDate(review.reviewedAt),
    'review needs a reviewer alias and ISO reviewedAt');
  ensure(review.rubricVersion === (modelReviewed ? MODEL_REVIEW_RUBRIC_VERSION : MANUAL_REVIEW_RUBRIC_VERSION), 'unsupported review rubric');
  if (modelReviewed) {
    ensure(review.reviewerProvider === 'openai' && text(review.reviewerModel) && review.reviewerModel.length <= 240
      && !/[\p{Cc}\p{Cf}]/u.test(review.reviewerModel), 'in-session model review needs reviewerProvider openai and an honest reviewerModel label');
  } else {
    ensure(review.reviewerModel === undefined && review.reviewerProvider === undefined, 'human review cannot carry model reviewer metadata');
  }
  ensure(review.rawOutputHash === rawOutputHash, 'review belongs to a different raw output');
  if (selector !== undefined) ensure(isRecord(review.outputSelector) && same(review.outputSelector, selector),
    'review belongs to a different Langflow output selection');
  ensure(isRecord(review.response), 'review needs a response annotation');
  return {
    annotation: review.response,
    mode: modelReviewed ? 'model_reviewed' : 'human_reviewed',
    audit: {
      method: review.method, reviewerId: review.reviewerId, reviewedAt: review.reviewedAt,
      rubricVersion: review.rubricVersion, rawOutputHash,
      ...(modelReviewed ? { reviewerProvider: review.reviewerProvider, reviewerModel: review.reviewerModel } : {}),
      ...(selector === undefined ? {} : { outputSelector: { ...selector } }),
    },
  };
}

/**
 * Pinned source: jeju-tour@b0602a1 prototype/src/assistant.ts runAssistant -> AssistantReply.
 * Recommendation order: displayed places first, then unseen add/replace action IDs. Remove-action IDs
 * are removal targets, not recommendations. Do not filter against the catalog or evaluation answer.
 * Semantic fields come from a hash-bound human or explicitly labelled in-session model annotation, never keywords.
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
  const { annotation, audit, mode } = reviewedAnnotation(review, rawOutputHash);
  ensure(Array.isArray(annotation.placeIds) && same(annotation.placeIds, placeIds), 'review must preserve all returned recommendation IDs in order');
  const response = normalizeResponse(itemId, annotation, measurements);
  return {
    response,
    normalization: {
      adapter: 'gemini-assistant', adapterVersion: BASELINE_ADAPTER_VERSION, mode,
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

const v2Id = z.string().min(1).max(160).refine((value) => value === value.trim());
const validatedResponseSchema = z.object({
  schemaVersion: z.literal('1.0'), requestId: v2Id,
  action: z.enum(['recommend', 'cannot_confirm', 'ask_clarification', 'impossible']),
  placeIds: z.array(v2Id).max(50).refine((values) => new Set(values).size === values.length),
  requirementClaims: z.array(z.object({ placeId: v2Id,
    requirement: z.enum(['indoor_required', 'open_at', 'wheelchair', 'budget_cap']),
    verdict: z.enum(['pass', 'fail', 'unknown']),
  }).strict()).max(200),
  confirmationNotice: z.boolean(),
  noticeTopics: z.array(z.enum(['indoor_unverified', 'hours_unverified', 'wheelchair_unverified', 'cost_unknown'])).max(4)
    .refine((values) => new Set(values).size === values.length),
}).strict();

/** Only the selected final JSON is eligible; blocked prose is a failed response, never a reviewable replacement. */
export function validateValidatedLangflowResponse({ raw, outputSelector, requestId } = {}) {
  ensure(text(requestId), 'validated Langflow import requires the original runtime requestId');
  const reply = selectedLangflowMessage(raw, outputSelector);
  const message = raw.outputs[outputSelector.outputIndex].outputs.find((entry) => entry.component_id === outputSelector.componentId).results.message;
  ensure(message.properties?.source?.id === 'JejuFinalValidation-v2'
    && message.properties.source.source === 'Jeju Final Validation', 'validated Langflow output must identify the final validation component');
  let parsed;
  try { parsed = JSON.parse(reply); } catch { throw new EvaluationSetError('validated Langflow final output is not exact JSON'); }
  if (isRecord(parsed) && Object.keys(parsed).length === 3 && parsed.status === 'blocked'
    && ['RECOMMENDATION_BLOCKED', 'VALIDATION_FAILED'].includes(parsed.code) && typeof parsed.message === 'string') {
    const error = new EvaluationSetError('validated Langflow final gate did not accept a response');
    error.code = parsed.code;
    throw error;
  }
  const result = validatedResponseSchema.safeParse(parsed);
  ensure(result.success, 'validated Langflow final output violates its strict schema');
  ensure(result.data.requestId === requestId, 'validated Langflow final output has a different requestId');
  return result.data;
}

/** Mechanical conversion only: runtime schema/identity become an audit; claims and IDs are not repaired. */
export function adaptValidatedLangflowResponse({ itemId, raw, outputSelector, review, measurements, requestId } = {}) {
  ensure(review === undefined, 'validated Langflow output cannot carry a review override');
  const annotation = validateValidatedLangflowResponse({ raw, outputSelector, requestId });
  return {
    response: normalizeResponse(itemId, annotation, measurements),
    normalization: { adapter: 'langflow', adapterVersion: VALIDATED_BASELINE_ADAPTER_VERSION,
      mode: 'structured', rawOutputHash: baselineRawOutputHash(raw), outputSelector: { ...outputSelector },
      review: null, requestMode: 'langflow-v2', runtimeRequestId: requestId },
  };
}

/**
 * Accept one explicitly selected run response. Exact JSON may use the scoring response contract;
 * Ordinary chat text requires manual-v1 human or provisional llm-review-v1 in-session annotations.
 * JSON in markdown fences is ordinary text here. This adapter never performs the annotation itself.
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
    // Neither a human nor a model may repair IDs or claims in an already structured response before scoring.
    ensure(review === undefined, 'structured Langflow output must be scored unchanged without a review override');
    ensure(isRecord(annotation), 'structured Langflow output must be a response object');
  } else {
    const reviewed = reviewedAnnotation(review, rawOutputHash, outputSelector);
    annotation = reviewed.annotation;
    audit = reviewed.audit;
    mode = reviewed.mode;
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
