import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { BASIS_POLICY, SOURCES, themeWeights } from '../src/agent-catalog.mjs';
import { canonicalJson, canonicalSha256, createRng } from '../src/evaluation-rng.mjs';
import {
  CONDITION_KINDS, EVALUATION_QUERIES_KIND, EXPECTED_ACTIONS, PLACE_ID_PREFIX, REQUIRED_HARD_CASES, buildEvaluationSet, checkQueryText,
  classifyDifficulty, computeGold, evaluationBriefs, evaluationInstant, expectedAction, personaVector, primaryThemes, requirementEvidence,
  summarizeEvaluationSet, tripConflict, validateEvaluationSet, validateEvaluationSpec,
} from '../src/evaluation-set.mjs';
import { aggregateScores, createScorer, validateResponse } from '../src/evaluation-scoring.mjs';
import { SPEC_PATH, main as buildMain, parseArguments } from '../scripts/build-evaluation-set.mjs';
import { PERSONA_PATH, THEME_MAP_PATH } from '../scripts/export-agent-catalog.mjs';

const execFileAsync = promisify(execFile);
const CLI = fileURLToPath(new URL('../scripts/build-evaluation-set.mjs', import.meta.url));
const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
const spec = await readJson(SPEC_PATH);
const personas = await readJson(PERSONA_PATH);
const themeMap = await readJson(THEME_MAP_PATH);
const themeIds = personas.themes.map((theme) => theme.id);
const ZONES = spec.zones.map((zone) => zone.id);
const CATEGORIES = spec.categories.map((category) => category.id);
const AT = Date.parse('2026-12-06T15:00:00Z'); // the spec anchor date, 00:00 in Korea

// Synthetic values only: names, coordinates and attributes are test data, not Jeju facts.
const inferred = (value) => ({ value, basis: 'inferred', rule: 'test: category default' });
const reportedHours = (openMin, closeMin, text = '매일 09:00~18:00') => ({ value: { text, openMin, closeMin }, basis: 'source_reported', source: 'kakao-map-hours' });
const listed = { value: { listed: true, summary: null, url: null, routeKm: null }, basis: 'source_reported', source: 'jeju-barrier-free-travel-info', match: 'dataset_record' };
const curatedIndoor = { value: true, basis: 'app_curated', source: 'raw-poi-indoor' };
const route = (km) => ({ value: { km, level: null, summary: null }, basis: 'source_reported', source: 'official-route-data' });
const verified = (attribute, from = '2026-11-01T00:00:00Z', until = '2027-11-01T00:00:00Z') => {
  const { rule, ...rest } = attribute;
  return { ...rest, basis: 'verified', source: attribute.source ?? 'app-curated-notes', verifiedAt: from, validUntil: until };
};
let serial = 0;
function place(category, zone, { kind = null, name, attributes = {} } = {}) {
  serial += 1;
  const base = { id: `kk-${String(serial).padStart(5, '0')}`, name: name ?? `시험 ${zone} ${category} ${serial}`, category, kind, lat: 33.4, lon: 126.5, zone, origin: 'raw_poi', web: null };
  const themes = themeWeights(base, themeMap);
  return { ...base, themes: themes.weights, themeRule: themes.rule, attributes: JSON.parse(JSON.stringify(attributes)) };
}
function catalogOf(places) {
  return {
    schemaVersion: '2.0', kind: 'jeju-agent-catalog', dataMode: 'app-export',
    source: { repository: 'OREUM-IT/jeju-tour', commit: 'c'.repeat(40), endpoint: '/api/course/places', placesContentSha256: 'a'.repeat(64), appGeneratedAt: null,
      rawPath: 'prototype/data/jeju_tour_data.json', rawSha256: 'd'.repeat(64), themeMapVersion: themeMap.version, personaVersion: personas.version },
    themeIds: [...themeIds], basisPolicy: { ...BASIS_POLICY }, sources: structuredClone(SOURCES), places: [...places].sort((a, b) => a.id < b.id ? -1 : 1), diagnostics: {},
  };
}
function fixtureCatalog(mutate = () => {}) {
  serial = 0;
  const places = [];
  const indoorByDefault = new Set(['herit', 'art', 'food', 'cafe']);
  for (const zone of ZONES) {
    for (const category of CATEGORIES) {
      if (category === 'leisure' && zone === 'JC') continue; // an empty category x zone cell
      for (let i = 1; i <= 3; i++) {
        const attributes = { indoor: inferred(indoorByDefault.has(category)) };
        let kind = null;
        if (category === 'hike') {
          kind = ['탐방로', '올레', '자전거 인증센터'][i - 1];
          attributes.route = route(i === 3 ? null : 9.6 * i);
        }
        if (i === 1) {
          attributes.hours = reportedHours(540, 1080);
          attributes.popularity = { value: 9000 - serial, basis: 'source_reported', source: 'jeju-tourism-bigdata-arrivals' };
          if (!['food', 'cafe', 'hike'].includes(category)) attributes.popularityRank = { value: 1 + (serial % 100), basis: 'computed', rule: 'test: rank' };
        }
        if (i === 2 && category !== 'hike') {
          attributes.barrierFree = listed;
          if (indoorByDefault.has(category)) attributes.indoor = curatedIndoor;
        }
        places.push(place(category, zone, { kind, attributes }));
      }
    }
  }
  // The neighborhood theme exists in two zones only, so the other six cells are empty.
  for (const zone of ['JC', 'S']) for (let i = 1; i <= 3; i++) places.push(place('herit', zone, { name: `시험 ${zone} ${i}번 거리`, attributes: { indoor: inferred(false) } }));
  places.push(place('stay', 'JC', { kind: '호텔', attributes: { indoor: inferred(true) } }));
  places.forEach(mutate);
  return catalogOf(places);
}
const fixture = fixtureCatalog();
const inputs = (overrides = {}) => ({ catalog: fixture, personas, themeMap, spec, ...overrides });
const build = (overrides) => buildEvaluationSet(inputs(overrides));
const vector = (id) => personaVector(personas.prototypes.find((persona) => persona.id === id), themeIds);
const conditionsOf = (list) => list.map((condition) => ({ kind: CONDITION_KINDS[condition.type], ...condition }));
const gold = (places, selector, conditions = [], extra = {}) => computeGold({
  places, vector: vector('P01'), selector: { source: 'theme', themes: [], category: null, ...selector }, zone: null,
  conditions: conditionsOf(conditions), atMs: AT, threshold: spec.themeThreshold, ...extra,
});
const rain = { type: 'indoor_required', value: true, reason: 'rain_forecast', date: '2026-12-09' };

test('seeded rng is reproducible, label-sensitive and pinned to known values', () => {
  const draws = (label) => { const rng = createRng(label); return Array.from({ length: 6 }, () => rng.next()); };
  assert.deepEqual(draws('seed|P01|S1'), draws('seed|P01|S1'));
  assert.notDeepEqual(draws('seed|P01|S1'), draws('seed|P01|S2'));
  assert.ok(draws('x').every((value) => value >= 0 && value < 1));
  // Golden values: a change in seeding, warm-up or arithmetic (or any clock or process input) breaks these.
  const pinned = createRng('seed|P01|S1');
  assert.deepEqual([pinned.next(), pinned.next(), pinned.int(1000)], [0.7591755532193929, 0.47699606395326555, 132]);
  assert.deepEqual(createRng('order').shuffle(['a', 'b', 'c', 'd', 'e']), ['b', 'd', 'a', 'c', 'e']);
  const rng = createRng('choices');
  assert.ok(['a', 'b'].includes(rng.pick(['a', 'b'])));
  assert.equal(rng.weighted([['only', 0.2]]), 'only');
  assert.deepEqual([...rng.shuffle([1, 2, 3, 4])].sort(), [1, 2, 3, 4]);
  assert.throws(() => rng.pick([]), /non-empty/u);
  assert.throws(() => rng.weighted([['a', 0]]), /positive/u);
  assert.throws(() => rng.int(0), /positive integer/u);
  assert.throws(() => createRng(''), /non-empty string/u);
  assert.equal(canonicalJson({ b: 1, a: [{ d: 1, c: 2 }], skipped: undefined }), '{"a":[{"c":2,"d":1}],"b":1}');
  assert.equal(canonicalSha256({ a: 1, b: 2 }), canonicalSha256({ b: 2, a: 1 }));
  assert.throws(() => canonicalJson({ a: Number.NaN }), /non-finite/u);
});

