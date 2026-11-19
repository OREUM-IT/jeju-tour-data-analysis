#!/usr/bin/env node
// CI wiring fixture only. Never fetches the private app or represents actual Jeju places.
import assert from 'node:assert/strict';
import { chmod, lstat, mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  APP_CATEGORIES, buildAgentCatalog, validateAgentCatalog,
} from '../../services/jeju-mcp/src/agent-catalog.mjs';
import { writeCatalogExclusive } from '../../services/jeju-mcp/scripts/import-catalog.mjs';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const WARNING = 'CI ONLY: 981 FICTIONAL test places; this checks Docker/DB/auth/MCP wiring, not real Jeju data or recommendation quality.';
const COUNT = 981;

export function makeCiCatalog(themeMap, personas) {
  assert.equal(themeMap.personaVersion, personas.version, 'Theme and persona versions must match.');
  const places = Array.from({ length: COUNT }, (_, index) => ({
    id: `ci-fictional-${String(index + 1).padStart(4, '0')}`,
    name: `CI test place ${String(index + 1).padStart(4, '0')} (FICTIONAL)`,
    cat: APP_CATEGORIES[index % APP_CATEGORIES.length],
    kind: 'CI fictional fixture',
    // Synthetic grid within the schema's QC envelope; these are not surveyed locations.
    lat: 33.4 + (index % 31) / 1000,
    lon: 126.5 + Math.floor(index / 31) / 1000,
    indoor: index % 2 === 0,
    nature: false,
    stay: 60,
  }));
  const rawData = { pois: places.map(place => ({ id: place.id, n: place.name })) };
  const catalog = structuredClone(buildAgentCatalog({
    appPayload: { success: true, data: { generated: '2000-01-01T00:00:00.000Z', places } },
    rawData,
    themeMap,
    personaThemeIds: personas.themes.map(theme => theme.id),
    provenance: {
      repository: 'CI/FICTIONAL-TEST-DATA-NOT-JEJU',
      commit: '0'.repeat(40),
      rawPath: 'generated-ci-fixture-only.json',
      rawSha256: '0'.repeat(64),
    },
  }));
  catalog.diagnostics.warnings.unshift(WARNING);
  assert.equal(catalog.places.length, COUNT);
  assert.equal(new Set(catalog.places.map(place => place.id)).size, COUNT);
  assert.ok(catalog.places.every(place => place.id.startsWith('ci-fictional-')
    && place.name.startsWith('CI test place ')
    && Object.values(place.attributes).every(attribute => attribute.basis !== 'verified')));
  return validateAgentCatalog(catalog);
}

export async function main(args = process.argv.slice(2)) {
  if (process.env.CI !== 'true') throw new Error('Refusing synthetic catalog generation outside CI=true.');
  if (args.length) throw new Error('This CI fixture generator takes no arguments and has one fixed output path.');
  console.warn(WARNING);
  const [themeMap, personas] = await Promise.all([
    readFile(path.join(ROOT, 'data/theme-category-map.json'), 'utf8').then(JSON.parse),
    readFile(path.join(ROOT, 'data/persona-prototypes.json'), 'utf8').then(JSON.parse),
  ]);
  const catalog = makeCiCatalog(themeMap, personas);
  const privateDirectory = path.join(ROOT, 'deploy/.local');
  const catalogDirectory = path.join(privateDirectory, 'catalog');
  for (const directory of [privateDirectory, catalogDirectory]) {
    await mkdir(directory, { mode: 0o700 }).catch(error => {
      if (error.code !== 'EEXIST') throw error;
    });
    const info = await lstat(directory);
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('CI catalog directories must be real directories.');
  }
  const output = path.join(catalogDirectory, 'agent-catalog-b0602a1.json');
  await writeCatalogExclusive(output, catalog);
  // The MCP container reads this single-file bind as UID 1000; host parents remain private.
  await chmod(output, 0o644);
  console.log(JSON.stringify({ status: 'created-ci-synthetic-fixture', placeCount: COUNT,
    realTourismData: false, verifiedAttributes: 0, modelExecuted: false, productionReady: false }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    console.error(`CI fixture generation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
