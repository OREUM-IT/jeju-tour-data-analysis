#!/usr/bin/env node
// Same 96 held-out questions through the deployed Agent Gateway, one flow at a time (Gemini, then GPT).
// This script never uses the network. deploy/collect-gateway-eval.py does the signed Gateway calls in between.
//
//   prepare  --catalog <.local/catalog.json> --dir <.local/dir>
//            held-out spec (published spec, only the seed replaced) -> evaluation set -> plan
//   requests --catalog <..> --dir <..> --label <name>
//            one Gateway payload per planned case, bound to a fresh opaque runtime id (<dir>/<label>-requests.json)
//   reference --catalog <..> --dir <..> --label <name>
//            no-model reference: the rule-action server composes every answer from the first 3 candidates (offline)
//   score    --catalog <..> --dir <..> --label <name> --model <declared model> --flow <flow build> --reports <dir outside .local>
//            results -> capture -> import -> score; the shareable aggregates go to --reports, everything else stays in .local
//
// Questions, gold answers, requests and responses stay inside .local. Only the aggregate report may be shared.
import { randomUUID } from 'node:crypto';
import { readFile, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { candidatePlaces } from '../src/agent-tools.mjs';
import { canonicalSha256 } from '../src/evaluation-rng.mjs';
import { createGatewayScopes } from '../src/gateway-scopes.mjs';
import { buildBoundValidatedRequest, GATEWAY_ADAPTER_VERSION, prepareValidatedCollection } from '../src/validated-capture-binding.mjs';
import { main as buildSet, SPEC_PATH } from './build-evaluation-set.mjs';
import { main as evaluate } from './evaluate-baselines.mjs';

export const HELD_OUT_SEED = 'jeju-evalset-heldout-20261122';
// Values of the held-out set used for the local Qwen runs (docs/33 2.1). A different catalog gives other values.
export const HELD_OUT_EXPECTED = Object.freeze({
  goldSha256: '8c2d8299fbc3caf10b7dbc2426b07e7e399a3ebdfdf4c78f806aae0d6e90bd9b',
});
const GATEWAY_FIELDS = ['schemaVersion', 'query', 'timezone', 'visitDate', 'filters', 'excludedIds', 'requirements'];
const LABEL = /^[a-z0-9][a-z0-9-]{0,40}$/u;
// Gateway outcomes that mean the run finished and the final validation refused the answer.
const BLOCKED = new Set(['RECOMMENDATION_BLOCKED', 'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES']);
const TIMEOUTS = new Set(['UPSTREAM_TIMEOUT_NO_RETRY', 'POLL_DEADLINE_REACHED', 'JOB_EXPIRED']);
const USAGE = 'Usage: node scripts/gateway-eval.mjs <prepare|requests|reference|score> --catalog <.local/catalog.json> --dir <.local/dir> [--label <name>] [--model <id> --flow <build> --reports <dir>]';

const files = (dir, label, reports) => ({
  spec: path.join(dir, 'heldout-spec.json'), set: path.join(dir, 'heldout-set.json'), plan: path.join(dir, 'heldout-plan.json'),
  requests: label && path.join(dir, `${label}-requests.json`), results: label && path.join(dir, `${label}-results.jsonl`),
  capture: label && path.join(dir, `${label}-capture.json`), run: label && path.join(dir, `${label}-run.json`),
  details: label && path.join(dir, `${label}-scores.json`),
  report: reports && path.join(reports, `${label}-report.json`), collection: reports && path.join(reports, `${label}-collection.json`),
});
const exists = (file) => stat(file).then(() => true, (error) => { if (error.code === 'ENOENT') return false; throw error; });
const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
const writeNew = (file, value) => writeFile(file, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx', mode: 0o600 });

export function parseArguments(args) {
  const [command, ...rest] = args;
  if (!['prepare', 'requests', 'reference', 'score'].includes(command)) throw new Error(USAGE);
  const options = { command };
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i]?.startsWith('--') ? rest[i].slice(2) : '';
    if (!['catalog', 'dir', 'label', 'model', 'flow', 'reports'].includes(key) || options[key] !== undefined || !rest[i + 1] || rest[i + 1].startsWith('--')) throw new Error(USAGE);
    options[key] = rest[i + 1];
  }
  if (!options.catalog || !options.dir) throw new Error(USAGE);
  for (const key of ['catalog', 'dir']) if (!path.resolve(options[key]).split(path.sep).includes('.local')) throw new Error(`--${key} must be inside .local`);
  if (command !== 'prepare' && !LABEL.test(options.label ?? '')) throw new Error('--label must be lowercase letters, digits and hyphens');
  if (command === 'score' && !(options.model && options.flow && options.reports)) throw new Error('score needs --model, --flow (the flow applied while collecting) and --reports');
  if (options.reports && path.resolve(options.reports).split(path.sep).includes('.local')) throw new Error('--reports holds only shareable aggregates; choose a directory outside .local');
  return options;
}

