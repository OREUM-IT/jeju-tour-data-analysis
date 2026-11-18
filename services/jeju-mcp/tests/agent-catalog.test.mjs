import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import http from 'node:http';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import {
  BASIS_LEVELS, buildAgentCatalog, coverageReport, findNearDuplicates, themeWeights, validateAgentCatalog, validateThemeMap,
} from '../src/agent-catalog.mjs';
import { loadThemeInputs, main as exportMain, parseArguments, verifyRawAtCommit } from '../scripts/export-agent-catalog.mjs';
import { fetchAppPlaces, parseArguments as parseDumpArguments } from '../scripts/dump-app-places.mjs';

const execFileAsync = promisify(execFile);
const { themeMap, personaThemeIds } = await loadThemeInputs();
const COMMIT = 'c'.repeat(40);
const provenance = { repository: 'OREUM-IT/jeju-tour', commit: COMMIT, rawPath: 'prototype/data/jeju_tour_data.json', rawSha256: 'd'.repeat(64) };

// Synthetic records only: names and coordinates are test values, not verified Jeju facts.
const rawData = { pois: [
  { id: 'kk-1', n: '시험 바다카페', c: 'cafe', la: 33.45, lo: 126.55, s: 1, in: 1, hv: '매일 09:00~18:00', h: { p: [] }, pop: 500, dur: 45 },
  { id: 'osm-2', n: '시험 숲길', c: 'spot', la: 33.40, lo: 126.60, s: 3, nat: 1 },
  { id: 'kk-3', n: '시험섬오름', c: 'stay', la: 33.30, lo: 126.30, s: 1, kid: '1' },
  { id: 'kk-4', n: '시험재래시장', c: 'market', la: 33.51, lo: 126.52, s: 1, pop: 900 },
  { id: 'kk-5', n: '시험오름', c: 'spot', la: 33.36, lo: 126.31, s: 2, k: '오름' },
] };
const place = (id, cat, extra = {}) => ({
  id, name: rawData.pois.find((p) => p.id === id)?.n ?? `시험 장소 ${id}`, cat, kind: null, lat: 33.45, lon: 126.55, zone: 'jeju',
  stay: 60, hours: null, open: null, close: null, indoor: false, nature: false, pop: null, popRank: null,
  phone: null, web: null, kid: null, nid: null, badges: [], auto: false, ...extra,
});
const appPayload = { success: true, data: { generated: '2026-11-18T00:00:00.000Z', count: 7, places: [
  place('kk-1', 'cafe', { indoor: true, hours: '매일 09:00~18:00', open: 540, close: 1080, stay: 45, pop: 500 }),
  place('osm-2', 'heal', { lat: 33.40, lon: 126.60, nature: true, stay: 90, badges: ['k100'], auto: true }),
  place('olle-1', 'hike', { kind: '올레', lat: 33.46, lon: 126.93, open: 420, close: 1080, nature: true, stay: 270, km: 15.1, level: '★★☆', info: '15.1km · 4-5시간' }),
  place('bf-9', 'herit', { kind: '무장애 여행지', lat: 33.25, lon: 126.56, indoor: true, badges: ['bfj'], info: '경사로 안내',
    bf: { id: 'bf-9', summary: '경사로 안내', url: 'https://example.test/bf-9', kind: '관광지', points: 3, routeKm: 0.5 } }),
  place('kk-3', 'stay', { kind: '호텔', lat: 33.30, lon: 126.30, indoor: true, stay: 0 }),
  place('kk-4', 'food', { kind: '재래시장', lat: 33.51, lon: 126.52, indoor: true, pop: 900 }),
  place('kk-5', 'heal', { kind: '오름', lat: 33.36, lon: 126.31, stay: 90 }),
] } };
const build = (overrides = {}) => buildAgentCatalog({ appPayload, rawData, themeMap, personaThemeIds, provenance, ...overrides });
const get = (catalog, id) => catalog.places.find((p) => p.id === id);

