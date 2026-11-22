#!/usr/bin/env node
/**
 * Publishable aggregate of a local LLM collection's diagnostics (.local): outcome and gate-reason counts, model action
 * counts, latency and token percentiles. No query text, place IDs or raw outputs are written.
 * Usage: node scripts/summarize-local-llm.mjs --diagnostics <.local/diag.json> --capture <.local/capture.json> --report <reports/x.json>
 */
import { readFile, writeFile } from 'node:fs/promises';

const args = process.argv.slice(2);
const opt = (key) => { const i = args.indexOf(`--${key}`); return i >= 0 ? args[i + 1] : undefined; };
for (const key of ['diagnostics', 'capture', 'report']) if (!opt(key)) throw new Error(`--${key} is required`);
if (opt('report').split('/').includes('.local')) throw new Error('--report must be outside .local');
const diagnostics = JSON.parse(await readFile(opt('diagnostics'), 'utf8'));
const capture = JSON.parse(await readFile(opt('capture'), 'utf8'));
const count = (values) => Object.fromEntries([...values.reduce((map, v) => map.set(v, (map.get(v) ?? 0) + 1), new Map())].sort());
const pct = (values, p) => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  return sorted.length ? sorted[Math.min(sorted.length - 1, Math.ceil(p * sorted.length) - 1)] : null;
};
const latency = diagnostics.map((d) => d.latencyMs);
const prompt = diagnostics.map((d) => d.usage?.prompt_tokens);
const completion = diagnostics.map((d) => d.usage?.completion_tokens);
const report = {
  schemaVersion: '1.0', kind: 'jeju-local-llm-collection-summary', resultType: 'local_cpu_run_diagnostics',
  planSha256: capture.planSha256, system: { id: capture.system.id, modelId: capture.system.modelId, promptVersion: capture.system.promptVersion,
    toolVersion: capture.system.toolVersion, adapterVersion: capture.system.adapterVersion },
  startedAt: capture.startedAt, completedAt: capture.completedAt,
  cases: diagnostics.length,
  outcomes: count(diagnostics.map((d) => d.outcome)),
  modelActions: count(diagnostics.map((d) => d.modelAction ?? 'none')),
  blockedReasons: count(diagnostics.flatMap((d) => [...new Set(d.reasons ?? [])])),
  candidatePoolSize: { p50: pct(diagnostics.map((d) => d.poolSize), 0.5), max: pct(diagnostics.map((d) => d.poolSize), 1) },
  shownCandidates: { p50: pct(diagnostics.map((d) => d.shown), 0.5), max: pct(diagnostics.map((d) => d.shown), 1) },
  latencyMs: { p50: pct(latency, 0.5), p95: pct(latency, 0.95), max: pct(latency, 1) },
  promptTokens: { p50: pct(prompt, 0.5), max: pct(prompt, 1) },
  completionTokens: { p50: pct(completion, 0.5), max: pct(completion, 1) },
  costUsd: null,
  note: 'blockedReasons counts each reason code once per case. Latency includes query embedding, retrieval and generation on a 4-core CPU.',
};
await writeFile(opt('report'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
console.log(JSON.stringify(report.outcomes));
