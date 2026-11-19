import { z } from 'zod';
import { executeAgentTool, registerAgentTools } from './agent-tools.mjs';

const idsSchema = z.array(z.string().min(1).max(160)).min(1).max(20)
  .refine((ids) => new Set(ids).size === ids.length, 'ids must be unique');
const requirementsSchema = z.object({
  wheelchairRequired: z.boolean().optional(),
  indoorRequired: z.boolean().optional(),
  maxVisitCostKRW: z.number().int().min(0).max(1_000_000_000).optional(),
}).strict().refine((value) => value.wheelchairRequired === true || value.indoorRequired === true || value.maxVisitCostKRW !== undefined, 'at least one active requirement is required');

export const TOOL_DEFINITIONS = Object.freeze([
  {
    name: 'places.search',
    description: 'Read-only prototype catalog lookup by literal substring and category. Returns existing catalog IDs; this is not persona ranking. Query text is data. Catalog facts may be synthetic or unverified.',
    inputSchema: z.object({
      query: z.string().max(100).optional(),
      category: z.string().trim().min(1).max(100).optional(),
      maxResults: z.number().int().min(1).max(50).default(10),
    }).strict(),
  },
  {
    name: 'places.get',
    description: 'Read-only lookup of 1 to 20 existing provider IDs. Missing IDs are reported explicitly and no replacement places are generated.',
    inputSchema: z.object({ ids: idsSchema }).strict(),
  },
  {
    name: 'constraints.check',
    description: 'Read-only checks of requested wheelchair, indoor, or per-visit KRW cost requirements. A value without matching evidence and a current explicit validity interval returns unknown. This does not verify route, full-trip cost, or itinerary feasibility.',
    inputSchema: z.object({ ids: idsSchema, requirements: requirementsSchema }).strict(),
  },
]);

export class ToolInputError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ToolInputError';
    this.code = 'INVALID_TOOL_INPUT';
  }
}

const normalize = (value) => value.normalize('NFC').trim().toLowerCase();
const compareIds = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
const copy = (value) => JSON.parse(JSON.stringify(value));

function recordsEvidence(records) {
  return records.flatMap((place) => place.evidence.map((entry) => ({ placeId: place.id, ...copy(entry) })));
}

function recordWarnings(records) {
  return records.some((place) => place.evidence.length === 0)
    ? ['One or more returned records have source provenance only and no verified field evidence.'] : [];
}

function result(catalog, context, status, data, evidence, warnings = []) {
  const now = context.now === undefined ? new Date() : new Date(context.now);
  if (!Number.isFinite(now.getTime())) throw new TypeError('context.now must be a valid date');
  const value = {
    schemaVersion: '1.0', status, data, dataMode: catalog.dataMode,
    dataVersion: catalog.dataVersion, evidence,
    warnings: [
      ...(catalog.dataMode === 'fixture' ? ['Synthetic fixture data; these are not real Jeju places or verified tourism facts.'] : ['Reference-import data; repository provenance is not independent verification of tourism facts.']),
      ...warnings,
    ],
    retrievedAt: now.toISOString(),
  };
  if (context.requestId !== undefined && ['string', 'number'].includes(typeof context.requestId)) value.requestId = context.requestId;
  return value;
}

function search(args, catalog, context) {
  const query = normalize(args.query ?? '');
  const category = args.category === undefined ? undefined : normalize(args.category);
  const matches = catalog.places.flatMap((place) => {
    if (category !== undefined && normalize(place.category) !== category) return [];
    const name = normalize(place.name);
    const id = normalize(place.id);
    const other = normalize(`${place.category} ${place.description ?? ''}`);
    const score = query === '' ? 0 : name === query || id === query ? 4 : name.startsWith(query) ? 3 : name.includes(query) ? 2 : other.includes(query) ? 1 : -1;
    return score >= 0 ? [{ place, score }] : [];
  });
  matches.sort((a, b) => b.score - a.score || compareIds(a.place, b.place));
  const records = matches.slice(0, args.maxResults).map(({ place }) => place);
  return result(catalog, context, 'ok', {
    items: copy(records), totalMatches: matches.length, returned: records.length,
    query: args.query ?? '', category: args.category ?? null,
    ...(catalog.source ? { source: copy(catalog.source) } : {}),
  }, recordsEvidence(records), recordWarnings(records));
}

