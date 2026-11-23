// Run on stdin from /app in the MCP container. Local scopes only: no HTTP calls,
// live scope registration, Langflow execution or model inference.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

globalThis.fetch = () => { throw new Error('NETWORK_NOT_ALLOWED_IN_OFFLINE_CHECK'); };
try {
  const fromApp = name => import(pathToFileURL(resolve('src', name)).href);
  const [{ loadCatalog }, { createGatewayScopes }, { createRuntimeValidator }] = await Promise.all([
    fromApp('catalog.mjs'), fromApp('gateway-scopes.mjs'), fromApp('recommendation-validation.mjs'),
  ]);
  const catalog = await loadCatalog(process.env.MCP_CATALOG_PATH);
  assert.equal(catalog.schemaVersion, '2.0');
  assert.equal(catalog.dataMode, 'app-export');
  assert.equal(catalog.places.length, 981);
  const [first, second, excluded] = catalog.places;
  const now = Date.now();
  // Same shape as the web questionnaire request: no filters, one budget requirement.
  const request = { schemaVersion: '1.0', requestId: 'gw-' + '0'.repeat(32),
    query: '배포 후 오프라인 합성 검사: 모델이 고른 ID만으로 서버가 응답을 만든다.',
    timezone: 'Asia/Seoul', filters: {}, excludedIds: [excluded.id],
    requirements: [{ type: 'budget_cap', value: 10000, unit: 'KRW_per_person_per_day' }] };
  const scopes = createGatewayScopes({ catalog, now });
  const strict = createRuntimeValidator({ catalog, now });
  const prepared = scopes.register({ request, principal: '0'.repeat(64), deadlineMs: now + 60_000 });
  const input = response => ({ request, response, expectedCatalogVersion: prepared.catalogVersion });
  const selection = { placeIds: [second.id, excluded.id, 'not-in-catalog', second.id, first.id] };
  assert.equal(strict.validate(input(selection)).status, 'blocked');
  const result = scopes.validate(input(selection));
  assert.equal(result.status, 'accepted');
  assert.deepEqual(result.response.placeIds, [second.id, first.id]);
  assert.equal(result.response.action, 'cannot_confirm');
  assert.deepEqual(result.response.requirementClaims, [second.id, first.id].map(placeId =>
    ({ placeId, requirement: 'budget_cap', verdict: 'unknown' })));
  assert.deepEqual(result.response.noticeTopics, ['cost_unknown']);
  // The Gateway re-validates the composed response; it must come back unchanged.
  assert.deepEqual(scopes.validate(input(result.response)), result);
  assert.equal(strict.validate(input(result.response)).status, 'accepted');
  assert.equal(scopes.validate(input({ placeIds: [] })).status, 'blocked');
  scopes.release(request.requestId);
  const none = { ...request, requestId: 'gw-' + '1'.repeat(32), filters: { text: '오프라인 검사용 존재하지 않는 장소 이름' } };
  const empty = scopes.register({ request: none, principal: '0'.repeat(64), deadlineMs: now + 60_000 });
  assert.equal(empty.candidateCount, 0);
  const impossible = scopes.validate({ request: none, response: { placeIds: [] }, expectedCatalogVersion: empty.catalogVersion });
  assert.equal(impossible.status, 'accepted');
  assert.equal(impossible.response.action, 'impossible');
  scopes.release(none.requestId);
  assert.equal(scopes.ready().activeScopes, 0);
  console.log(JSON.stringify({ status: 'passed', scope: 'rule-action-offline-runtime',
    placeCount: catalog.places.length, composedFromSelection: true, liveScopeRegistered: false,
    modelExecuted: false, productionReady: false }));
} catch {
  console.error('RULE_ACTION_OFFLINE_CHECK_FAILED: no model was executed; no catalog or secret values displayed.');
  process.exitCode = 1;
}