test('committed spec is valid and drifting versions, precedence, hard cases or slots are rejected', () => {
  assert.deepEqual(validateEvaluationSpec(spec, personas, themeMap).zoneIds, ZONES);
  const broken = (mutate) => { const copy = structuredClone(spec); mutate(copy); return () => validateEvaluationSpec(copy, personas, themeMap); };
  assert.throws(broken((s) => { s.personaVersion = 'other'; }), /versions must agree/u);
  assert.throws(broken((s) => { s.rulesVersion = 'evalset-rules-v0'; }), /rulesVersion/u);
  assert.throws(broken((s) => { s.anchorDate = '2026-02-30'; }), /invalid calendar date/u);
  assert.throws(broken((s) => { s.expectedActions.reverse(); }), /precedence order/u);
  assert.throws(broken((s) => { s.conditions.reverse(); }), /canonical order/u);
  assert.throws(broken((s) => { s.conditions[6].kind = 'preference'; }), /fixed kind/u);
  assert.throws(broken((s) => { s.categories.push({ id: 'stay', label: '숙소' }); }), /non-stay app categories/u);
  assert.throws(broken((s) => { s.blueprint.slots = s.blueprint.slots.filter((slot) => slot.hardCase !== 'empty_result'); s.blueprint.itemsPerPersona -= 1; }), /every mandatory hard case/u);
  assert.throws(broken((s) => { s.blueprint.slots[0].extras = [['transport'], ['companion'], ['walk'], ['wheelchair']]; }), /1 to 4 conditions/u);
  assert.throws(broken((s) => { s.blueprint.slots[0].extras = [['theme']]; }), /non-selector conditions/u);
  assert.throws(broken((s) => { s.blueprint.slots.find((slot) => slot.kind === 'walk_minimal').extras = [['wheelchair']]; }), /must always draw walk/u);
  assert.throws(broken((s) => { s.blueprint.slots[0].extras = [['trip_dates']]; }), /date_conflict slot/u);
  assert.throws(broken((s) => { s.blueprint.itemsPerPersona = 3; }), /number of slots/u);
  assert.throws(broken((s) => { s.conditions.find((c) => c.id === 'open_at').values = ['25:00']; }), /HH:MM/u);
  assert.throws(broken((s) => { s.noticeTopics.pop(); }), /every notice topic/u);
});

test('a hard requirement is judged only from verified evidence that is valid when the requirement applies', () => {
  const evidence = (attributes, requirement) => requirementEvidence({ attributes }, requirement, AT);
  // indoor: non-verified values are signals, never verdicts.
  assert.deepEqual(evidence({ indoor: curatedIndoor }, { type: 'indoor_required' }), { verdict: 'unknown', signal: 'supports', tier: 'reported', basis: 'app_curated' });
  assert.deepEqual(evidence({ indoor: inferred(false) }, { type: 'indoor_required' }), { verdict: 'unknown', signal: 'contradicts', tier: 'inferred', basis: 'inferred' });
  assert.deepEqual(evidence({}, { type: 'indoor_required' }), { verdict: 'unknown', signal: 'none', tier: null, basis: null });
  assert.equal(evidence({ indoor: verified(curatedIndoor) }, { type: 'indoor_required' }).verdict, 'pass');
  assert.equal(evidence({ indoor: verified({ value: false, source: 'raw-poi-indoor' }) }, { type: 'indoor_required' }).verdict, 'fail');
  // An expired or not-yet-valid verification is no longer a verdict; the boundary instants themselves are valid.
  assert.deepEqual(evidence({ indoor: verified(curatedIndoor, '2025-01-01T00:00:00Z', '2026-01-01T00:00:00Z') }, { type: 'indoor_required' }),
    { verdict: 'unknown', signal: 'supports', tier: 'reported', basis: 'verified' });
  assert.equal(evidence({ indoor: verified(curatedIndoor, '2027-01-01T00:00:00Z', '2028-01-01T00:00:00Z') }, { type: 'indoor_required' }).verdict, 'unknown');
  assert.equal(evidence({ indoor: verified(curatedIndoor, '2026-12-06T15:00:00Z', '2026-12-06T15:00:00Z') }, { type: 'indoor_required' }).verdict, 'pass');
  assert.equal(evidence({ indoor: verified(curatedIndoor, '2026-12-06T15:00:01Z', '2027-01-01T00:00:00Z') }, { type: 'indoor_required' }).verdict, 'unknown');
  // A requirement tied to a day needs the verification to hold until the end of that day in Korea.
  assert.equal(evidence({ indoor: verified(curatedIndoor, '2026-11-01T00:00:00Z', '2026-12-09T14:59:59Z') }, rain).verdict, 'unknown');
  assert.equal(evidence({ indoor: verified(curatedIndoor, '2026-11-01T00:00:00Z', '2026-12-09T15:00:00Z') }, rain).verdict, 'pass');
  // wheelchair: a list entry is not wheelchair access, so even a verified listing stays unknown.
  assert.deepEqual(evidence({ barrierFree: listed }, { type: 'wheelchair' }), { verdict: 'unknown', signal: 'supports', tier: 'reported', basis: 'source_reported' });
  assert.deepEqual(evidence({ barrierFree: verified(listed) }, { type: 'wheelchair' }), { verdict: 'unknown', signal: 'supports', tier: 'reported', basis: 'verified' });
  assert.equal(evidence({ designations: { value: ['bf', 'k100'], basis: 'app_curated', source: 'app-designation-lists' } }, { type: 'wheelchair' }).signal, 'supports');
  assert.equal(evidence({ designations: { value: ['k100'], basis: 'app_curated', source: 'app-designation-lists' } }, { type: 'wheelchair' }).signal, 'none');
  // visit time: reported windows support or contradict; closing time itself is not open; past-midnight windows wrap.
  const at = (value, hours) => evidence({ hours }, { type: 'open_at', value });
  assert.equal(at('10:00', reportedHours(540, 1080)).signal, 'supports');
  assert.equal(at('18:00', reportedHours(540, 1080)).signal, 'contradicts');
  assert.equal(at('08:59', reportedHours(540, 1080)).signal, 'contradicts');
  assert.equal(at('01:00', reportedHours(1080, 1560)).signal, 'supports');
  assert.equal(at('01:00', reportedHours(1080, 120)).signal, 'supports');
  assert.equal(at('10:00', reportedHours(540, 1080)).verdict, 'unknown');
  assert.equal(at('10:00', { value: { text: '상시 개방', openMin: null, closeMin: null }, basis: 'app_curated', source: 'app-curated-notes' }).signal, 'none');
  assert.equal(at('10:00', verified(reportedHours(540, 1080))).verdict, 'pass');
  assert.equal(at('20:00', verified(reportedHours(540, 1080))).verdict, 'fail');
  assert.equal(at('01:00', verified(reportedHours(1080, 1560, '매일 18:00~02:00'))).verdict, 'pass');
  assert.equal(at('12:00', verified(reportedHours(0, 1440, '매일 00:00~24:00'))).verdict, 'pass');
  assert.equal(at('12:00', verified(reportedHours(540, 1080, null))).verdict, 'pass');
  // Not judged even when verified: weekly closures in free text, numbers that disagree with the text, a zero-length window.
  assert.equal(at('10:00', verified(reportedHours(540, 1080, '화~일 (휴무: 월요일) 09:00~18:00'))).verdict, 'unknown');
  assert.equal(at('20:00', verified(reportedHours(540, 1320, '매일 09:00~18:00'))).verdict, 'unknown');
  assert.equal(at('10:00', verified(reportedHours(540, 540, null))).verdict, 'unknown');
  // budget: the catalog has no cost attribute.
  assert.deepEqual(evidence({ indoor: curatedIndoor }, { type: 'budget_cap', value: 30000 }), { verdict: 'unknown', signal: 'none', tier: null, basis: null });
  assert.throws(() => evidence({}, { type: 'parking' }), /unknown hard requirement/u);
});

