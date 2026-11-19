import http from 'node:http';
import { randomUUID, createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { verifyRequest, createAccessVerifier } from './auth.mjs';
import { createGatewayState } from './state.mjs';
import { createUpstream } from './upstream.mjs';

class RequestError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}
const MAX_BODY = 32 * 1024;
// Includes body and Access authentication. Scope revocation gets a separate 3s
// cleanup window; even with the 5s header limit this fits the 120s caller budget.
const DEADLINE_MS = 105_000;
const allowedFields = new Set(['schemaVersion', 'query', 'timezone', 'visitDate', 'filters', 'excludedIds', 'requirements']);
const codes = new Set(['AUTH_FAILED', 'REPLAY_DETECTED', 'RATE_LIMITED', 'BUSY', 'STATE_UNAVAILABLE',
  'INVALID_RECOMMENDATION_REQUEST', 'BODY_TOO_LARGE', 'INVALID_JSON', 'INVALID_HEADERS', 'BODY_TIMEOUT',
  'METHOD_NOT_ALLOWED', 'NOT_FOUND', 'ORIGIN_REJECTED', 'UNSUPPORTED_MEDIA_TYPE', 'ACCESS_REQUIRED',
  'FLOW_CHANGED', 'MCP_NOT_READY', 'RECOMMENDATION_BLOCKED', 'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES', 'SCOPE_CLEANUP_FAILED']);
function send(res, status, value, extra = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', ...extra });
  res.end(JSON.stringify(value));
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0, done = false; const chunks = [];
    const finish = (error, value) => {
      if (done) return; done = true; clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => finish(new RequestError('BODY_TIMEOUT', 408)), 5000);
    req.on('data', chunk => {
      if (done) return;
      size += chunk.length;
      if (size > MAX_BODY) { chunks.length = 0; finish(new RequestError('BODY_TOO_LARGE', 413)); }
      else chunks.push(chunk);
    });
    req.on('end', () => finish(null, Buffer.concat(chunks)));
    req.on('aborted', () => finish(new RequestError('INVALID_JSON', 400)));
    req.on('error', () => finish(new RequestError('INVALID_JSON', 400)));
  });
}
function headersValid(req, mode) {
  const seen = new Set();
  for (let n = 0; n < req.rawHeaders.length; n += 2) {
    const key = req.rawHeaders[n].toLowerCase();
    if (['host', 'content-type', 'content-length', 'cookie', 'cf-access-jwt-assertion'].includes(key) || key.startsWith('x-jeju-')) {
      if (seen.has(key)) throw new RequestError('INVALID_HEADERS', 400);
      seen.add(key);
    }
  }
  if (req.headers.origin !== undefined || req.headers.cookie !== undefined) {
    const cookie = req.headers.cookie;
    const assertion = req.headers['cf-access-jwt-assertion'];
    const single = typeof assertion === 'string' && assertion.length > 0 && typeof cookie === 'string'
      && /^CF_Authorization=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u.test(cookie);
    const matchesAssertion = single && cookie === `CF_Authorization=${assertion}`;
    const protectedAccessRoute = mode === 'access'
      && ((req.method === 'GET' && req.url === '/readyz') || (req.method === 'POST' && req.url === '/v1/recommend'));
    // Cloudflare forwards this exact redundant cookie in the observed service-auth
    // request. Passing the header filter still requires HMAC, exact Host, and full
    // Access JWT verification below; cookies never supply missing authentication.
    if (req.headers.origin === undefined && protectedAccessRoute && matchesAssertion) return;
    // Never retain the header values on the error or include them in a response.
    const error = new RequestError('ORIGIN_REJECTED', 403);
    error.headerContext = {
      originPresent: req.headers.origin !== undefined, cookiePresent: cookie !== undefined,
      cookieEmpty: cookie === '', singleCfAuthorizationCookie: single,
      cookieMatchesAssertion: matchesAssertion,
    };
    throw error;
  }
}

