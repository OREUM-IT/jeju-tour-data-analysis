// Shared constraint evidence. Historical evaluation policy is retained byte-for-byte in requirementEvidence;
// runtime callers use the stricter runtimeRequirementEvidence below and inject a trusted server clock.
export const DAY_MS = 86_400_000;
export const KST_OFFSET_MS = 9 * 3_600_000;
export const TIME = /^([01]\d|2[0-3]):([0-5]\d)$/u;

export class EvaluationSetError extends Error {
  constructor(message) {
    super(message);
    this.name = 'EvaluationSetError';
    this.code = 'INVALID_EVALUATION_SET';
  }
}

const ensure = (condition, message) => { if (!condition) throw new EvaluationSetError(message); };
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);

export function parseYmd(ymd) {
  const match = typeof ymd === 'string' ? /^(\d{4})-(\d\d)-(\d\d)$/u.exec(ymd) : null;
  ensure(match, 'dates must be YYYY-MM-DD');
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  ensure(new Date(ms).toISOString().slice(0, 10) === ymd, `invalid calendar date ${ymd}`);
  return ms;
}

export function minutesOf(time) {
  const match = TIME.exec(time ?? '');
  ensure(match, 'times must be HH:MM');
  return Number(match[1]) * 60 + Number(match[2]);
}

/**
 * Evidence for one hard requirement on one place at instant `atMs`.
 * verdict: pass/fail only from a `verified` attribute whose validity interval covers `atMs`; otherwise `unknown`.
 * signal/tier describe what non-verified evidence says so an agent can rank and explain; they never become a verdict.
 */
export function requirementEvidence(place, requirement, atMs) {
  const none = { verdict: 'unknown', signal: 'none', tier: null, basis: null };
  // A verification must exist on the evaluation day and still hold at the end of the day the requirement applies to.
  const neededUntil = requirement.date === undefined ? atMs : Math.max(atMs, parseYmd(requirement.date) + DAY_MS - KST_OFFSET_MS);
  const graded = (attribute, satisfied, judgeable = true) => {
    const valid = attribute.basis === 'verified' && Date.parse(attribute.verifiedAt) <= atMs && neededUntil <= Date.parse(attribute.validUntil);
    const signal = satisfied ? 'supports' : 'contradicts';
    if (valid && judgeable) return { verdict: satisfied ? 'pass' : 'fail', signal, tier: 'verified', basis: 'verified' };
    const tier = attribute.basis === 'inferred' || attribute.basis === 'computed' ? 'inferred' : 'reported';
    return { verdict: 'unknown', signal, tier, basis: attribute.basis };
  };
  const attributes = place.attributes;
  switch (requirement.type) {
    case 'wheelchair': {
      // Being on a barrier-free list is not the same fact as wheelchair access, so even a verified listing stays unknown.
      if (attributes.barrierFree?.value?.listed === true) return graded(attributes.barrierFree, true, false);
      if (attributes.designations?.value?.includes('bf')) return graded(attributes.designations, true, false);
      return none;
    }
    case 'indoor_required':
      return typeof attributes.indoor?.value === 'boolean' ? graded(attributes.indoor, attributes.indoor.value) : none;
    case 'open_at': {
      const hours = attributes.hours?.value;
      if (!isRecord(hours) || !finite(hours.openMin) || !finite(hours.closeMin)) return none;
      const at = minutesOf(requirement.value);
      const close = hours.closeMin < hours.openMin ? hours.closeMin + 1440 : hours.closeMin;
      const open = (hours.openMin <= at && at < close) || (hours.openMin <= at + 1440 && at + 1440 < close);
      return graded(attributes.hours, open, plainDailyWindow(hours));
    }
    case 'budget_cap':
      return none; // Catalog v2 has no cost attribute at all.
    default:
      throw new EvaluationSetError(`unknown hard requirement ${requirement.type}`);
  }
}

/**
 * Weekly closures and break times live in free text that these rules do not parse, so only a plain daily window may be
 * judged: no text, or exactly "매일 HH:MM~HH:MM" that agrees with the numbers. A zero-length window is not judged.
 */
function plainDailyWindow(hours) {
  if (hours.openMin === hours.closeMin) return false;
  if (hours.text === null) return true;
  const match = /^매일 (\d\d):(\d\d)~(\d\d):(\d\d)$/u.exec(hours.text);
  if (match === null) return false;
  const open = Number(match[1]) * 60 + Number(match[2]);
  const close = Number(match[3]) * 60 + Number(match[4]);
  return open % 1440 === hours.openMin % 1440 && close % 1440 === hours.closeMin % 1440;
}

// Date.parse normalizes some impossible dates (for example February 30). Runtime evidence must not do that.
function strictUtcInstant(value) {
  const match = typeof value === 'string'
    ? /^(\d{4}-\d\d-\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,3}))?Z$/u.exec(value) : null;
  if (!match || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return null;
  let date;
  try { date = parseYmd(match[1]); } catch { return null; }
  const millis = Number((match[5] ?? '').padEnd(3, '0'));
  return date + Number(match[2]) * 3_600_000 + Number(match[3]) * 60_000 + Number(match[4]) * 1000 + millis;
}

