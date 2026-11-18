// Connection-test sample only. Keep Ollama private; this is not the production Agent.
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const MAX_BODY = 32 * 1024;
const MAX_OUTPUT = 1024 * 1024;
class RequestError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function reply(res, status, data) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(JSON.stringify(data));
}

function authorized(header, token) {
  const received = Buffer.from(typeof header === 'string' ? header : '');
  const expected = Buffer.from(`Bearer ${token}`);
  return received.length === expected.length && timingSafeEqual(received, expected);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let finished = false;
    const chunks = [];
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => {
      req.resume();
      finish(new RequestError(408, 'Request body timeout.'));
    }, 8_000);
    req.on('data', chunk => {
      if (finished) return;
      size += chunk.length;
      if (size > MAX_BODY) {
        chunks.length = 0;
        finish(new RequestError(413, 'Request body too large.'));
      } else chunks.push(chunk);
    });
    req.on('end', () => {
      if (finished) return;
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        finish(null, JSON.parse(text));
      } catch { finish(new RequestError(400, 'Invalid JSON body.')); }
    });
    req.on('aborted', () => finish(new RequestError(400, 'Request interrupted.')));
    req.on('error', () => finish(new RequestError(400, 'Request interrupted.')));
  });
}

function validate(body, model) {
  if (!body || typeof body !== 'object' || Array.isArray(body)
    || Object.keys(body).some(key => !['model', 'messages', 'stream'].includes(key))
    || ('model' in body && body.model !== model)
    || ('stream' in body && body.stream !== false)
    || !Array.isArray(body.messages) || body.messages.length < 1 || body.messages.length > 16
    || body.messages.some(message => !message || typeof message !== 'object'
      || Array.isArray(message)
      || Object.keys(message).some(key => !['role', 'content'].includes(key))
      || !['user', 'assistant'].includes(message.role)
      || typeof message.content !== 'string'
      || message.content.length < 1 || message.content.length > 4000)) {
    throw new RequestError(400, 'Unsupported chat request.');
  }
}

async function readUpstream(response, maxBytes) {
  if (!response.ok || !response.body) {
    await response.body?.cancel();
    throw new RequestError(502, 'Model service unavailable.');
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new RequestError(502, 'Model response too large.');
    }
    chunks.push(Buffer.from(value));
  }
  let data;
  try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new RequestError(502, 'Invalid model response.'); }
  if (data?.error || data?.message?.role !== 'assistant'
    || typeof data.message.content !== 'string' || data.done !== true) {
    throw new RequestError(502, 'Invalid model response.');
  }
  return data.message.content;
}

// Overrides exist only for localhost mock tests; executable mode fixes the upstream.
export function createGateway({ token, model, upstreamOrigin = 'http://127.0.0.1:11434',
  upstreamTimeoutMs = 25_000, maxResponseBytes = MAX_OUTPUT }) {
  if (typeof token !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(token)) {
    throw new Error('OLLAMA_GATEWAY_TOKEN must contain 32–256 non-space ASCII characters.');
  }
  if (typeof model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$/.test(model)) {
    throw new Error('OLLAMA_GATEWAY_MODEL must be an exact installed model tag.');
  }
  const upstream = new URL(upstreamOrigin);
  if (upstream.protocol !== 'http:' || upstream.hostname !== '127.0.0.1'
    || upstream.pathname !== '/' || upstream.search || upstream.hash
    || upstream.username || upstream.password) throw new Error('Upstream must be localhost HTTP.');
  let busy = false;
  const server = http.createServer(async (req, res) => {
    if (!authorized(req.headers.authorization, token)) {
      req.resume();
      return reply(res, 401, { error: 'Unauthorized.' });
    }
    if (req.method === 'GET' && req.url === '/healthz') {
      return reply(res, 200, { ok: true, check: 'gateway_only' });
    }
    if (req.method !== 'POST' || req.url !== '/api/chat') {
      req.resume();
      return reply(res, 404, { error: 'Route not available.' });
    }
    if ((req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') {
      req.resume();
      return reply(res, 415, { error: 'Use application/json.' });
    }
    if (Number(req.headers['content-length'] || 0) > MAX_BODY) {
      req.resume();
      return reply(res, 413, { error: 'Request body too large.' });
    }
    if (busy) {
      req.resume();
      res.setHeader('Retry-After', '1');
      return reply(res, 429, { error: 'Gateway busy. Retry later.' });
    }
    busy = true;
    const controller = new AbortController();
    const disconnect = () => { if (!res.writableEnded) controller.abort(); };
    res.on('close', disconnect);
    let timeout;
    let timedOut = false;
    try {
      const body = await readJson(req);
      validate(body, model);
      timeout = setTimeout(() => { timedOut = true; controller.abort(); }, upstreamTimeoutMs);
      const response = await fetch(new URL('/api/chat', upstream), {
        method: 'POST', redirect: 'error', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ model, messages: body.messages, stream: false,
          options: { num_predict: 256, num_ctx: 4096 } }),
      });
      const content = await readUpstream(response, maxResponseBytes);
      reply(res, 200, { model, message: { role: 'assistant', content }, done: true });
    } catch (error) {
      const status = timedOut ? 504 : error instanceof RequestError ? error.status : 502;
      const message = timedOut ? 'Model service timed out.'
        : error instanceof RequestError ? error.message : 'Model service unavailable.';
      reply(res, status, { error: message });
    } finally {
      clearTimeout(timeout);
      res.off('close', disconnect);
      controller.abort();
      busy = false;
    }
  });
  server.headersTimeout = 5_000;
  server.requestTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  server.setTimeout(35_000, socket => socket.destroy());
  server.on('upgrade', (_req, socket) => socket.destroy());
  server.on('connect', (_req, socket) => socket.destroy());
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (Number(process.versions.node.split('.')[0]) < 20) throw new Error('Node.js 20 or newer is required.');
    const server = createGateway({ token: process.env.OLLAMA_GATEWAY_TOKEN,
      model: process.env.OLLAMA_GATEWAY_MODEL });
    server.on('error', () => { console.error('Gateway could not start; check port 8787.'); process.exitCode = 1; });
    server.listen(8787, '127.0.0.1', () => console.log('Connection-test gateway listening on 127.0.0.1:8787.'));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
