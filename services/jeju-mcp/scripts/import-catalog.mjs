#!/usr/bin/env node
/** Read-only import of the known jeju-tour POI snapshot; never invent constraints. */
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdir, open, readFile, realpath, stat, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
export const SOURCE_REPOSITORY = 'OREUM-IT/jeju-tour';
export const SOURCE_PATH = 'prototype/data/jeju_tour_data.json';
export const MAX_SOURCE_BYTES = 32 * 1024 * 1024;
export const MAX_POIS = 100_000;
export const SOURCE_CATEGORIES = Object.freeze(['spot', 'stay', 'ferry', 'food', 'sauna', 'cafe', 'market', 'rental']);
// Deliberately coarse QC envelope including Jeju's outlying islands (e.g. Chuja).
// This is not an administrative-boundary or a destination-accessibility test.
export const JEJU_QC_BOUNDS = Object.freeze({ minLat: 32.5, maxLat: 34.2, minLon: 125.8, maxLon: 127.3 });

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function validString(value, maxLength) {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= maxLength;
}
function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}
function validCommit(commit) {
  return typeof commit === 'string' && /^[a-f0-9]{40}$/.test(commit);
}

/** Pure deterministic conversion. `source` must describe the inspected Git input. */
export function convertCatalog(raw, source, sourceSha256) {
  if (!isRecord(raw) || !Array.isArray(raw.pois)) throw new Error('Expected the known jeju-tour object with a pois array.');
  if (raw.pois.length > MAX_POIS) throw new Error(`Source exceeds ${MAX_POIS} POI rows.`);
  if (!source || source.repository !== SOURCE_REPOSITORY || source.path !== SOURCE_PATH || !validCommit(source.commit)) {
    throw new Error('Source provenance must identify the exact jeju-tour repository, path and full commit.');
  }
  if (typeof sourceSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(sourceSha256)) throw new Error('A SHA-256 of the input bytes is required.');

  const idCounts = new Map();
  for (const row of raw.pois) if (isRecord(row) && typeof row.id === 'string') {
    idCounts.set(row.id, (idCounts.get(row.id) ?? 0) + 1);
  }
  const places = [];
  const rejected = [];
  const rejectionCounts = {};
  const bounds = JEJU_QC_BOUNDS;
  const reject = (row, index, code) => {
    // Diagnostics carry only the original identifier and row position, not a copy of raw data.
    rejected.push({ sourceArray: 'pois', sourceIndex: index, ...(validString(row?.id, 160) ? { id: row.id } : {}), code });
    rejectionCounts[code] = (rejectionCounts[code] ?? 0) + 1;
  };
  raw.pois.forEach((row, index) => {
    if (!isRecord(row)) return reject(row, index, 'invalid_record');
    if (!validString(row.id, 160) || row.id !== row.id.trim()) return reject(row, index, 'invalid_id');
    // Reject all occurrences instead of silently selecting the first duplicate.
    if (idCounts.get(row.id) !== 1) return reject(row, index, 'duplicate_id');
    if (!validString(row.n, 200)) return reject(row, index, 'invalid_name');
    if (!validString(row.c, 100) || !SOURCE_CATEGORIES.includes(row.c)) return reject(row, index, 'unsupported_source_category');
    if (typeof row.la !== 'number' || typeof row.lo !== 'number' || !Number.isFinite(row.la) || !Number.isFinite(row.lo)) {
      return reject(row, index, 'nonfinite_or_nonnumeric_coordinates');
    }
    if (row.la < -90 || row.la > 90 || row.lo < -180 || row.lo > 180) return reject(row, index, 'invalid_earth_coordinates');
    if (row.la < bounds.minLat || row.la > bounds.maxLat || row.lo < bounds.minLon || row.lo > bounds.maxLon) {
      return reject(row, index, 'outside_jeju_qc_envelope');
    }
    places.push({
      id: row.id,
      name: row.n,
      category: row.c,
      lat: row.la,
      lon: row.lo,
      evidence: [],
      constraints: {},
    });
  });
  // Code-point ordering is stable across locales; raw IDs remain unchanged.
  places.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  return {
    schemaVersion: '1.0',
    dataMode: 'reference-import',
    source: { repository: source.repository, commit: source.commit, path: source.path },
    places,
    diagnostics: {
      importer: 'jeju-tour-pois-v1',
      sourceSha256,
      sourceArray: 'pois',
      sourceIndexBase: 0,
      inputRows: raw.pois.length,
      importedRows: places.length,
      rejectedRows: rejected.length,
      rejectionCounts,
      rejected,
      coordinatePolicy: {
        name: 'coarse-jeju-qc-envelope-v1',
        bounds: { ...bounds },
        outsidePolicy: 'quarantine-until-reviewed',
        note: 'A coarse numeric QC envelope, not an administrative boundary or a verified location/accessibility claim.',
      },
      unimportedArrays: ['olle', 'trails'].filter(key => Array.isArray(raw[key])),
      warnings: [
        'Source categories are preserved; no thematic recategorization or persona inference was performed.',
        'Repository provenance is not field-level destination evidence, a license clearance, or proof of freshness.',
        'All constraints are unknown. Source in/h/oh/s/pop fields were not converted into accessibility, cost, indoor, or current opening claims.',
        'Only pois are imported. Olle/trail arrays require a separately reviewed adapter.',
      ],
    },
  };
}

async function git(cwd, args, encoding = 'utf8') {
  const { stdout } = await execFileAsync('git', ['-C', cwd, ...args], { encoding, maxBuffer: MAX_SOURCE_BYTES + 1024 * 1024 });
  return stdout;
}