test('tags every attribute with the basis its source supports and never produces verified values', () => {
  const catalog = build();
  assert.deepEqual(catalog.places.map((p) => p.id), ['bf-9', 'kk-1', 'kk-3', 'kk-4', 'kk-5', 'olle-1', 'osm-2']);
  assert.ok(Object.isFrozen(catalog) && Object.isFrozen(catalog.places[0].attributes));
  const cafe = get(catalog, 'kk-1');
  assert.equal(cafe.origin, 'raw_poi');
  assert.deepEqual(cafe.attributes.indoor, { value: true, basis: 'app_curated', source: 'raw-poi-indoor' });
  assert.deepEqual(cafe.attributes.hours, { value: { text: '매일 09:00~18:00', openMin: 540, closeMin: 1080 }, basis: 'source_reported', source: 'kakao-map-hours' });
  assert.deepEqual(cafe.attributes.visitMinutes, { value: 45, basis: 'app_curated', source: 'raw-poi-duration' });
  assert.equal(cafe.attributes.popularity.basis, 'source_reported');
  const forest = get(catalog, 'osm-2');
  assert.equal(forest.attributes.indoor.basis, 'inferred');
  assert.match(forest.attributes.indoor.rule, /course\.ts/u);
  assert.deepEqual(forest.attributes.nature, { value: true, basis: 'app_curated', source: 'raw-poi-nature' });
  assert.deepEqual(forest.attributes.designations.value, ['k100']);
  assert.equal(forest.attributes.visitMinutes.basis, 'inferred');
  const olle = get(catalog, 'olle-1');
  assert.equal(olle.origin, 'app_generated');
  assert.equal(olle.attributes.hours.basis, 'inferred');
  assert.deepEqual(olle.attributes.route, { value: { km: 15.1, level: '★★☆', summary: '15.1km · 4-5시간' }, basis: 'source_reported', source: 'official-route-data' });
  assert.equal(olle.attributes.note, undefined);
  const bf = get(catalog, 'bf-9');
  assert.deepEqual(bf.attributes.barrierFree, { value: { listed: true, summary: '경사로 안내', url: 'https://example.test/bf-9', routeKm: 0.5 },
    basis: 'source_reported', source: 'jeju-barrier-free-travel-info', match: 'dataset_record' });
  assert.equal(bf.attributes.note.basis, 'source_reported');
  const all = catalog.places.flatMap((p) => Object.values(p.attributes));
  assert.ok(all.length > 0 && all.every((a) => BASIS_LEVELS.includes(a.basis) && a.basis !== 'verified'));
  assert.equal(catalog.places.some((p) => 'cost' in p.attributes), false);
  assert.deepEqual(catalog.diagnostics.nameMismatches, []);
  assert.deepEqual(catalog.diagnostics.origins, { raw_poi: 5, app_generated: 2 });
  assert.equal(JSON.stringify(build()), JSON.stringify(catalog));
});

test('maps persona themes by category with ordered overrides and keeps stays out of theme recommendations', () => {
  const catalog = build();
  assert.deepEqual(get(catalog, 'kk-3').themes, {});
  assert.equal(get(catalog, 'kk-3').themeRule, 'category:stay');
  assert.deepEqual(get(catalog, 'kk-4').themes, { local_food: 0.6, neighborhood: 0.4 });
  assert.equal(get(catalog, 'kk-4').themeRule, 'override:market');
  assert.equal(get(catalog, 'kk-5').themeRule, 'override:oreum');
  assert.equal(get(catalog, 'olle-1').themeRule, 'override:olle');
  assert.deepEqual(get(catalog, 'kk-1').themes, { cafe: 1 });
  assert.deepEqual(themeWeights({ name: '시험 해변', category: 'sea', kind: null }, themeMap), { weights: { coast: 0.8, nature: 0.2 }, rule: 'category:sea' });
});

test('validates the committed theme map and rejects misaligned or unreachable designs', () => {
  assert.ok(validateThemeMap(themeMap, personaThemeIds).length >= 1);
  const broken = (mutate) => { const copy = structuredClone(themeMap); mutate(copy); return () => validateThemeMap(copy, personaThemeIds); };
  assert.throws(broken((m) => m.themes.reverse()), /persona theme IDs in order/u);
  assert.throws(broken((m) => { m.categoryWeights.sea = { coast: 0.5 }; }), /sum to 1/u);
  assert.throws(broken((m) => { m.categoryWeights.stay = { cafe: 1 }; }), /stay/u);
  assert.throws(broken((m) => { m.overrides[0].categories = ['stay']; }), /non-stay/u);
  assert.throws(broken((m) => { m.overrides[0].namePattern = '('; }), /does not compile/u);
  assert.throws(broken((m) => { m.overrides[0].weights = { invented: 1 }; }), /unknown theme/u);
  assert.throws(broken((m) => {
    m.categoryWeights.cafe = { local_food: 1 };
  }), /reachable/u);
  assert.throws(broken((m) => { delete m.categoryWeights.hike; }), /every app category/u);
});