export function createGatewayServer({ authKey, mode = 'private', publicHost, accessVerifier, state, upstream, now = Date.now }) {
  if (!['private', 'access'].includes(mode) || typeof authKey !== 'string' || !/^[a-f0-9]{64}$/.test(authKey)
    || (mode === 'access' && (typeof accessVerifier !== 'function' || !/^[a-z0-9.-]+$/.test(publicHost)))) throw new Error('GATEWAY_CONFIG_INVALID');
  const server = http.createServer(async (req, res) => {
    let requestId, reserved = false, submitted = false, complete = false;
    const deadlineMs = now() + DEADLINE_MS;
    const disconnect = new AbortController();
    const signal = AbortSignal.any([disconnect.signal, AbortSignal.timeout(DEADLINE_MS)]);
    res.on('close', () => { if (!res.writableEnded) disconnect.abort(); });
    try {
      headersValid(req, mode);
      if (req.method === 'GET' && req.url === '/healthz') { req.resume(); return send(res, 200, { ok: true, check: 'liveness_only' }); }
      if (!['/readyz', '/v1/recommend'].includes(req.url)) throw new RequestError('NOT_FOUND', 404);
      const method = req.url === '/readyz' ? 'GET' : 'POST';
      if (req.method !== method) throw new RequestError('METHOD_NOT_ALLOWED', 405);
      if (Number(req.headers['content-length'] || 0) > MAX_BODY) throw new RequestError('BODY_TOO_LARGE', 413);
      if (method === 'POST' && (req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') throw new RequestError('UNSUPPORTED_MEDIA_TYPE', 415);
      const body = await readBody(req);
      if (method === 'GET' && body.length) throw new RequestError('INVALID_JSON', 400);
      const auth = verifyRequest({ headers: req.headers, method, path: req.url, body, key: authKey, now: now() });
      if (mode === 'access') {
        if (req.headers.host !== publicHost) throw new RequestError('ACCESS_REQUIRED', 401);
        await accessVerifier(req.headers['cf-access-jwt-assertion']);
      }
      if (method === 'GET') return send(res, 200, { ...await upstream.ready(signal), mode });
      let input;
      try { input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
      catch { throw new RequestError('INVALID_JSON', 400); }
      if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(k => !allowedFields.has(k))
        || input.schemaVersion !== '1.0' || input.timezone !== 'Asia/Seoul' || typeof input.query !== 'string'
        || !input.query.trim() || input.query.length > 4000 || !Array.isArray(input.requirements) || !Array.isArray(input.excludedIds)
        || !input.filters || typeof input.filters !== 'object' || Array.isArray(input.filters)) throw new RequestError('INVALID_RECOMMENDATION_REQUEST', 400);
      requestId = 'gw-' + randomUUID().replaceAll('-', '');
      const started = now();
      state.reserve({ subject: auth.subject, nonce: auth.nonce, requestId, now: started });
      reserved = true;
      const result = await upstream.execute({ ...input, requestId }, {
        subjectHash: createHash('sha256').update(auth.subject).digest('hex'), deadlineMs,
        signal, onSubmitted: () => { submitted = true; },
      });
      complete = true;
      state.finish({ requestId, now: now() }); reserved = false;
      send(res, 200, result);
    } catch (error) {
      if (reserved) {
        try { state.finish({ requestId, now: now(), uncertain: submitted && !complete }); }
        catch { error = new RequestError('STATE_UNAVAILABLE', 503); }
      }
      req.resume();
      const timeout = ['TimeoutError', 'AbortError'].includes(error?.name);
      const code = timeout ? 'UPSTREAM_TIMEOUT_NO_RETRY' : codes.has(error?.code) ? error.code : 'GATEWAY_UNAVAILABLE';
      const status = timeout ? 504 : Number.isInteger(error?.status) && error.status >= 400 && error.status <= 599 ? error.status : 502;
      const diagnostic = code === 'ORIGIN_REJECTED' && error instanceof RequestError
        ? { headerContext: error.headerContext } : {};
      send(res, status, { error: { code, ...diagnostic }, ...(requestId ? { requestId } : {}) }, status === 429 ? { 'Retry-After': '120' } : {});
    }
  });
  server.headersTimeout = 5000; server.requestTimeout = 8000; server.keepAliveTimeout = 3000;
  server.setTimeout(120_000, socket => socket.destroy());
  server.maxConnections = 32;
  server.on('upgrade', (_req, socket) => socket.destroy()); server.on('connect', (_req, socket) => socket.destroy());
  return server;
}

function main() {
  const env = process.env;
  const mode = env.GATEWAY_MODE || 'private';
  const state = createGatewayState({ filename: env.GATEWAY_STATE_PATH || '/app/state/gateway.sqlite' });
  const upstream = createUpstream({ flowId: env.LANGFLOW_GATEWAY_FLOW_ID, flowSha256: env.LANGFLOW_GATEWAY_FLOW_SHA256,
    langflowApiKey: env.LANGFLOW_GATEWAY_API_KEY, mcpToken: env.MCP_SERVICE_TOKEN, controlToken: env.GATEWAY_CONTROL_TOKEN });
  const server = createGatewayServer({ authKey: env.GATEWAY_AUTH_KEY, mode, publicHost: env.GATEWAY_PUBLIC_HOST,
    accessVerifier: mode === 'access' ? createAccessVerifier({ issuer: env.GATEWAY_ACCESS_ISSUER, audience: env.GATEWAY_ACCESS_AUDIENCE }) : undefined,
    state, upstream });
  server.listen(8790, '0.0.0.0', () => console.log(JSON.stringify({ status: 'listening', port: 8790, mode, productionReady: false })));
  const stop = () => { server.close(() => { state.close(); }); server.closeIdleConnections(); };
  process.once('SIGTERM', stop); process.once('SIGINT', stop);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { main(); } catch { console.error('GATEWAY_STARTUP_FAILED'); process.exitCode = 1; }
}
