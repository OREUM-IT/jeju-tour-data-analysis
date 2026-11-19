// Read-only prototype: a static service token is not a public OAuth implementation.
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { loadCatalog } from './catalog.mjs';
import { registerTools } from './tools.mjs';
import { createRuntimeValidator, RecommendationValidationError } from './recommendation-validation.mjs';
import { createGatewayScopes, GatewayScopeError } from './gateway-scopes.mjs';

const MAX_BODY = 128 * 1024;
const DEFAULT_CATALOG = fileURLToPath(new URL('../fixtures/catalog.json', import.meta.url));

class HttpError extends Error {
  constructor(status, message, rpcCode = -32600) {
    super(message); this.status = status; this.rpcCode = rpcCode;
  }
}

function json(res, status, body, headers = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...headers,
  });
  res.end(JSON.stringify(body));
}

function tokenMatches(header, token) {
  const expected = Buffer.from(`Bearer ${token}`);
  const actual = Buffer.from(typeof header === 'string' ? header : '');
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let settled = false;
    const chunks = [];
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => finish(new HttpError(408, 'Request body timeout.')), 8_000);
    req.on('data', chunk => {
      if (settled) return;
      size += chunk.length;
      if (size > MAX_BODY) {
        chunks.length = 0;
        finish(new HttpError(413, 'Request body too large.'));
      } else chunks.push(chunk);
    });
    req.on('end', () => {
      if (settled) return;
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        finish(null, JSON.parse(text));
      } catch { finish(new HttpError(400, 'Invalid JSON body.', -32700)); }
    });
    req.on('aborted', () => finish(new HttpError(400, 'Request interrupted.')));
    req.on('error', () => finish(new HttpError(400, 'Request interrupted.')));
  });
}

