import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, generateKeyPairSync, sign } from 'node:crypto';
import { GatewayAuthError, createAccessVerifier, signRequest, verifyAccessJwt, verifyRequest } from './auth.mjs';

const NOW = Date.parse('2026-11-19T12:00:00.000Z');
const SECONDS = NOW / 1000;
const KEY = 'test-only-gateway-hmac-key-32-bytes-minimum';
const NONCE = '0123456789abcdef0123456789abcdef';
const SUBJECT = 'opaque-session-42';
const PATH = '/v1/recommend';
const BODY = Buffer.from('{"query":"제주 실내 추천"}', 'utf8');
const signed = (overrides = {}) => signRequest({ key: KEY, method: 'POST', path: PATH, body: BODY,
  subject: SUBJECT, timestamp: SECONDS, nonce: NONCE, ...overrides });
const verify = (headers = signed(), overrides = {}) => verifyRequest({ headers, method: 'POST', path: PATH,
  body: BODY, key: KEY, now: NOW, ...overrides });
const authError = (error) => {
  assert.ok(error instanceof GatewayAuthError);
  assert.equal(error.code, 'AUTH_FAILED');
  assert.equal(error.status, 401);
  assert.equal(error.message, 'Request authentication failed.');
  return true;
};

const pair = generateKeyPairSync('rsa', { modulusLength: 2048 });
const alternatePair = generateKeyPairSync('rsa', { modulusLength: 2048 });
const JWK = { ...pair.publicKey.export({ format: 'jwk' }), kid: 'test-key-1', alg: 'RS256', use: 'sig' };
const OTHER_JWK = { ...alternatePair.publicKey.export({ format: 'jwk' }), kid: 'test-key-2', alg: 'RS256', use: 'sig' };
const ISSUER = 'https://test-team.cloudflareaccess.com';
const AUDIENCE = 'test-access-application';
const claims = (overrides = {}) => ({ iss: ISSUER, aud: [AUDIENCE], sub: 'access-user', iat: SECONDS - 30,
  exp: SECONDS + 3600, ...overrides });
function token({ header = { alg: 'RS256', kid: JWK.kid, typ: 'JWT' }, payload = claims(), privateKey = pair.privateKey } = {}) {
  const encode = (value) => Buffer.from(typeof value === 'string' ? value : JSON.stringify(value)).toString('base64url');
  const input = `${encode(header)}.${encode(payload)}`;
  return `${input}.${sign('RSA-SHA256', Buffer.from(input), privateKey).toString('base64url')}`;
}
function certFetch(keys = [JWK]) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ keys }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  return { fetchImpl, calls };
}
const accessOptions = (extra = {}) => ({ issuer: ISSUER, audience: AUDIENCE, now: NOW, ...extra });

test('HMAC signs the exact method, path, subject, timestamp, nonce and raw-body digest', () => {
  const headers = signed();
  const content = ['POST', PATH, SUBJECT, String(SECONDS), NONCE, createHash('sha256').update(BODY).digest('hex')].join('\n');
  assert.equal(headers['x-jeju-signature'], createHmac('sha256', KEY).update(content).digest('hex'));
  assert.equal(headers['x-jeju-subject'], SUBJECT);
  assert.equal(headers['x-jeju-timestamp'], String(SECONDS));
  assert.equal(headers['x-jeju-nonce'], NONCE);
  assert.deepEqual(verify(headers), { subject: SUBJECT, nonce: NONCE, timestamp: SECONDS });
  assert.deepEqual(verify(signed({ timestamp: String(SECONDS) })), verify(headers));
});

test('changing any signed component, including raw JSON whitespace, invalidates the HMAC', () => {
  const headers = signed();
  for (const change of [{ method: 'GET' }, { path: `${PATH}?debug=true` }, { body: Buffer.from(`${BODY.toString()} `) },
    { body: Buffer.from('{"query":"다른 요청"}') }, { key: 'a-different-key-long-enough-for-signing' }]) {
    assert.throws(() => verify(headers, change), authError);
  }
  for (const [name, value] of [['x-jeju-subject', 'different-subject'], ['x-jeju-nonce', 'a'.repeat(32)],
    ['x-jeju-timestamp', String(SECONDS + 1)], ['x-jeju-signature', '0'.repeat(64)]]) {
    assert.throws(() => verify({ ...headers, [name]: value }), authError);
  }
});