test('expected action precedence and mechanical difficulty', () => {
  assert.equal(expectedAction({ allowed: 0, dateConflict: true, requirements: 2, confirmed: 0 }), 'impossible');
  assert.equal(expectedAction({ allowed: 3, dateConflict: true, requirements: 2, confirmed: 0 }), 'ask_clarification');
  assert.equal(expectedAction({ allowed: 3, dateConflict: false, requirements: 1, confirmed: 0 }), 'cannot_confirm');
  assert.equal(expectedAction({ allowed: 3, dateConflict: false, requirements: 1, confirmed: 2 }), 'recommend');
  assert.equal(expectedAction({ allowed: 3, dateConflict: false, requirements: 0, confirmed: 0 }), 'recommend');
  assert.equal(classifyDifficulty({ conflicts: [], statedConditions: 1 }), 'simple');
  assert.equal(classifyDifficulty({ conflicts: [], statedConditions: 4 }), 'compound');
  assert.equal(classifyDifficulty({ conflicts: ['no_candidates'], statedConditions: 1 }), 'conflict');
  assert.deepEqual(primaryThemes(vector('P04')), ['culture', 'art']);
  assert.deepEqual(primaryThemes(vector('P01')), ['coast']);
  assert.equal(evaluationInstant(spec), AT);
});

test('candidate set uses theme weight, category, zone, stay exclusion and user exclusions', () => {
  serial = 100;
  const beachE = place('sea', 'E');
  const beachW = place('sea', 'W');
  const olle = place('hike', 'E', { kind: '올레', attributes: { route: route(15) } }); // coast 0.3 only: below the threshold
  const hotel = place('stay', 'E', { kind: '호텔' });
  const cafe = place('cafe', 'E');
  const all = [beachE, beachW, olle, hotel, cafe];
  const coast = gold(all, { themes: ['coast'] });
  assert.deepEqual(coast.allowedIds, [beachE.id, beachW.id]);
  assert.equal(coast.expectedAction, 'recommend');
  assert.deepEqual(coast.relevantIds, coast.allowedIds);
  assert.deepEqual(coast.notice, { required: false, topics: [] });
  assert.deepEqual(gold(all, { themes: ['coast'] }, [{ type: 'zone', value: 'E' }], { zone: 'E' }).allowedIds, [beachE.id]);
  assert.deepEqual(gold(all, { source: 'category', category: 'hike' }).allowedIds, [olle.id]);
  assert.deepEqual(gold(all, { source: 'persona', themes: ['coast', 'cafe'] }).allowedIds, [beachE.id, beachW.id, cafe.id]);
  assert.deepEqual(gold([hotel], { source: 'category', category: 'stay' }).allowedIds, []);
  const excluded = gold(all, { themes: ['coast'] }, [{ type: 'exclude_places', value: [beachE.id], names: [beachE.name] }]);
  assert.deepEqual(excluded.allowedIds, [beachW.id]);
  assert.deepEqual(excluded.forbidden, [{ id: beachE.id, name: beachE.name, reason: 'user_excluded' }]);
  assert.throws(() => gold(all, { themes: ['coast'] }, [{ type: 'exclude_places', value: [cafe.id], names: [cafe.name] }]), /candidates of the same bundle/u);
  const emptied = gold(all, { themes: ['coast'] }, [{ type: 'exclude_places', value: [beachE.id, beachW.id], names: ['a', 'b'] }, { type: 'wheelchair', value: true }]);
  assert.equal(emptied.expectedAction, 'impossible');
  assert.deepEqual(emptied.conflicts, ['no_candidates']);
  assert.deepEqual(emptied.notice, { required: false, topics: [] });
});

test('barely-walks removes route places and asks to confirm walking distance instead of judging it', () => {
  serial = 200;
  const trail = place('hike', 'S', { kind: '탐방로', attributes: { route: route(9.6) } });
  const forest = place('heal', 'S');
  const result = gold([trail, forest], { themes: ['nature'] }, [{ type: 'walk', value: 'minimal' }]);
  assert.deepEqual(result.allowedIds, [forest.id]);
  assert.deepEqual(result.forbidden, [{ id: trail.id, name: trail.name, reason: 'route_for_minimal_walk' }]);
  assert.equal(result.expectedAction, 'recommend');
  assert.deepEqual(result.notice, { required: true, topics: ['walking_distance_unknown'] });
  assert.equal(gold([trail], { themes: ['nature'] }, [{ type: 'walk', value: 'minimal' }]).expectedAction, 'impossible');
  assert.deepEqual(gold([trail, forest], { themes: ['nature'] }).allowedIds, [trail.id, forest.id]);
});

test('unknown requirements never pass: confirmation is expected and evidence only narrows the gold candidates', () => {
  serial = 300;
  const listedBeach = place('sea', 'E', { attributes: { barrierFree: listed, indoor: inferred(false) } });
  const plainBeach = place('sea', 'E', { attributes: { indoor: inferred(false) } });
  const wheelchair = gold([listedBeach, plainBeach], { themes: ['coast'] }, [{ type: 'wheelchair', value: true }]);
  assert.equal(wheelchair.expectedAction, 'cannot_confirm');
  assert.deepEqual(wheelchair.allowedIds, [listedBeach.id, plainBeach.id]);
  assert.deepEqual(wheelchair.relevantIds, [listedBeach.id]);
  assert.deepEqual(wheelchair.hardRequirements[0].verdicts, { pass: 0, fail: 0, unknown: 2 });
  assert.equal(wheelchair.hardRequirements[0].expectedVerdict, 'unknown');
  assert.deepEqual(wheelchair.notice, { required: true, topics: ['wheelchair_unverified'] });
  assert.equal(wheelchair.conflicts.length, 0);
  // Every candidate contradicts the requirement by inference only: still unknown, flagged as a conflict, no gold candidate.
  const rainy = gold([listedBeach, plainBeach], { themes: ['coast'] }, [rain]);
  assert.equal(rainy.expectedAction, 'cannot_confirm');
  assert.deepEqual(rainy.conflicts, ['requirement_contradicted:indoor_required']);
  assert.deepEqual(rainy.relevantIds, []);
  assert.deepEqual(rainy.hardRequirements[0].verdicts, { pass: 0, fail: 0, unknown: 2 });
  assert.deepEqual(rainy.notice, { required: true, topics: ['indoor_unverified'] });
  // Budget has no evidence at all, so the gold candidates stay the allowed set and the cost notice is required.
  const budget = gold([listedBeach, plainBeach], { themes: ['coast'] }, [{ type: 'budget_cap', value: 30000 }, { type: 'transport', value: 'public_transit' }, { type: 'companion', value: 'child' }]);
  assert.deepEqual(budget.relevantIds, budget.allowedIds);
  assert.deepEqual(budget.notice.topics, ['cost_unknown', 'transit_access_unknown', 'companion_suitability_unknown']);
  assert.deepEqual(gold([plainBeach], { themes: ['coast'] }, [{ type: 'transport', value: 'rental_car' }]).notice, { required: false, topics: [] });
  // Visit time: reported support narrows the gold candidates; contradicted places stay allowed but are not gold.
  const openCafe = place('cafe', 'JC', { attributes: { hours: reportedHours(540, 1320) } });
  const closedCafe = place('cafe', 'JC', { attributes: { hours: reportedHours(540, 1080) } });
  const silentCafe = place('cafe', 'JC');
  const evening = gold([openCafe, closedCafe, silentCafe], { themes: ['cafe'] }, [{ type: 'open_at', value: '20:00' }]);
  assert.deepEqual(evening.allowedIds, [openCafe.id, closedCafe.id, silentCafe.id]);
  assert.deepEqual(evening.relevantIds, [openCafe.id]);
  assert.deepEqual(gold([closedCafe, silentCafe], { themes: ['cafe'] }, [{ type: 'open_at', value: '20:00' }]).relevantIds, [silentCafe.id]);
});

