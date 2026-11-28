// Run on stdin from /app in the MCP container after the image is rebuilt. Local scopes only: no HTTP calls,
// live scope registration, Langflow execution or model inference. Confirms that the installed MCP withholds
// route places for a minimal-walk request and changes nothing for the same request without that sentence.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

globalThis.fetch = () => { throw new Error('NETWORK_NOT_ALLOWED_IN_OFFLINE_CHECK'); };
try {
  const fromApp = name => import(pathToFileURL(resolve('src', name)).href);
  const [{ loadCatalog }, { createGatewayScopes }, { createRuntimeValidator }, { isRoutePlace, MINIMAL_WALK_SENTENCE }] = await Promise.all([
    fromApp('catalog.mjs'), fromApp('gateway-scopes.mjs'), fromApp('recommendation-validation.mjs'), fromApp('minimal-walk.mjs'),
  ]);
  const catalog = await loadCatalog(process.env.MCP_CATALOG_PATH);
  assert.equal(catalog.schemaVersion, '2.0');
  assert.equal(catalog.dataMode, 'app-export');
  assert.equal(catalog.places.length, 981);
  const routes = catalog.places.filter(isRoutePlace);
  const now = Date.now();
  const scopes = createGatewayScopes({ catalog, now });
  const strict = createRuntimeValidator({ catalog, now });
  // Same shape as the web questionnaire request (no filters), with and without the minimal-walk sentence.
  const base = { schemaVersion: '1.0', timezone: 'Asia/Seoul', filters: {}, excludedIds: [], requirements: [] };
  const walking = { ...base, requestId: 'gw-' + '0'.repeat(32), query: `배포 후 오프라인 합성 검사입니다. ${MINIMAL_WALK_SENTENCE}` };
  const plain = { ...base, requestId: 'gw-' + '1'.repeat(32), query: '배포 후 오프라인 합성 검사입니다.' };
  const register = request => scopes.register({ request, principal: '0'.repeat(64), deadlineMs: now + 60_000 });
  const tool = (request, name, args) => scopes.call({ requestId: request.requestId, name, arguments: args });
  const select = (request, prepared, placeIds) => scopes.validate({ request, response: { placeIds },
    expectedCatalogVersion: prepared.catalogVersion });
  const walkingPrepared = register(walking);
  const plainPrepared = register(plain);
  // Binding, hashes and candidate count stay those of the strict view.
  assert.deepEqual(walkingPrepared, strict.prepare(walking));
  assert.equal(walkingPrepared.candidateCount, plainPrepared.candidateCount);
  if (routes.length > 0) {
    const [route] = routes;
    const other = catalog.places.find(place => !isRoutePlace(place));
    const named = request => tool(request, 'places.search', { query: route.name.slice(0, 100) }).data.items;
    assert.ok(named(walking).every(place => !isRoutePlace(place)));
    assert.ok(named(plain).some(place => place.id === route.id));
    assert.throws(() => tool(walking, 'places.get', { ids: [route.id] }), error => error.code === 'GATEWAY_PLACE_OUTSIDE_SCOPE');
    assert.deepEqual(tool(plain, 'places.get', { ids: [route.id] }).data.items.map(place => place.id), [route.id]);
    assert.deepEqual(select(walking, walkingPrepared, [route.id, other.id]).response.placeIds, [other.id]);
    assert.deepEqual(select(plain, plainPrepared, [route.id, other.id]).response.placeIds, [route.id, other.id]);
    assert.equal(select(walking, walkingPrepared, [route.id]).status, 'blocked');
  } else {
    // A catalog without route places (the CI fixture): the sentence changes nothing.
    const ids = request => tool(request, 'places.search', {}).data.items.map(place => place.id);
    assert.deepEqual(ids(walking), ids(plain));
  }
  for (const request of [walking, plain]) scopes.release(request.requestId);
  assert.equal(scopes.ready().activeScopes, 0);
  console.log(JSON.stringify({ status: 'passed', scope: 'walk-route-offline-runtime', placeCount: catalog.places.length,
    routePlaces: routes.length, routesWithheld: routes.length > 0, liveScopeRegistered: false,
    modelExecuted: false, productionReady: false }));
} catch {
  console.error('WALK_ROUTE_OFFLINE_CHECK_FAILED: no model was executed; no catalog or secret values displayed.');
  process.exitCode = 1;
}
