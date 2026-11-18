import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import {
  convertCatalog, parseArguments, readVerifiedSource, writeCatalogExclusive,
  SOURCE_REPOSITORY, SOURCE_PATH,
} from '../scripts/import-catalog.mjs';

const execFileAsync = promisify(execFile);
const source = { repository: SOURCE_REPOSITORY, commit: 'a'.repeat(40), path: SOURCE_PATH };
const digest = raw => createHash('sha256').update(JSON.stringify(raw)).digest('hex');
const poi = (id, extra = {}) => ({ id, n: `관광지 ${id}`, c: 'spot', la: 33.45, lo: 126.55, ...extra });
const convert = raw => convertCatalog(raw, source, digest(raw));

test('preserves source fields and IDs without inventing constraints, evidence, descriptions or thematic categories', () => {
  const raw = { pois: [poi('z', { c: 'sauna', in: 1, h: { p: [] }, oh: '24/7', pop: 500, s: 9 }), poi('a', { n: '실제 이름', c: 'food' })], olle: [{}], trails: [{}] };
  const before = structuredClone(raw);
  const result = convert(raw);
  assert.deepEqual(raw, before);
  assert.deepEqual(result.source, source);
  assert.equal(result.dataMode, 'reference-import');
  assert.deepEqual(result.places.map(p => p.id), ['a', 'z']);
  assert.deepEqual(result.places[0], { id: 'a', name: '실제 이름', category: 'food', lat: 33.45, lon: 126.55, evidence: [], constraints: {} });
  assert.deepEqual(result.places[1].constraints, {});
  assert.equal(result.places[1].category, 'sauna');
  assert.equal('description' in result.places[1], false);
  assert.equal(result.diagnostics.importedRows, 2);
  assert.deepEqual(result.diagnostics.unimportedArrays, ['olle', 'trails']);
  assert.equal(JSON.stringify(result), JSON.stringify(convert(raw)));
});

test('quarantines every duplicate and invalid/out-of-region record with a source index and reason', () => {
  const raw = { pois: [
    poi('valid'), poi('dupe'), poi('dupe', { n: '다른 장소' }),
    poi('outside', { la: 37.56, lo: 126.97 }), poi('invalid-earth', { la: 100 }),
    poi('string-coord', { la: '33.45' }), poi('not-finite', { lo: Infinity }),
    poi('unknown-category', { c: 'invented-theme' }), poi('bad-name', { n: '' }),
    poi(' bad-id '), null,
  ] };
  const result = convert(raw);
  assert.deepEqual(result.places.map(p => p.id), ['valid']);
  assert.equal(result.diagnostics.inputRows, 11);
  assert.equal(result.diagnostics.rejectedRows, 10);
  assert.equal(result.diagnostics.rejectionCounts.duplicate_id, 2);
  assert.equal(result.diagnostics.rejectionCounts.outside_jeju_qc_envelope, 1);
  assert.equal(result.diagnostics.rejectionCounts.invalid_earth_coordinates, 1);
  assert.equal(result.diagnostics.rejectionCounts.nonfinite_or_nonnumeric_coordinates, 2);
  assert.deepEqual(result.diagnostics.rejected.map(r => r.sourceIndex), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(result.diagnostics.rejected[0].id, 'dupe');
  assert.equal(result.diagnostics.coordinatePolicy.outsidePolicy, 'quarantine-until-reviewed');
});

test('enforces canonical field limits and accepts numeric coordinates for Jeju outlying-island QC envelope', () => {
  const raw = { pois: [poi('chuja', { la: 33.96, lo: 126.3 }), poi('x'.repeat(161)), poi('long-name', { n: '가'.repeat(201) })] };
  const result = convert(raw);
  assert.deepEqual(result.places.map(p => p.id), ['chuja']);
  assert.deepEqual(result.diagnostics.rejectionCounts, { invalid_id: 1, invalid_name: 1 });
  assert.throws(() => convertCatalog({ places: [] }, source, digest(raw)), /pois array/);
  assert.throws(() => convertCatalog(raw, { ...source, commit: 'main' }, digest(raw)), /provenance/);
  assert.throws(() => convertCatalog(raw, source, 'not-a-hash'), /SHA-256/);
});

test('writes only exclusive .local JSON output and leaves existing data untouched', async t => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'jeju-import-write-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const output = path.join(temp, '.local', 'catalog.json');
  const catalog = convert({ pois: [poi('a')] });
  await writeCatalogExclusive(output, catalog);
  const bytes = await readFile(output, 'utf8');
  assert.deepEqual(JSON.parse(bytes), catalog);
  await assert.rejects(writeCatalogExclusive(output, convert({ pois: [poi('b')] })), /already exists/);
  assert.equal(await readFile(output, 'utf8'), bytes);
  await assert.rejects(writeCatalogExclusive(path.join(temp, 'tracked.json'), catalog), /\.local/);
});

test('requires explicit CLI paths and rejects accidental duplicate/unknown flags', () => {
  assert.deepEqual(parseArguments(['--input', '/tmp/source.json', '--output', '/tmp/.local/catalog.json']), { input: '/tmp/source.json', output: '/tmp/.local/catalog.json' });
  assert.deepEqual(parseArguments(['--help']), { help: true });
  assert.throws(() => parseArguments([]), /Both/);
  assert.throws(() => parseArguments(['--input', 'a', '--input', 'b', '--output', 'c']), /Duplicate/);
  assert.throws(() => parseArguments(['--input', 'a', '--overwrite', 'yes']), /Usage/);
});

test('verifies actual local Git origin, HEAD and byte identity; refuses modified sources without rewriting them', async t => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'jeju-import-git-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const input = path.join(temp, SOURCE_PATH);
  await mkdir(path.dirname(input), { recursive: true });
  const raw = { pois: [poi('committed')] };
  const original = `${JSON.stringify(raw)}\n`;
  await writeFile(input, original);
  const git = args => execFileAsync('git', ['-C', temp, ...args]);
  await git(['init', '--quiet']);
  await git(['config', 'user.name', 'Local import test']);
  await git(['config', 'user.email', 'import-test@example.invalid']);
  await git(['config', 'commit.gpgsign', 'false']);
  await git(['remote', 'add', 'origin', 'https://github.com/OREUM-IT/jeju-tour.git']);
  await git(['add', '--', SOURCE_PATH]);
  await git(['commit', '--quiet', '-m', 'Local importer fixture']);
  const result = await readVerifiedSource(input);
  const { stdout } = await git(['rev-parse', 'HEAD']);
  assert.equal(result.source.commit, stdout.trim());
  assert.deepEqual(result.raw, raw);
  assert.equal(result.sourceSha256, createHash('sha256').update(original).digest('hex'));
  const changed = `${JSON.stringify({ pois: [poi('local-change')] })}\n`;
  await writeFile(input, changed);
  await assert.rejects(readVerifiedSource(input), /differs from its recorded HEAD/);
  assert.equal(await readFile(input, 'utf8'), changed);
  await writeFile(input, original);
  await git(['remote', 'set-url', 'origin', 'https://github.com/unrelated/project.git']);
  await assert.rejects(readVerifiedSource(input), /local OREUM-IT\/jeju-tour/);
});
