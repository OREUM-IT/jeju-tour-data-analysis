import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';

const FINAL_OUTPUT = 'ChatOutput-jejuV2';
const MAX_RESPONSE = 2 * 1024 * 1024;
const binding = ['requestSha256', 'catalogSha256', 'catalogVersion', 'policyVersion', 'candidateCount'];
export class UpstreamError extends Error {
  constructor(code, status = 502) { super(code); this.code = code; this.status = status; }
}
const require = (ok, code, status) => { if (!ok) throw new UpstreamError(code, status); };
export const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value !== null && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`
    : JSON.stringify(value);
export const digest = value => createHash('sha256').update(canonical(value)).digest('hex');

export async function boundedJson(response, limit = MAX_RESPONSE) {
  const chunks = []; let size = 0;
  if (Number(response.headers.get('content-length') || 0) > limit) {
    await response.body?.cancel();
    throw new UpstreamError('UPSTREAM_TOO_LARGE');
  }
  if (!response.body) throw new UpstreamError('UPSTREAM_INVALID_JSON');
  try {
    for await (const chunk of response.body) {
      size += chunk.length;
      require(size <= limit, 'UPSTREAM_TOO_LARGE');
      chunks.push(chunk);
    }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
  } catch (error) {
    if (['AbortError', 'TimeoutError'].includes(error?.name)) throw error;
    if (error instanceof UpstreamError) throw error;
    throw new UpstreamError('UPSTREAM_INVALID_JSON');
  }
}

export function selectFinal(raw, requestId) {
  require(raw?.session_id === requestId, 'SESSION_MISMATCH');
  require(Array.isArray(raw.outputs) && raw.outputs.length === 1, 'OUTPUT_MISMATCH');
  const outputs = raw.outputs[0]?.outputs;
  require(Array.isArray(outputs), 'OUTPUT_MISMATCH');
  const selected = outputs.filter(x => x.component_id === FINAL_OUTPUT);
  require(selected.length === 1, 'OUTPUT_MISMATCH');
  const message = selected[0]?.results?.message;
  require(message?.sender === 'Machine' && message.properties?.source?.id === 'JejuFinalValidation-v2'
    && message.properties?.source?.source === 'Jeju Final Validation', 'VALIDATOR_SOURCE_MISMATCH');
  require(typeof message.text === 'string' && Buffer.byteLength(message.text) <= 256 * 1024, 'FINAL_INVALID');
  let value;
  try { value = JSON.parse(message.text); } catch { throw new UpstreamError('FINAL_INVALID'); }
  require(value?.status !== 'blocked', 'RECOMMENDATION_BLOCKED', 422);
  require(value?.requestId === requestId, 'REQUEST_MISMATCH');
  return value;
}

export function createUpstream({ flowId, flowSha256, langflowApiKey, mcpToken, controlToken,
  langflowUrl = 'http://langflow:7860', mcpUrl = 'http://jeju-mcp:8788', fetchImpl = fetch }) {
  require(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(flowId)
    && /^[a-f0-9]{64}$/.test(flowSha256) && /^sk-[A-Za-z0-9_-]{10,256}$/.test(langflowApiKey)
    && /^[a-f0-9]{64}$/.test(controlToken) && /^[\x21-\x7e]{32,256}$/.test(mcpToken)
    && controlToken !== mcpToken, 'CONFIG_INVALID', 503);
  // URLs are trusted deployment dependencies, never request fields. The CLI uses fixed Docker names.
  for (const value of [langflowUrl, mcpUrl]) {
    const url = new URL(value);
    require(url.protocol === 'http:' && !url.username && !url.password && url.pathname === '/'
      && !url.search && !url.hash && ['langflow', 'jeju-mcp', '127.0.0.1', 'localhost'].includes(url.hostname), 'CONFIG_INVALID', 503);
  }
  const mcpAuth = { Authorization: `Bearer ${mcpToken}` };
  const controlAuth = { Authorization: `Bearer ${controlToken}` };
  async function call(base, route, { method = 'GET', headers, body, signal, timeout = 5000 } = {}) {
    const response = await fetchImpl(base + route, { method, redirect: 'error',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
      headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!response.ok) {
      await response.body?.cancel();
      if (base === mcpUrl && response.status === 400) throw new UpstreamError('INVALID_RECOMMENDATION_REQUEST', 400);
      if (base === mcpUrl && response.status === 429) throw new UpstreamError('BUSY', 429);
      throw new UpstreamError('UPSTREAM_REJECTED');
    }
    return boundedJson(response);
  }
  async function ready(signal) {
    const [flow, mcp] = await Promise.all([
      call(langflowUrl, `/api/v1/flows/${flowId}`, { headers: { 'x-api-key': langflowApiKey }, signal }),
      call(mcpUrl, '/gateway/readyz', { headers: controlAuth, signal }),
    ]);
    require(flow.id === flowId && digest(flow.data) === flowSha256, 'FLOW_CHANGED', 503);
    require(mcp.ready === true && mcp.catalogSchemaVersion === '2.0' && mcp.dataMode === 'app-export'
      && mcp.placeCount === 981 && mcp.toolCallLimit === 8 && mcp.productionReady === false
      && /^sha256:[a-f0-9]{64}$/.test(mcp.dataVersion), 'MCP_NOT_READY', 503);
    return { ready: true, flowVerified: true, catalogPlaces: 981, productionReady: false };
  }
  async function execute(request, { subjectHash, deadlineMs, signal, onSubmitted = () => {} }) {
    let registered = false;
    try {
      await ready(signal);
      const prepared = await call(mcpUrl, '/gateway/scopes', { method: 'POST', headers: controlAuth,
        body: { request, principal: subjectHash, deadlineMs }, signal });
      registered = true;
      require(prepared.status === 'prepared' && isDeepStrictEqual(prepared.request, request), 'PREPARATION_MISMATCH');
      require(binding.slice(0, 2).every(k => /^[a-f0-9]{64}$/.test(prepared[k]))
        && prepared.policyVersion === 'recommendation-validation-v1'
        && /^sha256:[a-f0-9]{64}$/.test(prepared.catalogVersion), 'PREPARATION_MISMATCH');
      onSubmitted();
      const raw = await call(langflowUrl, `/api/v1/run/${flowId}?stream=false`, {
        method: 'POST', headers: { 'x-api-key': langflowApiKey }, signal, timeout: 100_000,
        body: { input_value: JSON.stringify(request), input_type: 'chat', output_type: 'chat',
          output_component: FINAL_OUTPUT, session_id: request.requestId },
      });
      const response = selectFinal(raw, request.requestId);
      const checked = await call(mcpUrl, '/gateway/validate-response', { method: 'POST', headers: mcpAuth, signal,
        body: { request, response, expectedCatalogVersion: prepared.catalogVersion } });
      require(checked.status === 'accepted' && Array.isArray(checked.reasons) && checked.reasons.length === 0
        && isDeepStrictEqual(checked.response, response) && binding.every(k => checked[k] === prepared[k]), 'FINAL_VALIDATION_REJECTED', 422);
      require(response.placeIds.length <= 5, 'TOO_MANY_PLACES', 422);
      return { schemaVersion: '1.0', status: 'accepted', requestId: request.requestId,
        response, catalogVersion: prepared.catalogVersion, policyVersion: prepared.policyVersion, productionReady: false };
    } finally {
      // Revoke even after disconnect/timeout; do not reuse the aborted request signal.
      if (registered) await call(mcpUrl, `/gateway/scopes/${request.requestId}`,
        { method: 'DELETE', headers: controlAuth, timeout: 3000 });
    }
  }
  return { ready, execute };
}
