// Step-3 evaluation set: persona x condition bundles grounded in agent-catalog v2 facts, with gold computed by rule.
// Pure and deterministic. No network, no model inference, no clock and no Math.random. A requirement can only be
// judged pass/fail from `verified` evidence; everything else stays `unknown` and is carried as a separate signal.
import { validateAgentCatalog, validateThemeMap } from './agent-catalog.mjs';
import { canonicalSha256, createRng } from './evaluation-rng.mjs';
import { DAY_MS, KST_OFFSET_MS, TIME, EvaluationSetError, parseYmd, minutesOf, requirementEvidence } from './constraint-evidence.mjs';
export { EvaluationSetError, requirementEvidence } from './constraint-evidence.mjs';

export const EVALUATION_SET_SCHEMA_VERSION = '1.0';
export const EVALUATION_SET_KIND = 'jeju-agent-evaluation-set';
export const EVALUATION_SUMMARY_KIND = 'jeju-agent-evaluation-set-summary';
export const EVALUATION_BRIEFS_KIND = 'jeju-agent-evaluation-briefs';
export const EVALUATION_QUERIES_KIND = 'jeju-agent-evaluation-queries';
export const RULES_VERSION = 'evalset-rules-v1';
export const EXPECTED_ACTIONS = Object.freeze(['recommend', 'ask_clarification', 'cannot_confirm', 'impossible']);
export const DIFFICULTIES = Object.freeze(['simple', 'compound', 'conflict']);
export const REQUIRED_HARD_CASES = Object.freeze(['walk_minimal', 'accessibility_unknown', 'date_conflict', 'disallowed_id', 'empty_result']);
export const HARD_REQUIREMENTS = Object.freeze(['indoor_required', 'open_at', 'wheelchair', 'budget_cap']);
export const CONDITION_KINDS = Object.freeze({
  zone: 'filter', theme: 'filter', category: 'filter', transport: 'context', companion: 'context', walk: 'preference',
  indoor_required: 'hard_requirement', crowd_avoid: 'preference', open_at: 'hard_requirement', wheelchair: 'hard_requirement',
  budget_cap: 'hard_requirement', trip_dates: 'context', exclude_places: 'filter',
});
export const CONDITION_ORDER = Object.freeze(Object.keys(CONDITION_KINDS));
export const NOTICE_TOPICS = Object.freeze({
  wheelchair: 'wheelchair_unverified', indoor_required: 'indoor_unverified', open_at: 'hours_unverified', budget_cap: 'cost_unknown',
  public_transit: 'transit_access_unknown', companion: 'companion_suitability_unknown', walk: 'walking_distance_unknown',
  crowd_avoid: 'crowd_not_guaranteed',
});
export const SLOT_KINDS = Object.freeze(['plain', 'walk_minimal', 'accessibility_unknown', 'date_conflict', 'disallowed_id', 'empty_result']);
export const SELECTOR_KINDS = Object.freeze(['theme', 'category', 'persona', 'theme_or_category']);
export const AUTHOR_KINDS = Object.freeze(['in_session_llm', 'external_llm', 'human']);
export const FORBIDDEN_REASONS = Object.freeze(['user_excluded', 'route_for_minimal_walk', 'verified_fail']);
export const EMPTY_MECHANISMS = Object.freeze(['empty_cell', 'all_routes_minimal_walk', 'all_excluded']);
/** Text rules for a query, in the order data/evaluation-spec.json documents them. The last one applies to authored text only. */
export const TEXT_CHECKS = Object.freeze(['length', 'single_line', 'korean', 'request_wording', 'no_place_id', 'named_places', 'condition_cues', 'no_unstated_cues', 'no_candidate_names']);
const CUE_MAPS = Object.freeze(['zone', 'theme', 'category', 'transport', 'companion', 'walk']);
const CUE_LISTS = Object.freeze(['indoor_required', 'rain', 'crowd_avoid', 'wheelchair', 'exclude_places', 'visited']);
/** Condition kinds that have a list of extra words a query must not use unless it states that kind. */
const UNSTATED_KINDS = Object.freeze(['zone', 'transport', 'companion', 'walk', 'indoor_required', 'crowd_avoid', 'open_at', 'wheelchair', 'budget_cap', 'exclude_places', 'trip_dates']);
const MAX_QUERY_LENGTH = 200; // the upper limit the reference method gives its query writer; the spec may be stricter, not looser
/** Place-ID prefixes of the reference app. Used to keep IDs out of query text and of the committed summary. */
export const PLACE_ID_PREFIX = /(?:^|[^a-z0-9])(?:kk|osm|cur|vj|nv|bf|olle|trail|hy|drama|bike|hub)-[a-z0-9]/iu;
const STAY = 'stay';
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
const SHA256 = /^[a-f0-9]{64}$/u;

const ensure = (condition, message) => { if (!condition) throw new EvaluationSetError(message); };
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, max = 2000) => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const byId = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
const round4 = (value) => Math.round(value * 10_000) / 10_000;
const countBy = (items, key) => items.reduce((acc, item) => {
  const k = key(item);
  if (k !== undefined && k !== null) acc[k] = (acc[k] ?? 0) + 1;
  return acc;
}, {});
const sortedObject = (object) => Object.fromEntries(Object.keys(object).sort().map((key) => [key, object[key]]));

const addDays = (ymd, days) => new Date(parseYmd(ymd) + days * DAY_MS).toISOString().slice(0, 10);
const daysBetween = (from, to) => Math.round((parseYmd(to) - parseYmd(from)) / DAY_MS);
function koreanDate(ymd) {
  const date = new Date(parseYmd(ymd));
  return `${date.getUTCMonth() + 1}월 ${date.getUTCDate()}일(${WEEKDAYS[date.getUTCDay()]})`;
}
function koreanTime(time) {
  const minutes = minutesOf(time);
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  const label = hour === 0 ? '밤 12시' : hour < 12 ? `오전 ${hour}시` : hour === 12 ? '낮 12시' : `오후 ${hour - 12}시`;
  return minute === 0 ? label : `${label} ${minute}분`;
}
const koreanWon = (amount) => amount % 10_000 === 0 ? `${amount / 10_000}만 원` : `${amount.toLocaleString('en-US')}원`;