test('several requirements are weighed together, independent of their order', () => {
  serial = 350;
  const evening = { type: 'open_at', value: '20:00' };
  // A looks best for indoor but is closed at 20:00; B is weaker on indoor yet not contradicted on either requirement.
  const a = place('cafe', 'JC', { attributes: { indoor: curatedIndoor, hours: reportedHours(540, 1080) } });
  const b = place('cafe', 'JC', { attributes: { indoor: inferred(true), hours: reportedHours(540, 1320) } });
  for (const order of [[rain, evening], [evening, rain]]) {
    const result = gold([a, b], { themes: ['cafe'] }, order);
    assert.deepEqual(result.allowedIds, [a.id, b.id]);
    assert.deepEqual(result.relevantIds, [b.id]);
    assert.deepEqual(result.conflicts, []);
    assert.equal(result.expectedAction, 'cannot_confirm');
    assert.ok(result.ruleTrace.includes('relevant.open_at:reported_support') && result.ruleTrace.includes('relevant.indoor_required:not_contradicted'));
  }
  // Better evidence on one requirement and equal on the other wins; a trade-off keeps both.
  const listedOpen = place('cafe', 'JC', { attributes: { barrierFree: listed, hours: reportedHours(540, 1320) } });
  const plainOpen = place('cafe', 'JC', { attributes: { hours: reportedHours(540, 1320) } });
  const listedSilent = place('cafe', 'JC', { attributes: { barrierFree: listed } });
  const wheel = { type: 'wheelchair', value: true };
  assert.deepEqual(gold([listedOpen, plainOpen, listedSilent], { themes: ['cafe'] }, [evening, wheel]).relevantIds, [listedOpen.id]);
  assert.deepEqual(gold([plainOpen, listedSilent], { themes: ['cafe'] }, [evening, wheel]).relevantIds, [plainOpen.id, listedSilent.id]);
  assert.deepEqual(gold([listedOpen, plainOpen], { themes: ['cafe'] }, [wheel, { type: 'budget_cap', value: 30000 }]).relevantIds, [listedOpen.id]);
  // Each candidate is contradicted on a different requirement: nothing is gold and the item is a conflict.
  const outdoorOpen = place('cafe', 'JC', { attributes: { indoor: inferred(false), hours: reportedHours(540, 1320) } });
  const joint = gold([a, outdoorOpen], { themes: ['cafe'] }, [rain, evening]);
  assert.deepEqual([joint.relevantIds, joint.conflicts, joint.expectedAction], [[], ['requirements_contradicted:jointly'], 'cannot_confirm']);
  assert.ok(joint.ruleTrace.includes('relevant.none:every_candidate_contradicts_a_requirement'));
  assert.deepEqual(joint.notice, { required: true, topics: ['indoor_unverified', 'hours_unverified'] });
});

test('verified evidence is the only path to pass or fail, and a verified fail cannot be scored back in', () => {
  serial = 400;
  const passes = place('art', 'S', { attributes: { indoor: verified(curatedIndoor) } });
  const fails = place('art', 'S', { attributes: { indoor: verified({ value: false, source: 'raw-poi-indoor' }) } });
  const unknown = place('art', 'S', { attributes: { indoor: curatedIndoor } });
  const result = gold([passes, fails, unknown], { themes: ['art'] }, [rain]);
  assert.equal(result.expectedAction, 'recommend');
  assert.deepEqual(result.allowedIds, [passes.id, unknown.id]);
  assert.deepEqual(result.relevantIds, [passes.id]);
  assert.deepEqual(result.confirmedIds, [passes.id]);
  assert.deepEqual(result.forbidden, [{ id: fails.id, name: fails.name, reason: 'verified_fail', requirements: ['indoor_required'] }]);
  assert.equal(result.hardRequirements[0].expectedVerdict, 'pass');
  // Every gold candidate is verified, so nothing is left to confirm about this requirement.
  assert.deepEqual(result.notice, { required: false, topics: [] });
  assert.deepEqual(gold([unknown], { themes: ['art'] }, [rain]).notice, { required: true, topics: ['indoor_unverified'] });
  assert.equal(gold([fails], { themes: ['art'] }, [rain]).expectedAction, 'impossible');
  // Two requirements: passing one is not enough.
  const both = gold([passes, unknown], { themes: ['art'] }, [rain, { type: 'budget_cap', value: 50000 }]);
  assert.equal(both.expectedAction, 'cannot_confirm');
  assert.deepEqual(both.confirmedIds, []);
  assert.deepEqual(both.relevantIds, [passes.id]);
  assert.deepEqual(both.hardRequirements.map((r) => [r.type, r.expectedVerdict]), [['indoor_required', 'pass'], ['budget_cap', 'unknown']]);
  assert.deepEqual(both.notice, { required: true, topics: ['cost_unknown'] });
});

test('crowd avoidance drops top-ranked attractions from the gold candidates but never empties them', () => {
  serial = 500;
  const famous = place('sea', 'E', { attributes: { popularityRank: { value: 3, basis: 'computed', rule: 'test: rank' } } });
  const quiet = place('sea', 'E');
  const crowd = [{ type: 'crowd_avoid', value: true }];
  const result = gold([famous, quiet], { themes: ['coast'] }, crowd);
  assert.deepEqual(result.allowedIds, [famous.id, quiet.id]);
  assert.deepEqual(result.relevantIds, [quiet.id]);
  assert.deepEqual(result.notice, { required: true, topics: ['crowd_not_guaranteed'] });
  assert.deepEqual(gold([famous], { themes: ['coast'] }, crowd).relevantIds, [famous.id]);
});

test('date conflicts are computed from the stated dates and ask before recommending', () => {
  serial = 600;
  const beach = place('sea', 'E');
  const base = { startDate: '2026-12-20', endDate: '2026-12-22', nights: 2 };
  assert.equal(tripConflict({ ...base, statedNights: 4 }), 'nights_mismatch');
  assert.equal(tripConflict({ ...base, statedNights: 2 }), null);
  assert.equal(tripConflict({ ...base, requestedDate: '2026-12-25' }), 'date_outside_trip');
  assert.equal(tripConflict({ ...base, requestedDate: '2026-12-19' }), 'date_outside_trip');
  assert.equal(tripConflict({ ...base, requestedDate: '2026-12-21' }), null);
  assert.equal(tripConflict(base), null);
  assert.throws(() => tripConflict({ ...base, nights: 3 }), /span between/u);
  const trip = (value) => [{ type: 'trip_dates', value }];
  const conflicted = gold([beach], { themes: ['coast'] }, trip({ ...base, conflict: 'nights_mismatch', statedNights: 4 }));
  assert.equal(conflicted.expectedAction, 'ask_clarification');
  assert.deepEqual(conflicted.conflicts, ['date_conflict']);
  assert.deepEqual(conflicted.allowedIds, [beach.id]);
  assert.deepEqual(conflicted.notice, { required: false, topics: [] });
  assert.ok(conflicted.ruleTrace.includes('conflict.trip_dates:nights_mismatch'));
  assert.equal(gold([beach], { themes: ['coast'] }, trip({ ...base, conflict: null })).expectedAction, 'recommend');
  // The label cannot disagree with the dates in either direction.
  assert.throws(() => gold([beach], { themes: ['coast'] }, trip({ ...base, conflict: 'nights_mismatch', statedNights: 2 })), /does not match its dates/u);
  assert.throws(() => gold([beach], { themes: ['coast'] }, trip({ ...base, conflict: null, statedNights: 4 })), /does not match its dates/u);
  assert.throws(() => gold([beach], { themes: ['coast'] }, trip({ ...base, conflict: 'date_outside_trip', requestedDate: '2026-12-21' })), /does not match its dates/u);
});

