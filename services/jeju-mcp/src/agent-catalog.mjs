// Agent catalog v2: the reference app's own course places, with every attribute tagged by how it is known.
// Pure and deterministic. No network, no model inference, and no upgrading of a value's evidence level:
// only field-level dated evidence may ever be `verified`, and this export produces none.
import { createHash } from 'node:crypto';

export const AGENT_CATALOG_SCHEMA_VERSION = '2.0';
export const AGENT_CATALOG_KIND = 'jeju-agent-catalog';
export const COVERAGE_KIND = 'jeju-agent-catalog-coverage';
export const MAX_AGENT_PLACES = 20_000;
export const APP_CATEGORIES = Object.freeze(['herit', 'art', 'leisure', 'heal', 'activity', 'sea', 'food', 'cafe', 'stay', 'hike']);
export const BASIS_LEVELS = Object.freeze(['verified', 'source_reported', 'app_curated', 'inferred', 'computed']);
export const ORIGINS = Object.freeze(['raw_poi', 'app_generated']);
// Deliberately coarse numeric QC envelope including outlying islands; not an administrative boundary.
export const JEJU_QC_BOUNDS = Object.freeze({ minLat: 32.5, maxLat: 34.2, minLon: 125.8, maxLon: 127.3 });
const ATTRIBUTE_KEYS = new Set(['indoor', 'nature', 'hours', 'visitMinutes', 'popularity', 'popularityRank',
  'barrierFree', 'designations', 'drama', 'route', 'note', 'appAutoCandidate']);
const DESIGNATIONS = new Set(['k100', 'unesco', 'bf']);
const SHA256 = /^[a-f0-9]{64}$/u;
const COMMIT = /^[a-f0-9]{40}$/u;
const ISO_UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/u;

/** How the agent may use each basis level. Only `verified` can turn a hard requirement into pass/fail. */
export const BASIS_POLICY = Object.freeze({
  verified: '필드별 확인일·유효기간이 있는 근거. 필수 조건(휠체어·실내·영업시간·비용)을 pass/fail로 판정할 수 있는 유일한 등급.',
  source_reported: '이름이 있는 외부 자료에 적힌 값. 순위와 "방문 전 확인 필요" 안내에 쓰되 필수 조건 pass로 올리지 않는다.',
  app_curated: '원본 앱 데이터·코드에 사람이 넣은 값(목록·문구·체류 시간). source_reported와 같게 다룬다.',
  inferred: '분류·종류·이름 규칙으로 추정한 값. 선호 신호로만 쓰고 사실처럼 말하지 않는다.',
  computed: '다른 필드에서 결정적으로 계산한 값(권역·인기 순위·테마 가중치). 원래 필드의 근거 수준을 넘지 않는다.',
});

/** Source keys referenced by attributes. Names describe where the reference app says the value came from. */
export const SOURCES = Object.freeze({
  'raw-poi-indoor': { name: 'jeju_tour_data.json pois[].in', note: '원 파일에 필드별 출처가 적혀 있지 않다.' },
  'raw-poi-nature': { name: 'jeju_tour_data.json pois[].nat', note: '원 파일에 필드별 출처가 적혀 있지 않다.' },
  'raw-poi-duration': { name: 'jeju_tour_data.json pois[].dur (큐레이션 체류 시간)', note: '계획용 권장값이며 실측 체류 시간이 아니다.' },
  'kakao-map-hours': { name: '카카오맵 영업정보 (원본 앱 수집 hv·h 필드)', note: '수집 시점 값. 휴무·변경은 방문 전 확인이 필요하고 이용 조건 검토가 남아 있다.' },
  'jeju-tourism-bigdata-arrivals': { name: '제주관광빅데이터플랫폼 지역별 인기 장소 (2025 차량 도착)', note: '집계 방문 지표다. 혼잡 예측·만족도·개인 선호가 아니다.' },
  'jeju-barrier-free-travel-info': { name: '제주특별자치도 무장애여행정보 (원본 앱 barrier_free.json)', note: '목록에 있다는 뜻이며 모든 동선의 휠체어 접근을 보장하지 않는다.' },
  'app-designation-lists': { name: '원본 앱 course.ts 이름 목록 (한국관광 100선·유네스코·열린관광지)', note: '공식 목록을 앱이 이름으로 대응했다.' },
  'tour-navigator-drama-theme': { name: '제주 드라마 배경 테마 (원본 앱 jeju_drama.json)', note: '촬영지·테마 동선 여부이며 관광 품질 지표가 아니다.' },
  'official-route-data': { name: '제주올레·한라산 탐방로·서귀포 하영올레·제주환상자전거길 경로 자료 (원본 앱)', note: '거리·난이도·소요시간 요약. 통제·기상은 별도 확인.' },
  'app-curated-notes': { name: '원본 앱 큐레이션 안내 문구', note: '앱 작성 문구. 예약·운영 조건은 별도 확인.' },
});

