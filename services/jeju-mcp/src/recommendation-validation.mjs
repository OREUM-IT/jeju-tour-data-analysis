// Runtime approval of structured claims, never benchmark scoring or prose repair.
// The application must supply the original user/app request separately from the Agent output.
import { createHash } from 'node:crypto';
import { z } from 'zod';
import { APP_CATEGORIES, validateAgentCatalog } from './agent-catalog.mjs';
import { candidatePlaces } from './agent-tools.mjs';
import { parseYmd, runtimeRequirementEvidence } from './constraint-evidence.mjs';

export const RECOMMENDATION_POLICY_VERSION = 'recommendation-validation-v1';
const TYPES = ['indoor_required', 'open_at', 'wheelchair', 'budget_cap'];
const TOPICS = Object.freeze({ indoor_required: 'indoor_unverified', open_at: 'hours_unverified', wheelchair: 'wheelchair_unverified', budget_cap: 'cost_unknown' });
const id = z.string().min(1).max(160).refine((value) => value === value.trim());
const validDate = (value) => { try { parseYmd(value); return true; } catch { return false; } };
const date = z.string().refine(validDate);
const binaryRequirement = (type) => z.object({ type: z.literal(type), value: z.literal(true), date: date.optional() }).strict();
const requirement = z.discriminatedUnion('type', [
  binaryRequirement('indoor_required'), binaryRequirement('wheelchair'),
  z.object({ type: z.literal('open_at'), value: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/u), date: date.optional() }).strict(),
  z.object({ type: z.literal('budget_cap'), value: z.number().int().min(0).max(1_000_000_000), unit: z.literal('KRW_per_person_per_day'), date: date.optional() }).strict(),
]);
const requestSchema = z.object({
  schemaVersion: z.literal('1.0'), requestId: id, query: z.string().min(1).max(4000),
  timezone: z.literal('Asia/Seoul'), visitDate: date.optional(),
  filters: z.object({ category: z.enum(APP_CATEGORIES).optional(), theme: id.optional(), zone: id.optional(), text: z.string().min(1).max(100).refine((value) => value.trim().length > 0).optional() }).strict(),
  excludedIds: z.array(id).max(1000), requirements: z.array(requirement).max(4),
}).strict();
const responseSchema = z.object({
  schemaVersion: z.literal('1.0'), requestId: id,
  action: z.enum(['recommend', 'cannot_confirm', 'ask_clarification', 'impossible']),
  placeIds: z.array(id).max(50),
  requirementClaims: z.array(z.object({ placeId: id, requirement: z.enum(TYPES), verdict: z.enum(['pass', 'fail', 'unknown']) }).strict()).max(200),
  confirmationNotice: z.boolean(), noticeTopics: z.array(z.enum(Object.values(TOPICS))).max(4),
}).strict();
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value !== null && typeof value === 'object' ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
const digest = (value) => createHash('sha256').update(canonical(value)).digest('hex');
const issuePath = (issue) => issue.path.length ? issue.path.join('.') : '$';

export class RecommendationValidationError extends Error {
  constructor(issues) {
    super('Invalid trusted recommendation request');
    this.name = 'RecommendationValidationError';
    this.code = 'INVALID_RECOMMENDATION_REQUEST';
    this.issues = issues;
  }
}

/**
 * catalog and now are trusted server dependencies. No request field can select a clock, evidence,
 * candidate IDs or evaluation gold. This gate checks the structured contract only, not arbitrary prose.
 */
