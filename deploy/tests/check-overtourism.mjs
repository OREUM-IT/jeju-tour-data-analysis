// Run on stdin from /app in the MCP container. Local scopes only: no HTTP calls,
// live scope registration, Langflow execution or model inference. Fictional data.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

globalThis.fetch = () => { throw new Error('NETWORK_NOT_ALLOWED_IN_OFFLINE_CHECK'); };
try {
  const { createOvertourismScopes, JEJU_REGIONS, sha256 } = await import(pathToFileURL(resolve('src', 'overtourism.mjs')).href);
  const now = Date.now();
  const today = new Date(now + 9 * 3_600_000).toISOString().slice(0, 10);
  const at = new Date(now).toISOString();
  const input = { schemaVersion: '1.0', today, dataVersion: 'offline-check', crowd: { ok: true, stale: false, at, total: 3, spots: [
    ['오프라인 점검 관광지 A', '제주시', 'udo', 'name', today, [70, 65, 50]],
    ['오프라인 점검 관광지 B', '제주시', 'udo', 'hub', today, [61, 20, 30]],
    ['오프라인 점검 관광지 C', '제주시', 'udo', 'near', today, [10, 80]],
  ] }, regions: JEJU_REGIONS.map(region => ({ id: region.id, places: 1, lodging: { businesses: 1, rooms: 1 },
    regulation: { places: 0, lifted: 0, cards: [] } })), sources: [] };
  const scopes = createOvertourismScopes({ now });
  const diagnosisId = 'ot-' + '0'.repeat(32);
  const prepared = scopes.register({ diagnosisId, input, deadlineMs: now + 60_000 });
  assert.equal(prepared.status, 'prepared');
  const overview = scopes.call({ diagnosisId, name: 'overtourism_overview', arguments: {} });
  assert.equal(overview.data.regions.length, 14);
  assert.equal(overview.data.regions.find(region => region.id === 'udo').ruleLevel, 'high');
  assert.equal(scopes.validate({ diagnosisId, response: { regions: [] } }).status, 'blocked');
  const accepted = scopes.validate({ diagnosisId, response: { overview: '점검 개요 3곳', regions: [{ id: 'udo', level: 'normal',
    summary: '점검 요약', evidence: ['crowd.highShare'], policy: '점검 정책', suggestions: [], missing: [] }] } });
  assert.equal(accepted.status, 'accepted');
  const result = scopes.result({ diagnosisId });
  assert.equal(result.indicatorsSha256, sha256(result.indicators));
  assert.equal(result.diagnosisSha256, sha256(result.diagnosis));
  const udo = result.diagnosis.regions.find(region => region.id === 'udo');
  // normal is two steps from high: the server restores the rule level; digits fall back to rule text.
  assert.equal(udo.level, 'high');
  assert.equal(udo.adjusted, true);
  assert.equal(result.diagnosis.overview.includes('3'), false);
  scopes.release(diagnosisId);
  assert.equal(scopes.ready().activeScopes, 0);
  console.log(JSON.stringify({ status: 'passed', scope: 'overtourism-offline-runtime', regions: 14,
    liveScopeRegistered: false, modelExecuted: false, productionReady: false }));
} catch {
  console.error('OVERTOURISM_OFFLINE_CHECK_FAILED: no model was executed; no data or secret values displayed.');
  process.exitCode = 1;
}