export function createMcpHttpServer({ catalog, token, gatewayControlToken, gatewayMaxConcurrent = 2, allowedOrigins = [], now }) {
  if (typeof token !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(token)) {
    throw new Error('MCP_SERVICE_TOKEN requires 32–256 non-space ASCII characters.');
  }
  if (!catalog || !Array.isArray(catalog.places) || !catalog.dataMode || !catalog.dataVersion) {
    throw new Error('A validated catalog is required.');
  }
  if (!Array.isArray(allowedOrigins) || allowedOrigins.some(origin => {
    try {
      const url = new URL(origin);
      return !['http:', 'https:'].includes(url.protocol) || url.origin !== origin;
    } catch { return true; }
  })) throw new Error('Allowed origins must be exact HTTP(S) origins.');
  const origins = new Set(allowedOrigins);
  const validator = catalog.schemaVersion === '2.0' ? createRuntimeValidator({ catalog, now }) : null;
  if (gatewayControlToken !== undefined && (typeof gatewayControlToken !== 'string'
    || !/^[\x21-\x7e]{32,256}$/.test(gatewayControlToken) || gatewayControlToken === token || validator === null)) {
    throw new Error('Gateway control requires a distinct valid token and catalog v2.');
  }
  const scopes = gatewayControlToken === undefined ? null
    : createGatewayScopes({ catalog, now, maxConcurrent: gatewayMaxConcurrent });
  const active = new Set();
  const server = http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    const origin = req.headers.origin;
    if (origin !== undefined && !origins.has(origin)) {
      req.resume();
      return json(res, 403, { error: 'Origin not allowed.' });
    }
    if (req.method === 'GET' && req.url === '/healthz') {
      req.resume();
      return json(res, 200, { ok: true, check: 'liveness_only' });
    }
    if (scopes !== null && req.url.startsWith('/gateway/')) {
      const release = /^\/gateway\/scopes\/(gw-[a-f0-9]{32})$/u.exec(req.url);
      const control = req.url === '/gateway/scopes' || req.url === '/gateway/readyz' || release !== null;
      const execution = ['/gateway/prepare-request', '/gateway/validate-response', '/gateway/tools/call'].includes(req.url);
      if (!control && !execution) { req.resume(); return json(res, 404, { error: { code: 'GATEWAY_ROUTE_UNAVAILABLE' } }); }
      if (!tokenMatches(req.headers.authorization, control ? gatewayControlToken : token)) {
        req.resume(); return json(res, 401, { error: { code: 'GATEWAY_UNAUTHORIZED' } });
      }
      const method = release ? 'DELETE' : req.url === '/gateway/readyz' ? 'GET' : 'POST';
      if (req.method !== method) { req.resume(); return json(res, 405, { error: { code: 'GATEWAY_METHOD_REJECTED' } }, { Allow: method }); }
      try {
        if (release) { req.resume(); return json(res, 200, scopes.release(release[1])); }
        if (req.url === '/gateway/readyz') { req.resume(); return json(res, 200, scopes.ready()); }
        if ((req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') {
          req.resume(); return json(res, 415, { error: { code: 'GATEWAY_CONTENT_TYPE_REJECTED' } });
        }
        if (Number(req.headers['content-length'] || 0) > MAX_BODY) {
          req.resume(); return json(res, 413, { error: { code: 'GATEWAY_REQUEST_TOO_LARGE' } });
        }
        const body = await readBody(req);
        if (res.destroyed) return;
        const handler = req.url === '/gateway/scopes' ? scopes.register
          : req.url === '/gateway/prepare-request' ? scopes.prepare
            : req.url === '/gateway/validate-response' ? scopes.validate : scopes.call;
        return json(res, 200, handler(body));
      } catch (error) {
        return json(res, error instanceof GatewayScopeError || error instanceof HttpError ? error.status : 500,
          { error: { code: error instanceof GatewayScopeError ? error.code
            : error instanceof HttpError ? 'INVALID_HTTP_REQUEST' : 'GATEWAY_SCOPE_FAILED' } });
      }
    }
    const validationRoute = validator !== null && ['/prepare-request', '/validate-response'].includes(req.url);
    if (!['/mcp', '/readyz'].includes(req.url) && !validationRoute) {
      req.resume();
      return json(res, 404, { error: 'Route not available.' });
    }
    if (!tokenMatches(req.headers.authorization, token)) {
      req.resume();
      return json(res, 401, { error: 'Unauthorized.' }, { 'WWW-Authenticate': 'Bearer realm="jeju-mcp"' });
    }
    if (req.url === '/readyz') {
      req.resume();
      return req.method === 'GET'
        ? json(res, 200, { ready: true, dataMode: catalog.dataMode,
          placeCount: catalog.places.length, readOnly: true, productionReady: false,
          ...(validator ? { catalogSchemaVersion: catalog.schemaVersion, dataVersion: catalog.dataVersion } : {}) })
        : json(res, 405, { error: 'Method not allowed.' }, { Allow: 'GET' });
    }
    if (req.method !== 'POST') {
      req.resume();
      return json(res, 405, { error: 'Method not allowed.' }, { Allow: 'POST' });
    }
    if ((req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') {
      req.resume();
      return json(res, 415, { error: 'Use application/json.' });
    }
    if (Number(req.headers['content-length'] || 0) > MAX_BODY) {
      req.resume();
      return json(res, 413, { error: 'Request body too large.' });
    }
    if (validationRoute) {
      try {
        const body = await readBody(req);
        if (res.destroyed) return;
        if (req.url === '/prepare-request') return json(res, 200, validator.prepare(body));
        if (body === null || typeof body !== 'object' || Array.isArray(body)
          || !Object.hasOwn(body, 'request') || !Object.hasOwn(body, 'response')
          || typeof body.expectedCatalogVersion !== 'string'
          || Object.keys(body).some(key => !['request', 'response', 'expectedCatalogVersion'].includes(key))) {
          return json(res, 400, { error: { code: 'INVALID_VALIDATION_ENVELOPE' } });
        }
        return json(res, 200, validator.validate(body));
      } catch (error) {
        if (error instanceof RecommendationValidationError) {
          return json(res, 400, { error: { code: error.code, issues: error.issues } });
        }
        return json(res, error instanceof HttpError ? error.status : 500,
          { error: { code: error instanceof HttpError ? 'INVALID_HTTP_REQUEST' : 'VALIDATION_UNAVAILABLE' } });
      }
    }
    let mcp;
    let cleanup;
    try {
      const body = await readBody(req);
      if (res.destroyed) return;
      // A fresh transport is mandatory for each stateless HTTP request in SDK v1.
      mcp = new McpServer({ name: 'jeju-tour-readonly-prototype', version: '0.1.0' });
      registerTools(mcp, catalog);
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: undefined, enableJsonResponse: true,
      });
      let closed = false;
      cleanup = () => {
        if (closed) return;
        closed = true;
        active.delete(cleanup);
        void mcp.close().catch(() => {});
      };
      active.add(cleanup);
      res.once('close', cleanup);
      await mcp.connect(transport);
      // SDK validates Accept, negotiated protocol headers, and JSON-RPC schemas.
      await transport.handleRequest(req, res, body);
    } catch (error) {
      cleanup?.();
      if (mcp && !cleanup) void mcp.close().catch(() => {});
      if (res.headersSent) { res.destroy(); return; }
      const status = error instanceof HttpError ? error.status : 500;
      const message = error instanceof HttpError ? error.message : 'Internal server error.';
      const code = error instanceof HttpError ? error.rpcCode : -32603;
      json(res, status, { jsonrpc: '2.0', id: null, error: { code, message } });
    }
  });
  server.headersTimeout = 5_000;
  server.requestTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  server.setTimeout(30_000, socket => socket.destroy());
  server.on('upgrade', (_req, socket) => socket.destroy());
  server.on('connect', (_req, socket) => socket.destroy());
  server.on('close', () => { for (const cleanup of active) cleanup(); });
  return server;
}

async function main() {
  if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('Node.js 22 or newer is required.');
  const catalogPath = process.env.MCP_CATALOG_PATH
    ? path.resolve(process.env.MCP_CATALOG_PATH) : DEFAULT_CATALOG;
  const catalog = await loadCatalog(catalogPath);
  const portText = process.env.PORT || '8788';
  if (!/^\d+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) throw new Error('Invalid port.');
  const concurrencyText = process.env.GATEWAY_MAX_CONCURRENT ?? '2';
  if (!/^[1-4]$/u.test(concurrencyText)) throw new Error('Invalid Gateway concurrency.');
  const server = createMcpHttpServer({ catalog, token: process.env.MCP_SERVICE_TOKEN,
    gatewayControlToken: process.env.GATEWAY_CONTROL_TOKEN,
    gatewayMaxConcurrent: Number(concurrencyText),
    allowedOrigins: (process.env.MCP_ALLOWED_ORIGINS || '').split(',').map(x => x.trim()).filter(Boolean) });
  server.on('error', () => {
    console.error('MCP server could not start; check the configured host and port.');
    process.exitCode = 1;
  });
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    const timer = setTimeout(() => server.closeAllConnections(), 5_000);
    timer.unref();
    server.close(() => clearTimeout(timer));
    server.closeIdleConnections();
  };
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
  server.listen(Number(portText), process.env.HOST || '127.0.0.1', () => {
    console.log(`Read-only MCP prototype listening on port ${portText}; dataMode=${catalog.dataMode}; productionReady=false.`);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => {
    console.error('MCP startup failed. Check Node version, service token, catalog, origins, and port configuration.');
    process.exitCode = 1;
  });
}