function runtimeDailyWindow(hours) {
  // End-only 24:00 is supported. Extended next-day minute counts (e.g. 1560) are deliberately not interpreted.
  if (!isRecord(hours) || !Number.isInteger(hours.openMin) || !Number.isInteger(hours.closeMin)
    || hours.openMin < 0 || hours.openMin > 1439 || hours.closeMin < 0 || hours.closeMin > 1440) return 'invalid_hours_range';
  if (hours.openMin === hours.closeMin) return 'hours_not_plain_daily_window';
  if (hours.text === null) return 'hours_daily_recurrence_unconfirmed';
  const match = typeof hours.text === 'string' ? /^매일 (\d\d):(\d\d)~(\d\d):(\d\d)$/u.exec(hours.text) : null;
  if (!match) return 'hours_not_plain_daily_window';
  const [openHour, openMinute, closeHour, closeMinute] = match.slice(1).map(Number);
  if (openHour > 23 || openMinute > 59 || closeHour > 24 || closeMinute > 59 || (closeHour === 24 && closeMinute !== 0)) return 'invalid_hours_text';
  if (openHour * 60 + openMinute !== hours.openMin || closeHour * 60 + closeMinute !== hours.closeMin) return 'hours_text_numeric_mismatch';
  return null;
}

/**
 * Runtime hard-condition policy. `atMs` is supplied by the server, never by a model request. A dated requirement
 * needs the evidence to cover both the current instant and the entire requested date in Asia/Seoul. Unknown
 * remains unknown. Numeric hours with text:null cannot prove daily recurrence and remain unknown; only an exact
 * daily-window text can be judged (without guaranteeing exceptions or a full itinerary). Historical gold uses the separate
 * requirementEvidence export above, so adding these guards cannot rewrite an existing evaluation result.
 */
export function runtimeRequirementEvidence(place, requirement, atMs) {
  if (!finite(atMs) || !Number.isFinite(new Date(atMs).getTime())) throw new TypeError('atMs must be a trusted finite timestamp');
  if (!isRecord(requirement) || !['indoor_required', 'wheelchair', 'open_at', 'budget_cap'].includes(requirement.type)) {
    throw new TypeError('unknown runtime hard requirement');
  }
  const visitStart = requirement.date === undefined ? atMs : parseYmd(requirement.date) - KST_OFFSET_MS;
  const neededFrom = Math.min(atMs, visitStart);
  const neededUntil = requirement.date === undefined ? atMs : Math.max(atMs, visitStart + DAY_MS);
  const none = (reason) => ({ verdict: 'unknown', signal: 'none', tier: null, basis: null, reason });
  if (!place || !isRecord(place.attributes)) return none('place_not_found');
  if (requirement.type === 'open_at') minutesOf(requirement.value);
  if (requirement.type === 'wheelchair') {
    // A malformed listing must not turn a read-only check into an exception. Neither shape proves accessibility.
    const listing = place.attributes.barrierFree?.value?.listed === true ? place.attributes.barrierFree
      : Array.isArray(place.attributes.designations?.value) && place.attributes.designations.value.includes('bf')
        ? place.attributes.designations : undefined;
    if (!listing) return none('wheelchair_access_attribute_not_provided');
    return { verdict: 'unknown', signal: 'supports', tier: ['inferred', 'computed'].includes(listing.basis) ? 'inferred' : 'reported',
      basis: listing.basis, reason: 'barrier_free_listing_is_not_wheelchair_access' };
  }
  const attribute = requirement.type === 'indoor_required' ? place.attributes.indoor
    : requirement.type === 'open_at' ? place.attributes.hours : undefined;
  if (requirement.type === 'budget_cap') return none('cost_attribute_not_provided');
  if (!attribute) return none('value_not_provided');
  if (requirement.type === 'indoor_required' && typeof attribute.value !== 'boolean') return none('invalid_indoor_value');
  if (requirement.type === 'open_at') {
    const problem = runtimeDailyWindow(attribute.value);
    if (problem) return none(problem);
  }
  const value = requirementEvidence(place, requirement, atMs);
  if (attribute.basis !== 'verified') return { ...value, reason: 'verified_field_evidence_not_provided' };
  const from = strictUtcInstant(attribute.verifiedAt);
  const until = strictUtcInstant(attribute.validUntil);
  if (from === null || until === null || from > until || typeof attribute.source !== 'string' || attribute.source.trim() === '') {
    return { ...value, verdict: 'unknown', tier: 'reported', reason: 'invalid_verification_interval' };
  }
  if (from > neededFrom || until < neededUntil) {
    return { ...value, verdict: 'unknown', tier: 'reported', reason: 'current_and_visit_dated_evidence_not_provided' };
  }
  return { ...value, reason: 'catalog_value_with_current_and_visit_field_evidence' };
}
