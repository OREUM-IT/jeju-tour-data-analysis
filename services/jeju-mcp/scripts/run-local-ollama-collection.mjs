#!/usr/bin/env node
/**
 * Local Ollama evaluation only (docs/33). The collector stops at every failed case, so this repeats it over ONE resume
 * chain until --target attempted cases. It never retries a failed case and never changes the model, prompt, flow or
 * timeout, and it talks only to http://127.0.0.1. Before every segment it checks that Ollama, the MCP server and Langflow
 * answer and that the stored Langflow flow still equals the committed flow file. Output is ASCII JSON lines without
 * credentials or model text; captures and telemetry stay in .local.
 */
import { open, readdir, readFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { main as collectMain } from './collect-baseline-responses.mjs';
import { loopbackHttpFetch } from '../src/baseline-runner.mjs';
import { canonicalSha256 } from '../src/evaluation-rng.mjs';

const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** The fixed evaluation contract of docs/33. Only the series name, the key file and the target vary. */
export const LOCAL_OLLAMA_EVALUATION = Object.freeze({
  localDir: path.join(PACKAGE_DIR, '.local'),
  flowPath: path.resolve(PACKAGE_DIR, '..', 'langflow', 'jeju-tour-agent-ollama-v2.flow.json'),
  catalog: 'agent-catalog-b0602a1.json', set: 'evaluation-set-template-b0602a1.json', plan: 'baseline-plan-template-b0602a1.json',
  model: 'qwen3:4b', promptVersion: 'jeju-v2-ollama-7857ef60', toolVersion: 'jeju-mcp-v2-agent-tools-v2.0',
  componentId: 'ChatOutput-jejuV2', timeoutMs: 960_000,
  langflowUrl: 'http://127.0.0.1:7860', mcpUrl: 'http://127.0.0.1:8789', ollamaUrl: 'http://127.0.0.1:11434',
});

/** Structured final-validation results are outcomes of the case itself: keep them and go on to the next case. */
const MODEL_RESULTS = new Set(['final_validation_blocked', 'final_validation_failed', 'invalid_validated_output']);
/** Three sanitized Agent errors in a row stop the chain, as in docs/28. */
export const MAX_CONSECUTIVE_AGENT_ERRORS = 3;
const SERVICE_TIMEOUT_MS = 15_000;
const SERIES = /^[a-z][a-z0-9-]{0,39}$/u;
const KEY_FILE = /^[A-Za-z0-9._-]{1,80}\.env$/u;
const API_KEY_LINE = /^LANGFLOW_API_KEY=(sk-[A-Za-z0-9_-]{32,256})\r?\n?$/u;
const FLOW_ID = /^[A-Za-z0-9_-]{1,120}$/u;

const USAGE = `Usage: node scripts/run-local-ollama-collection.mjs (--target <1..96> | --check)
  [--series <name>] (default ollama: .local/<name>-capture-<n>.json and <name>-telemetry-<n>.json)
  [--api-key-file <file.env>] (inside .local; default langflow-api-key.env from the setup script)
Runs the docs/33 collection over one resume chain until <target> cases have been attempted in total. A recorded model
result (final validation blocked or failed, or the Agent's own AGENT_EXECUTION_FAILED/_TIMEOUT error) goes on to the
next case. Three Agent errors in a row, an unavailable service, a changed flow, a timeout or any other failure, such as
an HTTP 500 from another component, stops the chain. Run the same command
again to continue. Ctrl+C once finishes the current case and stops; twice aborts it, and that case is recorded as failed.
--check runs only the service, flow and file checks and sends no evaluation request.`;

export function parseChainArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const options = { series: 'ollama', apiKeyFile: 'langflow-api-key.env', check: false, target: null };
  const seen = new Set();
  for (let index = 0; index < args.length; index += 1) {
    const key = args[index];
    if (seen.has(key) || !['--target', '--check', '--series', '--api-key-file'].includes(key)) throw new Error('Unknown or repeated option.');
    seen.add(key);
    if (key === '--check') { options.check = true; continue; }
    const value = args[index + 1];
    if (value === undefined || value.startsWith('--')) throw new Error('Missing option value.');
    index += 1;
    if (key === '--target') {
      if (!/^[1-9][0-9]{0,2}$/u.test(value)) throw new Error('--target must be an integer from 1 to the plan size.');
      options.target = Number(value);
    } else if (key === '--series') {
      if (!SERIES.test(value)) throw new Error('--series must be lowercase letters, digits and hyphens.');
      options.series = value;
    } else {
      if (!KEY_FILE.test(value)) throw new Error('--api-key-file must be a .env file name inside .local.');
      options.apiKeyFile = value;
    }
  }
  if (options.check === (options.target !== null)) throw new Error('Give exactly one of --target or --check.');
  return options;
}

