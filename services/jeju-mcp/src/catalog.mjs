import { createHash } from 'node:crypto';
import { open } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { validateAgentCatalog } from './agent-catalog.mjs';

export const MAX_CATALOG_BYTES = 32 * 1024 * 1024;
export const MAX_PLACES = 100_000;
const EVIDENCE_FIELDS = new Set([
  'id', 'name', 'category', 'lat', 'lon', 'description',
  'constraints.wheelchairAccessible', 'constraints.indoor', 'constraints.visitCostKRW',
]);
const CONSTRAINT_FIELDS = new Set(['wheelchairAccessible', 'indoor', 'visitCostKRW']);

export class CatalogValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = 'CatalogValidationError';
    this.code = 'INVALID_CATALOG';
  }
}

function requireValue(condition, message) {
  if (!condition) throw new CatalogValidationError(message);
}

function object(value, label) {
  requireValue(value !== null && typeof value === 'object' && !Array.isArray(value), `${label} must be an object`);
}

function text(value, label, max) {
  requireValue(typeof value === 'string' && value.trim().length > 0 && value.length <= max, `${label} must be a nonempty string of at most ${max} characters`);
  requireValue(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(value), `${label} contains unsupported control characters`);
}

function onlyKeys(value, allowed, label) {
  requireValue(Object.keys(value).every((key) => allowed.has(key)), `${label} contains unsupported fields`);
}

function date(value, label) {
  requireValue(typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/u.test(value), `${label} must be a UTC ISO timestamp`);
  const parsed = Date.parse(value);
  requireValue(Number.isFinite(parsed), `${label} must be a valid timestamp`);
  // Date.parse can normalize impossible calendar dates such as February 30.
  requireValue(new Date(parsed).toISOString().slice(0, 19) === value.slice(0, 19), `${label} must be a valid calendar timestamp`);
}

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

export function validateCatalog(input, { dataVersion } = {}) {
  object(input, 'catalog');
  onlyKeys(input, new Set(['schemaVersion', 'dataMode', 'source', 'places', 'diagnostics']), 'catalog');
  requireValue(input.schemaVersion === '1.0', 'schemaVersion must be 1.0');
  requireValue(['fixture', 'reference-import'].includes(input.dataMode), 'dataMode must be fixture or reference-import');
  requireValue(Array.isArray(input.places) && input.places.length > 0 && input.places.length <= MAX_PLACES, `places must contain 1..${MAX_PLACES} records`);
  if (input.source !== undefined) {
    object(input.source, 'source');
    onlyKeys(input.source, new Set(['repository', 'commit', 'path']), 'source');
    for (const key of Object.keys(input.source)) text(input.source[key], `source.${key}`, 1000);
  }
  if (input.diagnostics !== undefined) object(input.diagnostics, 'diagnostics');

  const ids = new Set();
  input.places.forEach((place, index) => {
    const label = `places[${index}]`;
    object(place, label);
    onlyKeys(place, new Set(['id', 'name', 'category', 'lat', 'lon', 'description', 'evidence', 'constraints']), label);
    text(place.id, `${label}.id`, 160);
    requireValue(place.id === place.id.trim(), `${label}.id must not have surrounding whitespace`);
    requireValue(!ids.has(place.id), `${label}.id is duplicated`);
    ids.add(place.id);
    text(place.name, `${label}.name`, 200);
    text(place.category, `${label}.category`, 100);
    requireValue(typeof place.lat === 'number' && Number.isFinite(place.lat) && place.lat >= -90 && place.lat <= 90, `${label}.lat must be a finite latitude`);
    requireValue(typeof place.lon === 'number' && Number.isFinite(place.lon) && place.lon >= -180 && place.lon <= 180, `${label}.lon must be a finite longitude`);
    if (place.description !== undefined) text(place.description, `${label}.description`, 2000);
    object(place.constraints, `${label}.constraints`);
    onlyKeys(place.constraints, CONSTRAINT_FIELDS, `${label}.constraints`);
    for (const key of ['wheelchairAccessible', 'indoor']) {
      if (place.constraints[key] !== undefined) requireValue(typeof place.constraints[key] === 'boolean', `${label}.constraints.${key} must be boolean`);
    }
    if (place.constraints.visitCostKRW !== undefined) {
      requireValue(Number.isSafeInteger(place.constraints.visitCostKRW) && place.constraints.visitCostKRW >= 0 && place.constraints.visitCostKRW <= 1_000_000_000, `${label}.constraints.visitCostKRW must be an integer from 0 to 1000000000`);
    }
    requireValue(Array.isArray(place.evidence) && place.evidence.length <= 20, `${label}.evidence must be an array with at most 20 entries`);
    place.evidence.forEach((entry, evidenceIndex) => {
      const evidenceLabel = `${label}.evidence[${evidenceIndex}]`;
      object(entry, evidenceLabel);
      onlyKeys(entry, new Set(['sourceId', 'recordId', 'fields', 'verifiedAt', 'validUntil', 'note']), evidenceLabel);
      text(entry.sourceId, `${evidenceLabel}.sourceId`, 500);
      if (entry.recordId !== undefined) text(entry.recordId, `${evidenceLabel}.recordId`, 160);
      if (entry.note !== undefined) text(entry.note, `${evidenceLabel}.note`, 500);
      requireValue(Array.isArray(entry.fields) && entry.fields.length > 0 && entry.fields.length <= EVIDENCE_FIELDS.size && entry.fields.every((field) => EVIDENCE_FIELDS.has(field)) && new Set(entry.fields).size === entry.fields.length, `${evidenceLabel}.fields must contain unique supported field paths`);
      if (entry.verifiedAt !== undefined) date(entry.verifiedAt, `${evidenceLabel}.verifiedAt`);
      if (entry.validUntil !== undefined) date(entry.validUntil, `${evidenceLabel}.validUntil`);
      if (entry.verifiedAt !== undefined && entry.validUntil !== undefined) requireValue(Date.parse(entry.verifiedAt) <= Date.parse(entry.validUntil), `${evidenceLabel} has a reversed validity interval`);
    });
  });

  let serialized;
  try {
    serialized = JSON.stringify(input);
  } catch {
    throw new CatalogValidationError('catalog must be serializable JSON');
  }
  requireValue(Buffer.byteLength(serialized) <= MAX_CATALOG_BYTES, 'catalog exceeds the byte limit');
  if (dataVersion !== undefined) requireValue(typeof dataVersion === 'string' && /^sha256:[a-f0-9]{64}$/u.test(dataVersion), 'dataVersion must be a SHA-256 content digest');
  const copy = JSON.parse(serialized);
  copy.dataVersion = dataVersion ?? `sha256:${createHash('sha256').update(serialized).digest('hex')}`;
  return freeze(copy);
}

