import { readFile } from 'node:fs/promises';
import { BASIS_POLICY, SOURCES, themeWeights } from '../../src/agent-catalog.mjs';
import { buildEvaluationSet } from '../../src/evaluation-set.mjs';
import { SPEC_PATH } from '../../scripts/build-evaluation-set.mjs';
import { PERSONA_PATH, THEME_MAP_PATH } from '../../scripts/export-agent-catalog.mjs';
const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
const spec = await readJson(SPEC_PATH);
const personas = await readJson(PERSONA_PATH);
const themeMap = await readJson(THEME_MAP_PATH);
const themeIds = personas.themes.map((theme) => theme.id);
const ZONES = spec.zones.map((zone) => zone.id);
const CATEGORIES = spec.categories.map((category) => category.id);

// Synthetic values only: names, coordinates and attributes are test data, not Jeju facts.
const inferred = (value) => ({ value, basis: 'inferred', rule: 'test: category default' });
const reportedHours = (openMin, closeMin, text = '매일 09:00~18:00') => ({ value: { text, openMin, closeMin }, basis: 'source_reported', source: 'kakao-map-hours' });
const listed = { value: { listed: true, summary: null, url: null, routeKm: null }, basis: 'source_reported', source: 'jeju-barrier-free-travel-info', match: 'dataset_record' };
const curatedIndoor = { value: true, basis: 'app_curated', source: 'raw-poi-indoor' };
const route = (km) => ({ value: { km, level: null, summary: null }, basis: 'source_reported', source: 'official-route-data' });
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

/** Independent in-memory synthetic catalog: no imported place data and no network. */
export function baselineInputs() {
  const inputs = { catalog: fixtureCatalog(), personas: structuredClone(personas), themeMap: structuredClone(themeMap), spec: structuredClone(spec) };
  return { ...inputs, set: buildEvaluationSet(inputs) };
}
