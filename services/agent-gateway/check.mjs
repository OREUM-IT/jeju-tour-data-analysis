// Administrative CLI only: no HTTP authentication bypass and no inference by default.
import { randomBytes } from 'node:crypto';
import { signRequest, createAccessVerifier } from './auth.mjs';
import { createUpstream, boundedJson } from './upstream.mjs';
import { gatewayPolicyFromEnv } from './server.mjs';

const env = process.env;
const require = condition => { if (!condition) throw new Error('CHECK_FAILED'); };
try {
  require(process.argv.slice(2).every(x => ['--internal', '--run-once'].includes(x)));
  const mode = env.GATEWAY_MODE || 'private';
  require(['private', 'access'].includes(mode));
  if (mode === 'access') {
    createAccessVerifier({ issuer: env.GATEWAY_ACCESS_ISSUER, audience: env.GATEWAY_ACCESS_AUDIENCE });
    require(/^[a-z0-9.-]+$/.test(env.GATEWAY_PUBLIC_HOST));
  }
  const upstream = createUpstream({ flowId: env.LANGFLOW_GATEWAY_FLOW_ID, flowSha256: env.LANGFLOW_GATEWAY_FLOW_SHA256,
    langflowApiKey: env.LANGFLOW_GATEWAY_API_KEY, mcpToken: env.MCP_SERVICE_TOKEN, controlToken: env.GATEWAY_CONTROL_TOKEN,
    expectedMaxConcurrent: gatewayPolicyFromEnv(env).maxConcurrent });
  const ready = await upstream.ready();
  const base = 'http://127.0.0.1:8790';
  for (const [path, status] of [['/readyz', 401], ['/api/v1/flows/', 404]]) {
    const response = await fetch(base + path, { redirect: 'error', signal: AbortSignal.timeout(3000) });
    require(response.status === status); await response.body?.cancel();
  }
  async function request(method, path, input) {
    const body = input ? Buffer.from(JSON.stringify(input)) : Buffer.alloc(0);
    const headers = signRequest({ key: env.GATEWAY_AUTH_KEY, method, path, body, subject: 'operator-check',
      timestamp: Math.floor(Date.now() / 1000), nonce: randomBytes(16).toString('hex') });
    const response = await fetch(base + path, { method, redirect: 'error', headers: { ...headers,
      ...(input ? { 'Content-Type': 'application/json' } : {}) }, ...(input ? { body } : {}), signal: AbortSignal.timeout(115_000) });
    require(response.ok); return boundedJson(response);
  }
  if (mode === 'private') require((await request('GET', '/readyz')).ready === true);
  if (process.argv.includes('--run-once')) {
    require(mode === 'private');
    const result = await request('POST', '/v1/recommend', { schemaVersion: '1.0', timezone: 'Asia/Seoul',
      query: '실내 방문 조건이 필요한 제주 장소 후보를 최대 3곳 찾아주세요. 미확인 조건은 확인 필요로 안내해 주세요.',
      filters: {}, excludedIds: [], requirements: [{ type: 'indoor_required', value: true }] });
    require(result.status === 'accepted' && result.response.action === 'cannot_confirm'
      && result.response.confirmationNotice === true && result.response.noticeTopics.includes('indoor_unverified'));
    console.log(JSON.stringify({ status: 'passed', mode, requestId: result.requestId, finalValidationAccepted: true,
      action: result.response.action, placeCount: result.response.placeIds.length, productionReady: false }));
  } else console.log(JSON.stringify({ status: 'passed', mode, ...ready, publicAccessVerified: false, modelExecuted: false }));
} catch {
  console.error(JSON.stringify({ status: 'failed', code: 'GATEWAY_CHECK_FAILED', automaticRetry: false }));
  process.exitCode = 1;
}