/** The Gateway accepts exactly these fields; it assigns its own gw- request id. */
export function gatewayPayload(request) {
  const payload = Object.fromEntries(GATEWAY_FIELDS.map((key) => [key, request[key]]));
  if (payload.schemaVersion !== '1.0' || payload.timezone !== 'Asia/Seoul' || typeof payload.query !== 'string' || !payload.query.trim()
    || payload.query.length > 4000 || !Array.isArray(payload.requirements) || !Array.isArray(payload.excludedIds)
    || payload.filters === null || typeof payload.filters !== 'object' || Array.isArray(payload.filters)) {
    throw new Error('validated request does not fit the Gateway request contract');
  }
  if (Buffer.byteLength(JSON.stringify(payload)) > 30 * 1024) throw new Error('Gateway payload is too large');
  return payload;
}

export function buildRequests(plan, catalog, { id = () => `baseline-${randomUUID()}` } = {}) {
  return plan.cases.map((entry) => {
    const { body, runtimeRequest } = buildBoundValidatedRequest(entry, catalog, id());
    return { caseId: entry.caseId, requestSha256: entry.requestSha256, runtimeRequest, payload: gatewayPayload(JSON.parse(body.input_value)) };
  });
}

/** One line per collected case, written by the collector. The last line for a case wins (a refused submission may be retried). */
export function parseResults(text) {
  const byCase = new Map();
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const value = JSON.parse(line);
    if (typeof value?.caseId !== 'string') throw new Error('result line without caseId');
    byCase.set(value.caseId, value);
  }
  return byCase;
}

function errorCode(result) {
  if (result.final === 'failed' && BLOCKED.has(result.code)) return 'invalid_output';
  if (TIMEOUTS.has(result.code)) return 'timeout';
  return 'transport_error';
}

/**
 * What the deployed rule-action server returns when the "model" simply takes the first `take` candidates of the
 * request's own filters: no model, no network. Action accuracy reached here is the server's, not a model's.
 */
export function referenceResults(requests, catalog, { take = 3, now = () => Date.now() } = {}) {
  const scopes = createGatewayScopes({ catalog, now });
  return requests.map((entry, index) => {
    const requestId = `gw-${index.toString(16).padStart(32, '0')}`;
    const request = { ...entry.payload, requestId };
    const at = new Date(now()).toISOString();
    const prepared = scopes.register({ request, principal: '0'.repeat(64), deadlineMs: now() + 60_000 });
    try {
      const placeIds = candidatePlaces(catalog, { ...request.filters, excludedIds: request.excludedIds }).slice(0, take).map((place) => place.id);
      const result = scopes.validate({ request, response: { placeIds }, expectedCatalogVersion: prepared.catalogVersion });
      const base = { caseId: entry.caseId, runtimeRequestId: entry.runtimeRequest.requestId, submittedAt: at, finishedAt: at };
      return result.status === 'accepted'
        ? { ...base, final: 'accepted', gateway: { status: 'accepted', requestId, catalogVersion: prepared.catalogVersion,
          policyVersion: 'recommendation-validation-v1' }, response: result.response }
        : { ...base, final: 'failed', code: 'RECOMMENDATION_BLOCKED' };
    } finally { scopes.release(requestId); }
  });
}