async function readJsonFile(file) {
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await readFile(file)));
}

async function getJson(fetchImpl, url, headers = {}) {
  let response;
  try {
    response = await fetchImpl(url, { method: 'GET', headers: { accept: 'application/json', ...headers },
      signal: AbortSignal.timeout(SERVICE_TIMEOUT_MS) });
  } catch { return { ok: false, status: null, body: null }; }
  let body = null;
  try { body = await response.json(); } catch { body = null; }
  return { ok: response.ok, status: response.status, body };
}

/**
 * Langflow 1.13.3 answers a sanitized Agent error with HTTP 500 and a detail such as
 * {"message": "Error running graph: Error building Component Agent: \n\nAGENT_EXECUTION_FAILED"}. That is a result of the
 * case. Any other HTTP 500, for example the MCP Tools component failing because the MCP server is down, is not.
 */
export function isAgentExecutionError(event) {
  if (event?.outcome !== 'http_error' || event.httpStatus !== 500 || typeof event.raw?.detail !== 'string') return false;
  let message = event.raw.detail;
  try {
    const parsed = JSON.parse(message);
    if (typeof parsed?.message === 'string') message = parsed.message;
  } catch { /* a plain-text detail */ }
  return message.includes('Error building Component Agent:') && /\bAGENT_EXECUTION_(?:FAILED|TIMEOUT)\b/u.test(message);
}

/** Liveness of the three local services. Returns the names of those that did not answer as expected. */
export async function checkServices(fetchImpl, urls) {
  const [ollama, mcp, langflow] = await Promise.all([
    getJson(fetchImpl, `${urls.ollamaUrl}/api/version`), getJson(fetchImpl, `${urls.mcpUrl}/healthz`),
    getJson(fetchImpl, `${urls.langflowUrl}/health_check`)]);
  return [
    ...(ollama.ok && typeof ollama.body?.version === 'string' ? [] : ['ollama']),
    ...(mcp.ok && mcp.body?.ok === true ? [] : ['mcp']),
    ...(langflow.ok && langflow.body?.status === 'ok' ? [] : ['langflow']),
  ];
}

/** Find the one flow named like the committed file and require its stored data to equal the file exactly. */
export async function findStoredFlow(fetchImpl, langflowUrl, apiKey, flowFile) {
  const headers = { 'x-api-key': apiKey };
  const listed = await getJson(fetchImpl, `${langflowUrl}/api/v1/flows/?get_all=true&header_flows=true&remove_example_flows=true`, headers);
  if (listed.status === 401 || listed.status === 403) return { problem: 'api_key_rejected' };
  if (!listed.ok || !Array.isArray(listed.body)) return { problem: 'flow_list_unavailable' };
  const matches = listed.body.filter((item) => item?.name === flowFile.name);
  if (matches.length !== 1) return { problem: matches.length === 0 ? 'flow_missing' : 'flow_name_conflict' };
  const flowId = matches[0].id;
  if (typeof flowId !== 'string' || !FLOW_ID.test(flowId)) return { problem: 'flow_id_invalid' };
  const stored = await getJson(fetchImpl, `${langflowUrl}/api/v1/flows/${flowId}`, headers);
  if (!stored.ok || stored.body?.name !== flowFile.name) return { problem: 'flow_unavailable' };
  let same = false;
  try { same = canonicalSha256(stored.body.data) === flowFile.dataSha256; } catch { same = false; }
  if (!same) return { problem: 'flow_changed' };
  return { flowId, endpoint: `${langflowUrl}/api/v1/run/${flowId}?stream=false` };
}