test('HMAC clock window is inclusive at 60 seconds and rejects stale or future signatures', () => {
  for (const drift of [-60, 0, 60]) assert.equal(verify(signed({ timestamp: SECONDS + drift })).subject, SUBJECT);
  for (const drift of [-61, 61]) assert.throws(() => verify(signed({ timestamp: SECONDS + drift })), authError);
  assert.throws(() => verify(signed(), { now: NaN }), authError);
});

test('missing or malformed authentication values always fail with a fixed non-secret error', () => {
  for (const input of [undefined, null, false, []]) {
    assert.throws(() => signRequest(input), authError);
    assert.throws(() => verifyRequest(input), authError);
    assert.throws(() => createAccessVerifier(input), authError);
  }
  const headers = signed();
  for (const name of ['x-jeju-subject', 'x-jeju-timestamp', 'x-jeju-nonce', 'x-jeju-signature']) {
    const missing = { ...headers };
    delete missing[name];
    assert.throws(() => verify(missing), authError);
    assert.throws(() => verify({ ...headers, [name]: [headers[name]] }), authError);
  }
  const malformed = {
    'x-jeju-subject': ['', 'subject with spaces', '한글', 'a'.repeat(129), 'a\nb'],
    'x-jeju-timestamp': ['0' + SECONDS, `${SECONDS}.0`, `+${SECONDS}`, `${SECONDS} `, 'NaN', '9'.repeat(40)],
    'x-jeju-nonce': ['a'.repeat(31), 'a'.repeat(33), 'G'.repeat(32), 'A'.repeat(32)],
    'x-jeju-signature': ['a'.repeat(63), 'a'.repeat(65), 'z'.repeat(64), 'A'.repeat(64)],
  };
  for (const [name, values] of Object.entries(malformed)) for (const value of values) {
    assert.throws(() => verify({ ...headers, [name]: value }), authError);
  }
  for (const key of ['', 'a'.repeat(31), `a${'한'.repeat(32)}`, `${'a'.repeat(32)}\n`]) {
    assert.throws(() => verify(headers, { key }), authError);
  }
});