test('validator rejects overstated, unknown or structurally invalid evidence', () => {
  const catalog = build();
  const mutated = (mutate) => { const copy = structuredClone(catalog); mutate(copy); return () => validateAgentCatalog(copy); };
  assert.throws(mutated((c) => { c.places[1].attributes.indoor.basis = 'verified'; }), /verified without a source and a dated validity interval/u);
  assert.throws(mutated((c) => { c.places[1].attributes.indoor.basis = 'certain'; }), /known basis/u);
  assert.throws(mutated((c) => { c.places[1].attributes.indoor.source = 'invented-source'; }), /unknown source/u);
  assert.throws(mutated((c) => { delete c.places[1].attributes.hours.source; }), /needs a source/u);
  assert.throws(mutated((c) => { c.places[1].attributes.cost = { value: 0, basis: 'inferred', rule: 'x' }; }), /unsupported attribute/u);
  assert.throws(mutated((c) => { c.places[1].themes = { cafe: 0.5 }; }), /sum to 1/u);
  assert.throws(mutated((c) => { c.places[2].themes = { cafe: 1 }; }), /stay places carry no theme weights/u);
  assert.throws(mutated((c) => { c.places[1].id = c.places[0].id; }), /unique/u);
  assert.throws(mutated((c) => { c.places[1].category = 'beach'; }), /category/u);
  assert.throws(mutated((c) => { c.places[1].lat = 37.56; }), /QC envelope/u);
  assert.throws(mutated((c) => { c.places[1].attributes.indoor.verifiedAt = '2026-11-18T00:00:00Z'; }), /verification dates without the verified basis/u);
  const verified = structuredClone(catalog);
  Object.assign(verified.places[1].attributes.indoor, { basis: 'verified', verifiedAt: '2026-11-18T00:00:00Z', validUntil: '2027-11-18T00:00:00Z' });
  assert.equal(validateAgentCatalog(verified).places[1].attributes.indoor.basis, 'verified');
});

test('build rejects inconsistent inputs instead of guessing', () => {
  assert.throws(() => build({ appPayload: { success: false, data: appPayload.data } }), /successful/u);
  assert.throws(() => build({ appPayload: null }), /successful/u);
  assert.throws(() => build({ rawData: { pois: [...rawData.pois, rawData.pois[0]] } }), /raw POI IDs must be unique/u);
  const dupe = structuredClone(appPayload);
  dupe.data.places.push(structuredClone(dupe.data.places[0]));
  assert.throws(() => build({ appPayload: dupe }), /unique/u);
  assert.throws(() => build({ provenance: { ...provenance, commit: 'short' } }), /provenance/u);
  const renamed = structuredClone(appPayload);
  renamed.data.places[0].name = '다른 이름';
  assert.deepEqual(build({ appPayload: renamed }).diagnostics.nameMismatches, ['kk-1']);
});

test('coverage report is aggregate-only and internally consistent', () => {
  const catalog = build();
  const report = coverageReport(catalog);
  const serialized = JSON.stringify(report);
  for (const p of catalog.places) {
    assert.equal(serialized.includes(p.id), false, `report leaks id ${p.id}`);
    assert.equal(serialized.includes(p.name), false, `report leaks name ${p.name}`);
  }
  assert.equal(Object.values(report.totals.byCategory).reduce((a, b) => a + b, 0), report.totals.places);
  assert.equal(report.hardRequirementReadiness.visitCost.noInformation, report.totals.places);
  assert.equal(report.hardRequirementReadiness.wheelchair.verified, 0);
  assert.equal(report.hardRequirementReadiness.wheelchair.sourceReportedOrCurated, 1);
  assert.equal(report.attributes.indoor.byBasis.app_curated, 1);
  assert.equal(report.themes.local_food.primary, 1);
});

