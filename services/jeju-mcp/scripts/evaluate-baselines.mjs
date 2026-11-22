#!/usr/bin/env node
/** Offline baseline preparation, response import, and scoring. Never calls a model or endpoint. */
import { mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBaselinePlan, validateBaselinePlan, createOracleRun, scoreBaselineRun } from '../src/baseline-evaluation.mjs';
import { adaptGeminiResponse, adaptLangflowResponse, adaptLocalLlmResponse, adaptValidatedLangflowResponse } from '../src/baseline-adapters.mjs';
import { validateValidatedCapture } from '../src/validated-capture-binding.mjs';
import { MAX_SOURCE_BYTES, writeCatalogExclusive } from './import-catalog.mjs';
import { PERSONA_PATH, THEME_MAP_PATH } from './export-agent-catalog.mjs';
import { SPEC_PATH } from './build-evaluation-set.mjs';

const USAGE = `Usage: node scripts/evaluate-baselines.mjs <prepare|import|score|self-check>
  --catalog <.local/catalog.json> --set <.local/evaluation-set.json>
  prepare:    --output <.local/plan.json> [--variants template,written]
  import:     --plan <.local/plan.json> --capture <.local/capture.json> --output <.local/run.json>
  score:      --plan <.local/plan.json> --run <.local/run.json> --details <.local/scores.json> --report <summary.json> [--check]
  self-check: --plan <.local/plan.json> --output <.local/oracle-run.json> --details <.local/oracle-scores.json> --report <oracle-summary.json>
No network calls. All input, request, response and detailed-score files must stay in .local.
Only an aggregate --report may be published. self-check is an oracle check, not model performance.`;
const COMMAND_OPTIONS = {
  prepare: ['catalog', 'set', 'output', 'variants'],
  import: ['catalog', 'set', 'plan', 'capture', 'output'],
  score: ['catalog', 'set', 'plan', 'run', 'details', 'report', 'check'],
  'self-check': ['catalog', 'set', 'plan', 'output', 'details', 'report'],
};
const REQUIRED = {
  prepare: ['catalog', 'set', 'output'],
  import: ['catalog', 'set', 'plan', 'capture', 'output'],
  score: ['catalog', 'set', 'plan', 'run', 'details', 'report'],
  'self-check': ['catalog', 'set', 'plan', 'output', 'details', 'report'],
};
const serialize = (value) => `${JSON.stringify(value, null, 2)}\n`;

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const [command, ...rest] = args;
  if (!Object.hasOwn(COMMAND_OPTIONS, command)) throw new Error(USAGE);
  const result = { command };
  for (let i = 0; i < rest.length; i++) {
    const option = rest[i];
    const key = option.startsWith('--') ? option.slice(2) : '';
    if (!COMMAND_OPTIONS[command].includes(key) || Object.hasOwn(result, key)) throw new Error('Unknown or repeated option.\n' + USAGE);
    if (key === 'check') { result.check = true; continue; }
    if (!rest[i + 1] || rest[i + 1].startsWith('--')) throw new Error('Missing option value.\n' + USAGE);
    result[key] = rest[++i];
  }
  for (const key of REQUIRED[command]) if (!result[key]) throw new Error(`--${key} is required.\n${USAGE}`);
  if (result.variants !== undefined) {
    result.variants = result.variants.split(',');
    if (!result.variants.length || result.variants.some((v) => !['template', 'written'].includes(v)) || new Set(result.variants).size !== result.variants.length) {
      throw new Error('--variants must contain unique template and/or written values.');
    }
  }
  return result;
}

const localPath = (file) => file.split(path.sep).includes('.local') && path.extname(file) === '.json';
async function readJson(file, label, localOnly = true) {
  const resolved = await realpath(path.resolve(file));
  if (localOnly && (!localPath(path.resolve(file)) || !localPath(resolved))) throw new Error(`${label} must be a .json file inside .local.`);
  const info = await stat(resolved);
  if (!info.isFile() || info.size < 1 || info.size > MAX_SOURCE_BYTES) throw new Error(`${label} must be a non-empty regular file of at most 32 MiB.`);
  const bytes = await readFile(resolved);
  if (bytes.length > MAX_SOURCE_BYTES) throw new Error(`${label} exceeds 32 MiB.`);
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new Error(`${label} is not valid UTF-8 JSON.`); }
}