test('the generated set is reproducible from its seed and covers every persona, slot and mandatory hard case', () => {
  const set = build();
  assert.equal(JSON.stringify(build()), JSON.stringify(set));
  // Pinned hashes of the synthetic fixture build. They change only when the rules, the spec or this fixture change;
  // update them together with such a change. Anything else (clock, process, platform) must leave them untouched.
  assert.equal(set.contentSha256.gold, 'e8f8a412669f6316c56bdf761acc55ff8146bf2314d4579cff50305eaea41492');
  assert.equal(set.items[0].brief.sha256, 'f3c21af18d4c3f464358e98d753a18203e5cc245b42b220f8e1b3990fdddf0b5');
  assert.equal(set.items.length, personas.prototypes.length * spec.blueprint.slots.length);
  assert.deepEqual(set.diagnostics, { requestedItems: 96, generatedItems: 96, dropped: [] });
  const reseeded = build({ spec: { ...spec, seed: 'another-seed' } });
  assert.notEqual(reseeded.contentSha256.gold, set.contentSha256.gold);
  assert.equal(JSON.stringify(build({ spec: { ...spec, seed: 'another-seed' } })), JSON.stringify(reseeded));
  const expected = { walk_minimal: 'recommend', accessibility_unknown: 'cannot_confirm', date_conflict: 'ask_clarification', disallowed_id: 'recommend', empty_result: 'impossible' };
  for (const persona of personas.prototypes) {
    const own = set.items.filter((item) => item.personaId === persona.id);
    assert.equal(own.length, spec.blueprint.slots.length);
    for (const hardCase of REQUIRED_HARD_CASES) {
      const item = own.find((candidate) => candidate.hardCase === hardCase);
      assert.ok(item, `${persona.id} lacks ${hardCase}`);
      assert.equal(item.gold.expectedAction, expected[hardCase]);
    }
  }
  for (const item of set.items) {
    assert.ok(item.conditions.length >= 1 && item.conditions.length <= 4, item.id);
    assert.ok(EXPECTED_ACTIONS.includes(item.gold.expectedAction));
    assert.equal(item.gold.expectedAction === 'impossible', item.gold.allowedIds.length === 0);
    // No verified evidence in the fixture: every hard requirement is unknown and never yields a plain recommendation.
    for (const requirement of item.gold.hardRequirements) {
      assert.equal(requirement.expectedVerdict, 'unknown');
      assert.equal(requirement.verdicts.pass + requirement.verdicts.fail, 0);
    }
    if (item.gold.hardRequirements.length > 0) assert.notEqual(item.gold.expectedAction, 'recommend');
    assert.ok(item.gold.relevantIds.every((id) => item.gold.allowedIds.includes(id)));
    assert.ok(item.gold.forbidden.every((entry) => !item.gold.allowedIds.includes(entry.id)));
    assert.ok(item.gold.ruleTrace.length >= 3 && item.facts.allowed === item.gold.allowedIds.length);
    assert.equal(item.query.source, 'template');
    assert.deepEqual(checkQueryText(item.query.template, item.brief), [], `${item.id} template breaks the text rules`);
  }
  const excludedItem = set.items.find((item) => item.hardCase === 'disallowed_id');
  assert.ok(excludedItem.gold.forbidden.length >= 1 && excludedItem.gold.forbidden.every((entry) => entry.reason === 'user_excluded'));
  assert.ok(excludedItem.brief.namedPlaces.length >= 1);
  for (const item of set.items.filter((candidate) => candidate.hardCase === 'date_conflict')) {
    const trip = item.conditions.find((condition) => condition.type === 'trip_dates').value;
    assert.ok(trip.conflict !== null && tripConflict(trip) === trip.conflict, `${item.id} must really conflict`);
    assert.ok(item.brief.notes.length === 1 && item.context.trip.startDate < item.context.trip.endDate);
  }
  assert.equal(set.items.filter((item) => item.hardCase === 'walk_minimal').every((item) => item.gold.notice.topics.includes('walking_distance_unknown')), true);
  // Within one slot no two personas are given the same cell while the fixture still offers unused ones.
  const cell = (item) => `${item.selector.category ?? item.selector.themes.join('+')}|${item.zone}`;
  for (const slot of ['C1', 'C3', 'H2', 'H4']) {
    const cells = set.items.filter((item) => item.slot === slot).map(cell);
    assert.equal(new Set(cells).size, cells.length, `slot ${slot} repeats a cell`);
  }
});

test('the evaluation instant is the anchor date in Korea, not the clock', () => {
  // Verified hours valid for exactly one second ending at the anchor instant: any other instant makes them unknown.
  const sharp = fixtureCatalog((entry) => {
    if (entry.category !== 'stay') entry.attributes.hours = verified(reportedHours(0, 1440, null), '2026-12-06T14:59:59Z', '2026-12-06T15:00:00Z');
  });
  const withTime = build({ catalog: sharp }).items.filter((item) => item.gold.hardRequirements.some((r) => r.type === 'open_at'));
  assert.ok(withTime.length > 0);
  for (const item of withTime) {
    assert.equal(item.gold.expectedAction, 'recommend', item.id);
    assert.equal(item.gold.confirmedIds.length, item.gold.allowedIds.length);
    assert.deepEqual(item.gold.notice.topics.filter((topic) => topic === 'hours_unverified'), []);
  }
  const summary = summarizeEvaluationSet(build({ catalog: sharp }));
  assert.equal(summary.gold.itemsWithVerifiedPass, withTime.length);
  assert.ok(summary.gold.hardRequirements.open_at.candidateVerdicts.pass > 0);
  const late = build({ catalog: sharp, spec: { ...spec, anchorDate: '2026-12-08' } }).items.filter((item) => item.gold.hardRequirements.some((r) => r.type === 'open_at'));
  assert.ok(late.length > 0 && late.every((item) => item.gold.expectedAction === 'cannot_confirm'));
});

test('validation rebuilds every item from its conditions and rejects tampering, stale provenance or a missing hard case', () => {
  const set = build();
  const tampered = (mutate) => { const copy = structuredClone(set); mutate(copy); return () => validateEvaluationSet(copy, inputs()); };
  assert.equal(validateEvaluationSet(structuredClone(set), inputs()).items.length, set.items.length);
  const withRequirement = set.items.findIndex((item) => item.gold.hardRequirements.length > 0);
  const withZone = set.items.findIndex((item) => item.zone !== null && item.selector.source === 'theme');
  const withTime = set.items.findIndex((item) => item.conditions.some((condition) => condition.type === 'open_at'));
  const mismatch = /does not match the rules/u;
  assert.throws(tampered((s) => { s.items[withRequirement].gold.expectedAction = 'recommend'; }), mismatch);
  assert.throws(tampered((s) => { s.items[withRequirement].gold.hardRequirements[0].expectedVerdict = 'pass'; }), mismatch);
  assert.throws(tampered((s) => { s.items[0].gold.allowedIds.pop(); }), mismatch);
  assert.throws(tampered((s) => { s.items[0].facts.allowed += 1; }), mismatch);
  assert.throws(tampered((s) => { s.items[0].difficulty = 'conflict'; }), mismatch);
  assert.throws(tampered((s) => { s.items[0].brief.statements.push('추가 조건'); }), mismatch);
  assert.throws(tampered((s) => { s.items[0].query.template = '다른 질문을 추천해 주세요.'; }), mismatch);
  assert.throws(tampered((s) => { s.items[0].context.today = '2026-01-01'; }), mismatch);
  // Fields that only restate the conditions cannot drift away from them.
  assert.throws(tampered((s) => { s.items[withZone].zone = null; }), mismatch);
  assert.throws(tampered((s) => { s.items[withZone].selector.themes = ['coast', 'nature']; }), mismatch);
  assert.throws(tampered((s) => { s.items[withZone].selector = null; }), mismatch);
  assert.throws(tampered((s) => { s.items[withTime].conditions.find((condition) => condition.type === 'open_at').value = '03:33'; }), /value the spec does not allow/u);
  assert.throws(tampered((s) => { s.items[0].conditions[0] = null; }), /unknown type or kind/u);
  assert.throws(tampered((s) => { s.items[0].conditions[0].extra = 1; }), /unsupported fields/u);
  assert.throws(tampered((s) => { delete s.items[0].generation; }), /generation record/u);
  assert.throws(tampered((s) => { s.items[0].query = { template: s.items[0].query.template, text: '바다를 추천해 주세요.', source: 'template' }; }), /query record is inconsistent/u);
  assert.throws(tampered((s) => { s.items[0].query = { template: s.items[0].query.template, text: '짧다', source: 'authored' }; }), /breaks the text rules/u);
  assert.throws(tampered((s) => { s.items[1].id = s.items[0].id; }), /unique/u);
  // Set-level records.
  assert.throws(tampered((s) => { s.contentSha256.gold = 'f'.repeat(64); }), /content hashes/u);
  assert.throws(tampered((s) => { s.contentSha256.queries = 'f'.repeat(64); }), /content hashes/u);
  assert.throws(tampered((s) => { s.queryAuthoring.mode = 'authored'; }), /authoring record/u);
  assert.throws(tampered((s) => { s.items.pop(); s.contentSha256.gold = s.contentSha256.gold; }), /content hashes|diagnostics/u);
  assert.throws(tampered((s) => { s.diagnostics.generatedItems = 1; }), /diagnostics/u);
  assert.throws(tampered((s) => { s.source.specSha256 = 'f'.repeat(64); }), /different catalog, spec or rule version/u);
  assert.throws(tampered((s) => { s.items = s.items.filter((item) => item.hardCase !== 'date_conflict'); }), /every mandatory hard case/u);
  assert.throws(() => validateEvaluationSet(set, inputs({ spec: { ...spec, seed: 'another-seed' } })), /different catalog, spec or rule version/u);
});

