import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { main, parseArguments, importCapture } from '../scripts/evaluate-baselines.mjs';
import { createBaselinePlan, createOracleRun, scoreBaselineRun } from '../src/baseline-evaluation.mjs';
import { BASELINE_ADAPTER_VERSION } from '../src/baseline-adapters.mjs';
import { baselineInputs } from './helpers/baseline-fixture.mjs';

const inputs = baselineInputs();
const plan = createBaselinePlan(inputs);
const at = '2026-11-19T00:00:00.000Z';
const oracle = createOracleRun({ ...inputs, plan, startedAt: at, completedAt: at });
const json = (value) => JSON.stringify(value, null, 2) + '\n';
const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
const selector = { outputIndex: 0, componentId: 'ChatOutput-fixture' };
function capture() {
  return {
    schemaVersion: '1.0', kind: 'jeju-baseline-capture', planSha256: plan.planSha256,
    system: { id: 'langflow', modelId: 'synthetic-test-model', promptVersion: 'synthetic-v1', toolVersion: 'synthetic-v1', adapterVersion: BASELINE_ADAPTER_VERSION },
    startedAt: at, completedAt: at,
    records: oracle.records.map(({ caseId, requestSha256, response }) => ({
      caseId, requestSha256, outputSelector: selector,
      raw: { outputs: [{ outputs: [{ component_id: selector.componentId, results: { message: { sender: 'Machine', text: JSON.stringify(response) } } }] }] },
    })),
  };
}
async function files(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jeju-baseline-cli-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const local = path.join(root, '.local');
  await mkdir(local);
  const p = Object.fromEntries(['catalog', 'set', 'plan', 'run', 'capture', 'details'].map((name) => [name, path.join(local, name + '.json')]));
  p.report = path.join(root, 'summary.json');
  await writeFile(p.catalog, json(inputs.catalog));
  await writeFile(p.set, json(inputs.set));
  return { root, local, ...p };
}
const common = (p) => ['--catalog', p.catalog, '--set', p.set];

test('CLI rejects unknown, repeated, missing and command-inappropriate options', () => {
  assert.deepEqual(parseArguments(['--help']), { help: true });
  assert.throws(() => parseArguments(['execute']));
  assert.throws(() => parseArguments(['prepare', '--catalog', 'a']));
  assert.throws(() => parseArguments(['prepare', '--catalog', 'a', '--set', 'b', '--output', 'c', '--check']));
  assert.throws(() => parseArguments(['prepare', '--catalog', 'a', '--set', 'b', '--output', 'c', '--output', 'd']));
  assert.throws(() => parseArguments(['prepare', '--catalog', 'a', '--set', 'b', '--output', 'c', '--variants', 'template,template']));
});

test('CLI prepare -> self-check -> reproducible scoring preserves local-only detail and null measurements', async (t) => {
  const p = await files(t);
  await main(['prepare', ...common(p), '--output', p.plan]);
  assert.deepEqual(await readJson(p.plan), plan);
  await main(['self-check', ...common(p), '--plan', p.plan, '--output', p.run, '--details', p.details, '--report', p.report]);
  const report = await readJson(p.report);
  assert.equal(report.resultType, 'scorer_self_check_not_model_quality');
  assert.equal(report.groups[0].plannedCases, 96);
  assert.equal(report.groups[0].metrics.latencyMs.measuredItems, 0);
  assert.equal(report.groups[0].metrics.costUsd.total, null);
  assert.equal(report.groups[0].actionAccuracyAllCases, 1);
  const serialized = json(report);
  for (const place of inputs.catalog.places) { assert.ok(!serialized.includes(place.id)); assert.ok(!serialized.includes(place.name)); }
  for (const item of inputs.set.items) assert.ok(!serialized.includes(item.query.template));
  const scoreArgs = ['score', ...common(p), '--plan', p.plan, '--run', p.run, '--details', p.details, '--report', p.report];
  await main([...scoreArgs, '--check']);
  await assert.rejects(main(scoreArgs), /already exists/);
  await writeFile(p.report, '{}\n');
  await assert.rejects(main([...scoreArgs, '--check']), /differs/);
});

