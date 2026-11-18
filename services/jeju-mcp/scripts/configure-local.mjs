// Create a private local service credential. Does not configure Cloudflare or Vercel.
import { randomBytes } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { loadCatalog } from '../src/catalog.mjs';

try {
  const { values } = parseArgs({ options: { catalog: { type: 'string' } } });
  let catalogLine = '';
  if (values.catalog) {
    const file = path.resolve(values.catalog).replaceAll('\\', '/');
    if (/["\r\n\0]/.test(file)) throw new Error('Invalid catalog path.');
    await loadCatalog(file);
    catalogLine = `MCP_CATALOG_PATH=${JSON.stringify(file)}\n`;
  }
  await writeFile('.env.local', `MCP_SERVICE_TOKEN=${randomBytes(32).toString('hex')}\n`
    + catalogLine + 'HOST=127.0.0.1\nPORT=8788\n', { flag: 'wx', mode: 0o600 });
  console.log('Created private .env.local without printing credentials. Existing files are never overwritten.');
  console.log('Start: node --env-file=.env.local src/server.mjs');
} catch (error) {
  console.error(error.code === 'EEXIST' ? '.env.local already exists; no changes made.'
    : 'Configuration failed. Check --catalog and write permissions; credentials were not printed.');
  process.exitCode = 1;
}
