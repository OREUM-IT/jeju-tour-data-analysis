// Run on the user's Ollama host. Creates a local secret; never prints it.
import { randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const model = process.argv[2];
if (!model || process.argv.length !== 3 || !/^[A-Za-z0-9_./:-]{1,160}$/.test(model)) {
  console.error('Usage: node configure.mjs EXACT_INSTALLED_MODEL_TAG');
  process.exit(1);
}
try {
  const response = await fetch('http://127.0.0.1:11434/api/tags', {
    redirect: 'error', signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Ollama did not return a model list.');
  const data = await response.json();
  if (!Array.isArray(data.models) || !data.models.some(item => item.name === model || item.model === model)) {
    throw new Error('Model not installed under this exact tag. Check ollama list first.');
  }
  writeFileSync('.env.local',
    `OLLAMA_GATEWAY_TOKEN=${randomBytes(32).toString('hex')}\nOLLAMA_GATEWAY_MODEL=${model}\n`,
    { flag: 'wx', mode: 0o600 });
  console.log('Created .env.local without printing its secret. Keep it private on this host.');
  console.log('Next: node --env-file=.env.local gateway.mjs');
} catch (error) {
  console.error(error.code === 'EEXIST'
    ? '.env.local already exists; it was not overwritten.'
    : 'Setup failed. Start local Ollama and check the exact installed model tag; no tunnel was opened.');
  process.exitCode = 1;
}
