#!/usr/bin/env node
/**
 * Ask a locally built scratch copy of the reference app for its own /api/course/places list over loopback.
 * Build the copy outside the read-only reference checkout (see docs/19). The reference app is not modified,
 * nothing is deployed, and the output stays in a .local directory because it contains third-party place data.
 */
import { createRequire } from 'node:module';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeCatalogExclusive } from './import-catalog.mjs';

const USAGE = 'Usage: node scripts/dump-app-places.mjs --app-dir <built jeju-tour prototype copy> --output <.local/app-places.json>';

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const result = {};
  for (let i = 0; i < args.length; i += 2) {
    const name = args[i];
    if (!['--app-dir', '--output'].includes(name) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(USAGE);
    const key = name.slice(2);
    if (result[key]) throw new Error(`Duplicate ${name} argument.`);
    result[key] = args[i + 1];
  }
  if (!result['app-dir'] || !result.output) throw new Error(`Both --app-dir and --output are required.\n${USAGE}`);
  return result;
}

/** Start the built Express app on an ephemeral loopback port, request the place list, and always close it. */
export async function fetchAppPlaces(app, { timeoutMs = 60_000 } = {}) {
  if (typeof app?.listen !== 'function') throw new Error('The built app module must export an Express app.');
  const server = await new Promise((resolve, reject) => {
    const instance = app.listen(0, '127.0.0.1', () => resolve(instance));
    instance.once('error', reject);
  });
  try {
    const { port } = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/course/places`, { signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.json();
    if (!response.ok || body?.success !== true || !Array.isArray(body?.data?.places)) {
      throw new Error(`The app did not return a successful place list (HTTP ${response.status}).`);
    }
    return body;
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(() => resolve()));
  }
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  if (options.help) {
    console.log(USAGE);
    return;
  }
  const appDir = path.resolve(options['app-dir']);
  const entry = path.join(appDir, 'dist', 'server.js');
  if (!(await stat(entry).catch(() => null))?.isFile()) throw new Error('Build the scratch copy first so that dist/server.js exists (see docs/19).');
  const app = createRequire(path.join(appDir, 'package.json'))(entry);
  const body = await fetchAppPlaces(app);
  await writeCatalogExclusive(options.output, body);
  console.log(JSON.stringify({ status: 'dumped', count: body.data.places.length, byCat: body.data.byCat, output: 'local-only' }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // The reference app may keep cache timers alive, so exit explicitly once the dump is finished.
  main().then(() => process.exit(0), (error) => { console.error(`Dump failed: ${error.message}`); process.exit(1); });
}