export function buildCapture({ plan, catalog, requests, results, model, flow, startedAt, completedAt }) {
  const byCase = new Map(requests.map((entry) => [entry.caseId, entry]));
  if (byCase.size !== plan.cases.length || plan.cases.some((entry) => byCase.get(entry.caseId)?.requestSha256 !== entry.requestSha256)) {
    throw new Error('requests file does not match the plan');
  }
  const outcomes = {};
  const timing = { totalSeconds: [], runSeconds: [], queueWaitSeconds: [] };
  const records = plan.cases.map((entry) => {
    const request = byCase.get(entry.caseId);
    const result = results.get(entry.caseId);
    const base = { caseId: entry.caseId, requestSha256: entry.requestSha256 };
    const name = !result ? 'not_executed' : result.final === 'accepted' ? 'accepted' : `${result.final}:${result.code}`;
    outcomes[name] = (outcomes[name] ?? 0) + 1;
    if (!result) return { ...base, error: { code: 'not_executed' } };
    if (result.runtimeRequestId !== request.runtimeRequest.requestId) throw new Error('result belongs to a different runtime request');
    for (const key of Object.keys(timing)) if (Number.isFinite(result[key])) timing[key].push(result[key]);
    if (result.final !== 'accepted') return { ...base, runtimeRequest: request.runtimeRequest, error: { code: errorCode(result) } };
    const raw = { kind: 'jeju-gateway-output', model, flow,
      gateway: { status: result.gateway.status, requestId: result.gateway.requestId, catalogVersion: result.gateway.catalogVersion,
        policyVersion: result.gateway.policyVersion }, response: result.response };
    return { ...base, runtimeRequest: request.runtimeRequest, raw,
      ...(Number.isFinite(result.totalSeconds) ? { measurements: { latencyMs: Math.round(result.totalSeconds * 1000) } } : {}) };
  });
  const capture = {
    schemaVersion: '1.0', kind: 'jeju-baseline-capture', planSha256: plan.planSha256,
    system: { id: 'gateway', modelId: model, promptVersion: flow, toolVersion: 'agent-gateway-jobs-v1', adapterVersion: GATEWAY_ADAPTER_VERSION },
    requestBinding: prepareValidatedCollection(plan, catalog), startedAt, completedAt, records,
  };
  const spread = (values) => {
    const sorted = [...values].sort((a, b) => a - b);
    if (!sorted.length) return { count: 0 };
    const at = (share) => { const p = (sorted.length - 1) * share; const lo = Math.floor(p); const hi = Math.ceil(p);
      return Math.round((sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo)) * 100) / 100; };
    return { count: sorted.length, p50: at(0.5), p95: at(0.95), max: sorted.at(-1) };
  };
  const collection = { kind: 'jeju-gateway-eval-collection', model, flow, planSha256: plan.planSha256, cases: plan.cases.length,
    outcomes: Object.fromEntries(Object.entries(outcomes).sort()), timingSeconds: Object.fromEntries(Object.entries(timing).map(([k, v]) => [k, spread(v)])),
    startedAt, completedAt, productionReady: false };
  return { capture, collection };
}

async function prepare(options, f) {
  const published = await readJson(SPEC_PATH);
  if (published.seed === HELD_OUT_SEED) throw new Error('held-out seed must differ from the published seed');
  if (!(await exists(f.spec))) await writeNew(f.spec, { ...published, seed: HELD_OUT_SEED });
  const spec = await readJson(f.spec);
  const { seed, ...restSpec } = spec; const { seed: publishedSeed, ...restPublished } = published;
  if (seed !== HELD_OUT_SEED || canonicalSha256(restSpec) !== canonicalSha256(restPublished) || publishedSeed === seed) {
    throw new Error('held-out spec may differ from the published spec only in seed');
  }
  const built = await buildSet(['--catalog', options.catalog, '--output', f.set], { spec: f.spec });
  const planned = await evaluate(['prepare', '--catalog', options.catalog, '--set', f.set, '--output', f.plan], { spec: f.spec });
  return { status: 'prepared', items: built.items, cases: planned.cases, goldSha256: built.goldSha256, planSha256: planned.planSha256,
    sameAsLocalHeldOut: built.goldSha256 === HELD_OUT_EXPECTED.goldSha256 };
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  const f = files(path.resolve(options.dir), options.label, options.reports && path.resolve(options.reports));
  const catalog = await readJson(options.catalog);
  let status;
  if (options.command === 'prepare') status = await prepare(options, f);
  else {
    const plan = await readJson(f.plan);
    if (options.command === 'reference') {
      const requests = await readJson(f.requests);
      const lines = referenceResults(requests, catalog);
      await writeFile(f.results, lines.map((line) => JSON.stringify(line)).join('\n') + '\n', { flag: 'wx', mode: 0o600 });
      status = { status: 'written', label: options.label, results: lines.length, accepted: lines.filter((l) => l.final === 'accepted').length };
    } else if (options.command === 'requests') {
      const requests = buildRequests(plan, catalog);
      await writeNew(f.requests, requests);
      status = { status: 'written', label: options.label, requests: requests.length, planSha256: plan.planSha256 };
    } else {
      const requests = await readJson(f.requests);
      const results = parseResults(await readFile(f.results, 'utf8'));
      const times = [...results.values()].flatMap((r) => [r.submittedAt, r.finishedAt]).filter((t) => typeof t === 'string').sort();
      const now = new Date().toISOString();
      const { capture, collection } = buildCapture({ plan, catalog, requests, results, model: options.model, flow: options.flow,
        startedAt: times[0] ?? now, completedAt: times.at(-1) ?? now });
      await writeNew(f.capture, capture);
      await evaluate(['import', '--catalog', options.catalog, '--set', f.set, '--plan', f.plan, '--capture', f.capture, '--output', f.run], { spec: f.spec });
      await evaluate(['score', '--catalog', options.catalog, '--set', f.set, '--plan', f.plan, '--run', f.run, '--details', f.details, '--report', f.report], { spec: f.spec });
      await writeNew(f.collection, collection);
      status = { status: 'scored', label: options.label, report: f.report, collection: f.collection, outcomes: collection.outcomes };
    }
  }
  console.log(JSON.stringify(status));
  return status;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
