import http from 'node:http';
import { randomUUID, createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { verifyRequest, createAccessVerifier } from './auth.mjs';
import { createGatewayState } from './state.mjs';
import { createUpstream } from './upstream.mjs';
import { createJobQueue } from './jobs.mjs';
import { createOvertourismService, createOvertourismUpstream, OVERTOURISM_POLICY } from './overtourism.mjs';

class RequestError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}
const MAX_BODY = 32 * 1024;
// Only the overtourism snapshot (14 regions + about 450 KTO spots) is larger.
const OVERTOURISM_MAX_BODY = 256 * 1024;
const OVERTOURISM_ROUTES = ['/v1/overtourism', '/v1/overtourism/readyz'];
// Includes body and Access authentication. Scope revocation gets a separate 3s
// cleanup window; even with the 5s header limit this fits the 120s caller budget.
const DEADLINE_MS = 105_000;
const allowedFields = new Set(['schemaVersion', 'query', 'timezone', 'visitDate', 'filters', 'excludedIds', 'requirements']);
const codes = new Set(['AUTH_FAILED', 'REPLAY_DETECTED', 'RATE_LIMITED', 'BUSY', 'STATE_UNAVAILABLE',
  'INVALID_RECOMMENDATION_REQUEST', 'BODY_TOO_LARGE', 'INVALID_JSON', 'INVALID_HEADERS', 'BODY_TIMEOUT',
  'METHOD_NOT_ALLOWED', 'NOT_FOUND', 'ORIGIN_REJECTED', 'UNSUPPORTED_MEDIA_TYPE', 'ACCESS_REQUIRED',
  'QUEUE_FULL', 'JOB_EXPIRED', 'JOB_CANCELLED', 'JOB_NOT_FOUND', 'JOB_ALREADY_RUNNING',
  'FLOW_CHANGED', 'MCP_NOT_READY', 'RECOMMENDATION_BLOCKED', 'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES', 'SCOPE_CLEANUP_FAILED',
  'OVERTOURISM_UNAVAILABLE', 'INVALID_OVERTOURISM_REQUEST', 'OVERTOURISM_FLOW_CHANGED', 'OVERTOURISM_MCP_NOT_READY',
  'DIAGNOSIS_BLOCKED', 'OVERTOURISM_RESULT_MISMATCH']);
const jobRoute = url => /^\/v1\/jobs\/(gw-[a-f0-9]{32})$/u.exec(url ?? '');
const protectedRoute = (method, url) => (method === 'GET' && ['/readyz', '/v1/overtourism/readyz'].includes(url))
  || (method === 'POST' && ['/v1/recommend', '/v1/jobs', '/v1/overtourism'].includes(url))
  || (['GET', 'DELETE'].includes(method) && jobRoute(url) !== null);
