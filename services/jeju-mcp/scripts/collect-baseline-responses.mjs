#!/usr/bin/env node
/** Explicit, bounded live collection. Provider keys belong to the target services, never this CLI's request body. */
import { lstat, mkdir, open, readFile, realpath, rename, stat, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectBaselineCapture, isLocalModelEndpoint, LOCAL_MODEL_TIMEOUT_MAX_MS } from '../src/baseline-runner.mjs';
import { validateBaselinePlan } from '../src/baseline-evaluation.mjs';
import { BASELINE_ADAPTER_VERSION } from '../src/baseline-adapters.mjs';
import { VALIDATED_BASELINE_ADAPTER_VERSION } from '../src/validated-capture-binding.mjs';
import { MAX_SOURCE_BYTES } from './import-catalog.mjs';
import { PERSONA_PATH, THEME_MAP_PATH } from './export-agent-catalog.mjs';
import { SPEC_PATH } from './build-evaluation-set.mjs';

const USAGE = `Usage: node scripts/collect-baseline-responses.mjs --execute
  --catalog <.local/catalog.json> --set <.local/evaluation-set.json> --plan <.local/plan.json>
  --system <gemini|langflow> --model <actual-model-id> --prompt-version <version> --tool-version <version>
  --endpoint <explicit-api-url> --output <.local/capture.json> --telemetry <.local/telemetry.json>
  [--max-cases <1..192>] [--timeout-ms <1..120000>] [--max-response-bytes <1..4194304>]
  [--resume-capture <.local/prior-capture.json> --resume-telemetry <.local/prior-telemetry.json>]
  [--request-mode <legacy|langflow-v2>] (v2 is Langflow only; all structured inputs are preflighted)
  [--local-model-timeout] (v2 Langflow at http://127.0.0.1:<port> only: --timeout-ms up to 1200000; resume must match)
  [--continue-on-model-mismatch] (Gemini only: preserve fallback failures and attempt the next case)
  Langflow also requires --output-index <number> --component-id <final-output-node-id>.
Default: one smoke case, 60-second timeout, sequential execution, stop on first failure, no retry.
Resume only fills not_executed cases; completed and failed cases are preserved. --max-cases counts new attempts.
Resume source files stay unchanged; choose new output paths.
Remaining cases are recorded as not_executed. Paid calls require prior operator authorization.
Each attempted response is checkpointed to the same private files before the next request.
Credentials: LANGFLOW_API_KEY for Langflow; optional BASELINE_APP_BEARER_TOKEN for the Gemini app.
GEMINI_API_KEY must be configured on the Gemini app/Langflow server, not sent to this endpoint by the collector.`;
const VALUE_OPTIONS = new Set(['catalog', 'set', 'plan', 'system', 'model', 'prompt-version', 'tool-version', 'endpoint',
  'output', 'telemetry', 'max-cases', 'timeout-ms', 'max-response-bytes', 'output-index', 'component-id', 'resume-capture', 'resume-telemetry', 'request-mode']);
const FLAG_OPTIONS = new Set(['execute', 'continue-on-model-mismatch', 'local-model-timeout']);
const REQUIRED = ['catalog', 'set', 'plan', 'system', 'model', 'prompt-version', 'tool-version', 'endpoint', 'output', 'telemetry'];
const boundedInteger = (value, min, max, label) => {
  if (!/^(?:0|[1-9]\d*)$/u.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < min || Number(value) > max) {
    throw new Error(`${label} must be an integer from ${min} to ${max}.`);
  }
  return Number(value);
};

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const options = {};
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    const key = argument.startsWith('--') ? argument.slice(2) : '';
    if (Object.hasOwn(options, key) || (!FLAG_OPTIONS.has(key) && !VALUE_OPTIONS.has(key))) throw new Error('Unknown or repeated option.');
    if (FLAG_OPTIONS.has(key)) { options[key] = true; continue; }
    if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error('Missing option value.');
    options[key] = args[++index];
  }
  if (!options.execute) throw new Error('Live collection requires the explicit --execute flag.');
  for (const key of REQUIRED) if (!options[key]) throw new Error(`--${key} is required.`);
  if ((options['resume-capture'] !== undefined) !== (options['resume-telemetry'] !== undefined)) {
    throw new Error('--resume-capture and --resume-telemetry must be supplied together.');
  }
  if (!['gemini', 'langflow'].includes(options.system)) throw new Error('--system must be gemini or langflow.');
  if (options['continue-on-model-mismatch'] && options.system !== 'gemini') throw new Error('--continue-on-model-mismatch is only supported for Gemini.');
  options.requestMode = options['request-mode'] ?? 'legacy';
  if (!['legacy', 'langflow-v2'].includes(options.requestMode) || (options.requestMode === 'langflow-v2' && options.system !== 'langflow')) throw new Error('--request-mode must be legacy or Langflow-only langflow-v2.');
  if (options['local-model-timeout']) {
    if (options.requestMode !== 'langflow-v2') throw new Error('--local-model-timeout is only supported for Langflow with --request-mode langflow-v2.');
    if (!isLocalModelEndpoint(options.endpoint)) throw new Error('--local-model-timeout requires an --endpoint origin of exactly http://127.0.0.1:<port>.');
  }
  options.maxCases = options['max-cases'] === undefined ? 1 : boundedInteger(options['max-cases'], 1, 192, '--max-cases');
  options.timeoutMs = options['timeout-ms'] === undefined ? 60_000
    : boundedInteger(options['timeout-ms'], 1, options['local-model-timeout'] ? LOCAL_MODEL_TIMEOUT_MAX_MS : 120_000, '--timeout-ms');
  options.maxResponseBytes = options['max-response-bytes'] === undefined ? 1024 * 1024 : boundedInteger(options['max-response-bytes'], 1, 4 * 1024 * 1024, '--max-response-bytes');
  if (options.system === 'langflow') {
    if (options['output-index'] === undefined || !options['component-id']) throw new Error('Langflow requires --output-index and --component-id.');
    options.outputSelector = { outputIndex: boundedInteger(options['output-index'], 0, 1000, '--output-index'), componentId: options['component-id'] };
  } else if (options['output-index'] !== undefined || options['component-id'] !== undefined) {
    throw new Error('Gemini does not use Langflow output selection.');
  }
  return options;
}