/** Pairs of this series in .local. The newest pair whose two files agree is the resume source; nothing is deleted. */
export async function findLatestPair(localDir, series) {
  const pattern = new RegExp(`^${series}-(capture|telemetry)-([1-9][0-9]{0,3})\\.json$`, 'u');
  const files = new Map();
  let highest = 0;
  for (const name of await readdir(localDir)) {
    const match = pattern.exec(name);
    if (!match) continue;
    const index = Number(match[2]);
    highest = Math.max(highest, index);
    files.set(index, { ...files.get(index), [match[1]]: path.join(localDir, name) });
  }
  const skipped = [];
  for (const index of [...files.keys()].sort((left, right) => right - left)) {
    const entry = files.get(index);
    if (!entry.capture || !entry.telemetry) { skipped.push(index); continue; }
    try {
      const [capture, telemetry] = await Promise.all([readJsonFile(entry.capture), readJsonFile(entry.telemetry)]);
      if (capture?.kind === 'jeju-baseline-capture' && telemetry?.kind === 'jeju-baseline-collection-telemetry'
        && typeof capture.completedAt === 'string' && capture.completedAt === telemetry.completedAt && Array.isArray(capture.records)
        && Number.isSafeInteger(telemetry.attemptedCases) && Number.isSafeInteger(telemetry.notExecutedCases)) {
        return { index, highest, skipped, ...entry,
          attemptedCases: telemetry.attemptedCases, notExecutedCases: telemetry.notExecutedCases,
          lastOutcome: Array.isArray(telemetry.events) ? telemetry.events.at(-1)?.outcome ?? null : null,
          successfulResponses: capture.records.filter((record) => record.raw !== undefined).length,
          endpointSha256: telemetry.endpointSha256 };
      }
    } catch { /* unreadable or partly written: fall back to an older pair */ }
    skipped.push(index);
  }
  return { index: null, highest, skipped, attemptedCases: 0, notExecutedCases: null, successfulResponses: 0 };
}

async function readApiKey(file) {
  let text;
  try { text = await readFile(file, 'utf8'); } catch { return null; }
  return API_KEY_LINE.exec(text)?.[1] ?? null;
}

/** One chain per series: a second window would fork the chain and share the CPU. A dead owner's lock is taken over. */
async function acquireLock(file, isAlive) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const handle = await open(file, 'wx', 0o600);
      try { await handle.writeFile(`${process.pid}\n`, 'utf8'); } finally { await handle.close(); }
      return { release: async () => { await unlink(file).catch(() => {}); }, tookOver: attempt === 1 };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const owner = Number((await readFile(file, 'utf8').catch(() => '')).trim());
      if (Number.isSafeInteger(owner) && owner > 0 && owner !== process.pid && isAlive(owner)) return null;
      await unlink(file).catch(() => {});
    }
  }
  return null;
}

function processAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}

const ADVICE = {
  service_unavailable: 'start the named service window again (docs/33 section 5), then run the same command',
  api_key_rejected: 'the key file does not match this Langflow; see docs/33 LOGIN and key recovery',
  flow_missing: 'run the setup script (docs/33 section 5 step 3) before collecting',
  flow_name_conflict: 'more than one flow has the evaluation name; remove the extra copy in Langflow',
  flow_changed: 'the stored flow differs from the committed file; do not edit the flow during the evaluation',
  flow_list_unavailable: 'Langflow did not list flows; check window 2', flow_unavailable: 'Langflow did not return the flow; check window 2',
  flow_id_invalid: 'Langflow returned an unexpected flow id; check window 2',
  endpoint_changed: 'the latest files belong to another Langflow flow; start a new --series and report it',
  repeated_agent_errors: 'three Agent errors in a row; check windows 1 and 2 and Ollama, then run the same command',
  collector_failed: 'the collector refused to continue; nothing was retried', other_run_active: 'another collection of this series is running; if none is, delete the lock file named here',
  api_key_missing: 'the key file is missing or malformed; rerun the setup script with --write-api-key',
  timeout: 'the case passed the collection limit; wait until window 2 shows that request finished, then run the same command',
  transport_failure: 'Langflow did not answer; check window 2, then run the same command',
  http_error: 'Langflow returned an error that is not a model result; check windows 1 and 2 and report this line',
  checkpoint_error: 'the result files could not be saved; check free disk space and that no program has them open',
};

/**
 * Repeat the collector until `target` attempted cases. config holds the docs/33 contract (see LOCAL_OLLAMA_EVALUATION)
 * plus series, apiKeyFile, target and check; deps can inject collect, serviceFetch, runFetch, log and the signals.
 */
