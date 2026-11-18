// Scoring of structured responses against the step-3 evaluation set. Pure functions: no model call and no network.
// A system under test is mapped to the response contract below by its own harness; this module never reads free text.
import {
  EXPECTED_ACTIONS, HARD_REQUIREMENTS, NOTICE_TOPICS, EvaluationSetError, evaluationInstant, personaVector, requirementEvidence, themeAffinity,
} from './evaluation-set.mjs';

export const RESPONSE_VERDICTS = Object.freeze(['pass', 'fail', 'unknown']);
export const MAX_RESPONSE_PLACES = 50;
const PLACE_ACTIONS = new Set(['recommend', 'cannot_confirm']);
const KNOWN_TOPICS = new Set(Object.values(NOTICE_TOPICS));

const ensure = (condition, message) => { if (!condition) throw new EvaluationSetError(message); };
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const measure = (value) => value === undefined || value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0);

/**
 * Response contract: { itemId, action, placeIds (ranked, unique), requirementClaims?: [{ requirement, placeId?, verdict }],
 * confirmationNotice?: boolean, noticeTopics?: string[], latencyMs?, costUsd? }.
 * A claim without placeId speaks for every place the response returns. A missing measurement stays null, never 0.
 */
export function validateResponse(response) {
  ensure(isRecord(response) && typeof response.itemId === 'string' && EXPECTED_ACTIONS.includes(response.action), 'response needs itemId and a known action');
  ensure(Array.isArray(response.placeIds) && response.placeIds.length <= MAX_RESPONSE_PLACES
    && response.placeIds.every((id) => typeof id === 'string' && id.length > 0 && id.length <= 160)
    && new Set(response.placeIds).size === response.placeIds.length, `placeIds must be up to ${MAX_RESPONSE_PLACES} unique strings`);
  const claims = response.requirementClaims ?? [];
  ensure(Array.isArray(claims) && claims.every((claim) => isRecord(claim) && HARD_REQUIREMENTS.includes(claim.requirement)
    && RESPONSE_VERDICTS.includes(claim.verdict) && (claim.placeId === undefined || claim.placeId === null || typeof claim.placeId === 'string')),
  'requirementClaims must name a hard requirement and a verdict');
  ensure(response.confirmationNotice === undefined || typeof response.confirmationNotice === 'boolean', 'confirmationNotice must be a boolean');
  const topics = response.noticeTopics;
  ensure(topics === undefined || topics === null || (Array.isArray(topics) && topics.every((topic) => KNOWN_TOPICS.has(topic)) && new Set(topics).size === topics.length),
    'noticeTopics must be unique known notice topics');
  ensure(measure(response.latencyMs) && measure(response.costUsd), 'latencyMs and costUsd must be non-negative numbers or absent');
  return {
    itemId: response.itemId, action: response.action, placeIds: [...response.placeIds],
    requirementClaims: claims.map((claim) => ({ requirement: claim.requirement, placeId: claim.placeId ?? null, verdict: claim.verdict })),
    confirmationNotice: response.confirmationNotice === true,
    noticeTopics: Array.isArray(topics) ? [...topics] : null,
    latencyMs: response.latencyMs ?? null, costUsd: response.costUsd ?? null,
  };
}