const localJson = (file) => file.split(path.sep).includes('.local') && path.extname(file) === '.json';
async function readJson(file, localOnly = true) {
  const lexical = path.resolve(file);
  const resolved = await realpath(lexical);
  if (localOnly && (!localJson(lexical) || !localJson(resolved))) throw new Error('Inputs must be .json files inside .local.');
  const info = await stat(resolved);
  if (!info.isFile() || info.size < 1 || info.size > MAX_SOURCE_BYTES) throw new Error('Input must be a non-empty regular file of at most 32 MiB.');
  const bytes = await readFile(resolved);
  if (bytes.length > MAX_SOURCE_BYTES) throw new Error('Input exceeds 32 MiB.');
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new Error('Input is not valid UTF-8 JSON.'); }
}

/** Resolve every path before reserving outputs or issuing a paid request. Dangling symlinks also count as existing. */
async function outputPaths(options) {
  const seen = new Set();
  for (const key of ['catalog', 'set', 'plan', 'resume-capture', 'resume-telemetry'].filter((key) => options[key] !== undefined)) {
    const resolved = await realpath(path.resolve(options[key]));
    if (seen.has(resolved)) throw new Error('Input and output paths must be distinct.');
    seen.add(resolved);
  }
  const outputs = {};
  for (const key of ['output', 'telemetry']) {
    const lexical = path.resolve(options[key]);
    if (!localJson(lexical)) throw new Error('Capture and telemetry must be .json files inside .local.');
    await mkdir(path.dirname(lexical), { recursive: true, mode: 0o700 });
    const resolved = path.join(await realpath(path.dirname(lexical)), path.basename(lexical));
    if (!localJson(resolved)) throw new Error('Output resolves outside .local.');
    if (seen.has(resolved)) throw new Error('Input and output paths must be distinct.');
    seen.add(resolved);
    const exists = await lstat(resolved).then(() => true, (error) => { if (error.code === 'ENOENT') return false; throw error; });
    if (exists) throw new Error('Output already exists; choose new filenames. No requests were sent.');
    outputs[key] = resolved;
  }
  return outputs;
}

async function reserveOutputs(outputs) {
  const reserved = [];
  try {
    for (const key of ['output', 'telemetry']) {
      const handle = await open(outputs[key], 'wx', 0o600);
      try { reserved.push({ key, path: outputs[key], identity: await handle.stat(), saved: false }); }
      finally { await handle.close(); }
    }
    return reserved;
  } catch (error) {
    for (const entry of reserved) await removeUnusedReservation(entry);
    throw error;
  }
}

const sameFile = (left, right) => left.isFile() && left.dev === right.dev && left.ino === right.ino;

async function removeUnusedReservation(entry) {
  if (entry.saved) return;
  const current = await lstat(entry.path).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
  if (current && sameFile(current, entry.identity)) await unlink(entry.path);
}

/** Replace only this run's reserved file. A failed temporary write leaves the previous JSON checkpoint intact. */
async function atomicCheckpoint(entry, serialized) {
  const parent = path.dirname(entry.path);
  if (await realpath(parent) !== parent || !localJson(entry.path)) throw new Error('Checkpoint directory changed outside the validated local path.');
  if (!sameFile(await lstat(entry.path), entry.identity)) throw new Error('Checkpoint destination changed after it was reserved.');
  let temporaryPath = path.join(parent, `.${path.basename(entry.path)}.${randomUUID()}.checkpoint`);
  let handle;
  let temporaryCreated = false;
  try {
    handle = await open(temporaryPath, 'wx', 0o600);
    temporaryCreated = true;
    await handle.writeFile(serialized, 'utf8');
    await handle.sync();
    const identity = await handle.stat();
    await handle.close();
    handle = undefined;
    if (await realpath(parent) !== parent || !sameFile(await lstat(entry.path), entry.identity)) {
      throw new Error('Checkpoint destination changed while writing.');
    }
    await rename(temporaryPath, entry.path);
    temporaryPath = null;
    entry.identity = identity;
    entry.saved = true;
    // POSIX directory fsync preserves the replacement across a machine restart. Windows lacks directory fsync.
    if (process.platform !== 'win32') {
      const directory = await open(parent, 'r');
      try { await directory.sync(); }
      finally { await directory.close(); }
    }
  } finally {
    if (handle) await handle.close();
    if (temporaryCreated && temporaryPath) await unlink(temporaryPath).catch((error) => { if (error.code !== 'ENOENT') throw error; });
  }
}

