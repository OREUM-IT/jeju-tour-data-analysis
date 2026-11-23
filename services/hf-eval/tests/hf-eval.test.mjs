import test from 'node:test';
import assert from 'node:assert/strict';
import { bigrams, createBm25, placeText, rankIds, rankingMetrics } from '../src/place-text.mjs';
import { cityDocuments, parseCsv } from '../scripts/synthtrips-retrieval.mjs';
import { buildUserPrompt, resolvedRequirements, responseSchema, selectCandidates } from '../scripts/collect-local-llm.mjs';

const place = (id, extra = {}) => ({ id, name: `장소${id}`, category: 'sea', kind: '해변', zone: 'W', themes: { coast: 0.8, nature: 0.2 },
  attributes: { indoor: { value: false, basis: 'inferred' } }, ...extra });

test('place text uses catalog fields only, never theme weights', () => {
  const text = placeText(place('a', { attributes: { note: { value: '  메모\n있음 ', basis: 'source_reported' } } }));
  assert.equal(text, '장소a · 해변 · 바다·해변 · 한림·애월 · 메모 있음');
  assert.doesNotMatch(text, /coast|0\.8/u);
});

test('bigram BM25 ranks literal matches first and ties break by ID', () => {
  assert.deepEqual(bigrams('제주 바다'), ['제주', '바다']);
  const score = createBm25(['협재 해수욕장 바다', '한라산 오름', '바다 카페']);
  const ranked = rankIds(['x', 'y', 'z'], score('바다 해수욕장'));
  assert.deepEqual(ranked, ['x', 'z', 'y']);
  assert.deepEqual(rankIds(['b', 'a'], [1, 1]), ['a', 'b']);
});

test('ranking metrics follow binary definitions', () => {
  const m = rankingMetrics(['a', 'b', 'c'], new Set(['b']), { k: 2, recallDepth: 3 });
  assert.equal(m.precisionAtK, 0.5);
  assert.equal(m.mrr, 0.5);
  assert.equal(m.recallAtDepth, 1);
  assert.ok(Math.abs(m.ndcgAtK - 1 / Math.log2(3)) < 1e-12);
});

test('CSV parser handles quotes, commas and newlines; city documents are sorted and bounded', () => {
  const rows = parseCsv('city,country,interest_title,interest_type,low_season\n"B, x",C,"say ""hi""",see,"[\'May\']"\nA,D,"two\nlines",do,[]\n');
  assert.equal(rows.length, 2);
  assert.equal(rows[0].city, 'B, x');
  assert.equal(rows[0].interest_title, 'say "hi"');
  const docs = cityDocuments(rows);
  assert.deepEqual(docs.map((d) => d.id), ['A', 'B, x']);
  assert.match(docs[1].text, /Low season: May\./u);
});

test('local pipeline schema only allows shown IDs and requested requirement types', () => {
  const request = { query: 'q', visitDate: '2026-12-08', filters: {}, excludedIds: [],
    requirements: [{ type: 'indoor_required', value: true }] };
  assert.deepEqual(resolvedRequirements(request), [{ type: 'indoor_required', value: true, date: '2026-12-08' }]);
  const catalog = { places: [place('a'), place('b'), place('c')] };
  const vectorsById = new Map([['a', [0, 1]], ['b', [1, 0]], ['c', [0.7, 0.7]]]);
  const { poolSize, candidates } = selectCandidates({ catalog, request, queryVector: [1, 0], vectorsById, top: 2, atMs: Date.parse('2026-12-07T00:00:00Z') });
  assert.equal(poolSize, 3);
  assert.deepEqual(candidates.map((c) => c.place.id), ['b', 'c']);
  assert.equal(candidates[0].checks[0].verdict, 'unknown');
  const schema = responseSchema(request, candidates);
  assert.deepEqual(schema.properties.placeIds.items.enum, ['b', 'c']);
  assert.deepEqual(schema.properties.requirementClaims.items.properties.requirement.enum, ['indoor_required']);
  assert.deepEqual(schema.properties.noticeTopics.items.enum, ['indoor_unverified']);
  assert.equal(responseSchema({ ...request, requirements: [] }, []).properties.placeIds.maxItems, 0);
  const prompt = buildUserPrompt(request, candidates);
  assert.match(prompt, /- b \| 장소b .*조건: indoor_required=unknown/u);
  assert.doesNotMatch(prompt, /gold|relevant/u);
});

test('rule-action: server decides ask/impossible and recommend vs cannot_confirm from evidence, never from gold', async () => {
  const { ruleDecision, assembleRuleResponse, selectionSchema } = await import('../scripts/collect-local-llm.mjs');
  const catalog = { places: [place('a'), place('b')] };
  const base = { query: 'q', visitDate: '2026-12-08', filters: {}, excludedIds: [], requirements: [] };
  const at = Date.parse('2026-12-07T00:00:00Z');
  assert.deepEqual(ruleDecision({ catalog, request: base, audit: { dateConflict: 'date_outside_trip' }, atMs: at }), { action: 'ask_clarification', reason: 'date_outside_trip' });
  assert.equal(ruleDecision({ catalog, request: { ...base, excludedIds: ['a', 'b'] }, audit: { dateConflict: null }, atMs: at }).reason, 'no_candidates');
  const open = ruleDecision({ catalog, request: base, audit: { dateConflict: null }, atMs: at });
  assert.equal(open.action, null);
  assert.deepEqual([...open.viableIds], ['a', 'b']);
  const byId = new Map(catalog.places.map((p) => [p.id, p]));
  assert.equal(assembleRuleResponse({ request: base, chosen: ['a'], atMs: at, placesById: byId }).action, 'recommend');
  const indoor = { ...base, requirements: [{ type: 'indoor_required', value: true }] };
  const r = assembleRuleResponse({ request: indoor, chosen: ['b', 'a'], atMs: at, placesById: byId });
  assert.equal(r.action, 'cannot_confirm');
  assert.deepEqual(r.requirementClaims.map((c) => [c.placeId, c.verdict]), [['b', 'unknown'], ['a', 'unknown']]);
  assert.deepEqual(r.noticeTopics, ['indoor_unverified']);
  assert.equal(r.confirmationNotice, true);
  assert.equal(selectionSchema([{ place: { id: 'a' } }]).properties.placeIds.minItems, 1);
});
