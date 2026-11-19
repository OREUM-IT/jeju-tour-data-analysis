import { createHash, createHmac, createPublicKey, randomBytes, timingSafeEqual, verify as verifySignature } from 'node:crypto';

const MAX_JWKS_BYTES = 65_536;
const CERT_TIMEOUT_MS = 5_000;
const CACHE_MAX_MS = 300_000;
const ASCII_TOKEN = /^[\x21-\x7e]{1,128}$/u;
const NONCE = /^[a-f0-9]{32}$/u;

export class GatewayAuthError extends Error {
  constructor() {
    super('Request authentication failed.');
    this.name = 'GatewayAuthError';
    this.code = 'AUTH_FAILED';
    this.status = 401;
  }
}

const fail = () => { throw new GatewayAuthError(); };
const requireThat = (value) => { if (!value) fail(); };
const nowMs = (now = Date.now) => {
  const value = typeof now === 'function' ? now() : now;
  requireThat(typeof value === 'number' && Number.isFinite(value) && value >= 0);
  return value;
};
const rawBody = (body) => {
  requireThat(typeof body === 'string' || body instanceof Uint8Array);
  return typeof body === 'string' ? Buffer.from(body, 'utf8') : body;
};
const keyBytes = (key) => {
  requireThat(typeof key === 'string' && /^[\x20-\x7e]{32,4096}$/u.test(key));
  return Buffer.from(key, 'ascii');
};
function canonicalRequest({ method, path, body, subject, timestamp, nonce }) {
  requireThat(typeof method === 'string' && /^[A-Z]{1,16}$/u.test(method));
  requireThat(typeof path === 'string' && /^\/[\x21-\x7e]{0,2047}$/u.test(path) && !path.includes('#'));
  requireThat(typeof subject === 'string' && ASCII_TOKEN.test(subject));
  requireThat(typeof nonce === 'string' && NONCE.test(nonce));
  const stamp = String(timestamp);
  requireThat(/^(?:0|[1-9]\d{0,11})$/u.test(stamp) && Number.isSafeInteger(Number(stamp)));
  return [method, path, subject, stamp, nonce, createHash('sha256').update(rawBody(body)).digest('hex')].join('\n');
}

/** The body is the exact bytes sent over HTTP; parsing and reserializing JSON changes the signature. */
export function signRequest(options) {
  try {
    const { key, method, path, body, subject, timestamp = Math.floor(Date.now() / 1000), nonce = randomBytes(16).toString('hex') } = options;
    const payload = canonicalRequest({ method, path, body, subject, timestamp, nonce });
    return {
      'x-jeju-subject': subject, 'x-jeju-timestamp': String(timestamp), 'x-jeju-nonce': nonce,
      'x-jeju-signature': createHmac('sha256', keyBytes(key)).update(payload).digest('hex'),
    };
  } catch { fail(); }
}

function headerValue(headers, wanted) {
  requireThat(headers !== null && typeof headers === 'object' && !Array.isArray(headers));
  const matches = Object.entries(headers).filter(([name]) => name.toLowerCase() === wanted);
  requireThat(matches.length === 1 && typeof matches[0][1] === 'string');
  return matches[0][1];
}

export function verifyRequest(options) {
  try {
    const { headers, method, path, body, key, now = Date.now } = options;
    const subject = headerValue(headers, 'x-jeju-subject');
    const timestamp = headerValue(headers, 'x-jeju-timestamp');
    const nonce = headerValue(headers, 'x-jeju-nonce');
    const signature = headerValue(headers, 'x-jeju-signature');
    requireThat(/^[a-f0-9]{64}$/u.test(signature));
    const payload = canonicalRequest({ method, path, body, subject, timestamp, nonce });
    requireThat(Math.abs(nowMs(now) / 1000 - Number(timestamp)) <= 60);
    const expected = createHmac('sha256', keyBytes(key)).update(payload).digest();
    requireThat(timingSafeEqual(expected, Buffer.from(signature, 'hex')));
    return { subject, nonce, timestamp: Number(timestamp) };
  } catch { fail(); }
}

