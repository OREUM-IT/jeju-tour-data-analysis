// A closed grammar for the published synthetic template, not a general Korean intent parser.
// Only the actual query/context and literal catalog names are inputs. No evaluation labels are read.
import { validateAgentCatalog } from './agent-catalog.mjs';
import { formatBaselineMessage } from './baseline-request-adapters.mjs';
import { DAY_MS, parseYmd } from './constraint-evidence.mjs';

export const VALIDATED_REQUEST_ADAPTER_VERSION = 'validated-template-v1';
export const VALIDATED_REQUEST_GRAMMAR_VERSION = 'jeju-template-grammar-v1';
export const VALIDATED_REQUEST_LIMITATIONS = Object.freeze([
  'Only the exact bounded synthetic template grammar is accepted; authored or general natural-language requests need a separate intent-confirmation interface.',
  'Trip conflicts, transport, companions, walking and crowd preferences remain verbatim in query; the current runtime gate does not enforce them.',
  'Soft theme wording remains a preference in query and is not silently promoted into a candidate filter.',
  'The context wrapper and structured fields differ from the legacy plain-text input; resulting scores describe the full configured system, not isolated model quality.',
]);

const THEMES = Object.freeze({ '해안': 'coast', '숲·자연': 'nature', '로컬 음식': 'local_food', '카페': 'cafe',
  '역사·문화': 'culture', '예술': 'art', '야외 활동': 'outdoor', '체험·배움': 'learning', '동네 산책': 'neighborhood' });
const CATEGORIES = Object.freeze({ '역사·문화 유적': 'herit', '미술·전시': 'art', '레저·스포츠': 'leisure',
  '숲·정원·명승': 'heal', '테마파크·체험': 'activity', '바다·해변': 'sea', '식당·시장': 'food',
  '카페·디저트': 'cafe', '오름·올레·탐방로': 'hike' });
const ZONES = Object.freeze({ '제주시 원도심': 'JC', '조천·구좌': 'NE', '성산·우도': 'E', '표선·남원': 'SE',
  '서귀포 시내': 'S', '중문': 'SW', '안덕·대정': 'W2', '한림·애월': 'W' });
const CONTEXT_SENTENCES = new Map([
  ['렌터카로 이동합니다.', 'transport'], ['버스 같은 대중교통으로만 이동합니다.', 'transport'],
  ['아이와 함께 갑니다.', 'companion'], ['부모님을 모시고 갑니다.', 'companion'],
  ['거의 걷지 않으려고 합니다.', 'walk'], ['사람이 붐비는 곳은 피하고 싶습니다.', 'crowd_avoid'],
]);
const DATE_FRAGMENT = '(\\d{1,2}월 \\d{1,2}일\\([일월화수목금토]\\))';
const indoorPattern = new RegExp(`^${DATE_FRAGMENT}에 비 예보가 있어서 실내여야 합니다\\.$`, 'u');
const outsideTripPattern = new RegExp(`^여행은 ${DATE_FRAGMENT}부터 ${DATE_FRAGMENT}까지이고, ${DATE_FRAGMENT}에 방문하려고 합니다\\.$`, 'u');
const nightsPattern = new RegExp(`^${DATE_FRAGMENT}부터 ${DATE_FRAGMENT}까지 (\\d{1,3})박 (\\d{1,3})일 일정입니다\\.$`, 'u');
const tripPattern = new RegExp(`^여행은 ${DATE_FRAGMENT}부터 ${DATE_FRAGMENT}까지입니다\\.$`, 'u');
const owns = (record, key) => Object.prototype.hasOwnProperty.call(record, key);
const opaque = (value) => typeof value === 'string' && /^[A-Za-z0-9_-]{8,128}$/u.test(value);