/** Validate the committed condition/difficulty spec against the persona definitions and the theme map. */
export function validateEvaluationSpec(spec, personas, themeMap) {
  ensure(isRecord(spec) && text(spec.version, 80) && text(spec.provenance) && text(spec.seed, 200), 'spec version, provenance and seed are required');
  ensure(spec.rulesVersion === RULES_VERSION, `spec rulesVersion must be ${RULES_VERSION}`);
  ensure(isRecord(personas) && Array.isArray(personas.themes) && Array.isArray(personas.prototypes) && personas.prototypes.length > 0, 'persona definitions are required');
  const themeIds = personas.themes.map((theme) => theme.id);
  validateThemeMap(themeMap, themeIds);
  ensure(spec.personaVersion === personas.version && spec.themeMapVersion === themeMap.version && themeMap.personaVersion === personas.version,
    'spec, persona and theme map versions must agree');
  parseYmd(spec.anchorDate);
  ensure(finite(spec.themeThreshold) && spec.themeThreshold > 0 && spec.themeThreshold <= 1, 'themeThreshold must be in (0,1]');
  ensure(finite(spec.personaFloor) && spec.personaFloor > 0 && spec.personaFloor < 1, 'personaFloor must be in (0,1)');
  const labelled = (list, label) => {
    ensure(Array.isArray(list) && list.length > 0 && list.every((row) => isRecord(row) && text(row.id, 40) && text(row.label, 80)), `${label} need id and label`);
    ensure(new Set(list.map((row) => row.id)).size === list.length, `${label} ids must be unique`);
    return list.map((row) => row.id);
  };
  const zoneIds = labelled(spec.zones, 'zones');
  const categoryIds = labelled(spec.categories, 'categories');
  const expectedCategories = themeMap.appCategories.filter((cat) => cat !== STAY);
  ensure(categoryIds.length === expectedCategories.length && expectedCategories.every((cat) => categoryIds.includes(cat)),
    'categories must be exactly the non-stay app categories');
  ensure(Array.isArray(spec.conditions) && spec.conditions.length === CONDITION_ORDER.length, 'conditions must define every condition type once');
  spec.conditions.forEach((condition, index) => {
    ensure(isRecord(condition) && condition.id === CONDITION_ORDER[index], `conditions[${index}] must be ${CONDITION_ORDER[index]} (canonical order)`);
    ensure(condition.kind === CONDITION_KINDS[condition.id] && text(condition.label, 80) && text(condition.evidence) && text(condition.rule),
      `condition ${condition.id} needs its fixed kind, a label, the evidence it reads and its rule`);
  });
  const valuesOf = (id) => spec.conditions.find((condition) => condition.id === id).values;
  const enumValues = { transport: ['rental_car', 'public_transit'], companion: ['child', 'parents'], walk: ['minimal'], trip_dates: ['nights_mismatch', 'date_outside_trip'] };
  for (const [id, allowed] of Object.entries(enumValues)) {
    const values = valuesOf(id);
    ensure(Array.isArray(values) && values.length > 0 && new Set(values).size === values.length && values.every((value) => allowed.includes(value)),
      `condition ${id} values must come from ${allowed.join(', ')}`);
  }
  const times = valuesOf('open_at');
  ensure(Array.isArray(times) && times.length > 0 && new Set(times).size === times.length && times.every((time) => TIME.test(time)), 'open_at values must be unique HH:MM times');
  const budgets = valuesOf('budget_cap');
  ensure(Array.isArray(budgets) && budgets.length > 0 && budgets.every((amount) => Number.isInteger(amount) && amount > 0), 'budget_cap values must be positive integers (KRW)');
  const idsOf = (list, label, expected) => {
    ensure(Array.isArray(list) && list.every((row) => isRecord(row) && text(row.id, 60)), `${label} must be a list of records with ids`);
    const ids = list.map((row) => row.id);
    ensure(ids.length === expected.length && expected.every((id) => ids.includes(id)), `${label} must be exactly: ${expected.join(', ')}`);
    return ids;
  };
  idsOf(spec.expectedActions, 'expectedActions', EXPECTED_ACTIONS);
  ensure(spec.expectedActions.every((row) => text(row.when)), 'every expected action needs its condition');
  ensure(JSON.stringify(spec.expectedActions.map((row) => row.id)) === JSON.stringify(['impossible', 'ask_clarification', 'cannot_confirm', 'recommend']),
    'expectedActions must be listed in precedence order');
  idsOf(spec.difficulties, 'difficulties', DIFFICULTIES);
  idsOf(spec.hardCases, 'hardCases', REQUIRED_HARD_CASES);
  ensure(Array.isArray(spec.noticeTopics) && JSON.stringify([...spec.noticeTopics].sort()) === JSON.stringify(Object.values(NOTICE_TOPICS).sort()),
    'noticeTopics must list every notice topic');
  const blueprint = spec.blueprint;
  ensure(isRecord(blueprint) && Array.isArray(blueprint.slots) && blueprint.slots.length > 0 && blueprint.itemsPerPersona === blueprint.slots.length,
    'blueprint.itemsPerPersona must equal the number of slots');
  const slotIds = new Set();
  for (const slot of blueprint.slots) {
    ensure(isRecord(slot) && /^[A-Z][A-Z0-9]{0,7}$/u.test(slot.id ?? '') && !slotIds.has(slot.id), 'slot ids must be unique short uppercase tokens');
    slotIds.add(slot.id);
    ensure(SLOT_KINDS.includes(slot.kind) && SELECTOR_KINDS.includes(slot.selector) && typeof slot.zone === 'boolean', `slot ${slot.id} has an unknown kind, selector or zone flag`);
    ensure((slot.kind === 'plain') === (slot.hardCase === undefined) && (slot.hardCase === undefined || slot.hardCase === slot.kind),
      `slot ${slot.id}: hard-case slots must name their hard case and plain slots must not`);
    ensure((slot.selector === 'theme_or_category') === (slot.kind === 'empty_result'), `slot ${slot.id}: theme_or_category is reserved for empty_result`);
    ensure(Array.isArray(slot.extras) && slot.extras.every((alternatives) => Array.isArray(alternatives) && alternatives.length > 0
      && alternatives.every((type) => CONDITION_ORDER.includes(type) && !['zone', 'theme', 'category'].includes(type))), `slot ${slot.id} extras must name non-selector conditions`);
    const drawn = slot.extras.flat();
    ensure(new Set(drawn).size === drawn.length, `slot ${slot.id} lists a condition twice`);
    const required = { walk_minimal: 'walk', accessibility_unknown: 'wheelchair', date_conflict: 'trip_dates', disallowed_id: 'exclude_places' }[slot.kind];
    ensure(required === undefined || slot.extras.some((alternatives) => alternatives.length === 1 && alternatives[0] === required), `slot ${slot.id} must always draw ${required}`);
    ensure(slot.kind === 'date_conflict' || !drawn.includes('trip_dates'), `slot ${slot.id}: trip_dates belongs to the date_conflict slot`);
    ensure(slot.kind === 'disallowed_id' || !drawn.includes('exclude_places'), `slot ${slot.id}: exclude_places belongs to the disallowed_id slot`);
    ensure(slot.kind !== 'empty_result' || (slot.zone && slot.extras.length === 0), `slot ${slot.id}: empty_result draws a zone and no extras`);
    ensure(!(slot.selector === 'category' && drawn.includes('walk')), `slot ${slot.id}: walk is not combined with a category selector`);
    const stated = (slot.selector === 'persona' ? 0 : 1) + (slot.zone ? 1 : 0) + slot.extras.length;
    ensure(stated >= 1 && stated <= 4 - (slot.kind === 'empty_result' ? 1 : 0), `slot ${slot.id} must state 1 to 4 conditions`);
  }
  const hardCases = blueprint.slots.map((slot) => slot.hardCase).filter(Boolean);
  ensure(REQUIRED_HARD_CASES.every((id) => hardCases.includes(id)), 'the blueprint must contain every mandatory hard case');
  ensure(isRecord(spec.metrics) && Number.isInteger(spec.metrics.recallK) && spec.metrics.recallK > 0 && Array.isArray(spec.metrics.definitions)
    && spec.metrics.definitions.every((row) => isRecord(row) && text(row.id, 60) && text(row.formula)), 'metrics need recallK and formulas');
  const reference = spec.methodReference;
  ensure(isRecord(reference) && text(reference.id, 60) && typeof reference.repository === 'string' && /^[\w.-]+\/[\w.-]+$/u.test(reference.repository)
    && typeof reference.commit === 'string' && /^[0-9a-f]{40}$/u.test(reference.commit) && text(reference.license, 40) && text(reference.scope),
  'methodReference needs an id, a repository, a full commit, a license and the scope that was reviewed');
  parseYmd(reference.reviewedOn);
  const authoring = spec.queryAuthoring;
  ensure(isRecord(authoring) && text(authoring.promptVersion, 80) && text(authoring.prompt, 4000), 'queryAuthoring needs a prompt and its version');
  const rules = authoring.textRules;
  ensure(isRecord(rules) && Object.keys(rules).length === 4 && Number.isInteger(rules.minLength) && Number.isInteger(rules.maxLength) && rules.minLength >= 1
    && rules.maxLength > rules.minLength && rules.maxLength <= MAX_QUERY_LENGTH && Number.isInteger(rules.candidateNameMinLength)
    && rules.candidateNameMinLength >= 2 && rules.candidateNameMinLength <= 4,
  `queryAuthoring.textRules needs minLength, maxLength (at most ${MAX_QUERY_LENGTH}), candidateNameMinLength (2 to 4) and checks`);
  ensure(Array.isArray(rules.checks) && JSON.stringify(rules.checks.map((row) => row?.id)) === JSON.stringify(TEXT_CHECKS) && rules.checks.every((row) => text(row.rule)),
    `queryAuthoring.textRules.checks must document, in order: ${TEXT_CHECKS.join(', ')}`);
  const cues = authoring.cues;
  ensure(isRecord(cues) && Object.keys(cues).length === CUE_MAPS.length + CUE_LISTS.length, 'queryAuthoring.cues must hold exactly the known cue groups');
  const words = (list) => Array.isArray(list) && list.length > 0 && new Set(list).size === list.length
    && list.every((word) => text(word, 40) && word === word.trim() && /[가-힣]/u.test(word));
  const cueIds = { zone: zoneIds, theme: themeIds, category: categoryIds, transport: valuesOf('transport'), companion: valuesOf('companion'), walk: valuesOf('walk') };
  for (const key of CUE_MAPS) {
    const map = cues[key];
    ensure(isRecord(map) && Object.keys(map).length === cueIds[key].length && cueIds[key].every((id) => words(map[id])),
      `queryAuthoring.cues.${key} must list Korean cue words for exactly: ${cueIds[key].join(', ')}`);
  }
  for (const key of CUE_LISTS) ensure(words(cues[key]), `queryAuthoring.cues.${key} must be a list of Korean cue words`);
  // A cue that belongs to one value must not also read as another value, or the unstated-condition check cannot tell them apart.
  for (const key of ['zone', 'transport', 'companion']) {
    const owned = Object.entries(cues[key]).flatMap(([id, list]) => list.map((word) => [id, word]));
    ensure(owned.every(([id, word]) => owned.every(([other, otherWord]) => other === id || !otherWord.includes(word))),
      `queryAuthoring.cues.${key} uses a cue word that also matches another value`);
  }
  const unstated = authoring.unstatedWords;
  ensure(isRecord(unstated) && Object.keys(unstated).length === UNSTATED_KINDS.length && UNSTATED_KINDS.every((kind) => words(unstated[kind])),
    `queryAuthoring.unstatedWords must list Korean words for exactly: ${UNSTATED_KINDS.join(', ')}`);
  const matching = authoring.cueMatching;
  ensure(isRecord(matching) && Object.keys(matching).length === 3 && text(matching.note) && Array.isArray(matching.wordStart)
    && new Set(matching.wordStart).size === matching.wordStart.length && matching.wordStart.every((key) => CUE_MAPS.includes(key) || CUE_LISTS.includes(key))
    && Array.isArray(matching.ignoredPhrases) && (matching.ignoredPhrases.length === 0 || words(matching.ignoredPhrases)),
  'queryAuthoring.cueMatching needs wordStart (known cue groups), ignoredPhrases (Korean phrases) and a note');
  // An ignored phrase is erased before cues are looked for, so it must not swallow a whole cue word.
  const everyCue = [...CUE_MAPS.flatMap((key) => Object.values(cues[key]).flat()), ...CUE_LISTS.flatMap((key) => cues[key])];
  ensure(matching.ignoredPhrases.every((phrase) => everyCue.every((word) => !phrase.includes(word) || phrase.length > word.length)
    && !everyCue.includes(phrase)), 'queryAuthoring.cueMatching.ignoredPhrases must not equal a cue word');
  return { themeIds, zoneIds, categoryIds };
}

/** The conflict a trip statement really contains, computed from its own dates; null when the statement is consistent. */
export function tripConflict(trip) {
  ensure(isRecord(trip) && Number.isInteger(trip.nights) && trip.nights >= 0 && daysBetween(trip.startDate, trip.endDate) === trip.nights,
    'trip nights must equal the span between its start and end dates');
  if (trip.statedNights !== undefined) {
    ensure(Number.isInteger(trip.statedNights) && trip.statedNights >= 0, 'stated nights must be a non-negative integer');
    if (trip.statedNights !== trip.nights) return 'nights_mismatch';
  }
  if (trip.requestedDate !== undefined) {
    parseYmd(trip.requestedDate);
    if (trip.requestedDate < trip.startDate || trip.requestedDate > trip.endDate) return 'date_outside_trip';
  }
  return null;
}

/** Precedence of the expected action. The order is part of the spec (expectedActions). */
export function expectedAction({ allowed, dateConflict, requirements, confirmed }) {
  if (allowed === 0) return 'impossible';
  if (dateConflict) return 'ask_clarification';
  if (requirements > 0 && confirmed === 0) return 'cannot_confirm';
  return 'recommend';
}

/** Mechanical difficulty: conflict beats the condition count. */
export function classifyDifficulty({ conflicts, statedConditions }) {
  if (conflicts.length > 0) return 'conflict';
  return statedConditions === 1 ? 'simple' : 'compound';
}

export function personaVector(persona, themeIds) {
  ensure(isRecord(persona) && Array.isArray(persona.themes) && persona.themes.length === themeIds.length && persona.themes.every((w) => finite(w) && w >= 0),
    'persona theme vector must align with the theme ids');
  return Object.fromEntries(themeIds.map((theme, index) => [theme, persona.themes[index]]));
}

/** Themes tied at the persona's maximum weight, in theme order. */
export function primaryThemes(vector) {
  const max = Math.max(...Object.values(vector));
  return Object.keys(vector).filter((theme) => vector[theme] >= max - 1e-9);
}