// JSON.parse alone accepts duplicate object members. Scan the already-valid JSON and reject duplicates at every
// nesting level, including differently escaped spellings of the same key. Inputs are separately byte-bounded.
function uniqueJson(text) {
  const parsed = JSON.parse(text);
  let index = 0;
  const whitespace = () => { while (/\s/u.test(text[index] ?? '') && index < text.length) index += 1; };
  const string = () => {
    const start = index++;
    while (index < text.length) {
      const char = text[index++];
      if (char === '\\') index += 1;
      else if (char === '"') return JSON.parse(text.slice(start, index));
    }
    fail();
  };
  const value = (depth) => {
    requireThat(depth <= 32);
    whitespace();
    if (text[index] === '"') { string(); return; }
    if (text[index] === '{') {
      index += 1;
      whitespace();
      const keys = new Set();
      if (text[index] === '}') { index += 1; return; }
      while (index < text.length) {
        const key = string();
        requireThat(!keys.has(key));
        keys.add(key);
        whitespace();
        requireThat(text[index++] === ':');
        value(depth + 1);
        whitespace();
        if (text[index] === '}') { index += 1; return; }
        requireThat(text[index++] === ',');
        whitespace();
      }
    } else if (text[index] === '[') {
      index += 1;
      whitespace();
      if (text[index] === ']') { index += 1; return; }
      while (index < text.length) {
        value(depth + 1);
        whitespace();
        if (text[index] === ']') { index += 1; return; }
        requireThat(text[index++] === ',');
      }
    } else {
      while (index < text.length && !/[\s,}\]]/u.test(text[index])) index += 1;
      return;
    }
    fail();
  };
  value(0);
  whitespace();
  requireThat(index === text.length);
  return parsed;
}

const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
function decodePart(part) {
  requireThat(typeof part === 'string' && /^[A-Za-z0-9_-]+$/u.test(part));
  const bytes = Buffer.from(part, 'base64url');
  requireThat(bytes.toString('base64url') === part);
  return bytes;
}

function configuration(issuer, audience) {
  requireThat(typeof issuer === 'string' && /^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$/u.test(issuer));
  requireThat(typeof audience === 'string' && ASCII_TOKEN.test(audience));
  return `${issuer}/cdn-cgi/access/certs`;
}

async function fetchKeys(url, fetchImpl) {
  const controller = new AbortController();
  let reader;
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new GatewayAuthError()); }, CERT_TIMEOUT_MS);
    timer.unref?.();
  });
  try {
    return await Promise.race([timeout, (async () => {
      const response = await fetchImpl(url, { method: 'GET', redirect: 'error', signal: controller.signal, headers: { accept: 'application/json' } });
      requireThat(response && response.status === 200 && !response.redirected && (!response.url || response.url === url));
      const length = response.headers.get('content-length');
      requireThat(length === null || (/^\d+$/u.test(length) && Number(length) <= MAX_JWKS_BYTES));
      requireThat(response.body && typeof response.body.getReader === 'function');
      reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        requireThat(next.value instanceof Uint8Array);
        size += next.value.byteLength;
        requireThat(size <= MAX_JWKS_BYTES);
        chunks.push(Buffer.from(next.value));
      }
      const payload = uniqueJson(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
      requireThat(object(payload) && Array.isArray(payload.keys) && payload.keys.length > 0 && payload.keys.length <= 32);
      const keys = new Map();
      for (const jwk of payload.keys) {
        requireThat(object(jwk) && typeof jwk.kid === 'string' && ASCII_TOKEN.test(jwk.kid) && !keys.has(jwk.kid));
        requireThat(jwk.kty === 'RSA' && (jwk.alg === undefined || jwk.alg === 'RS256') && (jwk.use === undefined || jwk.use === 'sig'));
        requireThat(jwk.key_ops === undefined || (Array.isArray(jwk.key_ops) && jwk.key_ops.length === 1 && jwk.key_ops[0] === 'verify'));
        requireThat(!['d', 'p', 'q', 'dp', 'dq', 'qi', 'oth'].some((field) => Object.hasOwn(jwk, field)));
        decodePart(jwk.n);
        decodePart(jwk.e);
        const key = createPublicKey({ key: jwk, format: 'jwk' });
        requireThat(key.asymmetricKeyType === 'rsa' && key.asymmetricKeyDetails.modulusLength >= 2048 && key.asymmetricKeyDetails.modulusLength <= 8192);
        keys.set(jwk.kid, key);
      }
      const maxAge = /(?:^|,)\s*max-age=(\d+)\s*(?:,|$)/iu.exec(response.headers.get('cache-control') ?? '');
      const ttl = maxAge ? Math.min(CACHE_MAX_MS, Number(maxAge[1]) * 1000) : CACHE_MAX_MS;
      return { keys, ttl };
    })()]);
  } finally {
    clearTimeout(timer);
    controller.abort();
    if (reader) void reader.cancel().catch(() => {});
  }
}