export class ValidatedRequestAdapterError extends Error {
  constructor(reason) {
    super(`Invalid validated template request: ${reason}`);
    this.name = 'ValidatedRequestAdapterError';
    this.code = 'INVALID_VALIDATED_TEMPLATE_REQUEST';
    this.reason = reason;
  }
}
const ensure = (condition, reason) => { if (!condition) throw new ValidatedRequestAdapterError(reason); };
function koreanDate(ymd) {
  const date = new Date(parseYmd(ymd));
  return `${date.getUTCMonth() + 1}월 ${date.getUTCDate()}일(${'일월화수목금토'[date.getUTCDay()]})`;
}
function softThemes(label, used = []) {
  for (const theme of Object.keys(THEMES)) {
    if (used.includes(theme)) continue;
    if (label === theme) return [...used, theme];
    if (label.startsWith(`${theme}·`)) {
      const result = softThemes(label.slice(theme.length + 1), [...used, theme]);
      if (result !== null) return result;
    }
  }
  return null;
}
function requestedDate(fragment, today) {
  const match = /^(\d{1,2})월 (\d{1,2})일\([일월화수목금토]\)$/u.exec(fragment);
  ensure(match !== null, 'invalid_date_fragment');
  const start = parseYmd(today);
  const year = Number(today.slice(0, 4));
  const matches = [];
  for (const candidateYear of [year, year + 1]) {
    const date = `${candidateYear}-${match[1].padStart(2, '0')}-${match[2].padStart(2, '0')}`;
    try {
      const ms = parseYmd(date);
      if (ms >= start && ms <= start + 366 * DAY_MS && koreanDate(date) === fragment) matches.push(date);
    } catch { /* Invalid calendar candidates never become inferred dates. */ }
  }
  ensure(matches.length === 1, 'ambiguous_or_out_of_window_date');
  return matches[0];
}