/** Cosine similarity between the persona theme vector and a place's theme weights; null when either side is empty. */
export function themeAffinity(vector, placeThemes) {
  let dot = 0;
  let a = 0;
  let b = 0;
  for (const [theme, weight] of Object.entries(vector)) {
    const other = placeThemes[theme] ?? 0;
    dot += weight * other;
    a += weight * weight;
    b += other * other;
  }
  return a > 0 && b > 0 ? dot / Math.sqrt(a * b) : null;
}

/** Evidence tiers used to pick gold candidates, best first. */
const EVIDENCE_TIERS = Object.freeze(['verified_pass', 'reported_support', 'not_contradicted']);
const evidenceTier = (evidence) => evidence.verdict === 'pass' ? 0 : evidence.signal === 'supports' && evidence.tier === 'reported' ? 1 : 2;

/** Gold rule's route test (route_for_minimal_walk). Exported so a test can hold the product predicate to it. */
export const hasRoute = (place) => place.attributes.route !== undefined;
const popularityOf = (place) => finite(place.attributes.popularity?.value) ? place.attributes.popularity.value : -1;

function matchesSelector(place, selector, threshold) {
  if (place.category === STAY) return false;
  if (selector.category !== null) return place.category === selector.category;
  return selector.themes.some((theme) => (place.themes[theme] ?? 0) >= threshold);
}

function tallyEvidence(rows, type) {
  const verdicts = { pass: 0, fail: 0, unknown: 0 };
  const signals = { supports: { verified: 0, reported: 0, inferred: 0 }, contradicts: { verified: 0, reported: 0, inferred: 0 }, none: 0 };
  const basis = {};
  for (const row of rows) {
    const evidence = row.evidence[type];
    verdicts[evidence.verdict] += 1;
    if (evidence.signal === 'none') signals.none += 1;
    else signals[evidence.signal][evidence.tier] += 1;
    if (evidence.basis !== null) basis[evidence.basis] = (basis[evidence.basis] ?? 0) + 1;
  }
  return { verdicts, signals, basis: sortedObject(basis) };
}

/**
 * Rule-computed gold for one bundle.
 * `places` are catalog places; `selector` = { source, themes, category }; `conditions` are canonical condition records.
 */
export function computeGold({ places, vector, selector, zone, conditions, atMs, threshold }) {
  const requirements = conditions.filter((condition) => CONDITION_KINDS[condition.type] === 'hard_requirement');
  const excluded = new Set(conditions.find((condition) => condition.type === 'exclude_places')?.value ?? []);
  const walkMinimal = conditions.some((condition) => condition.type === 'walk' && condition.value === 'minimal');
  const crowdAvoid = conditions.some((condition) => condition.type === 'crowd_avoid');
  const trip = conditions.find((condition) => condition.type === 'trip_dates');
  const statedConflict = trip === undefined ? null : tripConflict(trip.value);
  ensure(trip === undefined || statedConflict === trip.value.conflict, 'the trip conflict label does not match its dates');
  const dateConflict = statedConflict !== null;
  const selectorMatches = places.filter((place) => matchesSelector(place, selector, threshold));
  const base = selectorMatches.filter((place) => zone === null || place.zone === zone);
  ensure([...excluded].every((id) => base.some((place) => place.id === id)), 'excluded places must be candidates of the same bundle');

  const forbidden = [];
  const rows = [];
  for (const place of base) {
    if (excluded.has(place.id)) forbidden.push({ id: place.id, name: place.name, reason: 'user_excluded' });
    else if (walkMinimal && hasRoute(place)) forbidden.push({ id: place.id, name: place.name, reason: 'route_for_minimal_walk' });
    else rows.push({ place, evidence: Object.fromEntries(requirements.map((r) => [r.type, requirementEvidence(place, r, atMs)])) });
  }
  // Non-compensatory: a verified fail removes the place; nothing can score it back in.
  const failed = rows.filter((row) => requirements.some((r) => row.evidence[r.type].verdict === 'fail'));
  for (const row of failed) {
    forbidden.push({ id: row.place.id, name: row.place.name, reason: 'verified_fail',
      requirements: requirements.filter((r) => row.evidence[r.type].verdict === 'fail').map((r) => r.type) });
  }
  const allowed = rows.filter((row) => !failed.includes(row));
  const confirmed = requirements.length > 0 ? allowed.filter((row) => requirements.every((r) => row.evidence[r.type].verdict === 'pass')) : [];

  // Gold candidates for recall: the candidates with the best available evidence, never treating a signal as a verdict.
  // A candidate contradicted on any requirement is out. Among the rest, one is kept unless another is at least as well
  // evidenced on every requirement and better on one (so the result does not depend on the order of requirements).
  const relevantRules = [];
  const uncontradicted = allowed.filter((row) => requirements.every((r) => row.evidence[r.type].signal !== 'contradicts'));
  let relevant = allowed;
  if (confirmed.length > 0) {
    relevant = confirmed;
    relevantRules.push('relevant.verified_pass_on_every_requirement');
  } else if (requirements.length > 0) {
    const tiers = new Map(uncontradicted.map((row) => [row, requirements.map((r) => evidenceTier(row.evidence[r.type]))]));
    const dominates = (a, b) => a.every((tier, index) => tier <= b[index]) && a.some((tier, index) => tier < b[index]);
    relevant = uncontradicted.filter((row) => !uncontradicted.some((other) => dominates(tiers.get(other), tiers.get(row))));
    if (relevant.length === 0) relevantRules.push('relevant.none:every_candidate_contradicts_a_requirement');
    else requirements.forEach((r, index) => relevantRules.push(`relevant.${r.type}:${EVIDENCE_TIERS[Math.min(...relevant.map((row) => tiers.get(row)[index]))]}`));
  }
  if (crowdAvoid) {
    const calm = relevant.filter((row) => row.place.attributes.popularityRank === undefined);
    if (calm.length > 0 && calm.length < relevant.length) {
      relevant = calm;
      relevantRules.push('relevant.crowd_avoid:drop_top100_popularity_rank');
    } else relevantRules.push('relevant.crowd_avoid:no_change');
  }

  const conflicts = [];
  if (dateConflict) conflicts.push('date_conflict');
  if (allowed.length === 0) conflicts.push('no_candidates');
  for (const r of requirements) {
    if (allowed.length > 0 && allowed.every((row) => row.evidence[r.type].signal === 'contradicts')) conflicts.push(`requirement_contradicted:${r.type}`);
  }
  if (requirements.length > 0 && allowed.length > 0 && uncontradicted.length === 0 && !conflicts.some((conflict) => conflict.startsWith('requirement_contradicted:'))) {
    conflicts.push('requirements_contradicted:jointly');
  }
  const action = expectedAction({ allowed: allowed.length, dateConflict, requirements: requirements.length, confirmed: confirmed.length });

  // A requirement needs a notice while the candidates the answer should draw from are still unknown for it.
  const topics = [];
  const noticeRows = relevant.length > 0 ? relevant : allowed;
  for (const r of requirements) if (noticeRows.some((row) => row.evidence[r.type].verdict === 'unknown')) topics.push(NOTICE_TOPICS[r.type]);
  if (conditions.some((condition) => condition.type === 'transport' && condition.value === 'public_transit')) topics.push(NOTICE_TOPICS.public_transit);
  if (conditions.some((condition) => condition.type === 'companion')) topics.push(NOTICE_TOPICS.companion);
  if (walkMinimal) topics.push(NOTICE_TOPICS.walk);
  if (crowdAvoid) topics.push(NOTICE_TOPICS.crowd_avoid);
  const noticeRequired = topics.length > 0 && (action === 'recommend' || action === 'cannot_confirm');

  const relevantIds = new Set(relevant.map((row) => row.place.id));
  const hardRequirements = requirements.map((r) => ({
    type: r.type, value: r.value,
    // What may be said about this requirement for the gold candidates as a group: pass only if every one of them passes.
    expectedVerdict: relevant.length > 0 && relevant.every((row) => row.evidence[r.type].verdict === 'pass') ? 'pass' : 'unknown',
    ...tallyEvidence(allowed, r.type),
    noticeTopic: NOTICE_TOPICS[r.type],
  }));
  const sources = new Set();
  for (const row of allowed) {
    for (const r of requirements) {
      const key = { wheelchair: row.place.attributes.barrierFree ? 'barrierFree' : 'designations', indoor_required: 'indoor', open_at: 'hours' }[r.type];
      const attribute = key === undefined ? undefined : row.place.attributes[key];
      if (attribute !== undefined && row.evidence[r.type].signal !== 'none') sources.add(attribute.source ?? `rule:${key}`);
    }
  }
  const ruleTrace = [
    selector.category !== null ? `candidate.category:${selector.category}`
      : `candidate.theme_weight>=${threshold}:${selector.themes.join('+')}${selector.source === 'persona' ? ' (persona primary)' : ''}`,
    'candidate.exclude_category:stay',
    ...(zone === null ? [] : [`filter.zone:${zone}`]),
    ...(excluded.size > 0 ? [`exclude.user_named:${excluded.size}`] : []),
    ...(walkMinimal ? [`exclude.route_for_minimal_walk:${forbidden.filter((entry) => entry.reason === 'route_for_minimal_walk').length}`] : []),
    ...(dateConflict ? [`conflict.trip_dates:${trip.value.conflict}`] : []),
    ...requirements.map((r) => `requirement.${r.type}:${r.type === 'wheelchair' || r.type === 'budget_cap' ? 'always_unknown' : 'verified_only'}`),
    ...relevantRules,
    `action.${action}`,
  ];
  return {
    expectedAction: action,
    conflicts,
    allowedIds: allowed.map((row) => row.place.id),
    relevantIds: [...relevantIds],
    confirmedIds: confirmed.map((row) => row.place.id),
    forbidden: forbidden.sort(byId),
    hardRequirements,
    notice: { required: noticeRequired, topics: noticeRequired ? topics : [] },
    candidates: allowed.map((row) => ({
      id: row.place.id, name: row.place.name, category: row.place.category, zone: row.place.zone,
      affinity: (() => { const value = themeAffinity(vector, row.place.themes); return value === null ? null : round4(value); })(),
      relevant: relevantIds.has(row.place.id),
      evidence: row.evidence,
    })),
    facts: {
      selectorMatches: selectorMatches.length,
      zoneMatches: base.length,
      allowed: allowed.length,
      relevant: relevant.length,
      confirmed: confirmed.length,
      excluded: sortedObject(countBy(forbidden, (entry) => entry.reason)),
      allowedByCategory: sortedObject(countBy(allowed, (row) => row.place.category)),
      requirementEvidence: hardRequirements.map(({ type, verdicts, signals, basis }) => ({ type, verdicts, signals, basis })),
      top100PopularityAmongAllowed: crowdAvoid ? allowed.filter((row) => row.place.attributes.popularityRank !== undefined).length : undefined,
      evidenceSources: [...sources].sort(),
    },
    ruleTrace,
  };
}

