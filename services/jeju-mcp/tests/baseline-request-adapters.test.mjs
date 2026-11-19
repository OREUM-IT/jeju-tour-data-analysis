import test from 'node:test';
import assert from 'node:assert/strict';
import { buildGeminiRequest, buildLangflowRequest, formatBaselineMessage } from '../src/baseline-request-adapters.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const request = (overrides = {}) => ({
  query: '실내 체험 장소를 찾아주세요.',
  context: { today: '2026-11-18', timezone: 'Asia/Seoul' }, ...overrides,
});
const sessionId = 'baseline-opaque-001';

test('both baselines receive identical scenario text and the exact original query', () => {
  const input = request({
    query: '11월 20일부터 22일까지 4박 5일 여행입니다. 11월 24일에 실내 장소를 방문하고 싶어요.',
    context: {
      today: '2026-11-18', timezone: 'Asia/Seoul',
      forecast: [{ date: '2026-11-24', rain: true }],
      trip: { startDate: '2026-11-20', endDate: '2026-11-22' },
    },
  });
  const gemini = buildGeminiRequest(input);
  const langflow = buildLangflowRequest(input, { sessionId });
  assert.equal(gemini.messages[0].text, langflow.input_value);
  assert.ok(langflow.input_value.endsWith(input.query));
  for (const fact of ['2026-11-18', 'Asia/Seoul', '2026-11-24: 비 예보 있음', '2026-11-20부터 2026-11-22까지']) {
    assert.ok(langflow.input_value.includes(fact));
  }
  assert.deepEqual(gemini.course, { placeIds: [], startDate: '2026-11-20', endDate: '2026-11-22' });
  assert.deepEqual(gemini.days, [{ date: '2026-11-24', rain: true, tmax: null }]);
  assert.equal(gemini.locale, 'ko');
  assert.equal(langflow.session_id, sessionId);
  assert.deepEqual(Object.keys(langflow).sort(), ['input_type', 'input_value', 'output_type', 'session_id']);
});

test('missing forecast is not converted into a dry forecast or an invented trip date', () => {
  const output = buildGeminiRequest(request());
  assert.deepEqual(output.days, []);
  assert.deepEqual(output.course, { placeIds: [], startDate: null, endDate: null });
  assert.ok(output.messages[0].text.includes('날씨 예보: 제공되지 않음'));
  assert.ok(output.messages[0].text.includes('여행 기간: 제공되지 않음'));
  assert.ok(!output.messages[0].text.includes('비 예보 없음'));
  const dry = request({ context: { ...request().context, forecast: [{ date: '2026-11-19', rain: false }] } });
  assert.ok(formatBaselineMessage(dry).includes('2026-11-19: 비 예보 없음'));
  assert.equal(buildGeminiRequest(dry).days[0].rain, false);
});

test('full evaluation items and extra answer-bearing fields cannot leak through request construction', () => {
  for (const extra of [{ gold: { allowedIds: ['FIXTURE_GOLD'] } }, { personaId: 'P01' }, { itemId: 'EV-P01' }, { conditions: [] }]) {
    assert.throws(() => buildGeminiRequest({ ...request(), ...extra }), /only a query/);
  }
  for (const extra of [{ expectedAction: 'recommend' }, { candidateIds: ['FIXTURE_GOLD'] }]) {
    assert.throws(() => buildLangflowRequest(request({ context: { ...request().context, ...extra } }), { sessionId }), /context/);
  }
});

test('unrepresentable context is rejected instead of silently dropping facts or truncating the question', () => {
  const base = request().context;
  for (const context of [
    { ...base, today: '2026-02-30' }, { ...base, timezone: 'UTC' },
    { ...base, forecast: [{ date: '2026-11-19', rain: 'false' }] },
    { ...base, forecast: [{ date: '2026-11-19', rain: true, tmax: 20 }] },
    { ...base, forecast: [{ date: '2026-11-19', rain: true }, { date: '2026-11-19', rain: false }] },
    { ...base, trip: { startDate: '2026-11-22', endDate: '2026-11-20' } },
    { ...base, trip: { startDate: '2026-11-20', endDate: '2026-11-22', nights: 4 } },
  ]) assert.throws(() => buildGeminiRequest(request({ context })));
  assert.throws(() => buildGeminiRequest(request({ query: '가'.repeat(201) })), /200/);
  const tooManyDays = Array.from({ length: 11 }, (_, index) => ({ date: `2026-11-${String(index + 1).padStart(2, '0')}`, rain: true }));
  assert.throws(() => buildGeminiRequest(request({ context: { ...base, forecast: tooManyDays } })), /ten/);
});

test('Langflow requires an explicit safe session ID and never adds prompt or model tweaks', () => {
  for (const value of [undefined, '', 'short', 'bad\nidentifier', 'a'.repeat(129)]) {
    assert.throws(() => buildLangflowRequest(request(), { sessionId: value }), /sessionId/);
  }
  const first = buildLangflowRequest(request(), { sessionId: 'opaque-session-001' });
  const second = buildLangflowRequest(request(), { sessionId: 'opaque-session-002' });
  assert.notEqual(first.session_id, second.session_id);
  assert.equal(first.input_value, second.input_value);
  assert.equal(first.tweaks, undefined);
});

test('all 96 synthetic plan scenarios survive the pinned message length limit without label leakage', () => {
  const { set } = baselineInputs();
  for (const item of set.items) {
    const input = { query: item.query.template, context: item.context };
    const body = buildGeminiRequest(input);
    assert.ok(body.messages[0].text.endsWith(item.query.template));
    assert.ok(body.messages[0].text.length <= 600);
    const serialized = JSON.stringify(body);
    assert.ok(!serialized.includes(item.id));
    assert.ok(!serialized.includes('gold'));
    assert.ok(!serialized.includes('expectedAction'));
    assert.ok(!serialized.includes('personaId'));
  }
});

test('request construction is pure and does not share mutable forecast or trip values', () => {
  const input = request({ context: { ...request().context, forecast: [{ date: '2026-11-19', rain: true }] } });
  const original = structuredClone(input);
  const output = buildGeminiRequest(input);
  output.days[0].rain = false;
  output.course.placeIds.push('MUTATED');
  assert.deepEqual(input, original);
});