export class AgentCatalogError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AgentCatalogError';
    this.code = 'INVALID_AGENT_CATALOG';
  }
}

const ensure = (condition, message) => { if (!condition) throw new AgentCatalogError(message); };
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max
  && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const byId = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
const norm = (value) => String(value ?? '').normalize('NFC').replace(/[\s()·.,\-_]/gu, '').toLowerCase();

function haversineKm(aLat, aLon, bLat, bLon) {
  const rad = Math.PI / 180;
  const h = Math.sin(((bLat - aLat) * rad) / 2) ** 2
    + Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(((bLon - aLon) * rad) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** Validate the theme map against the persona theme IDs; returns compiled overrides. */
export function validateThemeMap(map, personaThemeIds) {
  ensure(isRecord(map) && text(map.version, 80) && text(map.personaVersion, 80), 'theme map version is required');
  ensure(Array.isArray(map.themes) && map.themes.length > 0 && new Set(map.themes).size === map.themes.length, 'theme map themes must be unique');
  if (personaThemeIds !== undefined) {
    ensure(JSON.stringify(map.themes) === JSON.stringify(personaThemeIds), 'theme map themes must equal the persona theme IDs in order');
  }
  ensure(Array.isArray(map.appCategories) && map.appCategories.length === APP_CATEGORIES.length
    && APP_CATEGORIES.every((cat) => map.appCategories.includes(cat)), 'theme map must list every app category');
  const themes = new Set(map.themes);
  const checkWeights = (weights, label, allowEmpty) => {
    ensure(isRecord(weights), `${label} must be an object`);
    const entries = Object.entries(weights);
    if (entries.length === 0) return ensure(allowEmpty, `${label} must not be empty`);
    ensure(entries.every(([theme, w]) => themes.has(theme) && finite(w) && w > 0 && w <= 1), `${label} has an unknown theme or weight outside (0,1]`);
    ensure(Math.abs(entries.reduce((sum, [, w]) => sum + w, 0) - 1) < 1e-9, `${label} weights must sum to 1`);
  };
  ensure(isRecord(map.categoryWeights) && Object.keys(map.categoryWeights).length === APP_CATEGORIES.length
    && APP_CATEGORIES.every((cat) => cat in map.categoryWeights), 'categoryWeights must cover every app category exactly');
  for (const cat of APP_CATEGORIES) checkWeights(map.categoryWeights[cat], `categoryWeights.${cat}`, cat === 'stay');
  ensure(Object.keys(map.categoryWeights.stay).length === 0, 'stay is not a theme recommendation category');
  ensure(Array.isArray(map.overrides), 'overrides must be an array');
  const seen = new Set();
  const compiled = map.overrides.map((rule, index) => {
    const label = `overrides[${index}]`;
    ensure(isRecord(rule) && text(rule.id, 80) && !seen.has(rule.id), `${label}.id must be unique`);
    seen.add(rule.id);
    ensure(Array.isArray(rule.categories) && rule.categories.length > 0
      && rule.categories.every((cat) => APP_CATEGORIES.includes(cat) && cat !== 'stay'), `${label}.categories must be non-stay app categories`);
    const kinds = rule.kinds ?? [];
    ensure(Array.isArray(kinds) && kinds.every((kind) => text(kind, 100)), `${label}.kinds must be strings`);
    let pattern = null;
    if (rule.namePattern !== undefined) {
      ensure(text(rule.namePattern, 200), `${label}.namePattern must be a string`);
      try { pattern = new RegExp(rule.namePattern, 'u'); } catch { throw new AgentCatalogError(`${label}.namePattern does not compile`); }
    }
    ensure(kinds.length > 0 || pattern !== null, `${label} needs kinds or namePattern`);
    checkWeights(rule.weights, `${label}.weights`, false);
    return { id: rule.id, categories: new Set(rule.categories), kinds: new Set(kinds), pattern, weights: rule.weights };
  });
  const reachable = new Set([
    ...Object.values(map.categoryWeights).flatMap((w) => Object.keys(w)),
    ...map.overrides.flatMap((rule) => Object.keys(rule.weights)),
  ]);
  ensure(map.themes.every((theme) => reachable.has(theme)), 'every persona theme must be reachable from a category or override');
  return compiled;
}

/** First matching override wins (category is required; kind or name may match); otherwise the category row. */
export function themeWeights(place, map, compiled = validateThemeMap(map)) {
  for (const rule of compiled) {
    if (!rule.categories.has(place.category)) continue;
    if (rule.kinds.has(place.kind ?? '') || (rule.pattern !== null && rule.pattern.test(place.name))) {
      return { weights: { ...rule.weights }, rule: `override:${rule.id}` };
    }
  }
  return { weights: { ...map.categoryWeights[place.category] }, rule: `category:${place.category}` };
}

function unwrapPayload(payload) {
  ensure(isRecord(payload) && (!('success' in payload) || payload.success === true), 'app payload must be a successful /api/course/places response');
  const data = 'success' in payload ? payload.data : payload;
  ensure(isRecord(data) && Array.isArray(data.places) && data.places.length > 0 && data.places.length <= MAX_AGENT_PLACES,
    `app payload must contain 1..${MAX_AGENT_PLACES} places`);
  return data;
}

function convertPlace(p, raw, map, compiled) {
  ensure(isRecord(p) && text(p.id, 160) && p.id === p.id.trim(), 'app place id is invalid');
  ensure(text(p.name, 200), `app place ${p.id} has an invalid name`);
  ensure(APP_CATEGORIES.includes(p.cat), `app place ${p.id} has an unknown category`);
  ensure(finite(p.lat) && finite(p.lon), `app place ${p.id} has non-numeric coordinates`);
  const attributes = {};
  const put = (key, value, basis, reference) => { attributes[key] = { value, basis, ...reference }; };
  const appDefault = /^(olle|hy|trail|bike)-/u.test(p.id);

  if (typeof p.indoor === 'boolean') {
    if (raw?.in === 1) put('indoor', true, 'app_curated', { source: 'raw-poi-indoor' });
    else put('indoor', p.indoor, 'inferred', { rule: 'app course.ts: in 필드가 없으면 분류(herit·art·food·cafe·stay)·자연 여부 또는 장소 유형 기본값' });
  }
  if (typeof p.nature === 'boolean') {
    if (raw?.nat === 1) put('nature', true, 'app_curated', { source: 'raw-poi-nature' });
    else put('nature', p.nature, 'inferred', { rule: 'app course.ts: nat 필드가 없으면 kind=beach·분류 sea 또는 장소 유형 기본값' });
  }
  if (text(p.hours, 500) || finite(p.open) || finite(p.close)) {
    const value = { text: text(p.hours, 500) ? p.hours : null, openMin: finite(p.open) ? p.open : null, closeMin: finite(p.close) ? p.close : null };
    const kakaoText = raw !== undefined && typeof raw.hv === 'string' && raw.hv !== '정보없음' && raw.hv === p.hours;
    const kakaoTimes = raw !== undefined && isRecord(raw.h) && (value.openMin !== null || value.closeMin !== null);
    if (kakaoText || kakaoTimes) put('hours', value, 'source_reported', { source: 'kakao-map-hours' });
    else if (appDefault) put('hours', value, 'inferred', { rule: 'app course.ts: 올레·탐방로·자전거길 계획용 기본 시간창' });
    else put('hours', value, 'app_curated', { source: 'app-curated-notes' });
  }
  if (p.cat !== 'stay' && finite(p.stay) && p.stay > 0) {
    if (raw !== undefined && finite(raw.dur) && raw.dur === p.stay) put('visitMinutes', p.stay, 'app_curated', { source: 'raw-poi-duration' });
    else put('visitMinutes', p.stay, 'inferred', { rule: 'app course.ts stayMin: 분류·종류별 기본 체류 시간 또는 경로 소요시간 기반 계획 여유' });
  }
  if (finite(p.pop) && p.pop > 0) put('popularity', p.pop, 'source_reported', { source: 'jeju-tourism-bigdata-arrivals' });
  if (Number.isInteger(p.popRank) && p.popRank > 0) {
    put('popularityRank', p.popRank, 'computed', { rule: 'app course.ts: 맛집·카페·숙박을 뺀 명소의 방문 지표 상위 100 순위' });
  }
  if (isRecord(p.bf)) {
    put('barrierFree', {
      listed: true,
      summary: text(p.bf.summary, 1000) ? p.bf.summary : null,
      url: text(p.bf.url, 1000) ? p.bf.url : null,
      routeKm: finite(p.bf.routeKm) ? p.bf.routeKm : null,
    }, 'source_reported', { source: 'jeju-barrier-free-travel-info', match: p.id.startsWith('bf-') ? 'dataset_record' : 'app_name_distance_match' });
  }
  const designations = (Array.isArray(p.badges) ? p.badges : []).filter((badge) => DESIGNATIONS.has(badge)).sort();
  if (designations.length) put('designations', designations, 'app_curated', { source: 'app-designation-lists', match: 'app_name_list' });
  const dramaScene = Array.isArray(p.badges) && p.badges.includes('drama');
  if (dramaScene || isRecord(p.drama)) {
    put('drama', { scene: dramaScene }, 'app_curated', {
      source: 'tour-navigator-drama-theme', match: p.id.startsWith('drama-') ? 'dataset_record' : 'app_name_distance_match',
    });
  }
  if (p.cat === 'hike' && (finite(p.km) || text(p.level, 200) || text(p.info, 1000))) {
    put('route', { km: finite(p.km) ? p.km : null, level: text(p.level, 200) ? p.level : null, summary: text(p.info, 1000) ? p.info : null },
      'source_reported', { source: 'official-route-data' });
  } else if (text(p.info, 1000)) {
    const fromBf = p.id.startsWith('bf-');
    put('note', p.info, fromBf ? 'source_reported' : 'app_curated', { source: fromBf ? 'jeju-barrier-free-travel-info' : 'app-curated-notes' });
  }
  if (typeof p.auto === 'boolean') put('appAutoCandidate', p.auto, 'computed', { rule: 'app course.ts auto: 큐레이션·인기·고득점 명소 여부' });

  const place = {
    id: p.id, name: p.name, category: p.cat, kind: text(p.kind, 100) ? p.kind : null,
    lat: p.lat, lon: p.lon, zone: text(p.zone, 40) ? p.zone : null,
    origin: raw === undefined ? 'app_generated' : 'raw_poi',
    web: text(p.web, 1000) ? p.web : null,
  };
  const themes = themeWeights(place, map, compiled);
  return { ...place, themes: themes.weights, themeRule: themes.rule, attributes };
}

/** Report possible duplicates for review. Nothing is removed: the app's IDs stay stable. */
export function findNearDuplicates(places) {
  const pairs = [];
  for (let i = 0; i < places.length; i++) {
    for (let j = i + 1; j < places.length; j++) {
      const a = places[i];
      const b = places[j];
      if (a.category === 'stay' || b.category === 'stay') continue;
      const km = haversineKm(a.lat, a.lon, b.lat, b.lon);
      if (km > 3) continue;
      const an = norm(a.name);
      const bn = norm(b.name);
      let reason = null;
      if (an === bn) reason = 'same_name_within_3km';
      else if (km <= 0.3 && an.length >= 3 && bn.length >= 3 && (an.includes(bn) || bn.includes(an))) reason = 'contained_name_within_300m';
      if (reason) pairs.push({ ids: [a.id, b.id], distanceKm: Math.round(km * 1000) / 1000, reason });
    }
  }
  return pairs;
}

/**
 * Build the catalog from the app's /api/course/places payload and the raw POI file of the same app commit.
 * `provenance` = { repository, commit, rawPath, rawSha256, personaVersion }.
 */
export function buildAgentCatalog({ appPayload, rawData, themeMap, personaThemeIds, provenance }) {
  const data = unwrapPayload(appPayload);
  ensure(isRecord(rawData) && Array.isArray(rawData.pois), 'raw data must be the jeju_tour_data.json object with pois');
  ensure(isRecord(provenance) && text(provenance.repository, 200) && COMMIT.test(provenance.commit ?? '')
    && text(provenance.rawPath, 500) && SHA256.test(provenance.rawSha256 ?? ''), 'provenance needs repository, commit, rawPath and rawSha256');
  const compiled = validateThemeMap(themeMap, personaThemeIds);
  const rawById = new Map();
  const rawDuplicates = new Set();
  for (const row of rawData.pois) {
    if (!isRecord(row) || typeof row.id !== 'string') continue;
    if (rawById.has(row.id)) rawDuplicates.add(row.id);
    rawById.set(row.id, row);
  }
  ensure(rawDuplicates.size === 0, 'raw POI IDs must be unique to attribute fields safely');
  const ids = new Set();
  const nameMismatches = [];
  const places = data.places.map((p) => {
    ensure(isRecord(p) && !ids.has(p.id), 'app place IDs must be unique');
    ids.add(p.id);
    const raw = rawById.get(p.id);
    if (raw !== undefined && raw.n !== p.name) nameMismatches.push(p.id);
    return convertPlace(p, raw, themeMap, compiled);
  }).sort(byId);
  const nearDuplicates = findNearDuplicates(places);
  const catalog = {
    schemaVersion: AGENT_CATALOG_SCHEMA_VERSION,
    kind: AGENT_CATALOG_KIND,
    dataMode: 'app-export',
    source: {
      repository: provenance.repository,
      commit: provenance.commit,
      endpoint: '/api/course/places',
      placesContentSha256: sha256(JSON.stringify(data.places)),
      appGeneratedAt: typeof data.generated === 'string' ? data.generated : null,
      rawPath: provenance.rawPath,
      rawSha256: provenance.rawSha256,
      themeMapVersion: themeMap.version,
      personaVersion: themeMap.personaVersion,
    },
    themeIds: [...themeMap.themes],
    basisPolicy: { ...BASIS_POLICY },
    sources: structuredClone(SOURCES),
    places,
    diagnostics: {
      inputPlaces: data.places.length,
      exportedPlaces: places.length,
      rawPoiRecords: rawById.size,
      origins: Object.fromEntries(ORIGINS.map((origin) => [origin, places.filter((p) => p.origin === origin).length])),
      nameMismatches: nameMismatches.sort(),
      nearDuplicates,
      warnings: [
        'No attribute is verified. Hard requirements stay unknown until dated field evidence exists.',
        'Visit cost is absent for every place; budget checks cannot pass from this catalog.',
        'Near-duplicates are reported for review only; the app IDs are unchanged.',
        'Repository presence is not redistribution, training or commercial-use permission.',
      ],
    },
  };
  return validateAgentCatalog(catalog);
}

function validateAttribute(attribute, key, label, sources) {
  ensure(ATTRIBUTE_KEYS.has(key), `${label} has an unsupported attribute ${key}`);
  ensure(isRecord(attribute) && 'value' in attribute && BASIS_LEVELS.includes(attribute.basis), `${label}.${key} needs a value and a known basis`);
  const allowed = new Set(['value', 'basis', 'source', 'rule', 'match', 'verifiedAt', 'validUntil']);
  ensure(Object.keys(attribute).every((field) => allowed.has(field)), `${label}.${key} has unsupported fields`);
  if (attribute.source !== undefined) ensure(Object.hasOwn(sources, attribute.source), `${label}.${key} references an unknown source`);
  if (attribute.basis === 'verified') {
    ensure(attribute.source !== undefined && ISO_UTC.test(attribute.verifiedAt ?? '') && ISO_UTC.test(attribute.validUntil ?? '')
      && Date.parse(attribute.verifiedAt) <= Date.parse(attribute.validUntil), `${label}.${key} is verified without a source and a dated validity interval`);
  } else {
    ensure(attribute.verifiedAt === undefined && attribute.validUntil === undefined, `${label}.${key} has verification dates without the verified basis`);
  }
  if (attribute.basis === 'source_reported' || attribute.basis === 'app_curated') ensure(attribute.source !== undefined, `${label}.${key} needs a source`);
  if (attribute.basis === 'inferred' || attribute.basis === 'computed') ensure(text(attribute.rule, 500), `${label}.${key} needs a rule`);
}

/** Strict structural validation; returns the same object deeply frozen. */
export function validateAgentCatalog(catalog) {
  ensure(isRecord(catalog) && catalog.schemaVersion === AGENT_CATALOG_SCHEMA_VERSION && catalog.kind === AGENT_CATALOG_KIND, 'unsupported agent catalog schema');
  ensure(catalog.dataMode === 'app-export', 'dataMode must be app-export');
  const source = catalog.source;
  ensure(isRecord(source) && text(source.repository, 200) && COMMIT.test(source.commit ?? '') && SHA256.test(source.placesContentSha256 ?? '')
    && SHA256.test(source.rawSha256 ?? '') && text(source.themeMapVersion, 80), 'catalog source provenance is incomplete');
  ensure(Array.isArray(catalog.themeIds) && catalog.themeIds.length > 0 && new Set(catalog.themeIds).size === catalog.themeIds.length, 'themeIds must be unique');
  ensure(isRecord(catalog.sources) && Object.values(catalog.sources).every((s) => isRecord(s) && text(s.name, 300)), 'sources must be named');
  ensure(isRecord(catalog.basisPolicy) && BASIS_LEVELS.every((level) => text(catalog.basisPolicy[level], 500)), 'basisPolicy must describe every basis');
  ensure(Array.isArray(catalog.places) && catalog.places.length > 0 && catalog.places.length <= MAX_AGENT_PLACES, `places must contain 1..${MAX_AGENT_PLACES} records`);
  const themeIds = new Set(catalog.themeIds);
  const ids = new Set();
  const placeKeys = new Set(['id', 'name', 'category', 'kind', 'lat', 'lon', 'zone', 'origin', 'web', 'themes', 'themeRule', 'attributes']);
  const b = JEJU_QC_BOUNDS;
  catalog.places.forEach((place, index) => {
    const label = `places[${index}]`;
    ensure(isRecord(place) && Object.keys(place).every((key) => placeKeys.has(key)), `${label} has unsupported fields`);
    ensure(text(place.id, 160) && !ids.has(place.id), `${label}.id must be unique`);
    ids.add(place.id);
    ensure(text(place.name, 200) && APP_CATEGORIES.includes(place.category) && ORIGINS.includes(place.origin), `${label} has an invalid name, category or origin`);
    ensure(place.kind === null || text(place.kind, 100), `${label}.kind is invalid`);
    ensure(finite(place.lat) && finite(place.lon) && place.lat >= b.minLat && place.lat <= b.maxLat
      && place.lon >= b.minLon && place.lon <= b.maxLon, `${label} is outside the Jeju QC envelope`);
    ensure(place.zone === null || text(place.zone, 40), `${label}.zone is invalid`);
    ensure(place.web === null || text(place.web, 1000), `${label}.web is invalid`);
    ensure(isRecord(place.themes) && text(place.themeRule, 120), `${label} needs themes and a theme rule`);
    const weights = Object.entries(place.themes);
    if (place.category === 'stay') ensure(weights.length === 0, `${label}: stay places carry no theme weights`);
    else {
      ensure(weights.length > 0 && weights.every(([theme, w]) => themeIds.has(theme) && finite(w) && w > 0 && w <= 1), `${label} has invalid theme weights`);
      ensure(Math.abs(weights.reduce((sum, [, w]) => sum + w, 0) - 1) < 1e-9, `${label} theme weights must sum to 1`);
    }
    ensure(isRecord(place.attributes), `${label}.attributes must be an object`);
    for (const [key, attribute] of Object.entries(place.attributes)) validateAttribute(attribute, key, label, catalog.sources);
  });
  ensure(isRecord(catalog.diagnostics), 'diagnostics are required');
  return deepFreeze(catalog);
}

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

const countBy = (items, key) => items.reduce((acc, item) => {
  const k = key(item);
  if (k !== undefined) acc[k] = (acc[k] ?? 0) + 1;
  return acc;
}, {});

/** Aggregate-only coverage: counts by category, origin, attribute basis and theme. No names or IDs. */
export function coverageReport(catalog) {
  const places = catalog.places;
  const attractions = places.filter((p) => !['stay', 'food', 'cafe'].includes(p.category));
  const attributes = {};
  for (const key of [...ATTRIBUTE_KEYS].sort()) {
    const present = places.filter((p) => p.attributes[key] !== undefined);
    attributes[key] = { present: present.length, byBasis: countBy(present, (p) => p.attributes[key].basis) };
    if (['indoor', 'nature', 'appAutoCandidate'].includes(key)) {
      attributes[key].trueByBasis = countBy(present.filter((p) => p.attributes[key].value === true), (p) => p.attributes[key].basis);
    }
  }
  const hard = (predicate) => {
    const known = places.filter(predicate);
    return { verified: 0, sourceReportedOrCurated: known.length, noInformation: places.length - known.length };
  };
  const themes = {};
  for (const theme of catalog.themeIds) {
    themes[theme] = {
      primary: places.filter((p) => (p.themes[theme] ?? 0) >= 0.5).length,
      any: places.filter((p) => (p.themes[theme] ?? 0) > 0).length,
    };
  }
  return {
    schemaVersion: '1.0',
    kind: COVERAGE_KIND,
    source: {
      repository: catalog.source.repository, commit: catalog.source.commit,
      placesContentSha256: catalog.source.placesContentSha256, rawSha256: catalog.source.rawSha256,
      themeMapVersion: catalog.source.themeMapVersion, personaVersion: catalog.source.personaVersion,
    },
    totals: {
      places: places.length,
      attractionsExcludingFoodCafeStay: attractions.length,
      byCategory: Object.fromEntries(APP_CATEGORIES.map((cat) => [cat, places.filter((p) => p.category === cat).length])),
      byOrigin: Object.fromEntries(ORIGINS.map((origin) => [origin, places.filter((p) => p.origin === origin).length])),
      byThemeRule: countBy(places, (p) => p.themeRule.startsWith('override:') ? p.themeRule : 'category'),
    },
    attributes,
    hardRequirementReadiness: {
      note: 'verified만 필수 조건 pass/fail에 쓸 수 있다. 나머지는 확인이 필요한 후보 신호다.',
      wheelchair: hard((p) => p.attributes.barrierFree !== undefined || p.attributes.designations?.value.includes('bf')),
      indoorSourceOrCurated: hard((p) => p.attributes.indoor !== undefined && p.attributes.indoor.basis !== 'inferred'),
      openingHours: hard((p) => p.attributes.hours !== undefined && p.attributes.hours.basis !== 'inferred'),
      visitCost: { verified: 0, sourceReportedOrCurated: 0, noInformation: places.length },
    },
    themes,
    review: {
      nearDuplicatePairs: catalog.diagnostics.nearDuplicates.length,
      nearDuplicateReasons: countBy(catalog.diagnostics.nearDuplicates, (pair) => pair.reason),
      rawNameMismatches: catalog.diagnostics.nameMismatches.length,
    },
  };
}