/** Operator-selected v2 catalog. Keep evidence grades intact; never flatten them into verified v1 facts. */
export function validateAgentServerCatalog(input, { dataVersion } = {}) {
  object(input, 'agent catalog');
  onlyKeys(input, new Set(['schemaVersion', 'kind', 'dataMode', 'source', 'themeIds', 'basisPolicy', 'sources', 'places', 'diagnostics']), 'agent catalog');
  let serialized;
  try { serialized = JSON.stringify(input); } catch { throw new CatalogValidationError('agent catalog must be serializable JSON'); }
  requireValue(Buffer.byteLength(serialized) <= MAX_CATALOG_BYTES, 'agent catalog exceeds the byte limit');
  if (dataVersion !== undefined) requireValue(typeof dataVersion === 'string' && /^sha256:[a-f0-9]{64}$/u.test(dataVersion), 'dataVersion must be a SHA-256 content digest');
  const copy = JSON.parse(serialized);
  copy.dataVersion = dataVersion ?? `sha256:${createHash('sha256').update(serialized).digest('hex')}`;
  try { return validateAgentCatalog(copy); }
  catch { throw new CatalogValidationError('agent catalog does not match the v2 schema'); }
}

export async function loadCatalog(catalogPath) {
  requireValue(typeof catalogPath === 'string' && isAbsolute(catalogPath), 'an explicit absolute operator catalog path is required');
  const handle = await open(catalogPath, 'r');
  try {
    const info = await handle.stat();
    requireValue(info.isFile(), 'catalog path must identify a regular file');
    requireValue(info.size > 0 && info.size <= MAX_CATALOG_BYTES, 'catalog exceeds the byte limit or is empty');
    const bytes = await handle.readFile();
    requireValue(bytes.length <= MAX_CATALOG_BYTES, 'catalog exceeds the byte limit');
    let input;
    try {
      input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch {
      throw new CatalogValidationError('catalog must contain valid UTF-8 JSON');
    }
    const options = { dataVersion: `sha256:${createHash('sha256').update(bytes).digest('hex')}` };
    return input?.schemaVersion === '2.0' ? validateAgentServerCatalog(input, options) : validateCatalog(input, options);
  } finally {
    await handle.close();
  }
}