/** Validate destinations before computing or writing any artifacts; never overwrite an existing run. */
async function checkPaths(options) {
  const paths = new Map();
  for (const key of ['catalog', 'set', 'plan', 'capture', 'run', 'output', 'details', 'report']) {
    if (!options[key]) continue;
    const file = path.resolve(options[key]);
    if (path.extname(file) !== '.json' || (key !== 'report' && !localPath(file))) throw new Error(`--${key} must be a .json file${key === 'report' ? '' : ' inside .local'}.`);
    if (key === 'report' && file.split(path.sep).includes('.local')) throw new Error('--report is the publishable aggregate; choose a path outside .local.');
    const isOutput = ['output', 'details', 'report'].includes(key);
    if (isOutput) await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
    const resolved = isOutput ? path.join(await realpath(path.dirname(file)), path.basename(file)) : await realpath(file);
    if (key !== 'report' && !localPath(resolved)) throw new Error(`--${key} resolves outside .local.`);
    if (key === 'report' && resolved.split(path.sep).includes('.local')) throw new Error('--report resolves inside .local.');
    if (paths.has(resolved)) throw new Error('Input and output paths must be distinct.');
    paths.set(resolved, key);
    if (isOutput && !options.check && await stat(file).then(() => true, (e) => { if (e.code === 'ENOENT') return false; throw e; })) {
      throw new Error(`--${key} already exists; choose a new filename. Nothing was written.`);
    }
  }
}

/** Captures contain human-reviewed semantics or an explicit structured Langflow message, never keyword guesses. */
export function importCapture(plan, capture, { catalog } = {}) {
  if (capture?.schemaVersion !== '1.0' || capture.kind !== 'jeju-baseline-capture' || capture.planSha256 !== plan.planSha256
      || !['gemini', 'langflow', 'local_llm'].includes(capture.system?.id) || !Array.isArray(capture.records)) {
    throw new Error('Capture must identify the plan and a supported baseline system.');
  }
  const requestMode = validateValidatedCapture(plan, capture, catalog);
  if (capture.system.id === 'local_llm' && requestMode !== 'langflow-v2') throw new Error('Local LLM captures need validated request provenance.');
  const cases = new Map(plan.cases.map((entry) => [entry.caseId, entry]));
  const seen = new Set();
  const records = capture.records.map((entry) => {
    const item = cases.get(entry.caseId);
    if (!item || seen.has(entry.caseId) || entry.requestSha256 !== item.requestSha256) throw new Error('Capture has duplicate, unknown or mismatched requests.');
    seen.add(entry.caseId);
    if (entry.error) {
      if (entry.raw !== undefined || entry.review !== undefined || entry.measurements !== undefined) throw new Error('Failed requests cannot also carry a response.');
      return { caseId: item.caseId, requestSha256: item.requestSha256, error: entry.error };
    }
    if (capture.system.id === 'gemini' && (entry.raw?.engine !== 'gemini' || entry.raw?.model !== capture.system.modelId)) {
      throw new Error('Gemini capture did not use the declared model; keyword fallback is not a Gemini baseline.');
    }
    const adapt = capture.system.id === 'gemini' ? adaptGeminiResponse : capture.system.id === 'local_llm' ? adaptLocalLlmResponse
      : requestMode === 'langflow-v2' ? adaptValidatedLangflowResponse : adaptLangflowResponse;
    const normalized = adapt({ itemId: item.itemId, raw: entry.raw, review: entry.review, measurements: entry.measurements, outputSelector: entry.outputSelector, requestId: entry.runtimeRequest?.requestId });
    if (capture.system.id === 'langflow') {
      // The adapter has already validated an unambiguous output selection. Missing metadata remains unverified.
      const selected = entry.raw.outputs[entry.outputSelector.outputIndex].outputs.find((output) => output.component_id === entry.outputSelector.componentId);
      const reportedModel = selected.results.message.properties?.source?.source;
      if (requestMode === 'legacy' && reportedModel !== undefined && reportedModel !== capture.system.modelId) {
        throw new Error('Langflow capture reported a different model than the declared baseline.');
      }
    }
    if (capture.system.id === 'local_llm' && normalized.normalization.reportedModel !== capture.system.modelId) {
      throw new Error('Local LLM capture reported a different model than the declared baseline.');
    }
    if (capture.system.adapterVersion !== normalized.normalization.adapterVersion) throw new Error('Capture adapterVersion does not match the normalizer.');
    return { caseId: item.caseId, requestSha256: item.requestSha256, ...normalized };
  });
  if (seen.size !== cases.size) throw new Error('Capture must cover every request; preserve failures as explicit error records.');
  return {
    schemaVersion: '1.0', kind: 'jeju-baseline-run', planSha256: plan.planSha256,
    system: capture.system, mode: 'external', startedAt: capture.startedAt, completedAt: capture.completedAt, records,
  };
}

