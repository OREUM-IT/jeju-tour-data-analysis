import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { validateAgentCatalog } from '../src/agent-catalog.mjs';
import { hasRoute } from '../src/evaluation-set.mjs';
import { asksMinimalWalk, isRoutePlace, MINIMAL_WALK_SENTENCE } from '../src/minimal-walk.mjs';
import { buildValidatedRequest } from '../src/validated-request-adapter.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
// The 981-place app catalog stays in .local and is read only where it exists (never in CI).
const LOCAL_CATALOG = process.env.JEJU_AGENT_CATALOG ?? fileURLToPath(new URL('../.local/agent-catalog-b0602a1.json', import.meta.url));

test('minimal walking is the exact template sentence anywhere in the query, in either Unicode form', () => {
  assert.equal(MINIMAL_WALK_SENTENCE, '거의 걷지 않으려고 합니다.');
  const template = '제주에서 해안 테마로 갈 만한 곳을 추천해 주세요. 거의 걷지 않으려고 합니다.';
  for (const query of [template, `여행 요청:\n${template} 아이와 함께 갑니다.`, template.normalize('NFD'),
    JSON.stringify([['q09_notes', '날짜별 메모', '거의 걷지 않으려고 합니다.']])]) {
    assert.equal(asksMinimalWalk(query), true, query);
  }
  // Other wording, a survey walking answer (Q08 long walks) and non-strings never switch the rule on.
  for (const query of ['제주에서 해안 테마로 갈 만한 곳을 추천해 주세요.', '거의 못 걸어요.', '많이 걸어도 좋습니다.',
    '거의 걷지 않으려고 합니다', '거의 걷지  않으려고 합니다.', JSON.stringify([['q08_items', '피하거나 확인할 항목', ['walk'], ['긴 걷기·등산']]]),
    '', undefined, null, 1, [MINIMAL_WALK_SENTENCE]]) {
    assert.equal(asksMinimalWalk(query), false, String(query));
  }
});

test('the Gateway sees minimal walking in exactly the template items whose conditions state it', () => {
  let walking = 0;
  for (const item of inputs.set.items) {
    const request = buildValidatedRequest({ query: item.query.template, context: item.context },
      { catalog: inputs.catalog, requestId: 'baseline-preflight' });
    const stated = item.conditions.some((condition) => condition.type === 'walk' && condition.value === 'minimal');
    assert.equal(asksMinimalWalk(request.query), stated, item.id);
    walking += stated ? 1 : 0;
  }
  assert.ok(walking > 0 && walking < inputs.set.items.length);
});

test('the route predicate is the gold rule hasRoute on every fixture place', () => {
  const places = inputs.catalog.places;
  assert.ok(places.some(isRoutePlace) && places.some((place) => !isRoutePlace(place)));
  for (const place of places) assert.equal(isRoutePlace(place), hasRoute(place), place.id);
  assert.equal(isRoutePlace(undefined), false);
});

test('on the local app catalog the route predicate agrees with hasRoute for every place',
  { skip: existsSync(LOCAL_CATALOG) ? false : 'the private app catalog is not present here (expected in CI)' }, () => {
    const catalog = validateAgentCatalog(JSON.parse(readFileSync(LOCAL_CATALOG, 'utf8')));
    const routes = catalog.places.filter(isRoutePlace).length;
    assert.ok(routes > 0 && routes < catalog.places.length);
    // Counts only: no place ID or name leaves .local through a failure message.
    const disagreeing = catalog.places.filter((place) => isRoutePlace(place) !== hasRoute(place)).length;
    assert.equal(disagreeing, 0);
  });