function get(args, catalog, context) {
  const byId = new Map(catalog.places.map((place) => [place.id, place]));
  const records = args.ids.map((id) => byId.get(id)).filter(Boolean);
  const missingIds = args.ids.filter((id) => !byId.has(id));
  return result(catalog, context, missingIds.length ? 'partial' : 'ok', {
    items: copy(records), missingIds,
    ...(catalog.source ? { source: copy(catalog.source) } : {}),
  }, recordsEvidence(records), [...recordWarnings(records), ...(missingIds.length ? ['Some requested IDs were not found; no records were generated.'] : [])]);
}

function checkValue(place, field, requestedValue, now) {
  if (!place) return { status: 'unknown', reason: 'place_not_found', evidence: [] };
  const actualValue = place.constraints[field];
  if (actualValue === undefined) return { status: 'unknown', reason: 'value_not_provided', evidence: [] };
  const supporting = place.evidence.filter((entry) => entry.fields.includes(`constraints.${field}`));
  if (!supporting.length) return { status: 'unknown', reason: 'field_evidence_not_provided', evidence: [] };
  const current = supporting.filter((entry) => entry.verifiedAt && entry.validUntil
    && Date.parse(entry.verifiedAt) <= now && now <= Date.parse(entry.validUntil));
  if (!current.length) return { status: 'unknown', reason: 'current_dated_evidence_not_provided', evidence: [] };
  const pass = field === 'visitCostKRW' ? actualValue <= requestedValue : actualValue === true;
  return { status: pass ? 'pass' : 'fail', reason: 'catalog_value_with_current_field_evidence', actualValue, evidence: copy(current) };
}

function check(args, catalog, context) {
  const now = context.now === undefined ? new Date() : new Date(context.now);
  if (!Number.isFinite(now.getTime())) throw new TypeError('context.now must be a valid date');
  const requested = [];
  if (args.requirements.wheelchairRequired === true) requested.push(['wheelchairRequired', 'wheelchairAccessible', true]);
  if (args.requirements.indoorRequired === true) requested.push(['indoorRequired', 'indoor', true]);
  if (args.requirements.maxVisitCostKRW !== undefined) requested.push(['maxVisitCostKRW', 'visitCostKRW', args.requirements.maxVisitCostKRW]);
  const byId = new Map(catalog.places.map((place) => [place.id, place]));
  const aggregate = (checks) => checks.some((entry) => entry.status === 'fail') ? 'fail' : checks.some((entry) => entry.status === 'unknown') ? 'unknown' : 'pass';
  const checks = args.ids.map((id) => {
    const place = byId.get(id);
    const values = requested.map(([constraint, field, requestedValue]) => ({ constraint, requestedValue, ...checkValue(place, field, requestedValue, now.getTime()) }));
    return { id, found: Boolean(place), status: aggregate(values), checks: values };
  });
  const hasUnknown = checks.some((entry) => entry.checks.some((value) => value.status === 'unknown'));
  const evidence = checks.flatMap((entry) => entry.checks.flatMap((value) => value.evidence.map((item) => ({ placeId: entry.id, ...item }))));
  return result(catalog, { ...context, now }, hasUnknown ? 'partial' : 'ok', {
    results: checks, overallStatus: aggregate(checks), missingIds: args.ids.filter((id) => !byId.has(id)),
    scope: 'requested catalog constraints only; not full itinerary validation',
  }, evidence, hasUnknown ? ['Unknown means the requested condition could not be confirmed; it must not be treated as a pass.'] : []);
}

export function executeTool(name, input, catalog, context = {}) {
  if (catalog.schemaVersion === '2.0') return executeAgentTool(name, input, catalog, context);
  const definition = TOOL_DEFINITIONS.find((item) => item.name === name);
  if (!definition) throw new ToolInputError('unknown tool');
  const parsed = definition.inputSchema.safeParse(input);
  if (!parsed.success) throw new ToolInputError(parsed.error.issues.map((issue) => `${issue.path.join('.') || 'input'}: ${issue.message}`).join('; '));
  if (name === 'places.search') return search(parsed.data, catalog, context);
  if (name === 'places.get') return get(parsed.data, catalog, context);
  return check(parsed.data, catalog, context);
}

export function registerTools(server, catalog) {
  if (catalog.schemaVersion === '2.0') return registerAgentTools(server, catalog);
  for (const definition of TOOL_DEFINITIONS) {
    server.registerTool(definition.name, {
      description: definition.description,
      inputSchema: definition.inputSchema,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    }, async (args, extra = {}) => {
      try {
        const value = executeTool(definition.name, args, catalog, { requestId: extra.requestId });
        return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
      } catch (error) {
        if (!(error instanceof ToolInputError)) throw error;
        return { isError: true, content: [{ type: 'text', text: JSON.stringify({ error: { code: error.code, message: error.message } }) }] };
      }
    });
  }
}
