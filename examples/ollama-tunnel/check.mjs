// Connection check for your own local host or your own Quick Tunnel URL.
const token = process.env.OLLAMA_GATEWAY_TOKEN;
const model = process.env.OLLAMA_GATEWAY_MODEL;
if (!token || token.length < 32 || !model) {
  console.error('Run with: node --env-file=.env.local check.mjs [YOUR_QUICK_TUNNEL_URL]');
  process.exit(1);
}
let base;
try {
  base = new URL(process.argv[2] || 'http://127.0.0.1:8787');
  const local = base.origin === 'http://127.0.0.1:8787';
  const quick = base.protocol === 'https:' && base.hostname.endsWith('.trycloudflare.com') && !base.port;
  if ((!local && !quick) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) {
    throw new Error('Invalid URL');
  }
} catch {
  console.error('Use the local default or the HTTPS origin of a Quick Tunnel you created yourself.');
  process.exit(1);
}
const call = (path, options = {}) => fetch(new URL(path, base), {
  ...options, redirect: 'error', signal: AbortSignal.timeout(30000),
});
try {
  const denied = await call('/healthz');
  await denied.body?.cancel();
  if (denied.status !== 401) throw new Error('Unauthorized health request was not rejected with 401.');
  const auth = { Authorization: `Bearer ${token}` };
  const health = await call('/healthz', { headers: auth });
  await health.body?.cancel();
  if (health.status !== 200) throw new Error(`Gateway health failed (${health.status}).`);
  const blocked = await call('/api/pull', { method: 'POST', headers: auth });
  await blocked.body?.cancel();
  if (blocked.status !== 404) throw new Error('Model-management route was not rejected with 404.');
  const chat = await call('/api/chat', {
    method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, stream: false, messages: [{ role: 'user', content: '안녕이라고 한 단어로 답해줘.' }] }),
  });
  if (!chat.ok) {
    await chat.body?.cancel();
    throw new Error(`Short inference failed (${chat.status}). Warm the model locally and retry manually.`);
  }
  const result = await chat.json();
  if (result.done !== true || typeof result.message?.content !== 'string' || !result.message.content.trim()) {
    throw new Error('Inference response did not contain a completed nonempty message.');
  }
  console.log('PASS: authentication, gateway health, blocked model management, and one short inference.');
  console.log('This is a connection check, not production readiness or recommendation-quality validation.');
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Connection check failed.');
  process.exitCode = 1;
}