export async function main(args = process.argv.slice(2), paths = {}) {
  const options = parseArguments(args);
  if (options.help) { console.log(USAGE); return { status: 'help' }; }
  await checkPaths(options);
  const [catalog, set, personas, themeMap, spec] = await Promise.all([
    readJson(options.catalog, '--catalog'), readJson(options.set, '--set'),
    readJson(paths.personas ?? PERSONA_PATH, 'personas', false), readJson(paths.themeMap ?? THEME_MAP_PATH, 'theme map', false),
    readJson(paths.spec ?? SPEC_PATH, 'spec', false),
  ]);
  const inputs = { catalog, set, personas, themeMap, spec };
  const outputs = [];
  let summary;
  if (options.command === 'prepare') {
    const plan = createBaselinePlan({ ...inputs, variants: options.variants ?? ['template'] });
    outputs.push(['output', plan]);
    summary = { cases: plan.cases.length, planSha256: plan.planSha256 };
  } else {
    const plan = await readJson(options.plan, '--plan');
    validateBaselinePlan(plan, inputs);
    let run;
    if (options.command === 'import') run = importCapture(plan, await readJson(options.capture, '--capture'), { catalog });
    else if (options.command === 'score') run = await readJson(options.run, '--run');
    else {
      const startedAt = new Date().toISOString();
      run = createOracleRun({ ...inputs, plan, startedAt, completedAt: new Date().toISOString() });
    }
    const result = scoreBaselineRun({ ...inputs, plan, run });
    if (options.command !== 'score') outputs.push(['output', run]);
    if (options.command !== 'import') outputs.push(['details', result.details], ['report', result.summary]);
    summary = { cases: plan.cases.length, mode: run.mode, planSha256: plan.planSha256 };
  }
  // Check serialization sizes before writing anything. All outputs are projections made by trusted code.
  for (const [, value] of outputs) if (Buffer.byteLength(serialize(value)) > MAX_SOURCE_BYTES) throw new Error('Output exceeds 32 MiB. Nothing was written.');
  if (options.check) {
    for (const [key, value] of outputs) {
      if (await readFile(options[key], 'utf8') !== serialize(value)) throw new Error(`--${key} differs from the regenerated output. Nothing was written.`);
    }
  } else {
    for (const [key, value] of outputs) {
      if (key === 'report') await writeFile(options[key], serialize(value), { encoding: 'utf8', flag: 'wx', mode: 0o600 });
      else await writeCatalogExclusive(options[key], value);
    }
  }
  const status = { status: options.check ? 'checked' : 'written', command: options.command, ...summary };
  console.log(JSON.stringify(status));
  return status;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { console.error('Baseline evaluation failed. Check the documented schema, hashes, coverage and local paths; no raw response is logged.'); process.exitCode = 1; });
}