/** The instant gold is computed for: 00:00 Korea time on the spec's anchor date. */
export const evaluationInstant = (spec) => parseYmd(spec.anchorDate) - KST_OFFSET_MS;

function compile({ catalog, personas, themeMap, spec }) {
  const { themeIds, zoneIds, categoryIds } = validateEvaluationSpec(spec, personas, themeMap);
  validateAgentCatalog(catalog);
  ensure(JSON.stringify(catalog.themeIds) === JSON.stringify(themeIds) && catalog.source.themeMapVersion === themeMap.version
    && catalog.source.personaVersion === personas.version, 'catalog was exported with a different theme map or persona version');
  const places = catalog.places.filter((place) => place.category !== STAY).sort(byId);
  const unknownZones = [...new Set(places.map((place) => place.zone))].filter((zone) => !zoneIds.includes(zone));
  ensure(unknownZones.length === 0, 'catalog has a zone the spec does not label');
  const label = (list) => Object.fromEntries(list.map((row) => [row.id, row.label]));
  const condition = (id) => spec.conditions.find((row) => row.id === id);
  return {
    spec, themeMap, themeIds, zoneIds, categoryIds, places,
    placeById: new Map(places.map((place) => [place.id, place])),
    threshold: spec.themeThreshold, floor: spec.personaFloor,
    themeLabels: label(personas.themes), zoneLabels: label(spec.zones), categoryLabels: label(spec.categories),
    values: Object.fromEntries(CONDITION_ORDER.map((id) => [id, condition(id).values ?? null])),
    atMs: evaluationInstant(spec),
    textRules: spec.queryAuthoring.textRules,
    cues: compileCues(spec.queryAuthoring),
  };
}

const selectorKey = (selector) => selector.category !== null ? `category:${selector.category}` : `${selector.source}:${selector.themes.join('+')}`;

/** How much a persona cares about an app category: its theme weights applied to the category's theme mix. */
const categoryAffinity = (category, vector, ctx) => round4(Object.entries(ctx.themeMap.categoryWeights[category]).reduce((sum, [theme, w]) => sum + w * vector[theme], 0));

function selectorOptions(kind, vector, ctx, onProfile = true) {
  const options = [];
  const eligible = (weight) => onProfile ? weight >= ctx.floor : weight > 0 && weight < ctx.floor;
  if (kind === 'theme' || kind === 'theme_or_category') {
    for (const theme of ctx.themeIds) {
      if (eligible(vector[theme])) options.push({ source: 'theme', themes: [theme], category: null, weight: vector[theme], onProfile });
    }
  }
  if (kind === 'category' || kind === 'theme_or_category') {
    for (const category of ctx.categoryIds) {
      const weight = categoryAffinity(category, vector, ctx);
      if (eligible(weight)) options.push({ source: 'category', themes: [], category, weight, onProfile });
    }
  }
  if (kind === 'persona' && onProfile) options.push({ source: 'persona', themes: primaryThemes(vector), category: null, weight: 1, onProfile });
  return options;
}

function cells(slot, options, ctx) {
  const zones = slot.zone ? ctx.zoneIds : [null];
  return options.flatMap((selector) => zones.map((zone) => ({
    selector, zone,
    base: ctx.places.filter((place) => matchesSelector(place, selector, ctx.threshold) && (zone === null || place.zone === zone)),
  })));
}

const wheelchairSignal = (place) => place.attributes.barrierFree?.value?.listed === true || place.attributes.designations?.value?.includes('bf') === true;

/** Feasible plans per slot kind, best tier first. Facts come first: a plan exists only if the catalog supports it. */
function planTiers(slot, vector, ctx) {
  const onProfile = cells(slot, selectorOptions(slot.selector, vector, ctx), ctx);
  const nonEmpty = onProfile.filter((cell) => cell.base.length > 0);
  switch (slot.kind) {
    case 'plain':
    case 'date_conflict':
      return [nonEmpty];
    case 'walk_minimal':
      return [nonEmpty.filter((cell) => cell.base.some(hasRoute) && cell.base.some((place) => !hasRoute(place))), nonEmpty.filter((cell) => cell.base.some((place) => !hasRoute(place)))];
    case 'accessibility_unknown':
      return [nonEmpty.filter((cell) => cell.base.some(wheelchairSignal)), nonEmpty];
    case 'disallowed_id': {
      const large = nonEmpty.filter((cell) => cell.base.length >= 3);
      return [large.filter((cell) => cell.base.filter((place) => popularityOf(place) >= 0).length >= 2), large];
    }
    case 'empty_result': {
      const offProfile = cells(slot, selectorOptions(slot.selector, vector, ctx, false), ctx);
      const tag = (list, mechanism) => list.map((cell) => ({ ...cell, mechanism }));
      return [
        tag(onProfile.filter((cell) => cell.base.length === 0), 'empty_cell'),
        tag(nonEmpty.filter((cell) => cell.selector.source === 'theme' && cell.base.every(hasRoute)), 'all_routes_minimal_walk'),
        tag(nonEmpty.filter((cell) => cell.base.length <= 2), 'all_excluded'),
        tag(offProfile.filter((cell) => cell.base.length === 0), 'empty_cell'),
      ];
    }
    default:
      throw new EvaluationSetError(`unknown slot kind ${slot.kind}`);
  }
}

/** A cell is the candidate filter itself: what is asked for and where, regardless of how the selector was chosen. */
const cellKey = (plan) => `${plan.selector.category !== null ? `category:${plan.selector.category}` : plan.selector.themes.join('+')}|${plan.zone ?? '-'}`;

/**
 * Pick a plan from the best tier that has one. Inside a tier, prefer cells this slot has not used for another persona
 * (weight 2) and cells this persona has not used in another slot (weight 1); `acrossTiers` lets a later tier with an
 * unused cell win over repeating a cell of an earlier tier.
 */
function choosePlan(tiers, rng, used, scope, acrossTiers) {
  const penalty = (plan) => (used.has(`${scope.slot}|${cellKey(plan)}`) ? 2 : 0) + (used.has(`${scope.persona}|${cellKey(plan)}`) ? 1 : 0);
  const pick = (tier, pool) => {
    const perSelector = countBy(pool, (plan) => selectorKey(plan.selector));
    const plan = rng.weighted(pool.map((option) => [option, option.selector.weight / perSelector[selectorKey(option.selector)]]));
    return { plan, tier, reused: penalty(plan) > 0 };
  };
  if (acrossTiers) {
    for (let tier = 0; tier < tiers.length; tier++) {
      const fresh = tiers[tier].filter((plan) => penalty(plan) === 0);
      if (fresh.length > 0) return pick(tier, fresh);
    }
  }
  for (let tier = 0; tier < tiers.length; tier++) {
    if (tiers[tier].length === 0) continue;
    const least = Math.min(...tiers[tier].map(penalty));
    return pick(tier, tiers[tier].filter((plan) => penalty(plan) === least));
  }
  return null;
}

function drawCondition(type, rng, plan, ctx) {
  const anchor = ctx.spec.anchorDate;
  switch (type) {
    case 'transport':
    case 'companion':
      return { type, value: rng.pick(ctx.values[type]) };
    case 'walk':
      return { type, value: 'minimal' };
    case 'crowd_avoid':
    case 'wheelchair':
      return { type, value: true };
    case 'indoor_required':
      return { type, value: true, reason: 'rain_forecast', date: addDays(anchor, 1 + rng.int(6)) };
    case 'open_at':
      return { type, value: rng.pick(ctx.values.open_at) };
    case 'budget_cap':
      return { type, value: rng.pick(ctx.values.budget_cap), unit: 'KRW_per_person_per_day' };
    case 'trip_dates': {
      const startDate = addDays(anchor, 5 + rng.int(21));
      const nights = 1 + rng.int(3);
      const endDate = addDays(startDate, nights);
      const conflict = rng.pick(ctx.values.trip_dates);
      const value = { startDate, endDate, nights, conflict };
      if (conflict === 'nights_mismatch') value.statedNights = nights + 1 + rng.int(2);
      else value.requestedDate = addDays(endDate, 2 + rng.int(4));
      return { type, value };
    }
    case 'exclude_places': {
      const ranked = [...plan.base].sort((a, b) => popularityOf(b) - popularityOf(a) || byId(a, b));
      const count = Math.min(1 + rng.int(2), plan.base.length - 1);
      const chosen = ranked.slice(0, count);
      const rankedBy = chosen.every((place) => popularityOf(place) >= 0) ? 'popularity' : 'id_order';
      chosen.sort(byId);
      return { type, value: chosen.map((place) => place.id), names: chosen.map((place) => place.name), rankedBy };
    }
    default:
      throw new EvaluationSetError(`condition ${type} cannot be drawn`);
  }
}

const orderConditions = (conditions) => [...conditions].sort((a, b) => CONDITION_ORDER.indexOf(a.type) - CONDITION_ORDER.indexOf(b.type))
  .map((condition) => ({ type: condition.type, kind: CONDITION_KINDS[condition.type], ...condition }));

function selectorLabel(selector, ctx) {
  if (selector.category !== null) return ctx.categoryLabels[selector.category];
  return selector.themes.map((theme) => ctx.themeLabels[theme]).join('·');
}

