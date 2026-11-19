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
  const [selected, excluded] = catalog.places;
  const now = Date.now();
  const request = { schemaVersion: '1.0', requestId: 'gw-' + '0'.repeat(32),
    query: '배포 후 오프라인 합성 검사: 원래 후보의 미검증 비용 조건을 보존한다.',
    timezone: 'Asia/Seoul', filters: { category: selected.category }, excludedIds: [excluded.id],
    requirements: [{ type: 'budget_cap', value: 10000, unit: 'KRW_per_person_per_day' }] };
  const scopes = createGatewayScopes({ catalog, now });
  const prepared = scopes.register({ request, principal: '0'.repeat(64), deadlineMs: now + 60_000 });
  const response = { schemaVersion: '1.0', requestId: request.requestId, action: 'recommend',
    placeIds: [selected.id, excluded.id, selected.id],
    requirementClaims: [{ placeId: selected.id, requirement: 'budget_cap', verdict: 'pass' }],
    confirmationNotice: false, noticeTopics: [] };
  const input = { request, response, expectedCatalogVersion: prepared.catalogVersion };
  const strict = createRuntimeValidator({ catalog, now });
  assert.equal(strict.validate(input).status, 'blocked');
  const result = scopes.validate(input);
  assert.equal(result.status, 'accepted');
  assert.deepEqual(result.response.placeIds, [selected.id]);
  assert.equal(result.response.action, 'cannot_confirm');
  assert.deepEqual(result.response.requirementClaims, [{ placeId: selected.id, requirement: 'budget_cap', verdict: 'unknown' }]);
  assert.equal(result.response.confirmationNotice, true);
  assert.deepEqual(result.response.noticeTopics, ['cost_unknown']);
  assert.equal(strict.validate({ ...input, response: result.response }).status, 'accepted');
  scopes.release(request.requestId);
  assert.equal(scopes.ready().activeScopes, 0);
  console.log(JSON.stringify({ status: 'passed', scope: 'candidate-recovery-offline-runtime',
    placeCount: catalog.places.length, unscopedStrictPreserved: true, liveScopeRegistered: false,
    modelExecuted: false, productionReady: false }));
} catch {
  console.error('CANDIDATE_RECOVERY_OFFLINE_CHECK_FAILED: no model was executed; no catalog or secret values displayed.');
  process.exitCode = 1;
}