/** Build a scorer bound to one catalog, persona file and spec. Validate a loaded set with validateEvaluationSet first. */
export function createScorer({ catalog, personas, spec }) {
  const themeIds = personas.themes.map((theme) => theme.id);
  const places = new Map(catalog.places.map((place) => [place.id, place]));
  const vectors = new Map(personas.prototypes.map((persona) => [persona.id, personaVector(persona, themeIds)]));
  const atMs = evaluationInstant(spec);
  const k = spec.metrics.recallK;

  /** What the catalog can say about one requirement for one place, by the same rule the generator uses. */
  function placeVerdict(item, requirement, placeId) {
    const place = places.get(placeId);
    if (place === undefined) return 'unknown';
    return requirementEvidence(place, item.conditions.find((condition) => condition.type === requirement), atMs).verdict;
  }

  function score(item, rawResponse) {
    const response = validateResponse(rawResponse);
    ensure(response.itemId === item.id, `response ${response.itemId} does not answer item ${item.id}`);
    const gold = item.gold;
    const allowed = new Set(gold.allowedIds);
    const forbidden = new Map(gold.forbidden.map((entry) => [entry.id, entry.reason]));
    const ids = { returned: response.placeIds.length, notInCatalog: 0, outsideCandidates: 0, user_excluded: 0, route_for_minimal_walk: 0, verified_fail: 0 };
    for (const id of response.placeIds) {
      if (allowed.has(id)) continue;
      if (!places.has(id)) ids.notInCatalog += 1;
      else if (forbidden.has(id)) ids[forbidden.get(id)] += 1;
      else ids.outsideCandidates += 1;
    }
    ids.disallowed = response.placeIds.filter((id) => !allowed.has(id)).length;

    const expected = new Map(gold.hardRequirements.map((r) => [r.type, r.expectedVerdict]));
    const claims = { total: response.requirementClaims.length, unrequested: 0, passOnUnknown: 0, failOnUnknown: 0, contradictsVerified: 0 };
    for (const claim of response.requirementClaims) {
      if (!expected.has(claim.requirement)) { claims.unrequested += 1; continue; }
      let verdict;
      if (claim.placeId !== null) verdict = placeVerdict(item, claim.requirement, claim.placeId);
      else if (response.placeIds.length > 0) {
        // A blanket claim covers every returned place: it is only as strong as the weakest of them.
        const each = response.placeIds.map((id) => placeVerdict(item, claim.requirement, id));
        verdict = each.every((value) => value === 'pass') ? 'pass' : each.every((value) => value === 'fail') ? 'fail' : 'unknown';
      } else verdict = expected.get(claim.requirement);
      if (verdict === 'unknown') {
        if (claim.verdict === 'pass') claims.passOnUnknown += 1;
        if (claim.verdict === 'fail') claims.failOnUnknown += 1;
      } else if (claim.verdict !== 'unknown' && claim.verdict !== verdict) claims.contradictsVerified += 1;
    }

    const placesExpected = PLACE_ACTIONS.has(gold.expectedAction);
    const relevant = new Set(gold.relevantIds);
    const topK = response.placeIds.slice(0, k);
    const hits = topK.filter((id) => relevant.has(id)).length;
    const recallAtK = placesExpected && relevant.size > 0 ? hits / Math.min(k, relevant.size) : null;
    // Precision needs returned places to divide by; an answer without places has none and stays null, not 0.
    const precisionAtK = placesExpected && relevant.size > 0 && topK.length > 0 ? hits / topK.length : null;
    const affinities = response.placeIds.map((id) => places.has(id) ? themeAffinity(vectors.get(item.personaId), places.get(id).themes) : null).filter((value) => value !== null);
    const topics = gold.notice.topics;
    return {
      itemId: item.id,
      expectedAction: gold.expectedAction,
      actionCorrect: response.action === gold.expectedAction,
      ids,
      claims,
      misjudgementApplicable: gold.hardRequirements.length > 0,
      passOnUnknown: claims.passOnUnknown > 0,
      failOnUnknown: claims.failOnUnknown > 0,
      recallAtK,
      precisionAtK,
      themeMatch: placesExpected && affinities.length > 0 ? affinities.reduce((sum, value) => sum + value, 0) / affinities.length : null,
      noticeRequired: gold.notice.required,
      noticeGiven: response.confirmationNotice,
      noticeTopicCoverage: gold.notice.required && response.noticeTopics !== null
        ? topics.filter((topic) => response.noticeTopics.includes(topic)).length / topics.length : null,
      latencyMs: response.latencyMs,
      costUsd: response.costUsd,
    };
  }

  /** A reference answer built from gold only: it must score perfectly, which checks gold and scorer against each other. */
  function oracle(item) {
    const gold = item.gold;
    const ranked = gold.candidates.filter((candidate) => candidate.relevant)
      .sort((a, b) => (b.affinity ?? -1) - (a.affinity ?? -1) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    return {
      itemId: item.id,
      action: gold.expectedAction,
      placeIds: PLACE_ACTIONS.has(gold.expectedAction) ? ranked.slice(0, k).map((candidate) => candidate.id) : [],
      requirementClaims: gold.hardRequirements.map((r) => ({ requirement: r.type, placeId: null, verdict: r.expectedVerdict })),
      confirmationNotice: gold.notice.required,
      noticeTopics: [...gold.notice.topics],
    };
  }

  return { k, score, oracle };
}

const mean = (values) => values.length === 0 ? null : values.reduce((sum, value) => sum + value, 0) / values.length;
const rate = (count, total) => total === 0 ? null : count / total;
function percentile(values, p) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1))];
}