export async function runLocalChain(config, { collect = collectMain, serviceFetch = loopbackHttpFetch, runFetch,
  log = (line) => console.log(line), stopSignal, abortSignal, isAlive = processAlive } = {}) {
  const emit = (value) => log(JSON.stringify(value));
  const finish = (result, extra = {}) => { emit({ status: 'chain-finished', result, ...extra }); return { result, ...extra }; };
  const stop = (reason, extra = {}) => {
    emit({ status: 'chain-stopped', reason, ...extra, advice: ADVICE[reason] ?? 'check the windows and report this line' });
    return { result: 'stopped', reason, ...extra };
  };
  const local = config.localDir;
  const input = (name) => path.join(local, name);
  const plan = await readJsonFile(input(config.plan));
  const planned = Array.isArray(plan?.cases) ? plan.cases.length : 0;
  if (config.target !== null && (config.target < 1 || config.target > planned)) throw new Error(`--target must be from 1 to ${planned}.`);
  const flowJson = await readJsonFile(config.flowPath);
  const flowFile = { name: flowJson.name, dataSha256: canonicalSha256(flowJson.data) };
  const apiKey = await readApiKey(input(config.apiKeyFile));
  if (apiKey === null) return stop('api_key_missing', { keyFile: config.apiKeyFile });
  const lock = config.check ? null : await acquireLock(input(`${config.series}.lock`), isAlive);
  if (!config.check && lock === null) return stop('other_run_active', { lockFile: `${config.series}.lock` });
  try {
    let latest = await findLatestPair(local, config.series);
    emit({ status: config.check ? 'chain-check' : 'chain-start', series: config.series, planned, target: config.target,
      resumeFrom: latest.index === null ? null : path.basename(latest.capture), attemptedCases: latest.attemptedCases,
      successfulResponses: latest.successfulResponses, ...(latest.skipped.length ? { skippedIndexes: latest.skipped } : {}),
      ...(lock?.tookOver ? { staleLockReplaced: true } : {}) });
    let next = latest.highest + 1;
    let segments = 0;
    let agentErrors = 0;
    while (true) {
      if (!config.check) {
        if (latest.notExecutedCases === 0) return finish('complete', summary(latest, segments));
        if (latest.attemptedCases >= config.target) return finish('target_reached', summary(latest, segments));
        if (stopSignal?.aborted || abortSignal?.aborted) return finish('operator_stop', summary(latest, segments));
      }
      const down = await checkServices(serviceFetch, config);
      // After a failed case in this run, that case may have failed because the service went down while it ran.
      if (down.length) return stop('service_unavailable', { services: down, ...summary(latest, segments),
        ...(segments > 0 && latest.lastOutcome !== 'completed' ? { lastCaseOutcome: latest.lastOutcome } : {}) });
      const flow = await findStoredFlow(serviceFetch, config.langflowUrl, apiKey, flowFile);
      if (flow.problem) return stop(flow.problem, summary(latest, segments));
      if (latest.index !== null && latest.endpointSha256 !== canonicalSha256(new URL(flow.endpoint).href)) {
        return stop('endpoint_changed', summary(latest, segments));
      }
      if (config.check) return finish('checked', { flowId: flow.flowId, endpoint: flow.endpoint, ...summary(latest, segments) });
      const output = input(`${config.series}-capture-${next}.json`);
      const telemetry = input(`${config.series}-telemetry-${next}.json`);
      const maxCases = config.target - latest.attemptedCases;
      segments += 1;
      emit({ status: 'segment-start', segment: segments, output: path.basename(output), maxCases });
      let caseStarted = performance.now();
      const progress = (line) => {
        let value;
        try { value = JSON.parse(line); } catch { return; }
        if (value?.status !== 'collecting') return;
        const now = performance.now();
        emit({ status: 'collecting', attempted: value.attempted, planned: value.planned, outcome: value.outcome,
          caseSeconds: Math.round((now - caseStarted) / 1000) });
        caseStarted = now;
      };
      const args = ['--execute', '--catalog', input(config.catalog), '--set', input(config.set), '--plan', input(config.plan),
        '--system', 'langflow', '--request-mode', 'langflow-v2', '--model', config.model, '--prompt-version', config.promptVersion,
        '--tool-version', config.toolVersion, '--endpoint', flow.endpoint, '--output-index', '0', '--component-id', config.componentId,
        '--local-model-timeout', '--timeout-ms', String(config.timeoutMs), '--max-cases', String(maxCases),
        ...(latest.index === null ? [] : ['--resume-capture', latest.capture, '--resume-telemetry', latest.telemetry]),
        '--output', output, '--telemetry', telemetry];
      let status;
      try {
        status = await collect(args, { env: { LANGFLOW_API_KEY: apiKey }, log: progress, signal: abortSignal, stopSignal,
          ...(runFetch === undefined ? {} : { fetchImpl: runFetch }) });
      } catch (error) {
        const message = typeof error?.message === 'string' && !error.message.includes(apiKey)
          ? error.message.replace(/[^\x20-\x7e]/gu, '?').slice(0, 300) : 'collector error';
        return stop('collector_failed', { segment: segments, message, ...summary(await findLatestPair(local, config.series), segments) });
      }
      next += 1;
      const written = await readJsonFile(telemetry);
      const lastEvent = written.events?.at(-1);
      latest = await findLatestPair(local, config.series);
      const reason = status.stoppedReason;
      const decision = status.notExecutedCases === 0 || status.attemptedCases >= config.target ? 'done'
        : reason === 'operator_stop' || reason === 'aborted' ? 'operator_stop'
          : MODEL_RESULTS.has(reason) ? 'continue'
            : reason === 'http_error' && isAgentExecutionError(lastEvent) ? 'agent_error' : 'stop';
      emit({ status: 'segment-end', segment: segments, newAttemptedCases: status.newAttemptedCases,
        attemptedCases: status.attemptedCases, successfulResponses: status.successfulResponses, stoppedReason: reason,
        ...(lastEvent?.httpStatus === undefined ? {} : { lastHttpStatus: lastEvent.httpStatus }), decision });
      if (decision === 'done') continue;
      if (decision === 'operator_stop') return finish('operator_stop', summary(latest, segments));
      if (decision === 'stop') {
        const servicesDown = await checkServices(serviceFetch, config);
        return stop(reason ?? 'collector_failed', { ...summary(latest, segments), ...(servicesDown.length ? { servicesDown } : {}) });
      }
      // The collector stops at its first failure, so only a segment's last case can have failed. A streak of Agent
      // errors continues only through segments whose single new case was one; any other case ends the streak.
      agentErrors = decision !== 'agent_error' ? 0 : status.newAttemptedCases === 1 ? agentErrors + 1 : 1;
      if (agentErrors >= MAX_CONSECUTIVE_AGENT_ERRORS) return stop('repeated_agent_errors', summary(latest, segments));
    }
  } finally {
    await lock?.release();
  }
}