test('JWT verifies a real RS256 signature against only the fixed Cloudflare cert endpoint', async () => {
  const { fetchImpl, calls } = certFetch();
  const validated = await verifyAccessJwt(token(), accessOptions({ fetchImpl }));
  assert.equal(validated.sub, 'access-user');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${ISSUER}/cdn-cgi/access/certs`);
  assert.ok(['error', 'manual'].includes(calls[0].options.redirect));
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  const stringAudience = await verifyAccessJwt(token({ payload: claims({ aud: AUDIENCE }) }), accessOptions({ fetchImpl }));
  assert.equal(stringAudience.aud, AUDIENCE);
});

test('JWT issuer, audience and dates are independently enforced without trusting signed but invalid claims', async () => {
  const { fetchImpl } = certFetch();
  const verify = createAccessVerifier(accessOptions({ fetchImpl }));
  for (const change of [
    { iss: 'https://another-team.cloudflareaccess.com' }, { iss: `${ISSUER}/` },
    { aud: 'another-application' }, { aud: [] }, { aud: [AUDIENCE, 7] },
    { exp: SECONDS }, { exp: SECONDS - 1 }, { exp: String(SECONDS + 100) }, { exp: SECONDS + 0.5 },
    { iat: SECONDS + 1 }, { iat: `${SECONDS}` }, { iat: SECONDS - 0.5 },
    { nbf: SECONDS + 1 }, { nbf: `${SECONDS}` },
  ]) await assert.rejects(verify(token({ payload: claims(change) })), authError);
  for (const missing of ['iss', 'aud', 'exp', 'iat']) {
    const value = claims(); delete value[missing];
    await assert.rejects(verify(token({ payload: value })), authError);
  }
  assert.equal((await verify(token({ payload: claims({ iat: SECONDS, nbf: SECONDS }) }))).sub, 'access-user');
});

test('JWT rejects forged signatures, unsupported algorithms and untrusted key URLs', async () => {
  const { fetchImpl } = certFetch();
  const verify = createAccessVerifier(accessOptions({ fetchImpl }));
  await assert.rejects(verify(token({ privateKey: alternatePair.privateKey })), authError);
  for (const header of [
    { alg: 'none', kid: JWK.kid }, { alg: 'HS256', kid: JWK.kid }, { alg: 'RS512', kid: JWK.kid },
    { alg: 'RS256' }, { alg: 'RS256', kid: 4 }, { alg: 'RS256', kid: 'unknown-key' },
    { alg: 'RS256', kid: JWK.kid, jku: 'https://attacker.invalid/keys' },
    { alg: 'RS256', kid: JWK.kid, jwk: OTHER_JWK },
  ]) await assert.rejects(verify(token({ header })), authError);
});

test('JWT parsing rejects malformed encoding, duplicate or escaped-duplicate JSON members', async () => {
  const { fetchImpl } = certFetch();
  const verify = createAccessVerifier(accessOptions({ fetchImpl }));
  const original = token();
  for (const malformed of ['', 'a.b.c', original + '.extra', ` ${original}`, original.replace('.', '=.'),
    token({ header: '{"alg":"RS256","alg":"RS256","kid":"test-key-1"}' }),
    token({ payload: JSON.stringify(claims()).replace('"exp":', '"exp":1,"exp":') }),
    token({ payload: JSON.stringify(claims()).replace('"iss":', '"\\u0069ss":"duplicate","iss":') }),
    token({ payload: '[1,2,3]' }),
  ]) await assert.rejects(verify(malformed), authError);
});

test('cert lookup rejects redirects, non-JSON, oversized bodies and unsupported JWKs', async () => {
  const responses = [
    () => new Response('', { status: 302, headers: { location: 'https://attacker.invalid/keys' } }),
    () => new Response('<html>unavailable</html>', { status: 503 }),
    () => new Response('invalid-json', { status: 200 }),
    () => new Response('{}', { headers: { 'content-length': '65537' } }),
    () => new Response(' '.repeat(65537)),
    () => new Response(JSON.stringify({ keys: [{ ...JWK, kty: 'EC' }] })),
    () => new Response(JSON.stringify({ keys: [{ ...JWK, use: 'enc' }] })),
    () => new Response(JSON.stringify({ keys: [{ ...JWK, alg: 'HS256' }] })),
  ];
  for (const make of responses) {
    await assert.rejects(verifyAccessJwt(token(), accessOptions({ fetchImpl: async () => make() })), authError);
  }
});

test('cert cache is per verifier, bounded by five minutes and refreshes rotated keys safely', async () => {
  let current = NOW;
  let activeKeys = [JWK];
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return new Response(JSON.stringify({ keys: activeKeys })); };
  const verify = createAccessVerifier(accessOptions({ now: () => current, fetchImpl }));
  await verify(token());
  await verify(token());
  assert.equal(calls, 1);
  current += 300_001;
  activeKeys = [OTHER_JWK];
  await verify(token({ header: { alg: 'RS256', kid: OTHER_JWK.kid }, privateKey: alternatePair.privateKey }));
  assert.equal(calls, 2);
  await assert.rejects(verify(token()), authError);
  const afterUnknown = calls;
  await assert.rejects(verify(token()), authError);
  assert.equal(calls, afterUnknown, 'Repeated unknown keys must not bypass the cache to flood the cert endpoint');
});

test('cert fetch cannot keep a token valid after its expiration while awaiting the response', async () => {
  let current = NOW;
  const verify = createAccessVerifier(accessOptions({ now: () => current, fetchImpl: async () => {
    current += 2000;
    return new Response(JSON.stringify({ keys: [JWK] }));
  } }));
  await assert.rejects(verify(token({ payload: claims({ exp: SECONDS + 1 }) })), authError);
});

test('cert collections reject duplicate key IDs and an unbounded number of keys', async () => {
  for (const keys of [[JWK, JWK], Array.from({ length: 33 }, (_, index) => ({ ...JWK, kid: `key-${index}` }))]) {
    const { fetchImpl } = certFetch(keys);
    await assert.rejects(verifyAccessJwt(token(), accessOptions({ fetchImpl })), authError);
  }
});

test('issuer configuration cannot redirect cert requests to arbitrary hosts or paths', async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return new Response(JSON.stringify({ keys: [JWK] })); };
  for (const issuer of ['http://test-team.cloudflareaccess.com', 'https://example.com', `${ISSUER}/evil`,
    `${ISSUER}:8443`, 'https://user:password@test-team.cloudflareaccess.com', `${ISSUER}?target=evil`,
    'https://test-team.cloudflareaccess.com.attacker.invalid']) {
    await assert.rejects(async () => verifyAccessJwt(token(), accessOptions({ issuer, fetchImpl })), authError);
  }
  assert.equal(calls, 0);
});

test('a stalled cert fetch is aborted within the five-second limit', { timeout: 7000 }, async () => {
  let aborted = false;
  const fetchImpl = async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => { aborted = true; reject(signal.reason); }, { once: true });
  });
  await assert.rejects(verifyAccessJwt(token(), accessOptions({ fetchImpl })), authError);
  assert.equal(aborted, true);
});