test('summary is aggregate-only: counts, identifiers and hashes with no place name, place ID or query text', () => {
  const set = build();
  const summary = summarizeEvaluationSet(set);
  const serialized = JSON.stringify(summary);
  for (const candidate of fixture.places) {
    assert.equal(serialized.includes(candidate.id), false, 'summary leaks a place id');
    assert.equal(serialized.includes(candidate.name), false, 'summary leaks a place name');
  }
  assert.equal(/[가-힣]/u.test(serialized), false, 'summary must not carry free Korean text');
  assert.equal(PLACE_ID_PREFIX.test(serialized), false, 'summary must not carry anything shaped like a place ID');
  assert.equal('items' in summary || 'places' in summary, false);
  const total = (object) => Object.values(object).reduce((sum, value) => sum + value, 0);
  assert.equal(summary.totals.items, set.items.length);
  for (const key of ['byPersona', 'bySlot', 'byDifficulty', 'byExpectedAction', 'byStatedConditions', 'bySelectorSource', 'byZone']) {
    assert.equal(total(summary.totals[key]), set.items.length, key);
  }
  assert.deepEqual(Object.keys(summary.totals.byHardCase), REQUIRED_HARD_CASES);
  assert.equal(summary.gold.itemsWithEmptyAllowedSet, summary.totals.byExpectedAction.impossible);
  assert.equal(summary.gold.itemsWithVerifiedPass, 0);
  assert.equal(summary.contentSha256.gold, set.contentSha256.gold);
  assert.equal(summary.contentSha256.queries, null);
  assert.deepEqual(summary.queries, { mode: 'template_only', promptVersion: spec.queryAuthoring.promptVersion, authored: 0, template: set.items.length, authorKind: null, authoringRecord: 'none' });
  for (const requirement of Object.values(summary.gold.hardRequirements)) assert.equal(requirement.candidateVerdicts.pass + requirement.candidateVerdicts.fail, 0);
  // The median is the true median: the mean of the two middle values for an even count.
  const bySize = [...set.items].sort((a, b) => a.gold.relevantIds.length - b.gold.relevantIds.length);
  const pair = [bySize[0], bySize[bySize.length - 1]];
  assert.ok(pair[1].gold.relevantIds.length > pair[0].gold.relevantIds.length);
  assert.deepEqual(summarizeEvaluationSet({ ...set, items: pair }).gold.relevantSetSize,
    { count: 2, min: pair[0].gold.relevantIds.length, median: (pair[0].gold.relevantIds.length + pair[1].gold.relevantIds.length) / 2, max: pair[1].gold.relevantIds.length });
  assert.equal(summarizeEvaluationSet({ ...set, items: bySize.slice(0, 3) }).gold.relevantSetSize.median, bySize[1].gold.relevantIds.length);
});

test('writer briefs carry stated conditions only, and authored queries attach only to the brief they were written for', () => {
  const set = build();
  const briefs = evaluationBriefs(set, spec);
  assert.equal(briefs.briefs.length, set.items.length);
  const serialized = JSON.stringify(briefs);
  for (const brief of briefs.briefs) {
    assert.deepEqual(Object.keys(brief).sort(), ['briefSha256', 'id', 'namedPlaces', 'notes', 'persona', 'statements', 'template', 'today']);
  }
  const named = new Set(set.items.flatMap((item) => item.brief.namedPlaces));
  for (const candidate of fixture.places) {
    assert.equal(serialized.includes(candidate.id), false, 'briefs must not show place ids');
    if (!named.has(candidate.name)) assert.equal(serialized.includes(candidate.name), false, 'briefs show a name the user does not have to say');
  }
  const target = set.items.find((item) => item.hardCase === 'disallowed_id');
  const other = set.items.find((item) => item.hardCase === 'date_conflict');
  const text = `${target.brief.namedPlaces.join(', ')}는 이미 다녀와서 빼고 추천해 주세요.`;
  const document = (queries, author = {}) => ({ schemaVersion: '1.0', kind: EVALUATION_QUERIES_KIND,
    author: { kind: 'in_session_llm', model: 'test-model', promptVersion: spec.queryAuthoring.promptVersion, ...author }, queries });
  const entry = (item, value) => ({ id: item.id, briefSha256: item.brief.sha256, text: value });
  const authored = build({ authoredQueries: document([entry(target, text)]) });
  const item = authored.items.find((candidate) => candidate.id === target.id);
  assert.deepEqual(item.query, { template: target.query.template, text, source: 'authored' });
  assert.equal(authored.contentSha256.gold, set.contentSha256.gold, 'authoring must not change gold');
  assert.match(authored.contentSha256.queries, /^[a-f0-9]{64}$/u);
  const reworded = build({ authoredQueries: document([entry(target, `${text} 부탁드려요.`)]) });
  assert.notEqual(reworded.contentSha256.queries, authored.contentSha256.queries, 'the queries hash must follow the text');
  assert.equal(reworded.contentSha256.gold, set.contentSha256.gold);
  assert.deepEqual(authored.queryAuthoring, { mode: 'authored', promptVersion: spec.queryAuthoring.promptVersion, authored: 1, template: set.items.length - 1,
    author: { kind: 'in_session_llm', model: 'test-model', promptVersion: spec.queryAuthoring.promptVersion, seed: null, samplingSeed: null, authoredOn: null, review: null } });
  const summary = summarizeEvaluationSet(authored);
  assert.deepEqual(summary.queries, { mode: 'authored', promptVersion: spec.queryAuthoring.promptVersion, authored: 1, template: set.items.length - 1, authorKind: 'in_session_llm', authoringRecord: 'local_only' });
  assert.equal(JSON.stringify(summary).includes('test-model'), false, 'the committed summary must not name the authoring model');
  assert.equal(build({ authoredQueries: document([]) }).queryAuthoring.mode, 'template_only');
  const rejects = (queries, pattern, author) => assert.throws(() => build({ authoredQueries: document(queries, author) }), pattern);
  rejects([entry({ id: target.id, brief: other.brief }, text)], /different brief/u);
  rejects([{ id: 'EV-P99-S1', briefSha256: target.brief.sha256, text }], /exist in the set/u);
  rejects([entry(target, text), entry(target, text)], /unique/u);
  rejects([entry(target, '그곳은 빼고 추천해 주세요. 다른 곳이 좋아요.')], /name every place/u);
  rejects([entry(other, 'kk-12345 장소를 일정에 넣어 추천해 주세요.')], /place ID/u);
  rejects([entry(other, 'Please recommend places.')], /Korean/u);
  rejects([entry(other, '짧다')], /10 to 400/u);
  rejects([entry(other, '첫 줄입니다.\n둘째 줄을 추천해 주세요.')], /single line/u);
  rejects([entry(other, '다음 주에 제주에 가려고 합니다. 바다를 좋아해요.')], /ask for something/u);
  rejects([entry(target, text)], /prompt version/u, { promptVersion: 'old-prompt' });
  rejects([entry(target, text)], /the model/u, { model: '' });
  // Nothing free-form from the queries file may reach the summary: the author kind is an enum and extra fields are refused.
  rejects([entry(target, text)], /author\.kind must be one of/u, { kind: 'some-model-name 자유 문장' });
  rejects([entry(target, text)], /unsupported fields/u, { mode: 'template_only' });
  rejects([entry(target, text)], /seed or review/u, { samplingSeed: 'x' });
  assert.throws(() => build({ authoredQueries: { author: { kind: 'human', model: 'none', promptVersion: spec.queryAuthoring.promptVersion }, queries: [] } }), /schemaVersion "1\.0" and kind/u);
  assert.deepEqual(checkQueryText('바다가 보이는 조용한 곳을 추천해 주세요.', { namedPlaces: [] }), []);
});