/** What the query writer may see: persona, stated conditions as plain facts, and names only where the user must say them. */
function buildBrief(persona, selector, conditions, ctx) {
  const statements = [];
  const notes = [];
  let namedPlaces = [];
  if (selector.source === 'theme') statements.push(`원하는 테마: ${selectorLabel(selector, ctx)}`);
  else if (selector.source === 'category') statements.push(`원하는 장소 분류: ${selectorLabel(selector, ctx)}`);
  else statements.push(`따로 정한 테마는 없고 평소 관심사(${selectorLabel(selector, ctx)})에 맞는 곳을 원함. 이 관심사가 드러나게 말한다`);
  for (const condition of conditions) {
    switch (condition.type) {
      case 'zone': statements.push(`권역: ${ctx.zoneLabels[condition.value]}`); break;
      case 'theme': case 'category': break;
      case 'transport': statements.push(condition.value === 'rental_car' ? '이동수단: 렌터카' : '이동수단: 버스 등 대중교통만 이용'); break;
      case 'companion': statements.push(condition.value === 'child' ? '동행: 아이' : '동행: 부모님'); break;
      case 'walk': statements.push('걷기: 거의 걷지 않으려 함'); break;
      case 'indoor_required': statements.push(`${koreanDate(condition.date)}에 비 예보가 있어 실내 장소여야 함(필수)`); break;
      case 'crowd_avoid': statements.push('붐비는 곳은 피하고 싶음'); break;
      case 'open_at': statements.push(`${koreanTime(condition.value)}에 방문할 수 있어야 함(필수)`); break;
      case 'wheelchair': statements.push('휠체어로 이용할 수 있어야 함(필수)'); break;
      case 'budget_cap': statements.push(`항공·숙박을 빼고 1인 하루 ${koreanWon(condition.value)}을 넘기면 안 됨(필수)`); break;
      case 'trip_dates': {
        const trip = condition.value;
        statements.push(`여행 날짜: ${koreanDate(trip.startDate)}부터 ${koreanDate(trip.endDate)}까지`);
        if (trip.conflict === 'nights_mismatch') statements.push(`사용자가 말하는 일정 길이: ${trip.statedNights}박 ${trip.statedNights + 1}일`);
        if (trip.conflict === 'date_outside_trip') statements.push(`이 장소들을 방문하려는 날: ${koreanDate(trip.requestedDate)}`);
        if (trip.conflict !== null) notes.push('날짜가 서로 맞지 않지만 사용자는 모순을 모른다. 지적하거나 고치지 않고 그대로 말한다.');
        break;
      }
      case 'exclude_places':
        statements.push(`이미 가 본 곳이라 빼 달라고 할 장소: ${condition.names.join(', ')}`);
        namedPlaces = [...condition.names];
        break;
      default: throw new EvaluationSetError(`condition ${condition.type} has no brief statement`);
    }
  }
  const brief = {
    persona: { id: persona.id, label: persona.label, description: persona.description },
    today: `${ctx.spec.anchorDate} ${koreanDate(ctx.spec.anchorDate)}`,
    statements, notes, namedPlaces,
  };
  return { ...brief, sha256: canonicalSha256(brief) };
}

/** Deterministic fallback wording. It states every condition literally and is replaced by an authored query when one exists. */
function templateQuery(selector, conditions, ctx) {
  const zone = conditions.find((condition) => condition.type === 'zone');
  const what = selector.source === 'theme' ? `${selectorLabel(selector, ctx)} 테마로` : selector.source === 'category' ? `${selectorLabel(selector, ctx)} 위주로` : `${selectorLabel(selector, ctx)} 쪽으로`;
  const sentences = [`${zone ? `${ctx.zoneLabels[zone.value]} 쪽에서` : '제주에서'} ${what} 갈 만한 곳을 추천해 주세요.`];
  for (const condition of conditions) {
    switch (condition.type) {
      case 'transport': sentences.push(condition.value === 'rental_car' ? '렌터카로 이동합니다.' : '버스 같은 대중교통으로만 이동합니다.'); break;
      case 'companion': sentences.push(condition.value === 'child' ? '아이와 함께 갑니다.' : '부모님을 모시고 갑니다.'); break;
      case 'walk': sentences.push('거의 걷지 않으려고 합니다.'); break;
      case 'indoor_required': sentences.push(`${koreanDate(condition.date)}에 비 예보가 있어서 실내여야 합니다.`); break;
      case 'crowd_avoid': sentences.push('사람이 붐비는 곳은 피하고 싶습니다.'); break;
      case 'open_at': sentences.push(`${koreanTime(condition.value)}에 방문할 수 있어야 합니다.`); break;
      case 'wheelchair': sentences.push('휠체어로 이용할 수 있어야 합니다.'); break;
      case 'budget_cap': sentences.push(`항공·숙박을 빼고 1인 하루 ${koreanWon(condition.value)}을 넘기면 안 됩니다.`); break;
      case 'trip_dates': {
        const trip = condition.value;
        if (trip.conflict === 'nights_mismatch') sentences.push(`${koreanDate(trip.startDate)}부터 ${koreanDate(trip.endDate)}까지 ${trip.statedNights}박 ${trip.statedNights + 1}일 일정입니다.`);
        else if (trip.conflict === 'date_outside_trip') sentences.push(`여행은 ${koreanDate(trip.startDate)}부터 ${koreanDate(trip.endDate)}까지이고, ${koreanDate(trip.requestedDate)}에 방문하려고 합니다.`);
        else sentences.push(`여행은 ${koreanDate(trip.startDate)}부터 ${koreanDate(trip.endDate)}까지입니다.`);
        break;
      }
      case 'exclude_places': sentences.push(`다음 장소는 이미 가 봐서 빼 주세요: ${condition.names.join(', ')}.`); break;
      default: break;
    }
  }
  return sentences.join(' ');
}

/** Scenario facts a harness needs besides the query text: the synthetic today, a rain forecast and the stated trip window. */
function itemContext(conditions, ctx) {
  const context = { today: ctx.spec.anchorDate, timezone: 'Asia/Seoul' };
  const rain = conditions.find((condition) => condition.type === 'indoor_required');
  if (rain !== undefined) context.forecast = [{ date: rain.date, rain: true }];
  const trip = conditions.find((condition) => condition.type === 'trip_dates');
  if (trip !== undefined) context.trip = { startDate: trip.value.startDate, endDate: trip.value.endDate };
  return context;
}

/** The selector an item's own conditions imply: a stated theme or category, otherwise the persona's primary themes. */
function selectorFor(conditions, vector, ctx) {
  const theme = conditions.find((condition) => condition.type === 'theme');
  const category = conditions.find((condition) => condition.type === 'category');
  ensure(theme === undefined || category === undefined, 'an item states a theme or a category, not both');
  if (theme !== undefined) {
    return { source: 'theme', themes: [theme.value], category: null, personaWeight: vector[theme.value], onProfile: vector[theme.value] >= ctx.floor };
  }
  if (category !== undefined) {
    const weight = categoryAffinity(category.value, vector, ctx);
    return { source: 'category', themes: [], category: category.value, personaWeight: weight, onProfile: weight >= ctx.floor };
  }
  return { source: 'persona', themes: primaryThemes(vector), category: null, personaWeight: 1, onProfile: true };
}

/**
 * Everything an item holds besides its authored text follows from the persona, the slot and the stated conditions.
 * Generation and validation both go through here, so a stored item can be rebuilt and compared field by field.
 */
function assembleItem({ persona, slot, vector, conditions, generation, ctx }) {
  const selector = selectorFor(conditions, vector, ctx);
  const zone = conditions.find((condition) => condition.type === 'zone')?.value ?? null;
  const { facts, ...gold } = computeGold({ places: ctx.places, vector, selector, zone, conditions, atMs: ctx.atMs, threshold: ctx.threshold });
  return {
    id: `EV-${persona.id}-${slot.id}`,
    personaId: persona.id,
    slot: slot.id,
    hardCase: slot.hardCase ?? null,
    difficulty: classifyDifficulty({ conflicts: gold.conflicts, statedConditions: conditions.length }),
    statedConditions: conditions.length,
    selector,
    zone,
    conditions,
    context: itemContext(conditions, ctx),
    generation,
    facts,
    gold,
    brief: buildBrief(persona, selector, conditions, ctx),
    query: { template: templateQuery(selector, conditions, ctx), text: null, source: 'template' },
  };
}

function buildItem({ persona, slot, vector, ctx, used }) {
  const rng = createRng(`${ctx.spec.seed}|${persona.id}|${slot.id}`);
  const choice = choosePlan(planTiers(slot, vector, ctx), rng, used, { slot: slot.id, persona: persona.id }, slot.kind === 'empty_result');
  if (choice === null) return { dropped: { personaId: persona.id, slot: slot.id, reason: 'no_feasible_plan' } };
  const { plan } = choice;
  used.add(`${slot.id}|${cellKey(plan)}`);
  used.add(`${persona.id}|${cellKey(plan)}`);
  const drawn = [];
  if (plan.zone !== null) drawn.push({ type: 'zone', value: plan.zone });
  if (plan.selector.source === 'theme') drawn.push({ type: 'theme', value: plan.selector.themes[0] });
  if (plan.selector.source === 'category') drawn.push({ type: 'category', value: plan.selector.category });
  for (const alternatives of slot.extras) drawn.push(drawCondition(rng.pick(alternatives), rng, plan, ctx));
  if (plan.mechanism === 'all_excluded') {
    const all = [...plan.base].sort(byId);
    drawn.push({ type: 'exclude_places', value: all.map((place) => place.id), names: all.map((place) => place.name), rankedBy: 'whole_cell' });
  }
  if (plan.mechanism === 'all_routes_minimal_walk') drawn.push({ type: 'walk', value: 'minimal' });
  const item = assembleItem({
    persona, slot, vector, ctx, conditions: orderConditions(drawn),
    generation: { tier: choice.tier, reusedCell: choice.reused, mechanism: plan.mechanism ?? null },
  });
  ensure(selectorKey(item.selector) === selectorKey(plan.selector) && item.zone === plan.zone, `slot ${slot.id} for ${persona.id}: the drawn conditions do not describe the chosen plan`);
  const expected = { walk_minimal: 'recommend', accessibility_unknown: 'cannot_confirm', date_conflict: 'ask_clarification', disallowed_id: 'recommend', empty_result: 'impossible' }[slot.kind];
  ensure(expected === undefined || expected === item.gold.expectedAction, `slot ${slot.id} for ${persona.id} did not produce its hard case (${item.gold.expectedAction})`);
  const templateProblems = itemTextProblems(item.query.template, item, ctx, { authored: false });
  ensure(templateProblems.length === 0, `slot ${slot.id} for ${persona.id}: the template wording breaks the text rules (${templateProblems.join('; ')})`);
  return { item };
}

const REQUEST_WORDING = /추천|알려|어디|있을까|있나요|찾아|찾고|찾는|좋을까|괜찮을까|부탁|골라/u;

/**
 * The text without the places the user has to name: one mention of each name is removed, longest names first, so the
 * characters of a name can neither stand in for a condition nor be read as one, while a zone that shares a word with an
 * excluded place still has to be said in its own right.
 */
function withoutNamedPlaces(textValue, namedPlaces) {
  return [...namedPlaces].sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0)).reduce((rest, name) => {
    const at = rest.lastIndexOf(name);
    return at < 0 ? rest : `${rest.slice(0, at)} ${rest.slice(at + name.length)}`;
  }, textValue);
}