async function persistSnapshot(reserved, { capture, telemetry }) {
  const values = { output: capture, telemetry };
  const serialized = reserved.map((entry) => [entry, JSON.stringify(values[entry.key]) + '\n']);
  if (serialized.some(([, value]) => Buffer.byteLength(value) > MAX_SOURCE_BYTES)) throw new Error('Collected artifact exceeds the 32 MiB local limit.');
  // Each file is atomic, but the pair is not one transaction. completedAt and planSha256 identify their snapshots.
  for (const [entry, value] of serialized) await atomicCheckpoint(entry, value);
}

/** Without an injected fetchImpl the runner picks the transport: built-in fetch, or loopback HTTP for --local-model-timeout. */
export async function main(args = process.argv.slice(2), { paths = {}, env = process.env, fetchImpl, signal, log = console.log } = {}) {
  const options = parseArguments(args);
  if (options.help) { log(USAGE); return { status: 'help' }; }
  const [catalog, set, plan, personas, themeMap, spec, resumeCapture, resumeTelemetry] = await Promise.all([
    readJson(options.catalog), readJson(options.set), readJson(options.plan),
    readJson(paths.personas ?? PERSONA_PATH, false), readJson(paths.themeMap ?? THEME_MAP_PATH, false), readJson(paths.spec ?? SPEC_PATH, false),
    options['resume-capture'] === undefined ? undefined : readJson(options['resume-capture']),
    options['resume-telemetry'] === undefined ? undefined : readJson(options['resume-telemetry']),
  ]);
  validateBaselinePlan(plan, { set, catalog, personas, themeMap, spec });
  const outputs = await outputPaths(options);
  const headers = {};
  if (options.system === 'langflow') {
    if (!env.LANGFLOW_API_KEY) throw new Error('LANGFLOW_API_KEY is required for the selected Langflow server.');
    headers['x-api-key'] = env.LANGFLOW_API_KEY;
  } else if (env.BASELINE_APP_BEARER_TOKEN) headers.authorization = `Bearer ${env.BASELINE_APP_BEARER_TOKEN}`;
  const system = { id: options.system, modelId: options.model, promptVersion: options['prompt-version'], toolVersion: options['tool-version'], adapterVersion: options.requestMode === 'langflow-v2' ? VALIDATED_BASELINE_ADAPTER_VERSION : BASELINE_ADAPTER_VERSION };
  // Reserve both files now, so a late permission/collision error cannot discard a completed paid call.
  const reserved = await reserveOutputs(outputs);
  try {
    const result = await collectBaselineCapture({ plan, system, endpoint: options.endpoint, headers,
      outputSelector: options.outputSelector, maxCases: options.maxCases, timeoutMs: options.timeoutMs,
      localModelTimeout: options['local-model-timeout'] === true,
      maxResponseBytes: options.maxResponseBytes, maxTotalResponseBytes: 16 * 1024 * 1024,
      stopOnError: true, continueOnModelMismatch: options['continue-on-model-mismatch'] === true,
      signal, fetchImpl, resumeCapture, resumeTelemetry, requestMode: options.requestMode, catalog,
      onProgress: ({ attempted, total, outcome }) => log(JSON.stringify({ status: 'collecting', attempted, planned: total, outcome })),
      onCheckpoint: (snapshot) => persistSnapshot(reserved, snapshot),
    });
    await persistSnapshot(reserved, result);
    const status = { status: 'captured', system: options.system, plannedCases: plan.cases.length,
      attemptedCases: result.telemetry.attemptedCases, notExecutedCases: result.telemetry.notExecutedCases,
      newAttemptedCases: result.telemetry.newAttemptedCases ?? result.telemetry.attemptedCases,
      successfulResponses: result.capture.records.filter((entry) => entry.raw !== undefined).length,
      stoppedReason: result.telemetry.stoppedReason, planSha256: plan.planSha256 };
    log(JSON.stringify(status));
    return status;
  } finally {
    for (const entry of reserved) await removeUnusedReservation(entry);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  main(process.argv.slice(2), { signal: controller.signal }).then((status) => {
    if (controller.signal.aborted) process.exitCode = 130;
    else if (status.status === 'captured' && status.attemptedCases > status.successfulResponses) process.exitCode = 1;
  }).catch(() => {
    console.error('Baseline collection failed. Check local paths, manifest, endpoint, and credential setup; no raw response or secret is logged.');
    process.exitCode = 1;
  }).finally(() => {
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  });
}