/** Each configured verifier owns a bounded certificate cache; no token or claims are stored or logged. */
export function createAccessVerifier(options) {
  requireThat(object(options));
  const { issuer, audience, fetchImpl = fetch, now = Date.now } = options;
  const url = configuration(issuer, audience);
  requireThat(typeof fetchImpl === 'function');
  let cache;
  let pending;
  return async function verify(token) {
    try {
      requireThat(typeof token === 'string' && token.length <= 32_768);
      const parts = token.split('.');
      requireThat(parts.length === 3 && parts[0].length <= 2048);
      const decoder = new TextDecoder('utf-8', { fatal: true });
      const header = uniqueJson(decoder.decode(decodePart(parts[0])));
      const claims = uniqueJson(decoder.decode(decodePart(parts[1])));
      const signature = decodePart(parts[2]);
      requireThat(object(header) && Object.keys(header).every((key) => ['alg', 'kid', 'typ'].includes(key))
        && header.alg === 'RS256' && typeof header.kid === 'string' && ASCII_TOKEN.test(header.kid)
        && (header.typ === undefined || header.typ === 'JWT'));
      requireThat(object(claims) && claims.iss === issuer);
      const audiences = typeof claims.aud === 'string' ? [claims.aud] : claims.aud;
      requireThat(Array.isArray(audiences) && audiences.length > 0 && audiences.length <= 32
        && audiences.every((value) => typeof value === 'string' && ASCII_TOKEN.test(value))
        && new Set(audiences).size === audiences.length && audiences.includes(audience));
      const current = nowMs(now);
      const seconds = current / 1000;
      requireThat(Number.isSafeInteger(claims.exp) && Number.isSafeInteger(claims.iat)
        && claims.exp > seconds && claims.iat >= 0 && claims.iat <= seconds && claims.iat < claims.exp);
      requireThat(claims.nbf === undefined || (Number.isSafeInteger(claims.nbf) && claims.nbf >= 0 && claims.nbf <= seconds && claims.nbf < claims.exp));
      if (!cache || current >= cache.expiresAt || current < cache.fetchedAt) {
        if (!pending) pending = fetchKeys(url, fetchImpl).then((result) => {
          const fetchedAt = nowMs(now);
          cache = { ...result, fetchedAt, expiresAt: fetchedAt + result.ttl };
        }).finally(() => { pending = undefined; });
        await pending;
      }
      // Unknown keys fail until the bounded cache expires. An attacker cannot force fetches with random kid values.
      const key = cache.keys.get(header.kid);
      requireThat(key && verifySignature('RSA-SHA256', Buffer.from(`${parts[0]}.${parts[1]}`, 'ascii'), key, signature));
      // Fetching certificates may span the expiration instant. Revalidate time after the async boundary.
      const verifiedAt = nowMs(now) / 1000;
      requireThat(claims.exp > verifiedAt && claims.iat <= verifiedAt && (claims.nbf === undefined || claims.nbf <= verifiedAt));
      return claims;
    } catch { fail(); }
  };
}

/** One-shot convenience API; long-lived servers should use createAccessVerifier to retain its bounded cache. */
export async function verifyAccessJwt(token, options) {
  try { return await createAccessVerifier(options)(token); } catch { fail(); }
}