/** Text rules that need only the text and the writer brief: length, one line, Korean, a request, no place ID, named places. */
export function checkQueryText(textValue, brief, rules) {
  const problems = [];
  if (typeof textValue !== 'string' || textValue !== textValue.trim()) return ['query text must be a trimmed string'];
  if (textValue.length < rules.minLength || textValue.length > rules.maxLength) problems.push(`query text must be ${rules.minLength} to ${rules.maxLength} characters`);
  // Place names are third-party strings and are taken as they are; everything the writer adds must be plain text.
  const own = withoutNamedPlaces(textValue, brief.namedPlaces);
  if (/[\p{Cc}\p{Zl}\p{Zp}]/u.test(textValue) || /\p{Cf}/u.test(own)) problems.push('query text must be a single line without control or format characters');
  if (own !== own.normalize('NFKC')) problems.push('query text must be in Unicode NFKC form (no full-width or compatibility characters)');
  if (!/[가-힣]/u.test(textValue)) problems.push('query text must be Korean');
  if (!REQUEST_WORDING.test(textValue)) problems.push('query text must ask for something (for example 추천해 주세요)');
  if (PLACE_ID_PREFIX.test(textValue)) problems.push('query text must not contain a place ID');
  for (const name of brief.namedPlaces) if (!textValue.includes(name)) problems.push('query text must name every place the brief lists, verbatim');
  return problems;
}

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
const NOT_AFTER_DIGIT = '(?<![0-9])';
const NOT_INSIDE_WORD = '(?<![가-힣])';
/** Any of the words; with `wordStart` only where no Hangul syllable comes directly before (경우도 does not say 우도). */
const anyOf = (words, wordStart = false) => new RegExp(`${wordStart ? NOT_INSIDE_WORD : ''}(?:${words.map(escapeRegExp).join('|')})`, 'u');
function compileCues({ cues, unstatedWords, cueMatching }) {
  const wordStart = (key) => cueMatching.wordStart.includes(key);
  const compiled = {};
  for (const key of CUE_MAPS) compiled[key] = Object.fromEntries(Object.entries(cues[key]).map(([id, words]) => [id, anyOf(words, wordStart(key))]));
  for (const key of CUE_LISTS) compiled[key] = anyOf(cues[key], wordStart(key));
  // A zone is a group of areas: a question has to name every one of them, so the words are also kept one by one.
  compiled.zoneWords = Object.fromEntries(Object.entries(cues.zone).map(([id, words]) => [id, words.map((word) => anyOf([word], wordStart('zone')))]));
  compiled.unstated = Object.fromEntries(UNSTATED_KINDS.map((kind) => [kind, anyOf(unstatedWords[kind])]));
  compiled.ignored = cueMatching.ignoredPhrases.length > 0 ? new RegExp(cueMatching.ignoredPhrases.map(escapeRegExp).join('|'), 'gu') : null;
  return compiled;
}

const WEEKDAY = '(?:\\s?\\([일월화수목금토]\\))?';
const monthDay = (ymd) => { const date = new Date(parseYmd(ymd)); return `${NOT_AFTER_DIGIT}${date.getUTCMonth() + 1}월\\s?${date.getUTCDate()}일${WEEKDAY}`; };
/** The first day of a trip: the date directly followed by 부터. */
const tripStartCue = (ymd) => new RegExp(`${monthDay(ymd)}\\s?부터`, 'u');
/** The last day of a trip: the day directly followed by 까지, written with its own month or with none. */
function tripEndCue(ymd) {
  const date = new Date(parseYmd(ymd));
  return new RegExp(`(?:${NOT_AFTER_DIGIT}${date.getUTCMonth() + 1}월\\s?|(?<![0-9]|월\\s?))${date.getUTCDate()}일${WEEKDAY}\\s?까지`, 'u');
}
/** One day on its own (a rain forecast, the day of a visit): the date, not used as the start or end of a range. */
const singleDayCue = (ymd) => new RegExp(`${monthDay(ymd)}(?!\\s?(?:부터|까지))`, 'u');
/** A stated night count, taking the 'M일' of 'N박 M일' with it so that day number is not read as a date. */
const nightsCue = (nights) => new RegExp(`${NOT_AFTER_DIGIT}${nights}\\s?박(?:\\s?[0-9]{1,2}\\s?일)?`, 'u');
/** Day-period words a speaker uses for an hour; a word outside its hours would name another time (밤 2시 is not 14:00). */
function periodWords(hour) {
  if (hour === 0) return ['밤', '자정', '새벽', '오전'];
  if (hour < 12) return ['오전', ...(hour <= 6 ? ['새벽'] : []), ...(hour >= 5 ? ['아침'] : [])];
  return ['오후', ...(hour <= 17 ? ['낮'] : []), ...(hour >= 17 && hour <= 21 ? ['저녁'] : []), ...(hour >= 18 ? ['밤'] : [])];
}
/** A clock time as a user says it: a day-period word with the 12-hour number, or the 24-hour number from 13시 on (and 0시). */
function timeCue(time) {
  const minutes = minutesOf(time);
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  const tail = minute === 0 ? '(?!\\s?[0-9]{1,2}\\s?분|\\s?반(?!드시))' : `\\s?(?:0?${minute}분${minute === 30 ? '|반(?!드시)' : ''})`;
  const spoken = `(?:${periodWords(hour).join('|')})\\s?${hour % 12 === 0 ? 12 : hour % 12}시${tail}`;
  return new RegExp(hour >= 13 || hour === 0 ? `${spoken}|${NOT_AFTER_DIGIT}${hour}시${tail}` : spoken, 'u');
}
const amountCue = (amount) => new RegExp(amount % 10_000 === 0
  ? `${NOT_AFTER_DIGIT}${amount / 10_000}만\\s?원|${NOT_AFTER_DIGIT}${escapeRegExp(amount.toLocaleString('en-US'))}\\s?원|${NOT_AFTER_DIGIT}${amount}\\s?원`
  : `${NOT_AFTER_DIGIT}${escapeRegExp(amount.toLocaleString('en-US'))}\\s?원|${NOT_AFTER_DIGIT}${amount}\\s?원`, 'u');

/**
 * Numbers a query may contain only as the value of a stated condition. They are looked for after the stated values have
 * been taken out of the text, so a second time, amount, date or night count is refused whether or not the kind is stated.
 */