test('scorer gives a gold-built answer full marks and flags unknown-as-pass, forbidden IDs and missing notices', () => {
  const set = build();
  const scorer = createScorer(inputs());
  const oracle = aggregateScores(set.items.map((item) => scorer.score(item, scorer.oracle(item))), scorer.k);
  assert.equal(oracle.actionAccuracy, 1);
  assert.equal(oracle.disallowedIds.rate, 0);
  assert.equal(oracle.hardRequirementMisjudgement.rate, 0);
  assert.equal(oracle.hardRequirementMisjudgement.applicableItems, set.items.filter((item) => item.gold.hardRequirements.length > 0).length);
  assert.equal(oracle.recallAtK.mean, 1);
  assert.equal(oracle.recallAtK.scoredItems, set.items.filter((item) => ['recommend', 'cannot_confirm'].includes(item.gold.expectedAction) && item.gold.relevantIds.length > 0).length);
  assert.deepEqual([oracle.confirmationNotice.rate, oracle.confirmationNotice.topicCoverage.mean], [1, 1]);
  assert.deepEqual([oracle.latencyMs.p50, oracle.costUsd.total], [null, null], 'unmeasured values stay null');

  const wheelchair = set.items.find((item) => item.hardCase === 'accessibility_unknown');
  const careless = scorer.score(wheelchair, { itemId: wheelchair.id, action: 'recommend', placeIds: [...wheelchair.gold.allowedIds.slice(0, 2), 'kk-not-in-catalog'],
    requirementClaims: [{ requirement: 'wheelchair', placeId: wheelchair.gold.allowedIds[0], verdict: 'pass' }, { requirement: 'budget_cap', verdict: 'pass' }], latencyMs: 1200, costUsd: 0.002 });
  assert.equal(careless.actionCorrect, false);
  assert.equal(careless.passOnUnknown, true);
  assert.deepEqual([careless.ids.disallowed, careless.ids.notInCatalog, careless.claims.unrequested], [1, 1, 1]);
  assert.deepEqual([careless.noticeRequired, careless.noticeGiven, careless.noticeTopicCoverage], [true, false, null]);
  const blanket = scorer.score(wheelchair, { itemId: wheelchair.id, action: 'cannot_confirm', placeIds: wheelchair.gold.relevantIds.slice(0, 1),
    requirementClaims: [{ requirement: 'wheelchair', verdict: 'pass' }], confirmationNotice: true, noticeTopics: [] });
  assert.deepEqual([blanket.passOnUnknown, blanket.noticeGiven, blanket.noticeTopicCoverage], [true, true, 0]);

  const excluded = set.items.find((item) => item.hardCase === 'disallowed_id');
  const usesExcluded = scorer.score(excluded, { itemId: excluded.id, action: 'recommend', placeIds: [excluded.gold.forbidden[0].id, excluded.gold.allowedIds[0]] });
  assert.deepEqual([usesExcluded.ids.user_excluded, usesExcluded.ids.disallowed], [1, 1]);
  const stay = fixture.places.find((candidate) => candidate.category === 'stay');
  assert.equal(scorer.score(excluded, { itemId: excluded.id, action: 'recommend', placeIds: [stay.id] }).ids.outsideCandidates, 1);

  const empty = set.items.find((item) => item.hardCase === 'empty_result');
  const invented = scorer.score(empty, { itemId: empty.id, action: 'recommend', placeIds: [fixture.places[0].id] });
  assert.deepEqual([invented.actionCorrect, invented.ids.disallowed, invented.recallAtK], [false, 1, null]);
  const honest = scorer.score(empty, { itemId: empty.id, action: 'impossible', placeIds: [] });
  assert.deepEqual([honest.actionCorrect, honest.ids.disallowed], [true, 0]);

  const totals = aggregateScores([careless, usesExcluded, honest], scorer.k);
  assert.equal(totals.disallowedIds.rate, 2 / 5);
  assert.deepEqual(totals.disallowedIds.byReason, { notInCatalog: 1, outsideCandidates: 0, userExcluded: 1, routeForMinimalWalk: 0, verifiedFail: 0 });
  assert.deepEqual([totals.hardRequirementMisjudgement.applicableItems, totals.hardRequirementMisjudgement.passOnUnknownItems, totals.hardRequirementMisjudgement.rate], [1, 1, 1]);
  assert.deepEqual([totals.latencyMs.measuredItems, totals.latencyMs.p50, totals.costUsd.total], [1, 1200, 0.002]);
  assert.equal(aggregateScores([honest], scorer.k).disallowedIds.rate, null, 'no returned IDs means the rate is undefined, not 0');

  assert.throws(() => scorer.score(empty, { itemId: 'EV-P01-S1', action: 'impossible', placeIds: [] }), /does not answer item/u);
  assert.throws(() => validateResponse({ itemId: 'x', action: 'maybe', placeIds: [] }), /known action/u);
  assert.throws(() => validateResponse({ itemId: 'x', action: 'recommend', placeIds: ['a', 'a'] }), /unique strings/u);
  assert.throws(() => validateResponse({ itemId: 'x', action: 'recommend', placeIds: [], requirementClaims: [{ requirement: 'parking', verdict: 'pass' }] }), /hard requirement/u);
  assert.throws(() => validateResponse({ itemId: 'x', action: 'recommend', placeIds: [], noticeTopics: ['made_up_topic'] }), /known notice topics/u);
  assert.throws(() => validateResponse({ itemId: 'x', action: 'recommend', placeIds: [], latencyMs: -1 }), /non-negative/u);
});