test('near-duplicate review flags same and contained names without removing IDs', () => {
  const base = { category: 'heal', lat: 33.4, lon: 126.5 };
  const pairs = findNearDuplicates([
    { ...base, id: 'a', name: '시험 폭포' },
    { ...base, id: 'b', name: '시험폭포', lat: 33.41 },
    { ...base, id: 'c', name: '시험 폭포 전망대', lon: 126.5005 },
    { ...base, id: 'd', name: '시험 폭포', lat: 33.6 },
    { ...base, id: 'e', name: '시험 폭포', category: 'stay' },
  ]);
  assert.deepEqual(pairs.map((p) => [p.ids.join(), p.reason]), [
    ['a,b', 'same_name_within_3km'], ['a,c', 'contained_name_within_300m'],
  ]);
});

test('exporter CLI requires explicit inputs, a full commit and verifies raw bytes against that commit', async () => {
  assert.throws(() => parseArguments(['--places', 'p.json']), /--raw is required/u);
  assert.throws(() => parseArguments(['--places', 'p', '--raw', 'r', '--commit', 'abc', '--output', 'o']), /full 40-character/u);
  assert.throws(() => parseArguments(['--places', 'p', '--raw', 'r', '--commit', COMMIT, '--output', 'o', '--report', 'r.txt']), /\.json/u);
  assert.throws(() => parseArguments(['--unknown', 'x']), /Usage/u);
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'agent-catalog-'));
  try {
    const repo = path.join(tmp, 'jeju-tour');
    const rawFile = path.join(repo, 'prototype', 'data', 'jeju_tour_data.json');
    await mkdir(path.dirname(rawFile), { recursive: true });
    await writeFile(rawFile, JSON.stringify(rawData));
    const git = (...args) => execFileAsync('git', ['-C', repo, ...args]);
    await git('init', '-q');
    await git('config', 'user.email', 'test@example.invalid');
    await git('config', 'user.name', 'test');
    await git('remote', 'add', 'origin', 'https://github.com/OREUM-IT/jeju-tour.git');
    await git('add', '.');
    await git('commit', '-q', '-m', 'fixture');
    const commit = (await git('rev-parse', 'HEAD')).stdout.trim();
    const verified = await verifyRawAtCommit(rawFile, commit);
    assert.equal(verified.rawSha256, createHash('sha256').update(JSON.stringify(rawData)).digest('hex'));

    const placesFile = path.join(tmp, 'places.json');
    await writeFile(placesFile, JSON.stringify(appPayload));
    const output = path.join(tmp, '.local', 'agent.json');
    const report = path.join(tmp, 'coverage.json');
    const args = ['--places', placesFile, '--raw', rawFile, '--commit', commit, '--output', output, '--report', report];
    await exportMain(args);
    const written = JSON.parse(await readFile(output, 'utf8'));
    assert.equal(validateAgentCatalog(written).source.commit, commit);
    assert.equal(JSON.parse(await readFile(report, 'utf8')).totals.places, 7);
    await assert.rejects(exportMain(args), /already exists/u);
    await assert.rejects(exportMain([...args.slice(0, 7), path.join(tmp, 'agent.json')]), /\.local/u);

    await writeFile(rawFile, JSON.stringify({ pois: [] }));
    await assert.rejects(verifyRawAtCommit(rawFile, commit), /differs/u);
    await assert.rejects(verifyRawAtCommit(placesFile, commit), /inside a local OREUM-IT\/jeju-tour checkout/u);
    await assert.rejects(verifyRawAtCommit(rawFile, 'e'.repeat(40)), /already has --commit/u);
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});

test('app place dump reads the list over loopback and always closes the server', async () => {
  assert.throws(() => parseDumpArguments(['--app-dir', 'x']), /required/u);
  let server;
  const fakeApp = (body) => ({
    listen(port, host, callback) {
      server = http.createServer((req, res) => {
        res.writeHead(req.url === '/api/course/places' ? 200 : 404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(body));
      });
      return server.listen(port, host, callback);
    },
  });
  const body = await fetchAppPlaces(fakeApp(appPayload));
  assert.equal(body.data.places.length, 7);
  assert.equal(server.listening, false);
  await assert.rejects(fetchAppPlaces(fakeApp({ success: false })), /did not return a successful place list/u);
  assert.equal(server.listening, false);
  await assert.rejects(fetchAppPlaces({}), /Express app/u);
});