const LEFTOVER_NUMBERS = Object.freeze([
  ['a clock time', /(?<![0-9])[0-9]{1,2}\s?시(?!간)|(?<![0-9])[0-9]{1,2}:[0-9]{2}(?![0-9])|(?<![가-힣])(?:열한|열두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s?시(?=에|쯤|까지|부터|경|반|\s|[.,!?]|$)/u],
  ['an amount of money', /(?<![0-9])[0-9][0-9,]*\s?(?:천\s?|만\s?)?원|(?<![가-힣])[일이삼사오육칠팔구십백]+\s?(?:만|천)\s?원/u],
  ['a night count', /(?<![0-9])[0-9]{1,2}\s?박|(?<![가-힣])[일이삼사오육칠]\s?박(?=\s|[0-9일이삼사오육칠팔]|[.,!?]|$)/u],
  ['a calendar date', /(?<![0-9])[0-9]{1,2}\s?월|(?<![0-9])[0-9]{1,2}\s?일(?!정|장|\s?(?:동안|간|정도|째|차))/u],
]);

/**
 * What a query has to say for an item, as [label, pattern, isValue]: one cue per stated condition (every area word for a
 * zone) and per primary theme when the persona selects. Value cues (dates, times, amounts, nights) are taken out of the
 * text once found; the order matters for trip dates, where the range is read before the single day.
 */
function requiredCues(item, ctx) {
  const cues = [];
  if (item.selector.source === 'persona') for (const theme of item.selector.themes) cues.push([`persona_interest:${theme}`, ctx.cues.theme[theme], false]);
  for (const condition of item.conditions) {
    const { type, value } = condition;
    switch (type) {
      case 'zone': for (const pattern of ctx.cues.zoneWords[value]) cues.push([type, pattern, false]); break;
      case 'theme': case 'category': case 'transport': case 'companion': case 'walk': cues.push([type, ctx.cues[type][value], false]); break;
      case 'indoor_required':
        cues.push([type, ctx.cues.indoor_required, false], [`${type}:rain`, ctx.cues.rain, false], [`${type}:date`, singleDayCue(condition.date), true]);
        break;
      case 'crowd_avoid': case 'wheelchair': case 'exclude_places': cues.push([type, ctx.cues[type], false]); break; // excluded names: checkQueryText
      case 'open_at': cues.push([type, timeCue(value), true]); break;
      case 'budget_cap': cues.push([type, amountCue(value), true]); break;
      case 'trip_dates':
        cues.push([`${type}:start`, tripStartCue(value.startDate), true], [`${type}:end`, tripEndCue(value.endDate), true]);
        if (value.conflict === 'nights_mismatch') cues.push([`${type}:nights`, nightsCue(value.statedNights), true]);
        if (value.conflict === 'date_outside_trip') cues.push([`${type}:requested`, singleDayCue(value.requestedDate), true]);
        break;
      default: throw new EvaluationSetError(`condition ${type} has no text cue`);
    }
  }
  return cues;
}

/**
 * What a query must not say: cue words of condition values the item does not state, and the extra words listed for kinds it
 * does not state at all. Themes and categories are left to the writer, and wording outside these lists is not recognised.
 */
function unstatedCues(item, ctx) {
  const stated = new Map(item.conditions.map((condition) => [condition.type, condition]));
  const cues = [];
  for (const type of ['zone', 'transport', 'companion', 'walk']) {
    for (const [value, pattern] of Object.entries(ctx.cues[type])) if (stated.get(type)?.value !== value) cues.push([`${type}:${value}`, pattern]);
  }
  if (!stated.has('indoor_required')) cues.push(['indoor_required', ctx.cues.indoor_required], ['indoor_required:rain', ctx.cues.rain]);
  if (!stated.has('crowd_avoid')) cues.push(['crowd_avoid', ctx.cues.crowd_avoid]);
  if (!stated.has('wheelchair')) cues.push(['wheelchair', ctx.cues.wheelchair]);
  if (!stated.has('exclude_places')) cues.push(['exclude_places', ctx.cues.visited]);
  for (const kind of UNSTATED_KINDS) {
    // A rain forecast also brings a date into the question, so date words are only refused when no date is stated at all.
    const absent = kind === 'trip_dates' ? !stated.has('trip_dates') && !stated.has('indoor_required') : !stated.has(kind);
    if (absent) cues.push([kind, ctx.cues.unstated[kind]]);
  }
  return cues;
}

const squash = (value) => value.replace(/\s+/gu, '');

/**
 * Every text rule for one item. Cues are looked for outside the place names the user has to say and outside the ignored
 * phrases. `authored` adds the rule that the question must not name one of its own candidate places.
 */
function itemTextProblems(textValue, item, ctx, { authored }) {
  const problems = checkQueryText(textValue, item.brief, ctx.textRules);
  if (typeof textValue !== 'string') return problems;
  let rest = withoutNamedPlaces(textValue, item.brief.namedPlaces);
  if (ctx.cues.ignored !== null) rest = rest.replace(ctx.cues.ignored, ' ');
  for (const [label, pattern, isValue] of requiredCues(item, ctx)) {
    if (!pattern.test(rest)) problems.push(`query text does not mention the stated condition ${label}`);
    else if (isValue) rest = rest.replace(new RegExp(pattern.source, 'gu'), ' ');
  }
  for (const [label, pattern] of unstatedCues(item, ctx)) if (pattern.test(rest)) problems.push(`query text mentions ${label}, which the item does not state`);
  for (const [label, pattern] of LEFTOVER_NUMBERS) if (pattern.test(rest)) problems.push(`query text mentions ${label} that no stated condition accounts for`);
  if (authored) {
    // Compared without whitespace: a writer who knows a place writes its name with its own spacing.
    const wording = squash(item.brief.statements.join('\n'));
    const flat = squash(textValue);
    const candidates = [...item.gold.allowedIds, ...item.gold.forbidden.map((entry) => entry.id)];
    const named = candidates.some((id) => {
      const name = squash(ctx.placeById.get(id)?.name ?? '');
      return name.length >= ctx.textRules.candidateNameMinLength && !wording.includes(name) && flat.includes(name);
    });
    if (named) problems.push('query text names one of the item\'s candidate places; the answer must not appear in the question');
  }
  return problems;
}

/** A checker bound to one catalog, persona file, theme map and spec: (text, item, { authored }) => list of broken text rules. */
export function createQueryChecker(inputs) {
  const ctx = compile(inputs);
  return (textValue, item, { authored = true } = {}) => itemTextProblems(textValue, item, ctx, { authored });
}

const AUTHOR_FIELDS = new Set(['kind', 'model', 'promptVersion', 'seed', 'samplingSeed', 'authoredOn', 'review']);

/** Attach authored texts to their items. Only a fixed, validated author record is kept; nothing else is copied through. */
function attachQueries(items, authored, ctx) {
  const { spec } = ctx;
  ensure(isRecord(authored) && authored.schemaVersion === '1.0' && authored.kind === EVALUATION_QUERIES_KIND,
    `authored queries need schemaVersion "1.0" and kind "${EVALUATION_QUERIES_KIND}"`);
  const author = authored.author;
  ensure(isRecord(author) && Object.keys(author).every((key) => AUTHOR_FIELDS.has(key)), 'the author record has unsupported fields');
  ensure(AUTHOR_KINDS.includes(author.kind), `author.kind must be one of ${AUTHOR_KINDS.join(', ')}`);
  ensure(text(author.model, 200) && author.promptVersion === spec.queryAuthoring.promptVersion, 'authored queries must record the model and the current prompt version');
  ensure((author.seed === undefined || text(author.seed, 200)) && (author.review === undefined || text(author.review, 500))
    && (author.samplingSeed === undefined || author.samplingSeed === null || Number.isInteger(author.samplingSeed)), 'the author record has an invalid seed or review note');
  if (author.authoredOn !== undefined) parseYmd(author.authoredOn);
  ensure(Array.isArray(authored.queries), 'authored queries must be a list');
  const byItem = new Map(items.map((item) => [item.id, item]));
  const seen = new Set();
  for (const entry of authored.queries) {
    ensure(isRecord(entry) && byItem.has(entry.id) && !seen.has(entry.id), 'authored query ids must be unique and exist in the set');
    seen.add(entry.id);
    const item = byItem.get(entry.id);
    ensure(entry.briefSha256 === item.brief.sha256, `authored query ${entry.id} was written for a different brief; write it again`);
    const problems = itemTextProblems(entry.text, item, ctx, { authored: true });
    ensure(problems.length === 0, `authored query ${entry.id}: ${problems.join('; ')}`);
    item.query = { template: item.query.template, text: entry.text, source: 'authored' };
  }
  return {
    kind: author.kind, model: author.model, promptVersion: author.promptVersion, seed: author.seed ?? null,
    samplingSeed: author.samplingSeed ?? null, authoredOn: author.authoredOn ?? null, review: author.review ?? null,
  };
}

/** The text rules a set was built under, as the set records them: the limits and the names of the checks that ran. */
const textRulesRecord = ({ queryAuthoring: { textRules } }) => ({
  minLength: textRules.minLength, maxLength: textRules.maxLength, candidateNameMinLength: textRules.candidateNameMinLength, checks: [...TEXT_CHECKS],
});

/** The rule-computed part of an item: everything except the authored text. */
const goldView = (item) => { const { query, ...rest } = item; return { ...rest, query: { template: query.template } }; };
const goldHash = (items) => canonicalSha256(items.map(goldView));
const queriesHash = (items) => items.some((item) => item.query.text !== null) ? canonicalSha256(items.map((item) => [item.id, item.query.text])) : null;

/**
 * Build the whole set. Inputs: validated catalog v2, persona definitions, theme map, spec and, optionally,
 * an authored-queries document. The result holds place IDs and names and belongs in a .local directory.
 */
export function buildEvaluationSet({ catalog, personas, themeMap, spec, authoredQueries }) {
  const ctx = compile({ catalog, personas, themeMap, spec });
  const used = new Set();
  const items = [];
  const dropped = [];
  for (const persona of personas.prototypes) {
    const vector = personaVector(persona, ctx.themeIds);
    for (const slot of spec.blueprint.slots) {
      const result = buildItem({ persona, slot, vector, ctx, used });
      if (result.item) items.push(result.item);
      else dropped.push(result.dropped);
    }
  }
  const author = authoredQueries === undefined ? null : attachQueries(items, authoredQueries, ctx);
  const authored = items.filter((item) => item.query.source === 'authored').length;
  const set = {
    schemaVersion: EVALUATION_SET_SCHEMA_VERSION,
    kind: EVALUATION_SET_KIND,
    source: {
      repository: catalog.source.repository, commit: catalog.source.commit,
      placesContentSha256: catalog.source.placesContentSha256, rawSha256: catalog.source.rawSha256,
      themeMapVersion: themeMap.version, personaVersion: personas.version,
      specVersion: spec.version, specSha256: canonicalSha256(spec), rulesVersion: RULES_VERSION,
      seed: spec.seed, anchorDate: spec.anchorDate,
    },
    policy: [
      'Gold is computed by rule from catalog v2; no model wrote or judged it.',
      'Only verified evidence yields pass/fail. With none, a hard requirement is unknown and the expected answer says confirmation is needed.',
      'Synthetic queries are not tourist responses, satisfaction or demand.',
      'This file holds third-party place IDs and names: keep it in .local and publish aggregates only.',
    ],
    contentSha256: { gold: goldHash(items), queries: queriesHash(items) },
    queryAuthoring: {
      mode: authored > 0 ? 'authored' : 'template_only', promptVersion: spec.queryAuthoring.promptVersion,
      textRules: textRulesRecord(spec),
      authored, template: items.length - authored, author: authored > 0 ? author : null,
    },
    items,
    diagnostics: { requestedItems: personas.prototypes.length * spec.blueprint.slots.length, generatedItems: items.length, dropped },
  };
  return validateEvaluationSet(set, { catalog, personas, themeMap, spec });
}

const CONDITION_FIELDS = Object.freeze({
  zone: [], theme: [], category: [], transport: [], companion: [], walk: [], indoor_required: ['reason', 'date'], crowd_avoid: [], open_at: [],
  wheelchair: [], budget_cap: ['unit'], trip_dates: [], exclude_places: ['names', 'rankedBy'],
});
const TRIP_FIELDS = new Set(['startDate', 'endDate', 'nights', 'conflict', 'statedNights', 'requestedDate']);

/** One stored condition must be a value the spec allows, in the shape the generator writes. */
function checkCondition(condition, ctx) {
  ensure(isRecord(condition) && CONDITION_ORDER.includes(condition.type) && condition.kind === CONDITION_KINDS[condition.type], 'a condition has an unknown type or kind');
  const { type, value } = condition;
  const fields = new Set(['type', 'kind', 'value', ...CONDITION_FIELDS[type]]);
  ensure(Object.keys(condition).every((key) => fields.has(key)), `condition ${type} has unsupported fields`);
  const fail = `condition ${type} has a value the spec does not allow`;
  switch (type) {
    case 'zone': ensure(ctx.zoneIds.includes(value), fail); break;
    case 'theme': ensure(ctx.themeIds.includes(value), fail); break;
    case 'category': ensure(ctx.categoryIds.includes(value), fail); break;
    case 'transport': case 'companion': case 'walk': case 'open_at': ensure(ctx.values[type].includes(value), fail); break;
    case 'budget_cap': ensure(ctx.values.budget_cap.includes(value) && condition.unit === 'KRW_per_person_per_day', fail); break;
    case 'crowd_avoid': case 'wheelchair': ensure(value === true, fail); break;
    case 'indoor_required':
      ensure(value === true && condition.reason === 'rain_forecast', fail);
      parseYmd(condition.date);
      break;
    case 'trip_dates':
      ensure(isRecord(value) && Object.keys(value).every((key) => TRIP_FIELDS.has(key)) && (value.conflict === null || ctx.values.trip_dates.includes(value.conflict)), fail);
      ensure(tripConflict(value) === value.conflict, 'the trip conflict label does not match its dates');
      break;
    case 'exclude_places':
      ensure(Array.isArray(value) && value.length > 0 && new Set(value).size === value.length && Array.isArray(condition.names) && condition.names.length === value.length
        && value.every((id, index) => ctx.placeById.get(id)?.name === condition.names[index]) && ['popularity', 'id_order', 'whole_cell'].includes(condition.rankedBy),
      'excluded places must be catalog places listed with their own names');
      break;
    default: throw new EvaluationSetError(`condition ${type} cannot be checked`);
  }
}

/**
 * Full validation of a stored set against the catalog, personas, theme map and spec it claims to come from. Every item
 * is rebuilt from its persona, slot and conditions and compared with what is stored, so an edited selector, zone, brief,
 * template, gold field or hash is rejected. Call this after loading a .local set and before scoring with it.
 */
export function validateEvaluationSet(set, { catalog, personas, themeMap, spec }) {
  const ctx = compile({ catalog, personas, themeMap, spec });
  ensure(isRecord(set) && set.schemaVersion === EVALUATION_SET_SCHEMA_VERSION && set.kind === EVALUATION_SET_KIND, 'unsupported evaluation set schema');
  const source = set.source;
  ensure(isRecord(source) && source.commit === catalog.source.commit && source.placesContentSha256 === catalog.source.placesContentSha256
    && source.specVersion === spec.version && source.specSha256 === canonicalSha256(spec) && source.rulesVersion === RULES_VERSION,
  'the set was generated from a different catalog, spec or rule version');
  ensure(Array.isArray(set.items) && set.items.length > 0, 'the set has no items');
  const personaById = new Map(personas.prototypes.map((persona) => [persona.id, persona]));
  const slotById = new Map(spec.blueprint.slots.map((slot) => [slot.id, slot]));
  const ids = new Set();
  for (const item of set.items) {
    ensure(isRecord(item) && text(item.id, 40) && !ids.has(item.id), 'item ids must be unique');
    ids.add(item.id);
    const label = `item ${item.id}`;
    const persona = personaById.get(item.personaId);
    const slot = slotById.get(item.slot);
    ensure(persona !== undefined && slot !== undefined, `${label} has an unknown persona or slot`);
    ensure(Array.isArray(item.conditions) && item.conditions.length >= 1 && item.conditions.length <= 4, `${label} must state 1 to 4 conditions`);
    for (const condition of item.conditions) checkCondition(condition, ctx);
    ensure(new Set(item.conditions.map((condition) => condition.type)).size === item.conditions.length
      && JSON.stringify(orderConditions(item.conditions)) === JSON.stringify(item.conditions), `${label} conditions are not canonical`);
    const generation = item.generation;
    ensure(isRecord(generation) && Object.keys(generation).length === 3 && Number.isInteger(generation.tier) && generation.tier >= 0
      && typeof generation.reusedCell === 'boolean' && (generation.mechanism === null || EMPTY_MECHANISMS.includes(generation.mechanism)), `${label} generation record is invalid`);
    ensure(isRecord(item.query) && Object.keys(item.query).length === 3 && ['template', 'authored'].includes(item.query.source)
      && (item.query.source === 'authored') === (item.query.text !== null), `${label} query record is inconsistent`);
    const rebuilt = assembleItem({ persona, slot, vector: personaVector(persona, ctx.themeIds), conditions: item.conditions, generation, ctx });
    ensure(canonicalSha256(goldView(rebuilt)) === canonicalSha256(goldView(item)), `${label} does not match the rules for its conditions`);
    if (item.query.text !== null) ensure(itemTextProblems(item.query.text, item, ctx, { authored: true }).length === 0, `${label} authored query breaks the text rules`);
  }
  const hardCases = new Set(set.items.map((item) => item.hardCase).filter(Boolean));
  ensure(REQUIRED_HARD_CASES.every((id) => hardCases.has(id)), 'the set must contain every mandatory hard case');
  ensure(isRecord(set.contentSha256) && set.contentSha256.gold === goldHash(set.items) && set.contentSha256.queries === queriesHash(set.items),
    'stored content hashes do not match the items');
  const authored = set.items.filter((item) => item.query.source === 'authored').length;
  const authoring = set.queryAuthoring;
  ensure(isRecord(authoring) && authoring.authored === authored && authoring.template === set.items.length - authored
    && authoring.mode === (authored > 0 ? 'authored' : 'template_only') && authoring.promptVersion === spec.queryAuthoring.promptVersion
    && isRecord(authoring.textRules) && canonicalSha256(authoring.textRules) === canonicalSha256(textRulesRecord(spec))
    && (authored > 0 ? isRecord(authoring.author) && AUTHOR_KINDS.includes(authoring.author.kind) && text(authoring.author.model, 200) : authoring.author === null),
  'the query authoring record does not match the items');
  const diagnostics = set.diagnostics;
  ensure(isRecord(diagnostics) && Array.isArray(diagnostics.dropped) && diagnostics.generatedItems === set.items.length
    && diagnostics.requestedItems === personas.prototypes.length * spec.blueprint.slots.length
    && set.items.length + diagnostics.dropped.length === diagnostics.requestedItems, 'diagnostics do not add up to the requested items');
  return set;
}

/** Writer-facing briefs only: no gold, no candidate lists. Names appear only where the user has to say them. */
export function evaluationBriefs(set, spec) {
  return {
    schemaVersion: '1.0',
    kind: EVALUATION_BRIEFS_KIND,
    specVersion: set.source.specVersion,
    goldSha256: set.contentSha256.gold,
    promptVersion: spec.queryAuthoring.promptVersion,
    prompt: spec.queryAuthoring.prompt,
    textRules: spec.queryAuthoring.textRules,
    cues: spec.queryAuthoring.cues,
    unstatedWords: spec.queryAuthoring.unstatedWords,
    cueMatching: spec.queryAuthoring.cueMatching,
    briefs: set.items.map((item) => ({ id: item.id, briefSha256: item.brief.sha256, ...(({ sha256, ...brief }) => brief)(item.brief), template: item.query.template })),
  };
}

function distribution(values) {
  if (values.length === 0) return { count: 0, min: null, median: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length / 2;
  const median = Number.isInteger(middle) ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[Math.floor(middle)];
  return { count: sorted.length, min: sorted[0], median, max: sorted[sorted.length - 1] };
}

/** Aggregate-only summary for Git: counts, identifiers and hashes. No place name, no place ID, no query text. */
export function summarizeEvaluationSet(set) {
  const items = set.items;
  const conditions = items.flatMap((item) => item.conditions);
  const requirementTypes = [...new Set(items.flatMap((item) => item.gold.hardRequirements.map((r) => r.type)))].sort();
  const add = (target, source) => { for (const [key, value] of Object.entries(source)) target[key] = (target[key] ?? 0) + value; };
  const hardRequirements = {};
  for (const type of requirementTypes) {
    const entries = items.flatMap((item) => item.gold.hardRequirements.filter((r) => r.type === type));
    const verdicts = { pass: 0, fail: 0, unknown: 0 };
    const supports = { verified: 0, reported: 0, inferred: 0 };
    const contradicts = { verified: 0, reported: 0, inferred: 0 };
    let none = 0;
    for (const entry of entries) {
      add(verdicts, entry.verdicts);
      add(supports, entry.signals.supports);
      add(contradicts, entry.signals.contradicts);
      none += entry.signals.none;
    }
    hardRequirements[type] = {
      items: entries.length,
      itemsExpectingUnknown: entries.filter((entry) => entry.expectedVerdict === 'unknown').length,
      candidateVerdicts: verdicts, candidateSignals: { supports, contradicts, none },
    };
  }
  const forbidden = items.flatMap((item) => item.gold.forbidden);
  const authoring = set.queryAuthoring;
  return {
    schemaVersion: '1.0',
    kind: EVALUATION_SUMMARY_KIND,
    source: { ...set.source },
    contentSha256: { ...set.contentSha256 },
    totals: {
      items: items.length,
      requestedItems: set.diagnostics.requestedItems,
      droppedItems: set.diagnostics.dropped.length,
      byPersona: sortedObject(countBy(items, (item) => item.personaId)),
      bySlot: sortedObject(countBy(items, (item) => item.slot)),
      byDifficulty: Object.fromEntries(DIFFICULTIES.map((id) => [id, items.filter((item) => item.difficulty === id).length])),
      byExpectedAction: Object.fromEntries(EXPECTED_ACTIONS.map((id) => [id, items.filter((item) => item.gold.expectedAction === id).length])),
      byHardCase: Object.fromEntries(REQUIRED_HARD_CASES.map((id) => [id, items.filter((item) => item.hardCase === id).length])),
      byStatedConditions: sortedObject(countBy(items, (item) => String(item.statedConditions))),
      byConditionType: Object.fromEntries(CONDITION_ORDER.map((id) => [id, conditions.filter((condition) => condition.type === id).length])),
      bySelectorSource: sortedObject(countBy(items, (item) => item.selector.source)),
      byTheme: sortedObject(countBy(items.flatMap((item) => item.selector.themes), (theme) => theme)),
      byCategory: sortedObject(countBy(items, (item) => item.selector.category)),
      byZone: sortedObject(countBy(items, (item) => item.zone ?? 'none')),
      byConflict: sortedObject(countBy(items.flatMap((item) => item.gold.conflicts), (conflict) => conflict)),
      byEmptyMechanism: sortedObject(countBy(items, (item) => item.generation.mechanism)),
    },
    gold: {
      allowedSetSize: distribution(items.map((item) => item.gold.allowedIds.length)),
      relevantSetSize: distribution(items.map((item) => item.gold.relevantIds.length)),
      itemsWithEmptyAllowedSet: items.filter((item) => item.gold.allowedIds.length === 0).length,
      itemsWithEmptyRelevantSet: items.filter((item) => item.gold.relevantIds.length === 0).length,
      itemsWithVerifiedPass: items.filter((item) => item.gold.confirmedIds.length > 0).length,
      hardRequirements,
      noticeRequiredItems: items.filter((item) => item.gold.notice.required).length,
      noticeTopics: sortedObject(countBy(items.flatMap((item) => item.gold.notice.topics), (topic) => topic)),
      forbiddenPlaces: { total: forbidden.length, byReason: sortedObject(countBy(forbidden, (entry) => entry.reason)), items: items.filter((item) => item.gold.forbidden.length > 0).length },
    },
    generation: {
      offProfileSelectors: items.filter((item) => !item.selector.onProfile).length,
      fallbackTierItems: items.filter((item) => item.generation.tier > 0).length,
      reusedCellItems: items.filter((item) => item.generation.reusedCell).length,
      walkMinimalItemsWithRouteExclusion: items.filter((item) => item.hardCase === 'walk_minimal' && item.gold.forbidden.some((entry) => entry.reason === 'route_for_minimal_walk')).length,
      accessibilityItemsWithListedCandidates: items.filter((item) => item.hardCase === 'accessibility_unknown'
        && item.gold.hardRequirements.some((r) => r.type === 'wheelchair' && r.signals.supports.reported > 0)).length,
      disallowedIdItemsRankedByPopularity: items.filter((item) => item.hardCase === 'disallowed_id'
        && item.conditions.some((condition) => condition.type === 'exclude_places' && condition.rankedBy === 'popularity')).length,
    },
    // Only fixed vocabulary leaves the local set: the author kind is an enum, and the model stays in the .local record.
    queries: {
      mode: authoring.mode, promptVersion: authoring.promptVersion, authored: authoring.authored, template: authoring.template,
      authorKind: authoring.author === null ? null : authoring.author.kind,
      authoringRecord: authoring.authored > 0 ? 'local_only' : 'none',
      textRules: {
        minLength: authoring.textRules.minLength, maxLength: authoring.textRules.maxLength,
        candidateNameMinLength: authoring.textRules.candidateNameMinLength, checks: [...authoring.textRules.checks],
      },
      textLength: {
        authored: distribution(items.filter((item) => item.query.text !== null).map((item) => item.query.text.length)),
        template: distribution(items.map((item) => item.query.template.length)),
      },
    },
  };
}