/** Aggregate item scores into the metrics of data/evaluation-spec.json. Undefined ratios are null, not 0. */
export function aggregateScores(scores, k) {
  ensure(Array.isArray(scores) && scores.length > 0 && new Set(scores.map((score) => score.itemId)).size === scores.length, 'scores must cover distinct items');
  const sum = (key) => scores.reduce((total, score) => total + score.ids[key], 0);
  const returned = sum('returned');
  const applicable = scores.filter((score) => score.misjudgementApplicable);
  const noticeRequired = scores.filter((score) => score.noticeRequired);
  const recalls = scores.map((score) => score.recallAtK).filter((value) => value !== null);
  const precisions = scores.map((score) => score.precisionAtK).filter((value) => value !== null);
  const matches = scores.map((score) => score.themeMatch).filter((value) => value !== null);
  const coverage = scores.map((score) => score.noticeTopicCoverage).filter((value) => value !== null);
  const latencies = scores.map((score) => score.latencyMs).filter((value) => value !== null);
  const costs = scores.map((score) => score.costUsd).filter((value) => value !== null);
  return {
    items: scores.length,
    actionAccuracy: rate(scores.filter((score) => score.actionCorrect).length, scores.length),
    disallowedIds: {
      returnedIds: returned,
      rate: rate(sum('disallowed'), returned),
      itemsWithViolation: scores.filter((score) => score.ids.disallowed > 0).length,
      byReason: {
        notInCatalog: sum('notInCatalog'), outsideCandidates: sum('outsideCandidates'), userExcluded: sum('user_excluded'),
        routeForMinimalWalk: sum('route_for_minimal_walk'), verifiedFail: sum('verified_fail'),
      },
    },
    hardRequirementMisjudgement: {
      applicableItems: applicable.length,
      passOnUnknownItems: applicable.filter((score) => score.passOnUnknown).length,
      failOnUnknownItems: applicable.filter((score) => score.failOnUnknown).length,
      contradictsVerifiedItems: applicable.filter((score) => score.claims.contradictsVerified > 0).length,
      rate: rate(applicable.filter((score) => score.passOnUnknown || score.failOnUnknown).length, applicable.length),
    },
    recallAtK: { k, scoredItems: recalls.length, mean: mean(recalls) },
    precisionAtK: { k, scoredItems: precisions.length, mean: mean(precisions) },
    personaThemeMatch: { scoredItems: matches.length, mean: mean(matches) },
    confirmationNotice: {
      requiredItems: noticeRequired.length, given: noticeRequired.filter((score) => score.noticeGiven).length,
      rate: rate(noticeRequired.filter((score) => score.noticeGiven).length, noticeRequired.length),
      topicCoverage: { scoredItems: coverage.length, mean: mean(coverage) },
    },
    latencyMs: { measuredItems: latencies.length, p50: percentile(latencies, 0.5), p95: percentile(latencies, 0.95) },
    costUsd: { measuredItems: costs.length, total: costs.length === 0 ? null : costs.reduce((total, value) => total + value, 0) },
  };
}
