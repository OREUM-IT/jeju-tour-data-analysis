#!/usr/bin/env node
/**
 * Build the step-3 evaluation set from the local agent catalog v2, the persona definitions, the theme map and
 * data/evaluation-spec.json. Local files only: no network and no model call. The set (place IDs, names, gold) and the
 * writer briefs are written once inside a .local directory; only the aggregate summary may go to a tracked path.
 */
import { readFile, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildEvaluationSet, evaluationBriefs, summarizeEvaluationSet } from '../src/evaluation-set.mjs';
import { MAX_SOURCE_BYTES, writeCatalogExclusive } from './import-catalog.mjs';
import { PERSONA_PATH, THEME_MAP_PATH } from './export-agent-catalog.mjs';

const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
export const SPEC_PATH = path.join(REPO_ROOT, 'data', 'evaluation-spec.json');
const USAGE = 'Usage: node scripts/build-evaluation-set.mjs --catalog <.local/agent-catalog.json> [--queries <.local/evaluation-queries.json>] [--output <.local/evaluation-set.json>] [--briefs <.local/evaluation-briefs.json>] [--report <evaluation-set-summary.json>] [--check]';
const OPTIONS = new Set(['--catalog', '--queries', '--output', '--briefs', '--report']);

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const result = { check: false };
  for (let i = 0; i < args.length; i++) {
    const name = args[i];
    if (name === '--check') {
      if (result.check) throw new Error('Duplicate --check argument.');
      result.check = true;
      continue;
    }
    if (!OPTIONS.has(name) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(USAGE);
    const key = name.slice(2);
    if (result[key]) throw new Error(`Duplicate ${name} argument.`);
    result[key] = args[++i];
  }
  if (!result.catalog) throw new Error(`--catalog is required; no implicit input is used.\n${USAGE}`);
  if (!result.output && !result.briefs && !result.report) throw new Error(`Name at least one of --output, --briefs or --report.\n${USAGE}`);
  for (const key of ['output', 'briefs', 'report']) {
    if (result[key] !== undefined && path.extname(result[key]) !== '.json') throw new Error(`--${key} must be a .json file.`);
  }
  return result;
}

async function readJson(file, label) {
  const resolved = await realpath(path.resolve(file));
  const info = await stat(resolved);
  if (!info.isFile() || info.size === 0 || info.size > MAX_SOURCE_BYTES) throw new Error(`${label} must be a non-empty regular file no larger than 32 MiB.`);
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await readFile(resolved)));
  } catch {
    throw new Error(`${label} is not valid UTF-8 JSON.`);
  }
}

const serialize = (value) => `${JSON.stringify(value, null, 2)}\n`;

/** Refuse path mix-ups before anything is read or written, so a failed run leaves nothing half done. */
async function checkPaths(options) {
  const named = ['catalog', 'queries', 'output', 'briefs', 'report'].filter((key) => options[key] !== undefined).map((key) => [key, path.resolve(options[key])]);
  const owner = new Map();
  for (const [key, file] of named) {
    if (owner.has(file)) throw new Error(`--${key} and --${owner.get(file)} name the same file.`);
    owner.set(file, key);
  }
  const files = Object.fromEntries(named);
  if (files.report !== undefined && files.report.split(path.sep).includes('.local')) {
    throw new Error('--report is the aggregate meant for Git; it is not written inside a .local directory.');
  }
  if (options.check) return;
  for (const key of ['output', 'briefs']) {
    if (files[key] !== undefined && await stat(files[key]).then(() => true, () => false)) {
      throw new Error(`--${key} already exists; choose a new versioned filename. Nothing was written.`);
    }
  }
}

async function sameBytes(file, expected) {
  try {
    return (await readFile(path.resolve(file), 'utf8')) === expected;
  } catch {
    return false;
  }
}

export async function main(args = process.argv.slice(2), paths = {}) {
  const options = parseArguments(args);
  if (options.help) {
    console.log(USAGE);
    console.log('Reads local files only. --check regenerates in memory and compares with the named files instead of writing.');
    return { status: 'help' };
  }
  await checkPaths(options);
  const catalog = await readJson(options.catalog, '--catalog');
  const personas = await readJson(paths.personas ?? PERSONA_PATH, 'persona definitions');
  const themeMap = await readJson(paths.themeMap ?? THEME_MAP_PATH, 'theme map');
  const spec = await readJson(paths.spec ?? SPEC_PATH, 'evaluation spec');
  const authoredQueries = options.queries === undefined ? undefined : await readJson(options.queries, '--queries');
  const set = buildEvaluationSet({ catalog, personas, themeMap, spec, authoredQueries });
  const summary = summarizeEvaluationSet(set);
  const outputs = [
    ['output', set, true], ['briefs', evaluationBriefs(set, spec), true], ['report', summary, false],
  ].filter(([key]) => options[key] !== undefined);
  const status = {
    status: options.check ? 'checked' : 'built', items: summary.totals.items, dropped: summary.totals.droppedItems,
    byExpectedAction: summary.totals.byExpectedAction, byDifficulty: summary.totals.byDifficulty,
    authoredQueries: summary.queries.authored, goldSha256: summary.contentSha256.gold,
    [options.check ? 'compared' : 'written']: outputs.map(([key]) => key),
  };
  if (options.check) {
    const mismatched = [];
    for (const [key, value] of outputs) if (!(await sameBytes(options[key], serialize(value)))) mismatched.push(key);
    status.mismatched = mismatched;
    console.log(JSON.stringify(status));
    if (mismatched.length > 0) throw new Error(`Regenerated content differs from: ${mismatched.join(', ')}. Nothing was written.`);
    return status;
  }
  for (const [key, value, localOnly] of outputs) {
    if (localOnly) await writeCatalogExclusive(options[key], value);
    else await writeFile(path.resolve(options[key]), serialize(value), 'utf8');
  }
  console.log(JSON.stringify(status));
  return status;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(`Evaluation set build failed: ${error.message}`); process.exitCode = 1; });
}
