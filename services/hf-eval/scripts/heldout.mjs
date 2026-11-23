#!/usr/bin/env node
/**
 * Held-out evaluation set: the published spec with only `seed` replaced, written to .local.
 * Calls the existing builder/evaluator with that spec so no rule, metric or query sentence check changes.
 * Usage:
 *   node scripts/heldout.mjs spec   --seed <name> --output <.local/spec.json>
 *   node scripts/heldout.mjs build  --spec <.local/spec.json> <build-evaluation-set.mjs args...>
 *   node scripts/heldout.mjs eval   --spec <.local/spec.json> <evaluate-baselines.mjs args...>
 */
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { main as build, SPEC_PATH } from '../../jeju-mcp/scripts/build-evaluation-set.mjs';
import { main as evaluate } from '../../jeju-mcp/scripts/evaluate-baselines.mjs';

const [command, ...rest] = process.argv.slice(2);
const take = (name) => { const i = rest.indexOf(`--${name}`); if (i < 0) return undefined; const v = rest[i + 1]; rest.splice(i, 2); return v; };
const local = (file) => path.resolve(file).split(path.sep).includes('.local');
if (command === 'spec') {
  const seed = take('seed'); const output = take('output');
  if (!seed || !output || !local(output)) throw new Error('spec needs --seed and an --output inside .local');
  const spec = JSON.parse(await readFile(SPEC_PATH, 'utf8'));
  if (seed === spec.seed) throw new Error('held-out seed must differ from the published seed');
  await writeFile(output, `${JSON.stringify({ ...spec, seed }, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify({ written: output, publishedSeed: spec.seed, heldOutSeed: seed }));
} else if (command === 'build' || command === 'eval') {
  const specFile = take('spec');
  if (!specFile || !local(specFile)) throw new Error('--spec inside .local is required');
  const published = JSON.parse(await readFile(SPEC_PATH, 'utf8'));
  const spec = JSON.parse(await readFile(specFile, 'utf8'));
  const { seed: a, ...restA } = published; const { seed: b, ...restB } = spec;
  if (JSON.stringify(restA) !== JSON.stringify(restB) || a === b) throw new Error('held-out spec may differ from the published spec only in seed');
  await (command === 'build' ? build(rest, { spec: path.resolve(specFile) }) : evaluate(rest, { spec: path.resolve(specFile) }));
} else {
  throw new Error('command must be spec, build or eval');
}