/** The audit is local metadata, never an additional model instruction or an oracle action. */
export function inspectValidatedRequest(input, { catalog, requestId } = {}) {
  ensure(opaque(requestId), 'invalid_opaque_request_id');
  // This validates the complete input key set and preserves every supplied scenario field in query.
  const query = formatBaselineMessage(input);
  // The shared catalog validator freezes its argument; validate an isolated copy to keep this adapter pure.
  const checkedCatalog = validateAgentCatalog(structuredClone(catalog));
  parseYmd(input.context.today);
  const header = /^(제주에서|.+? 쪽에서) (.+?) (테마로|위주로|쪽으로) 갈 만한 곳을 추천해 주세요\.(?: |$)/u.exec(input.query);
  ensure(header !== null, 'unsupported_template_header');
  const filters = {};
  const contextOnlyConditions = [];
  if (header[1] !== '제주에서') {
    const label = header[1].slice(0, -4);
    ensure(owns(ZONES, label), 'unknown_zone_label');
    filters.zone = ZONES[label];
  }
  if (header[3] === '테마로') {
    ensure(owns(THEMES, header[2]), 'unknown_or_multiple_explicit_themes');
    filters.theme = THEMES[header[2]];
  } else if (header[3] === '위주로') {
    ensure(owns(CATEGORIES, header[2]), 'unknown_category_label');
    filters.category = CATEGORIES[header[2]];
  } else {
    ensure(softThemes(header[2]) !== null, 'unknown_soft_theme_label');
    contextOnlyConditions.push('theme_preference');
  }

  const requirements = [];
  let excludedIds = [];
  let visitDate = input.context.today;
  let dateConflict = null;
  const seen = new Set();
  const add = (type) => { ensure(!seen.has(type), 'duplicate_condition'); seen.add(type); };
  const remainder = input.query.slice(header[0].length);
  // Exclusion names may contain periods; the exclusion sentence is consumed as one final clause.
  const exclusionAt = remainder.indexOf('다음 장소는 이미 가 봐서 빼 주세요: ');
  const plain = exclusionAt < 0 ? remainder : remainder.slice(0, exclusionAt);
  const sentences = plain.length ? plain.trimEnd().split(/(?<=\.) /u) : [];
  if (exclusionAt >= 0) sentences.push(remainder.slice(exclusionAt));
  for (const sentence of sentences) {
    let match;
    if (CONTEXT_SENTENCES.has(sentence)) {
      const type = CONTEXT_SENTENCES.get(sentence);
      add(type); contextOnlyConditions.push(type);
    } else if ((match = indoorPattern.exec(sentence))) {
      add('indoor_required');
      const dates = (input.context.forecast ?? []).filter(day => day.rain === true && koreanDate(day.date) === match[1]);
      ensure(dates.length === 1, 'indoor_date_forecast_mismatch');
      requirements.push({ type: 'indoor_required', value: true, date: dates[0].date });
      visitDate = dates[0].date;
    } else if (sentence === '휠체어로 이용할 수 있어야 합니다.') {
      add('wheelchair'); requirements.push({ type: 'wheelchair', value: true });
    } else if ((match = /^(오전|오후|낮|밤) (\d{1,2})시(?: (\d{1,2})분)?에 방문할 수 있어야 합니다\.$/u.exec(sentence))) {
      add('open_at');
      const hour = Number(match[2]); const minute = Number(match[3] ?? 0);
      ensure(minute >= 0 && minute < 60 && (match[3] === undefined || minute > 0), 'invalid_visit_time');
      ensure(['낮', '밤'].includes(match[1]) ? hour === 12 : hour >= 1 && hour < 12, 'invalid_visit_time');
      const hours = match[1] === '밤' ? 0 : match[1] === '낮' ? 12 : hour + (match[1] === '오후' ? 12 : 0);
      requirements.push({ type: 'open_at', value: `${String(hours).padStart(2, '0')}:${String(minute).padStart(2, '0')}` });
    } else if ((match = /^항공·숙박을 빼고 1인 하루 (\d{1,5})만 원을 넘기면 안 됩니다\.$/u.exec(sentence))) {
      add('budget_cap');
      requirements.push({ type: 'budget_cap', value: Number(match[1]) * 10_000, unit: 'KRW_per_person_per_day' });
    } else if ((match = outsideTripPattern.exec(sentence)) || (match = nightsPattern.exec(sentence)) || (match = tripPattern.exec(sentence))) {
      add('trip_dates'); contextOnlyConditions.push('trip_dates');
      const trip = input.context.trip;
      ensure(trip !== undefined && koreanDate(trip.startDate) === match[1] && koreanDate(trip.endDate) === match[2], 'trip_context_mismatch');
      visitDate = trip.startDate;
      if (outsideTripPattern.test(sentence)) {
        visitDate = requestedDate(match[3], input.context.today);
        if (visitDate < trip.startDate || visitDate > trip.endDate) dateConflict = 'date_outside_trip';
      } else if (nightsPattern.test(sentence)) {
        ensure(Number(match[4]) === Number(match[3]) + 1, 'unsupported_nights_day_mismatch');
        if (Number(match[3]) !== (parseYmd(trip.endDate) - parseYmd(trip.startDate)) / DAY_MS) dateConflict = 'nights_mismatch';
      }
    } else if ((match = /^다음 장소는 이미 가 봐서 빼 주세요: (.+)\.$/u.exec(sentence))) {
      add('exclude_places');
      const names = match[1].split(', ');
      ensure(names.every(name => name.length > 0 && name === name.trim()), 'invalid_exclusion_names');
      excludedIds = names.map(name => {
        const places = checkedCatalog.places.filter(place => place.name === name);
        ensure(places.length === 1, 'unknown_or_ambiguous_exclusion_name');
        return places[0].id;
      });
      ensure(new Set(excludedIds).size === excludedIds.length, 'duplicate_exclusions');
    } else throw new ValidatedRequestAdapterError('unsupported_template_clause');
  }
  ensure(input.context.trip === undefined || seen.has('trip_dates'), 'unrepresented_trip_context');
  ensure(!seen.has('trip_dates') || !seen.has('indoor_required'), 'multiple_visit_date_conditions');
  const request = { schemaVersion: '1.0', requestId, query, timezone: input.context.timezone,
    visitDate, filters, excludedIds, requirements };
  return { request, audit: { adapterVersion: VALIDATED_REQUEST_ADAPTER_VERSION,
    grammarVersion: VALIDATED_REQUEST_GRAMMAR_VERSION, contextOnlyConditions, dateConflict } };
}

export function buildValidatedRequest(input, options) {
  return inspectValidatedRequest(input, options).request;
}

/** Uses the session's fresh opaque ID for response binding; no case/persona/gold identifiers are sent. */
export function buildValidatedLangflowRequest(input, { catalog, sessionId } = {}) {
  ensure(opaque(sessionId), 'invalid_opaque_session_id');
  return { input_value: JSON.stringify(buildValidatedRequest(input, { catalog, requestId: sessionId })),
    input_type: 'chat', output_type: 'chat', session_id: sessionId };
}