function send(res, status, value, extra = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', ...extra });
  res.end(JSON.stringify(value));
}
function readBody(req, limit = MAX_BODY) {
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
      if (size > limit) { chunks.length = 0; finish(new RequestError('BODY_TOO_LARGE', 413)); }
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
    const protectedAccessRoute = mode === 'access' && protectedRoute(req.method, req.url);
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

export function createGatewayServer({ authKey, mode = 'private', publicHost, accessVerifier, state, upstream, now = Date.now,
  maxQueue = state?.policy?.maxQueue ?? 8, overtourism = null }) {
  if (!['private', 'access'].includes(mode) || typeof authKey !== 'string' || !/^[a-f0-9]{64}$/.test(authKey)
    || (mode === 'access' && (typeof accessVerifier !== 'function' || !/^[a-z0-9.-]+$/.test(publicHost)))) throw new Error('GATEWAY_CONFIG_INVALID');
  const jobs = createJobQueue({ state, upstream, now, deadlineMs: DEADLINE_MS, maxQueue });
  const server = http.createServer(async (req, res) => {
    let requestId, reserved = false, submitted = false, settled = false;
    const deadlineMs = now() + DEADLINE_MS;
    const disconnect = new AbortController();
    const signal = AbortSignal.any([disconnect.signal, AbortSignal.timeout(DEADLINE_MS)]);
    res.on('close', () => { if (!res.writableEnded) disconnect.abort(); });
    try {
      headersValid(req, mode);
      if (req.method === 'GET' && req.url === '/healthz') { req.resume(); return send(res, 200, { ok: true, check: 'liveness_only' }); }
      const job = jobRoute(req.url);
      if (!['/readyz', '/v1/recommend', '/v1/jobs', ...OVERTOURISM_ROUTES].includes(req.url) && !job) throw new RequestError('NOT_FOUND', 404);
      const methods = job ? ['GET', 'DELETE'] : ['/readyz', '/v1/overtourism/readyz'].includes(req.url) ? ['GET'] : ['POST'];
      if (!methods.includes(req.method)) throw new RequestError('METHOD_NOT_ALLOWED', 405);
      const method = req.method;
      const limit = req.url === '/v1/overtourism' ? OVERTOURISM_MAX_BODY : MAX_BODY;
      if (Number(req.headers['content-length'] || 0) > limit) throw new RequestError('BODY_TOO_LARGE', 413);
      if (method === 'POST' && (req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') throw new RequestError('UNSUPPORTED_MEDIA_TYPE', 415);
      const body = await readBody(req, limit);
      if (method !== 'POST' && body.length) throw new RequestError('INVALID_JSON', 400);
      const auth = verifyRequest({ headers: req.headers, method, path: req.url, body, key: authKey, now: now() });
      if (mode === 'access') {
        if (req.headers.host !== publicHost) throw new RequestError('ACCESS_REQUIRED', 401);
        await accessVerifier(req.headers['cf-access-jwt-assertion']);
      }
      if (req.url === '/readyz') return send(res, 200, { ...await upstream.ready(signal), mode,
        asyncJobs: true, maxConcurrent: state.policy.maxConcurrent, jobs: jobs.ready() });
      if (OVERTOURISM_ROUTES.includes(req.url)) {
        if (!overtourism) throw new RequestError('OVERTOURISM_UNAVAILABLE', 503);
        if (req.url === '/v1/overtourism/readyz') return send(res, 200, { ...await overtourism.ready(signal), mode });
        let snapshot;
        try { snapshot = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
        catch { throw new RequestError('INVALID_JSON', 400); }
        // Synchronous, but unlike /v1/recommend a client disconnect does not abort
        // the run: the result is public and shared, so the next call for the same
        // snapshot reuses it. Only the fixed deadline bounds the run.
        return send(res, 200, await overtourism.diagnose({ subject: auth.subject, nonce: auth.nonce,
          input: snapshot, deadlineMs, signal: AbortSignal.timeout(Math.max(1, deadlineMs - now())) }));
      }
      if (job) {
        const result = method === 'GET' ? jobs.get({ subject: auth.subject, requestId: job[1] })
          : jobs.cancel({ subject: auth.subject, requestId: job[1] });
        return send(res, result.statusCode, result.body, result.statusCode === 429 ? { 'Retry-After': '120' } : {});
      }
      let input;
      try { input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
      catch { throw new RequestError('INVALID_JSON', 400); }
      if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(k => !allowedFields.has(k))
        || input.schemaVersion !== '1.0' || input.timezone !== 'Asia/Seoul' || typeof input.query !== 'string'
        || !input.query.trim() || input.query.length > 4000 || !Array.isArray(input.requirements) || !Array.isArray(input.excludedIds)
        || !input.filters || typeof input.filters !== 'object' || Array.isArray(input.filters)) throw new RequestError('INVALID_RECOMMENDATION_REQUEST', 400);
      if (req.url === '/v1/jobs') {
        // A disconnect after acceptance does not cancel or duplicate the worker.
        // Subsequent authenticated polls only read the owner-bound in-memory job.
        const result = jobs.submit({ subject: auth.subject, nonce: auth.nonce, input });
        return send(res, result.statusCode, result.body);
      }
      requestId = 'gw-' + randomUUID().replaceAll('-', '');
      const started = now();
      state.reserve({ subject: auth.subject, nonce: auth.nonce, requestId, now: started });
      reserved = true;
      const result = await upstream.execute({ ...input, requestId }, {
        subjectHash: createHash('sha256').update(auth.subject).digest('hex'), deadlineMs,
        signal, onSubmitted: () => { submitted = true; }, onSettled: () => { settled = true; },
      });
      state.finish({ requestId, now: now() }); reserved = false;
      send(res, 200, result);
    } catch (error) {
      if (reserved) {
        try { state.finish({ requestId, now: now(), uncertain: submitted && !settled }); }
        catch { error = new RequestError('STATE_UNAVAILABLE', 503); }
      }
      req.resume();
      const timeout = ['TimeoutError', 'AbortError'].includes(error?.name);
      const code = timeout ? 'UPSTREAM_TIMEOUT_NO_RETRY' : codes.has(error?.code) ? error.code : 'GATEWAY_UNAVAILABLE';
      const status = timeout ? 504 : Number.isInteger(error?.status) && error.status >= 400 && error.status <= 599 ? error.status : 502;
      const diagnostic = code === 'ORIGIN_REJECTED' && error instanceof RequestError
        ? { headerContext: error.headerContext } : {};
      const retryAfter = Number.isInteger(error?.retryAfterSeconds) && error.retryAfterSeconds >= 1
        && error.retryAfterSeconds <= 3600 ? error.retryAfterSeconds : 120;
      send(res, status, { error: { code, ...diagnostic }, ...(requestId ? { requestId } : {}) }, status === 429 ? { 'Retry-After': String(retryAfter) } : {});
    }
  });
  server.headersTimeout = 5000; server.requestTimeout = 8000; server.keepAliveTimeout = 3000;
  server.setTimeout(120_000, socket => socket.destroy());
  server.maxConnections = 32;
  server.on('upgrade', (_req, socket) => socket.destroy()); server.on('connect', (_req, socket) => socket.destroy());
  server.closeJobs = () => jobs.close();
  return server;
}

export function gatewayPolicyFromEnv(env) {
  const integer = (name, fallback, min, max) => {
    const raw = env[name];
    if (raw === undefined || raw === '') return fallback;
    if (!/^\d+$/u.test(raw)) throw new Error('GATEWAY_CONFIG_INVALID');
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error('GATEWAY_CONFIG_INVALID');
    return value;
  };
  const policy = { maxConcurrent: integer('GATEWAY_MAX_CONCURRENT', 2, 1, 4),
    globalLimit: integer('GATEWAY_GLOBAL_HOURLY_LIMIT', 60, 1, 240),
    subjectLimit: integer('GATEWAY_SUBJECT_HOURLY_LIMIT', 12, 1, 48),
    maxQueue: integer('GATEWAY_MAX_QUEUE', 8, 0, 8) };
  if (policy.subjectLimit > policy.globalLimit) throw new Error('GATEWAY_CONFIG_INVALID');
  return policy;
}

/**
 * Optional overtourism route from .local/gateway-overtourism.env. Missing or
 * invalid settings disable only this route; recommendations keep starting.
 */
export function overtourismFromEnv(env, { state, fetchImpl } = {}) {
  if (!env.LANGFLOW_OVERTOURISM_FLOW_ID && !env.LANGFLOW_OVERTOURISM_FLOW_SHA256) return { status: 'not_configured', service: null };
  try {
    const raw = env.GATEWAY_OVERTOURISM_DAILY_LIMIT;
    if (raw !== undefined && raw !== '' && !/^\d{1,2}$/u.test(raw)) throw new Error('GATEWAY_CONFIG_INVALID');
    const dailyLimit = raw === undefined || raw === '' ? OVERTOURISM_POLICY.dailyLimit : Number(raw);
    const upstream = createOvertourismUpstream({ flowId: env.LANGFLOW_OVERTOURISM_FLOW_ID,
      flowSha256: env.LANGFLOW_OVERTOURISM_FLOW_SHA256, langflowApiKey: env.LANGFLOW_GATEWAY_API_KEY,
      mcpToken: env.MCP_SERVICE_TOKEN, controlToken: env.GATEWAY_CONTROL_TOKEN, ...(fetchImpl ? { fetchImpl } : {}) });
    return { status: 'configured', service: createOvertourismService({ state, upstream, dailyLimit }) };
  } catch {
    return { status: 'disabled_invalid_config', service: null };
  }
}

function main() {
  const env = process.env;
  const mode = env.GATEWAY_MODE || 'private';
  const policy = gatewayPolicyFromEnv(env);
  const state = createGatewayState({ filename: env.GATEWAY_STATE_PATH || '/app/state/gateway.sqlite', ...policy });
  const upstream = createUpstream({ flowId: env.LANGFLOW_GATEWAY_FLOW_ID, flowSha256: env.LANGFLOW_GATEWAY_FLOW_SHA256,
    langflowApiKey: env.LANGFLOW_GATEWAY_API_KEY, mcpToken: env.MCP_SERVICE_TOKEN, controlToken: env.GATEWAY_CONTROL_TOKEN,
    expectedMaxConcurrent: policy.maxConcurrent });
  const overtourism = overtourismFromEnv(env, { state });
  const server = createGatewayServer({ authKey: env.GATEWAY_AUTH_KEY, mode, publicHost: env.GATEWAY_PUBLIC_HOST,
    accessVerifier: mode === 'access' ? createAccessVerifier({ issuer: env.GATEWAY_ACCESS_ISSUER, audience: env.GATEWAY_ACCESS_AUDIENCE }) : undefined,
    state, upstream, maxQueue: policy.maxQueue, overtourism: overtourism.service });
  server.listen(8790, '0.0.0.0', () => console.log(JSON.stringify({ status: 'listening', port: 8790, mode,
    overtourism: overtourism.status, productionReady: false })));
  const stop = () => {
    const workers = Promise.resolve().then(() => server.closeJobs());
    server.close(() => { void workers.catch(() => { process.exitCode = 1; }).finally(() => state.close()); });
    server.closeIdleConnections();
  };
  process.once('SIGTERM', stop); process.once('SIGINT', stop);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { main(); } catch { console.error('GATEWAY_STARTUP_FAILED'); process.exitCode = 1; }
}