export function repositoryIdentity(remote) {
  // Parse only to validate identity; never include the remote (possibly credential-bearing) in errors/logs.
  const value = remote.trim();
  try {
    const url = new URL(value);
    if (['https:', 'ssh:'].includes(url.protocol) && url.hostname.toLowerCase() === 'github.com') {
      return url.pathname.replace(/^\/+|\/+$/g, '').replace(/\.git$/i, '').toLowerCase();
    }
  } catch { /* Git's SCP form is not a URL. */ }
  const scp = /^[^@\s]+@github\.com:([^\s]+)$/i.exec(value);
  return scp ? scp[1].replace(/\.git$/i, '').toLowerCase() : null;
}

/** Verify the local file is the committed known source, without any Git network request. */
export async function readVerifiedSource(inputPath) {
  const input = await realpath(path.resolve(inputPath));
  const info = await stat(input);
  if (!info.isFile() || info.size > MAX_SOURCE_BYTES) throw new Error('Input must be a regular JSON file no larger than 32 MiB.');
  const bytes = await readFile(input);
  if (bytes.length > MAX_SOURCE_BYTES) throw new Error('Input exceeds 32 MiB.');
  let root, commit, remote, committedBytes;
  try {
    root = await realpath((await git(path.dirname(input), ['rev-parse', '--show-toplevel'])).trim());
    const relativePath = path.relative(root, input).split(path.sep).join('/');
    if (relativePath !== SOURCE_PATH) throw new Error('unexpected_source_path');
    commit = (await git(root, ['rev-parse', '--verify', 'HEAD^{commit}'])).trim();
    remote = await git(root, ['config', '--get', 'remote.origin.url']);
    if (!validCommit(commit) || repositoryIdentity(remote) !== SOURCE_REPOSITORY.toLowerCase()) throw new Error('unexpected_source_identity');
    committedBytes = await git(root, ['show', `HEAD:${SOURCE_PATH}`], null);
  } catch {
    throw new Error('Input must be the tracked prototype/data/jeju_tour_data.json in a local OREUM-IT/jeju-tour Git checkout with a valid HEAD.');
  }
  const digest = sha256(bytes);
  if (digest !== sha256(committedBytes)) throw new Error('Input differs from its recorded HEAD. Preserve and review local changes before importing; no source changes were made.');
  let raw;
  try { raw = JSON.parse(bytes.toString('utf8')); } catch { throw new Error('Input is not valid JSON.'); }
  return { raw, source: { repository: SOURCE_REPOSITORY, commit, path: SOURCE_PATH }, sourceSha256: digest };
}

/** Create the destination once. Existing files/symlinks are never overwritten. */
export async function writeCatalogExclusive(outputPath, catalog) {
  const output = path.resolve(outputPath);
  if (!output.split(path.sep).includes('.local') || path.extname(output) !== '.json') {
    throw new Error('Output must be a .json file inside a .local directory, not a tracked data copy.');
  }
  const serialized = `${JSON.stringify(catalog, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_SOURCE_BYTES) throw new Error('Canonical catalog exceeds the 32 MiB loader limit.');
  await mkdir(path.dirname(output), { recursive: true, mode: 0o700 });
  // The check above is lexical. Resolve the directory so that a symlink inside .local cannot redirect the write elsewhere.
  const target = path.join(await realpath(path.dirname(output)), path.basename(output));
  if (!target.split(path.sep).includes('.local')) {
    throw new Error('Output must be a .json file inside a .local directory, not a tracked data copy.');
  }
  let handle;
  try {
    handle = await open(target, 'wx', 0o600);
  } catch (error) {
    if (error.code === 'EEXIST') throw new Error('Output already exists; choose a new versioned filename. It was not overwritten.');
    throw error;
  }
  try {
    await handle.writeFile(serialized, 'utf8');
    await handle.sync();
  } catch (error) {
    await handle.close();
    await unlink(target); // Only this call's newly created, incomplete output is removed.
    throw error;
  }
  await handle.close();
}

export function parseArguments(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  const result = {};
  for (let i = 0; i < args.length; i += 2) {
    const name = args[i];
    if (!['--input', '--output'].includes(name) || !args[i + 1] || args[i + 1].startsWith('--')) {
      throw new Error('Usage: node scripts/import-catalog.mjs --input <local-source.json> --output <.local/catalog.json>');
    }
    const key = name.slice(2);
    if (result[key]) throw new Error(`Duplicate ${name} argument.`);
    result[key] = args[i + 1];
  }
  if (!result.input || !result.output) throw new Error('Both --input and --output are required; no implicit source or destination is used.');
  return result;
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  if (options.help) {
    console.log('Usage: node scripts/import-catalog.mjs --input <local-source.json> --output <.local/catalog.json>');
    console.log('Reads the committed OREUM-IT/jeju-tour POI snapshot; writes once; no network, inference, or source changes.');
    return;
  }
  const verified = await readVerifiedSource(options.input);
  const catalog = convertCatalog(verified.raw, verified.source, verified.sourceSha256);
  await writeCatalogExclusive(options.output, catalog);
  console.log(JSON.stringify({
    status: 'imported', dataMode: catalog.dataMode, commit: catalog.source.commit,
    inputRows: catalog.diagnostics.inputRows, importedRows: catalog.places.length,
    rejectedRows: catalog.diagnostics.rejectedRows, rejectionCounts: catalog.diagnostics.rejectionCounts,
    productionReady: false,
  }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(`Import failed: ${error.message}`); process.exitCode = 1; });
}