test('scorer judges claims against verified evidence per place, including blanket claims and verified fails', () => {
  serial = 700;
  const passes = place('art', 'S', { attributes: { indoor: verified(curatedIndoor) } });
  const fails = place('art', 'S', { attributes: { indoor: verified({ value: false, source: 'raw-poi-indoor' }) } });
  const unknown = place('art', 'S', { attributes: { indoor: curatedIndoor } });
  const catalog = catalogOf([passes, fails, unknown]);
  const scorer = createScorer({ catalog, personas, spec });
  const itemFor = (id, conditions) => {
    const { facts, ...rest } = gold(catalog.places, { themes: ['art'] }, conditions);
    return { id, personaId: 'P01', conditions: conditionsOf(conditions), gold: rest };
  };
  const one = itemFor('EV-TEST-1', [rain]);
  assert.equal(one.gold.expectedAction, 'recommend');
  const answer = (item, fields) => scorer.score(item, { itemId: item.id, action: item.gold.expectedAction, ...fields });
  // Returning only the verified place and calling it a pass is right, and needs no notice.
  const exact = answer(one, { placeIds: [passes.id], requirementClaims: [{ requirement: 'indoor_required', verdict: 'pass' }] });
  assert.deepEqual([exact.actionCorrect, exact.passOnUnknown, exact.failOnUnknown, exact.noticeRequired, exact.recallAtK], [true, false, false, false, 1]);
  // A blanket pass also covers the unverified place that was returned with it.
  const blanket = answer(one, { placeIds: [passes.id, unknown.id], requirementClaims: [{ requirement: 'indoor_required', verdict: 'pass' }] });
  assert.equal(blanket.passOnUnknown, true);
  const perPlace = answer(one, { placeIds: [passes.id], requirementClaims: [{ requirement: 'indoor_required', placeId: unknown.id, verdict: 'pass' }] });
  assert.equal(perPlace.passOnUnknown, true);
  assert.deepEqual((({ applicableItems, passOnUnknownItems, rate }) => [applicableItems, passOnUnknownItems, rate])(aggregateScores([perPlace], scorer.k).hardRequirementMisjudgement), [1, 1, 1]);
  // Saying the verified-fail place fails is correct; calling it a pass contradicts verified evidence and uses a forbidden ID.
  const two = itemFor('EV-TEST-2', [rain, { type: 'budget_cap', value: 50000 }]);
  assert.equal(two.gold.expectedAction, 'cannot_confirm');
  const careful = answer(two, { placeIds: [passes.id], confirmationNotice: true, noticeTopics: ['cost_unknown'], requirementClaims: [
    { requirement: 'indoor_required', placeId: fails.id, verdict: 'fail' }, { requirement: 'indoor_required', placeId: passes.id, verdict: 'pass' }, { requirement: 'budget_cap', verdict: 'unknown' }] });
  assert.deepEqual([careful.passOnUnknown, careful.failOnUnknown, careful.claims.contradictsVerified, careful.noticeTopicCoverage], [false, false, 0, 1]);
  assert.equal(aggregateScores([careful], scorer.k).hardRequirementMisjudgement.rate, 0);
  const wrong = answer(two, { placeIds: [fails.id], requirementClaims: [{ requirement: 'indoor_required', placeId: fails.id, verdict: 'pass' }, { requirement: 'budget_cap', verdict: 'pass' }] });
  assert.deepEqual([wrong.claims.contradictsVerified, wrong.passOnUnknown, wrong.ids.verified_fail, wrong.ids.disallowed], [1, true, 1, 1]);
  // The reference answer stays perfect when verified evidence exists.
  for (const item of [one, two]) {
    const score = scorer.score(item, scorer.oracle(item));
    assert.deepEqual([score.actionCorrect, score.ids.disallowed, score.passOnUnknown, score.failOnUnknown, score.recallAtK, score.noticeGiven === score.noticeRequired], [true, 0, false, false, 1, true]);
  }
});

test('builder CLI writes local-only outputs once, an aggregate report, and --check proves reproducibility', async () => {
  assert.throws(() => parseArguments(['--output', '.local/set.json']), /--catalog is required/u);
  assert.throws(() => parseArguments(['--catalog', 'c.json']), /at least one/u);
  assert.throws(() => parseArguments(['--catalog', 'c.json', '--report', 'summary.txt']), /\.json/u);
  assert.throws(() => parseArguments(['--catalog', 'c.json', '--unknown', 'x']), /Usage/u);
  assert.throws(() => parseArguments(['--catalog', 'c.json', '--catalog', 'd.json', '--report', 'r.json']), /Duplicate/u);
  assert.deepEqual(parseArguments(['--help']), { help: true });
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'evaluation-set-'));
  try {
    const local = path.join(tmp, '.local');
    await mkdir(local, { recursive: true });
    const catalogFile = path.join(local, 'catalog.json');
    await writeFile(catalogFile, JSON.stringify(fixture));
    const output = path.join(local, 'set.json');
    const briefs = path.join(local, 'briefs.json');
    const report = path.join(tmp, 'summary.json');
    const args = ['--catalog', catalogFile, '--output', output, '--briefs', briefs, '--report', report];
    const built = await buildMain(args);
    assert.deepEqual([built.status, built.items, built.dropped, built.written], ['built', 96, 0, ['output', 'briefs', 'report']]);
    const written = await readJson(output);
    assert.equal(validateEvaluationSet(written, inputs()).items.length, 96);
    assert.equal((await readJson(report)).contentSha256.gold, written.contentSha256.gold);
    assert.equal((await readJson(briefs)).briefs.length, 96);
    const checked = await buildMain([...args, '--check']);
    assert.deepEqual([checked.mismatched, checked.compared, 'written' in checked], [[], ['output', 'briefs', 'report'], false]);
    // A separate process produces the same gold: nothing depends on in-process state, the clock or the process id.
    const child = await execFileAsync(process.execPath, [CLI, ...args, '--check'], { cwd: os.tmpdir() });
    assert.equal(JSON.parse(child.stdout.trim().split('\n').pop()).goldSha256, built.goldSha256);
    assert.equal(built.goldSha256, build().contentSha256.gold);

    // Local-only outputs are never overwritten and never written outside a .local directory, whichever option names them.
    await assert.rejects(buildMain(args), /already exists/u);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--briefs', briefs]), /--briefs already exists/u);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--output', path.join(tmp, 'set.json')]), /\.local/u);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--briefs', path.join(tmp, 'briefs.json')]), /\.local/u);
    await mkdir(path.join(tmp, 'tracked'));
    await symlink(path.join(tmp, 'tracked'), path.join(local, 'link'));
    await assert.rejects(buildMain(['--catalog', catalogFile, '--output', path.join(local, 'link', 'set.json')]), /\.local/u);
    await assert.rejects(readFile(path.join(tmp, 'tracked', 'set.json')), /ENOENT/u);
    // The report cannot replace an input or a local output, and a rejected run writes nothing.
    await assert.rejects(buildMain(['--catalog', catalogFile, '--report', catalogFile]), /name the same file/u);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--report', path.join(local, 'summary.json')]), /not written inside a \.local/u);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--output', path.join(local, 'new.json'), '--briefs', briefs, '--report', report]), /--briefs already exists/u);
    await assert.rejects(readFile(path.join(local, 'new.json')), /ENOENT/u);
    assert.equal(JSON.parse(await readFile(catalogFile, 'utf8')).kind, 'jeju-agent-catalog');

    // --check notices a changed or missing file of every kind.
    await writeFile(report, `${JSON.stringify({ ...(await readJson(report)), kind: 'edited' }, null, 2)}\n`);
    await assert.rejects(buildMain([...args, '--check']), /differs from: report/u);
    await writeFile(output, `${await readFile(output, 'utf8')} `);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--output', output, '--check']), /differs from: output/u);
    await unlink(briefs);
    await assert.rejects(buildMain(['--catalog', catalogFile, '--briefs', briefs, '--check']), /differs from: briefs/u);
    await assert.rejects(buildMain(['--catalog', path.join(local, 'missing.json'), '--report', report]), /ENOENT/u);

    // With authored queries the report still carries no author model and no authoring record.
    const set = build();
    const target = set.items[0];
    const queriesFile = path.join(local, 'queries.json');
    await writeFile(queriesFile, JSON.stringify({ schemaVersion: '1.0', kind: EVALUATION_QUERIES_KIND,
      author: { kind: 'in_session_llm', model: 'secret-model-name', promptVersion: spec.queryAuthoring.promptVersion },
      queries: [{ id: target.id, briefSha256: target.brief.sha256, text: '바다 풍경이 좋은 곳을 추천해 주세요.' }] }));
    const authoredReport = path.join(tmp, 'summary-authored.json');
    const authored = await buildMain(['--catalog', catalogFile, '--queries', queriesFile, '--output', path.join(local, 'set-authored.json'), '--report', authoredReport]);
    assert.equal(authored.authoredQueries, 1);
    const reportText = await readFile(authoredReport, 'utf8');
    assert.equal(reportText.includes('secret-model-name') || reportText.includes('queryAuthoring') || reportText.includes('바다'), false);
    assert.equal((await readJson(path.join(local, 'set-authored.json'))).queryAuthoring.author.model, 'secret-model-name');
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});