test('CLI imports a complete Langflow capture with audit then scores against the pinned plan', async (t) => {
  const p = await files(t);
  await writeFile(p.plan, json(plan));
  const rawCapture = capture();
  rawCapture.records[0] = { caseId: plan.cases[0].caseId, requestSha256: plan.cases[0].requestSha256, error: { code: 'timeout' } };
  await writeFile(p.capture, json(rawCapture));
  await main(['import', ...common(p), '--plan', p.plan, '--capture', p.capture, '--output', p.run]);
  const run = await readJson(p.run);
  assert.equal(run.records[1].normalization.adapter, 'langflow');
  const result = scoreBaselineRun({ ...inputs, plan, run });
  assert.equal(result.summary.groups[0].failedCases, 1);
  assert.equal(result.summary.groups[0].plannedCases, 96);
  assert.equal(result.summary.groups[0].actionAccuracyAllCases, 95 / 96);
});

test('capture import rejects incomplete, duplicated, mismatched and fallback records without losing IDs', () => {
  const incomplete = capture(); incomplete.records.pop();
  assert.throws(() => importCapture(plan, incomplete), /every request/);
  const duplicate = capture(); duplicate.records[1] = duplicate.records[0];
  assert.throws(() => importCapture(plan, duplicate), /duplicate/);
  const wrongHash = capture(); wrongHash.records[0].requestSha256 = '0'.repeat(64);
  assert.throws(() => importCapture(plan, wrongHash), /mismatched/);
  const fallback = capture(); fallback.system.id = 'gemini'; fallback.records[0].raw = { engine: 'keyword' };
  assert.throws(() => importCapture(plan, fallback), /declared model/);
  const wrongVersion = capture(); wrongVersion.system.adapterVersion = 'unknown';
  assert.throws(() => importCapture(plan, wrongVersion), /adapterVersion/);
  const errorWithRaw = capture(); errorWithRaw.records[0].error = { code: 'timeout' };
  assert.throws(() => importCapture(plan, errorWithRaw), /also carry/);
  const invalidId = capture();
  invalidId.records[0].raw.outputs[0].outputs[0].results.message.text = JSON.stringify({
    ...oracle.records[0].response, placeIds: ['FIXTURE_OUTSIDE_CATALOG'],
  });
  const result = scoreBaselineRun({ ...inputs, plan, run: importCapture(plan, invalidId) });
  assert.equal(result.summary.groups[0].metrics.disallowedIds.byReason.notInCatalog, 1);
});

test('Langflow imports reject reported model drift while preserving compatibility with unreported metadata', () => {
  const valid = capture();
  assert.equal(importCapture(plan, valid).records.length, 96);
  valid.records[0].raw.outputs[0].outputs[0].results.message.properties = { source: { source: valid.system.modelId } };
  assert.equal(importCapture(plan, valid).records.length, 96);
  for (const reportedModel of ['different-model', null, '', `${valid.system.modelId} `]) {
    const changed = structuredClone(valid);
    changed.records[0].raw.outputs[0].outputs[0].results.message.properties.source.source = reportedModel;
    assert.throws(() => importCapture(plan, changed), /different model/);
  }
});

test('CLI prevents raw output publication, symlink escape and path collisions', async (t) => {
  const p = await files(t);
  await assert.rejects(main(['prepare', ...common(p), '--output', path.join(p.root, 'public-plan.json')]), /inside .local/);
  await symlink(p.root, path.join(p.local, 'escape'));
  await assert.rejects(main(['prepare', ...common(p), '--output', path.join(p.local, 'escape', 'plan.json')]), /outside .local/);
  await assert.rejects(main(['prepare', ...common(p), '--output', p.catalog]), /distinct|already exists/);
  const escapedInput = path.join(p.root, 'catalog.json');
  await writeFile(escapedInput, json(inputs.catalog));
  const link = path.join(p.local, 'linked-catalog.json');
  await symlink(escapedInput, link);
  await assert.rejects(main(['prepare', '--catalog', link, '--set', p.set, '--output', p.plan]), /outside .local/);
});