export function createRuntimeValidator({ catalog, now } = {}) {
  validateAgentCatalog(catalog);
  const { dataVersion, ...content } = catalog;
  const catalogSha256 = digest(content);
  const catalogVersion = dataVersion ?? `sha256:${catalogSha256}`;
  if (typeof catalogVersion !== 'string' || !/^sha256:[a-f0-9]{64}$/u.test(catalogVersion)) throw new TypeError('Invalid server catalog version');
  const places = new Map(catalog.places.map((place) => [place.id, place]));
  const zones = new Set(catalog.places.map((place) => place.zone).filter((zone) => zone !== null));

  function clock() {
    const value = typeof now === 'function' ? now() : now ?? new Date();
    const result = value instanceof Date ? value.getTime() : typeof value === 'number' ? value : Date.parse(value);
    if (!Number.isFinite(result)) throw new TypeError('Invalid server clock');
    return result;
  }

  function inspect(raw) {
    const parsed = requestSchema.safeParse(raw);
    if (!parsed.success) throw new RecommendationValidationError(parsed.error.issues.map((issue) => ({ code: 'invalid_request', path: issuePath(issue) })));
    const request = parsed.data;
    const issues = [];
    if (new Set(request.excludedIds).size !== request.excludedIds.length) issues.push({ code: 'duplicate_exclusion', path: 'excludedIds' });
    if (request.excludedIds.some((value) => !places.has(value))) issues.push({ code: 'unknown_exclusion', path: 'excludedIds' });
    if (new Set(request.requirements.map((entry) => entry.type)).size !== request.requirements.length) issues.push({ code: 'duplicate_requirement', path: 'requirements' });
    if (request.filters.theme !== undefined && !catalog.themeIds.includes(request.filters.theme)) issues.push({ code: 'unknown_theme', path: 'filters.theme' });
    if (request.filters.zone !== undefined && !zones.has(request.filters.zone)) issues.push({ code: 'unknown_zone', path: 'filters.zone' });
    request.requirements.forEach((entry, index) => {
      if (entry.type === 'open_at' && entry.date === undefined && request.visitDate === undefined) issues.push({ code: 'missing_visit_date', path: `requirements.${index}.date` });
    });
    if (issues.length) throw new RecommendationValidationError(issues);
    const atMs = clock();
    const candidates = candidatePlaces(catalog, { ...request.filters, excludedIds: request.excludedIds });
    return { request, atMs, candidates, metadata: {
      schemaVersion: '1.0', requestSha256: digest(request), catalogSha256, catalogVersion,
      policyVersion: RECOMMENDATION_POLICY_VERSION, evaluatedAt: new Date(atMs).toISOString(), candidateCount: candidates.length,
    } };
  }

  function prepare(request) {
    const context = inspect(request);
    return { ...context.metadata, status: 'prepared', request: context.request };
  }

  function validate({ request: rawRequest, response: rawResponse, expectedCatalogVersion } = {}) {
    const { request, atMs, candidates, metadata } = inspect(rawRequest);
    const reasons = [];
    const checks = [];
    const add = (code, path, extra = {}) => reasons.push({ code, path, ...extra });
    const blocked = () => ({ ...metadata, status: 'blocked', reasons, checks });
    if (expectedCatalogVersion !== undefined && expectedCatalogVersion !== catalogVersion) {
      add('catalog_version_mismatch', 'expectedCatalogVersion');
      return blocked();
    }
    const parsed = responseSchema.safeParse(rawResponse);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) add('invalid_response', issuePath(issue));
      return blocked();
    }
    const response = parsed.data;
    if (response.requestId !== request.requestId) add('request_id_mismatch', 'requestId');
    const candidateIds = new Set(candidates.map((place) => place.id));
    const excluded = new Set(request.excludedIds);
    const returned = new Set();
    response.placeIds.forEach((placeId, index) => {
      const extra = { placeId };
      if (returned.has(placeId)) add('duplicate_id', `placeIds.${index}`, extra);
      returned.add(placeId);
      if (!places.has(placeId)) add('not_in_catalog', `placeIds.${index}`, extra);
      else if (excluded.has(placeId)) add('excluded_id', `placeIds.${index}`, extra);
      else if (!candidateIds.has(placeId)) add('outside_candidates', `placeIds.${index}`, extra);
    });
    const requirements = new Map(request.requirements.map((entry) => {
      const visitDate = entry.date ?? request.visitDate;
      return [entry.type, { ...entry, ...(visitDate === undefined ? {} : { date: visitDate }) }];
    }));
    const claimKey = (placeId, type) => JSON.stringify([placeId, type]);
    const claims = new Map();
    response.requirementClaims.forEach((claim, index) => {
      const extra = { placeId: claim.placeId, requirement: claim.requirement };
      if (!returned.has(claim.placeId)) add('claim_outside_response', `requirementClaims.${index}`, extra);
      if (!requirements.has(claim.requirement)) add('unrequested_claim', `requirementClaims.${index}`, extra);
      const key = claimKey(claim.placeId, claim.requirement);
      if (claims.has(key)) add('duplicate_claim', `requirementClaims.${index}`, extra);
      else claims.set(key, claim);
    });
    const unknownTopics = new Set();
    for (const placeId of returned) {
      for (const [type, requirementValue] of requirements) {
        const extra = { placeId, requirement: type };
        const claim = claims.get(claimKey(placeId, type));
        if (!claim) add('missing_claim', 'requirementClaims', extra);
        if (!places.has(placeId)) continue;
        const evidence = runtimeRequirementEvidence(places.get(placeId), requirementValue, atMs);
        checks.push({ ...extra, verdict: evidence.verdict, basis: evidence.basis, reason: evidence.reason });
        if (evidence.verdict === 'unknown') unknownTopics.add(TOPICS[type]);
        if (claim && claim.verdict !== evidence.verdict) add(evidence.verdict === 'unknown' ? 'assertion_on_unknown' : 'claim_verdict_mismatch', 'requirementClaims', extra);
        if (evidence.verdict === 'fail') add('verified_requirement_failed', 'placeIds', extra);
      }
    }
    if (new Set(response.noticeTopics).size !== response.noticeTopics.length) add('duplicate_notice_topic', 'noticeTopics');
    for (const topic of unknownTopics) if (!response.noticeTopics.includes(topic)) add('missing_confirmation_topic', 'noticeTopics');
    for (const topic of response.noticeTopics) if (!unknownTopics.has(topic)) add('unsupported_confirmation_topic', 'noticeTopics');
    if (unknownTopics.size > 0 && !response.confirmationNotice) add('missing_confirmation_notice', 'confirmationNotice');

    if (response.action === 'recommend') {
      if (returned.size === 0) add('empty_recommendation', 'placeIds');
      if (checks.some((entry) => entry.verdict !== 'pass')) add('unconfirmed_recommendation', 'action');
    } else if (response.action === 'cannot_confirm') {
      if (returned.size === 0 || unknownTopics.size === 0) add('no_unconfirmed_candidate', 'action');
    } else {
      if (returned.size > 0 || response.requirementClaims.length > 0) add('non_place_action_has_places', 'action');
      if (response.action === 'impossible' && candidates.some((place) => ![...requirements.values()].some((entry) => runtimeRequirementEvidence(place, entry, atMs).verdict === 'fail'))) add('impossibility_not_established', 'action');
    }
    return reasons.length ? blocked() : { ...metadata, status: 'accepted', reasons, checks, response };
  }

  return Object.freeze({ prepare, validate });
}
