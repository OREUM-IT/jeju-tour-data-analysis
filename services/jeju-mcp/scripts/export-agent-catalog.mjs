#!/usr/bin/env node
/**
 * Export the agent catalog v2 from the reference app's own /api/course/places JSON and the raw POI file
 * of the same app commit. The raw bytes are checked against that commit; nothing is fetched or inferred.
 * The catalog holds third-party place data and is written once inside a .local directory, never to Git.
 */
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { readFile, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { buildAgentCatalog, coverageReport } from '../src/agent-catalog.mjs';
import { MAX_SOURCE_BYTES, SOURCE_PATH, SOURCE_REPOSITORY, repositoryIdentity, writeCatalogExclusive } from './import-catalog.mjs';

const execFileAsync = promisify(execFile);
const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
export const THEME_MAP_PATH = path.join(REPO_ROOT, 'data', 'theme-category-map.json');
export const PERSONA_PATH = path.join(REPO_ROOT, 'data', 'persona-prototypes.json');
const USAGE = 'Usage: node scripts/export-agent-catalog.mjs --places <app-places.json> --raw <jeju-tour>/prototype/data/jeju_tour_data.json --commit <40-hex app commit> --output <.local/agent-catalog.json> [--report <coverage.json>]';
const OPTIONS = new Set(['--places', '--raw', '--commit', '--output', '--report']);

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const result = {};
  for (let i = 0; i < args.length; i += 2) {
    const name = args[i];
    if (!OPTIONS.has(name) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(USAGE);
    const key = name.slice(2);
    if (result[key]) throw new Error(`Duplicate ${name} argument.`);
    result[key] = args[i + 1];
  }
  for (const key of ['places', 'raw', 'commit', 'output']) {
    if (!result[key]) throw new Error(`--${key} is required; no implicit input, commit or destination is used.\n${USAGE}`);
  }
  if (!/^[a-f0-9]{40}$/u.test(result.commit)) throw new Error('--commit must be the full 40-character app commit.');
  if (result.report !== undefined && path.extname(result.report) !== '.json') throw new Error('--report must be a .json file.');
  return result;
}

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function readBounded(file) {
  const resolved = await realpath(path.resolve(file));
  const info = await stat(resolved);
  if (!info.isFile() || info.size === 0 || info.size > MAX_SOURCE_BYTES) throw new Error('Inputs must be non-empty regular files no larger than 32 MiB.');
  return { resolved, bytes: await readFile(resolved) };
}

function parseJson(bytes, label) {
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new Error(`${label} is not valid UTF-8 JSON.`);
  }
}

async function git(cwd, args, encoding = 'utf8') {
  const { stdout } = await execFileAsync('git', ['-C', cwd, ...args], { encoding, maxBuffer: MAX_SOURCE_BYTES + 1024 * 1024 });
  return stdout;
}

/** The raw file must be the tracked jeju-tour POI file and byte-identical to that path at `commit` (local objects only). */
export async function verifyRawAtCommit(rawPath, commit) {
  const { resolved, bytes } = await readBounded(rawPath);
  let committed;
  try {
    const root = await realpath((await git(path.dirname(resolved), ['rev-parse', '--show-toplevel'])).trim());
    if (path.relative(root, resolved).split(path.sep).join('/') !== SOURCE_PATH) throw new Error('unexpected_path');
    if (repositoryIdentity(await git(root, ['config', '--get', 'remote.origin.url'])) !== SOURCE_REPOSITORY.toLowerCase()) throw new Error('unexpected_repository');
    committed = await git(root, ['show', `${commit}:${SOURCE_PATH}`], null);
  } catch {
    throw new Error(`--raw must be ${SOURCE_PATH} inside a local ${SOURCE_REPOSITORY} checkout that already has --commit (git fetch --depth 1 origin <commit>).`);
  }
  const rawSha256 = sha256(bytes);
  if (rawSha256 !== sha256(committed)) throw new Error('The raw file differs from that path at --commit. Use the matching app commit; no source changes were made.');
  return { raw: parseJson(bytes, '--raw'), rawSha256 };
}

export async function loadThemeInputs(themeMapPath = THEME_MAP_PATH, personaPath = PERSONA_PATH) {
  const themeMap = parseJson((await readBounded(themeMapPath)).bytes, 'theme map');
  const persona = parseJson((await readBounded(personaPath)).bytes, 'persona definitions');
  if (persona.version !== themeMap.personaVersion) throw new Error('Theme map and persona definitions have different versions.');
  return { themeMap, personaThemeIds: persona.themes.map((theme) => theme.id) };
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  if (options.help) {
    console.log(USAGE);
    console.log('Reads local files only; writes the catalog once inside .local and an optional aggregate-only coverage report.');
    return;
  }
  const places = parseJson((await readBounded(options.places)).bytes, '--places');
  const { raw, rawSha256 } = await verifyRawAtCommit(options.raw, options.commit);
  const { themeMap, personaThemeIds } = await loadThemeInputs();
  const catalog = buildAgentCatalog({
    appPayload: places, rawData: raw, themeMap, personaThemeIds,
    provenance: { repository: SOURCE_REPOSITORY, commit: options.commit, rawPath: SOURCE_PATH, rawSha256 },
  });
  if (catalog.diagnostics.nameMismatches.length > 0) {
    throw new Error(`${catalog.diagnostics.nameMismatches.length} app places disagree with the raw names at --commit; export the places from the same app commit.`);
  }
  await writeCatalogExclusive(options.output, catalog);
  const report = coverageReport(catalog);
  if (options.report !== undefined) await writeFile(path.resolve(options.report), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({
    status: 'exported', commit: options.commit, places: report.totals.places, byOrigin: report.totals.byOrigin,
    nearDuplicatePairs: report.review.nearDuplicatePairs, verifiedAttributes: 0, productionReady: false,
  }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(`Export failed: ${error.message}`); process.exitCode = 1; });
}