function summary(latest, segments) {
  return { segments, attemptedCases: latest.attemptedCases, successfulResponses: latest.successfulResponses,
    notExecutedCases: latest.notExecutedCases,
    latestCapture: latest.index === null ? null : path.basename(latest.capture),
    latestTelemetry: latest.index === null ? null : path.basename(latest.telemetry) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const soft = new AbortController();
  const hard = new AbortController();
  let interrupts = 0;
  const onInterrupt = () => {
    interrupts += 1;
    if (interrupts === 1) {
      soft.abort();
      console.log(JSON.stringify({ status: 'stop-requested',
        advice: 'finishing the current case, then stopping; Ctrl+C again aborts it and records it as failed' }));
    } else hard.abort();
  };
  process.on('SIGINT', onInterrupt);
  process.once('SIGTERM', () => hard.abort());
  (async () => {
    const options = parseChainArguments(process.argv.slice(2));
    if (options.help) { console.log(USAGE); return 0; }
    const outcome = await runLocalChain({ ...LOCAL_OLLAMA_EVALUATION, ...options }, { stopSignal: soft.signal, abortSignal: hard.signal });
    if (outcome.result === 'operator_stop') return 130;
    return ['complete', 'target_reached', 'checked'].includes(outcome.result) ? 0 : 1;
  })().then((code) => { process.exitCode = code; }, (error) => {
    const message = typeof error?.message === 'string' ? error.message.replace(/[^\x20-\x7e]/gu, '?').slice(0, 300) : 'error';
    console.log(JSON.stringify({ status: 'chain-error', message,
      advice: 'run from the repository with the docs/33 files in services/jeju-mcp/.local' }));
    process.exitCode = 1;
  }).finally(() => { process.removeListener('SIGINT', onInterrupt); });
}
