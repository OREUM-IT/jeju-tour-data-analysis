// Pure request construction. No network, gold labels, catalog access, or model/provider configuration.
export const REQUEST_ADAPTER_VERSION = '1.0.0';
export const PINNED_ASSISTANT_COMMIT = 'b0602a175c1d08959b7187da2b29ac450b52e0ba';
export const REQUEST_ADAPTER_LIMITATIONS = Object.freeze([
  'The pinned Gemini assistant uses the server clock in its system prompts; context.today is conveyed in user text and does not override that clock.',
  'The deployed app may differ from the pinned source and uses its own current catalog, crowd forecast, model configuration, fallback policy and daily budget.',
  'The common context wrapper is part of the baseline prompt version. Gemini additionally receives the stated trip window and forecast in its native request fields.',
  'Each Langflow case needs a distinct session_id to avoid memory from earlier cases. No model, prompt or tool tweaks are added by this adapter.',
]);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const ensure = (condition, message) => { if (!condition) throw new Error(`Invalid baseline request: ${message}`); };
const keysAre = (record, allowed) => isRecord(record) && Object.keys(record).every((key) => allowed.includes(key));
function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const at = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(at) && new Date(at).toISOString().slice(0, 10) === value;
}

function validateRequest(request) {
  ensure(keysAre(request, ['query', 'context']) && typeof request.query === 'string'
    && request.query.trim().length > 0 && request.query.length <= 200, 'only a query of 1–200 characters and context are accepted');
  const { context } = request;
  ensure(keysAre(context, ['today', 'timezone', 'forecast', 'trip']) && validDate(context.today)
    && context.timezone === 'Asia/Seoul', 'context needs a real today date and Asia/Seoul timezone');
  if (context.forecast !== undefined) {
    ensure(Array.isArray(context.forecast) && context.forecast.length <= 10
      && context.forecast.every((day) => keysAre(day, ['date', 'rain']) && validDate(day.date) && typeof day.rain === 'boolean')
      && new Set(context.forecast.map((day) => day.date)).size === context.forecast.length,
    'forecast must contain at most ten distinct dates with explicit rain booleans');
  }
  if (context.trip !== undefined) {
    ensure(keysAre(context.trip, ['startDate', 'endDate']) && validDate(context.trip.startDate)
      && validDate(context.trip.endDate) && context.trip.startDate <= context.trip.endDate,
    'trip must contain a valid startDate/endDate window');
  }
  return request;
}

/** The same wrapper is sent to both baselines; the original query remains verbatim, including contradictions. */
export function formatBaselineMessage(request) {
  validateRequest(request);
  const { query, context } = request;
  const forecast = context.forecast?.length
    ? context.forecast.map((day) => `${day.date}: ${day.rain ? '비 예보 있음' : '비 예보 없음'}`).join('; ')
    : '제공되지 않음';
  const trip = context.trip ? `${context.trip.startDate}부터 ${context.trip.endDate}까지` : '제공되지 않음';
  const message = [
    '다음 가상 여행 요청은 아래 기준 상황을 사용합니다.',
    `기준 날짜: ${context.today} (${context.timezone})`,
    `날씨 예보: ${forecast}`,
    `여행 기간: ${trip}`,
    '',
    '여행 요청:',
    query,
  ].join('\n');
  // assistant.ts slices each message to 600 UTF-16 code units. Never let the query lose its ending silently.
  ensure(message.length <= 600, 'context wrapper exceeds the pinned Gemini 600-character message limit');
  return message;
}

/** POST /api/assistant body at the pinned commit; empty course IDs cannot mutate or carry an existing user course. */
export function buildGeminiRequest(request) {
  const message = formatBaselineMessage(request);
  const { context } = request;
  return {
    messages: [{ role: 'user', text: message }],
    locale: 'ko',
    course: { placeIds: [], startDate: context.trip?.startDate ?? null, endDate: context.trip?.endDate ?? null },
    days: (context.forecast ?? []).map((day) => ({ date: day.date, rain: day.rain, tmax: null })),
  };
}

/** POST /api/v1/run/{flow_id}?stream=false body. Caller creates a fresh opaque session ID for every case. */
export function buildLangflowRequest(request, { sessionId } = {}) {
  ensure(typeof sessionId === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(sessionId),
    'Langflow needs a fresh opaque sessionId of 8–128 letters, digits, underscores or hyphens');
  return {
    input_value: formatBaselineMessage(request),
    input_type: 'chat', output_type: 'chat', session_id: sessionId,
  };
}
