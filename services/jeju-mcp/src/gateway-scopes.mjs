// Volatile, server-owned execution scopes. Restarting the MCP loses all scopes
// and therefore refuses outstanding calls; durable replay protection lives in Gateway.
import { isDeepStrictEqual } from 'node:util';
import { z } from 'zod';
import { AGENT_TOOL_DEFINITIONS, candidatePlaces, executeAgentTool } from './agent-tools.mjs';
import { parseYmd, runtimeRequirementEvidence, TIME } from './constraint-evidence.mjs';
import { asksMinimalWalk, isRoutePlace } from './minimal-walk.mjs';
import { createRuntimeValidator, RecommendationValidationError } from './recommendation-validation.mjs';

export const GATEWAY_TOOL_CALL_LIMIT = 8;
const REQUEST_ID = /^gw-[a-f0-9]{32}$/u;
const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => record(value) && Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value, key));
const copy = (value) => structuredClone(value);
const aggregate = (entries) => entries.some(entry => entry.status === 'fail') ? 'fail'
  : entries.some(entry => entry.status === 'unknown') ? 'unknown' : 'pass';
const definitions = new Map(AGENT_TOOL_DEFINITIONS.map(definition => [definition.name, definition]));
const confirmationTopics = Object.freeze({ indoor_required: 'indoor_unverified', open_at: 'hours_unverified',
  wheelchair: 'wheelchair_unverified', budget_cap: 'cost_unknown' });
const WITHHELD_ROUTES_WARNING = 'Route places (trails, Olle courses, bike routes) are left out: the original request asks for minimal walking.';
const checkArguments = z.object({
  ids: definitions.get('places.get').inputSchema.shape.ids,
  requirements: z.object({
    wheelchairRequired: z.boolean().optional(), indoorRequired: z.boolean().optional(),
    maxVisitCostKRW: z.number().int().min(0).max(1_000_000_000).optional(),
    openAt: z.object({ date: z.string().refine(value => {
      try { parseYmd(value); return true; } catch { return false; }
    }), time: z.string().regex(TIME) }).strict().optional(),
  }).strict().optional(),
}).strict();

export class GatewayScopeError extends Error {
  constructor(status, code) { super(code); this.name = 'GatewayScopeError'; this.status = status; this.code = code; }
}
const require = (condition, status, code) => { if (!condition) throw new GatewayScopeError(status, code); };

export function createGatewayScopes({ catalog, now, maxConcurrent = 2 } = {}) {
  require(Number.isInteger(maxConcurrent) && maxConcurrent >= 1 && maxConcurrent <= 4,
    503, 'INVALID_GATEWAY_CONCURRENCY');
  const clock = () => {
    const value = typeof now === 'function' ? now() : now ?? Date.now();
    const ms = value instanceof Date ? value.getTime() : typeof value === 'number' ? value : Date.parse(value);
    require(Number.isFinite(ms), 503, 'GATEWAY_CLOCK_UNAVAILABLE');
    return ms;
  };
  const validator = createRuntimeValidator({ catalog, now: clock });
  const active = new Map();
  // Keep a bounded-lifetime tombstone after release so a duplicate cannot reset its budget.
  const seen = new Map();
  const prune = (at) => {
    for (const [id, scope] of active) if (scope.deadlineMs <= at) active.delete(id);
    for (const [id, deadline] of seen) if (deadline <= at) seen.delete(id);
  };
  const lookup = (requestId) => {
    require(typeof requestId === 'string' && REQUEST_ID.test(requestId), 400, 'INVALID_GATEWAY_REQUEST_ID');
    const scope = active.get(requestId);
    require(scope !== undefined, 404, 'GATEWAY_SCOPE_UNAVAILABLE');
    if (scope.deadlineMs <= clock()) {
      active.delete(requestId);
      throw new GatewayScopeError(410, 'GATEWAY_SCOPE_EXPIRED');
    }
    return scope;
  };
  const bound = (request) => {
    const scope = lookup(request?.requestId);
    require(isDeepStrictEqual(request, scope.prepared.request), 409, 'GATEWAY_REQUEST_MISMATCH');
    return scope;
  };

  function register(body) {
    require(exact(body, ['request', 'principal', 'deadlineMs']) && record(body.request)
      && typeof body.request.requestId === 'string' && REQUEST_ID.test(body.request.requestId)
      && typeof body.principal === 'string' && /^[a-f0-9]{64}$/u.test(body.principal)
      && Number.isSafeInteger(body.deadlineMs), 400, 'INVALID_GATEWAY_SCOPE');
    const at = clock();
    require(body.deadlineMs > at && body.deadlineMs <= at + 110_000, 400, 'INVALID_GATEWAY_DEADLINE');
    prune(at);
    require(!seen.has(body.request.requestId), 409, 'GATEWAY_SCOPE_DUPLICATE');
    require(active.size < maxConcurrent, 429, 'GATEWAY_SCOPE_BUSY');
    let prepared;
    try { prepared = validator.prepare(body.request); }
    catch (error) {
      if (error instanceof RecommendationValidationError) throw new GatewayScopeError(400, 'INVALID_GATEWAY_REQUEST');
      throw error;
    }
    const request = prepared.request;
    const registered = candidatePlaces(catalog, { ...request.filters, excludedIds: request.excludedIds });
    // Minimal walking (minimal-walk.mjs): like an original exclusion, a route place is not offered by any
    // scoped tool and never enters a response the server composes. The prepared request, its hashes and
    // candidate count, and the strict check stay those of the registered request.
    const candidates = asksMinimalWalk(request.query) ? registered.filter(place => !isRoutePlace(place)) : registered;
    active.set(request.requestId, { prepared: copy(prepared), principal: body.principal,
      deadlineMs: body.deadlineMs, calls: 0, registered, candidates,
      withheldRoutes: registered.length - candidates.length, ids: new Set(candidates.map(place => place.id)) });
    seen.set(request.requestId, body.deadlineMs);
    return copy(prepared);
  }

  // Server-owned response from explicit model-selected IDs: filters/exclusions and
  // verified failures come from the registered scope, verdicts and notices from
  // catalog evidence. The model's own action, claims and notices are never used.
  function compose(scope, ids, at) {
    const request = scope.prepared.request;
    const evidence = place => request.requirements.map(entry => {
      const date = entry.date ?? request.visitDate;
      const requirement = { ...entry, ...(date === undefined ? {} : { date }) };
      return { placeId: place.id, requirement: entry.type, verdict: runtimeRequirementEvidence(place, requirement, at).verdict };
    });
    const candidates = new Map(scope.candidates.map(place => [place.id, place]));
    const seen = new Set(), placeIds = [], requirementClaims = [], topics = new Set();
    for (const placeId of ids) {
      if (seen.has(placeId)) continue;
      seen.add(placeId);
      const place = candidates.get(placeId);
      if (!place) continue; // Includes original exclusions, filters, withheld route places and unknown catalog IDs.
      const claims = evidence(place);
      if (claims.some(claim => claim.verdict === 'fail')) continue;
      placeIds.push(placeId);
      requirementClaims.push(...claims);
      for (const claim of claims) if (claim.verdict === 'unknown') topics.add(confirmationTopics[claim.requirement]);
      if (placeIds.length === 5) break;
    }
    if (placeIds.length === 0) return null;
    return { schemaVersion: '1.0', requestId: request.requestId,
      action: topics.size ? 'cannot_confirm' : 'recommend', placeIds, requirementClaims,
      confirmationNotice: topics.size > 0, noticeTopics: [...topics] };
  }
  // Rule-action selection: the model returns only {"placeIds": [...]}. The server
  // decides impossible when no registered candidate survives verified failures;
  // otherwise the selection must keep at least one eligible ID or stay blocked.
  // Impossibility is judged on the registered candidates, as the unchanged strict
  // check judges it: when only withheld route places could still be recommended,
  // impossible cannot be established, so the selection is composed and, with no
  // offered ID left, stays blocked.
  function selection(scope, raw, input, at) {
    if (!Array.isArray(raw.placeIds) || raw.placeIds.length > 50
      || !raw.placeIds.every(id => typeof id === 'string' && id.length > 0 && id.length <= 160 && id === id.trim())) return null;
    const request = scope.prepared.request;
    const viable = scope.registered.some(place => request.requirements.every(entry => {
      const date = entry.date ?? request.visitDate;
      return runtimeRequirementEvidence(place, { ...entry, ...(date === undefined ? {} : { date }) }, at).verdict !== 'fail';
    }));
    const response = viable ? compose(scope, raw.placeIds, at)
      : { schemaVersion: '1.0', requestId: request.requestId, action: 'impossible', placeIds: [],
        requirementClaims: [], confirmationNotice: false, noticeTopics: [] };
    return response && validator.validate({ ...input, response });
  }

  function prepare(request) { return copy(bound(request).prepared); }
  function validate(body) {
    require(exact(body, ['request', 'response', 'expectedCatalogVersion'])
      && typeof body.expectedCatalogVersion === 'string', 400, 'INVALID_VALIDATION_ENVELOPE');
    const scope = bound(body.request);
    require(body.expectedCatalogVersion === scope.prepared.catalogVersion, 409, 'GATEWAY_CATALOG_MISMATCH');
    const input = { ...body, request: scope.prepared.request };
    const original = validator.validate(input);
    // Use the strict check's trusted server time; the second strict check of a
    // composed response still rejects it if evidence expires in between.
    const at = Date.parse(original.evaluatedAt);
    const raw = body.response;
    if (exact(raw, ['placeIds'])) {
      const checked = selection(scope, raw, input, at);
      return checked?.status === 'accepted' ? checked : original;
    }
    if (original.status === 'accepted' && original.response.placeIds.length <= 5) return original;

    // Recover only explicit model-selected IDs bound to this original request. Never
    // extract IDs from prose, invent replacements, or trust model-authored claims.
    if (!record(raw) || !['schemaVersion', 'requestId', 'action', 'placeIds'].every(key => Object.hasOwn(raw, key))
      || raw.schemaVersion !== '1.0' || raw.requestId !== scope.prepared.request.requestId
      || !['recommend', 'cannot_confirm'].includes(raw.action)
      || !Array.isArray(raw.placeIds) || raw.placeIds.length === 0 || raw.placeIds.length > 50
      || !raw.placeIds.every(id => typeof id === 'string' && id.length > 0 && id.length <= 160 && id === id.trim())) return original;
    const response = compose(scope, raw.placeIds, at);
    if (!response) return original;
    const checked = validator.validate({ ...input, response });
    return checked.status === 'accepted' ? checked : original;
  }
  function release(requestId) {
    require(typeof requestId === 'string' && REQUEST_ID.test(requestId), 400, 'INVALID_GATEWAY_REQUEST_ID');
    active.delete(requestId);
    prune(clock());
    return { status: 'released' };
  }
  function ready() {
    prune(clock());
    return { ready: true, activeScopes: active.size, maxConcurrentScopes: maxConcurrent, toolCallLimit: GATEWAY_TOOL_CALL_LIMIT,
      catalogSchemaVersion: catalog.schemaVersion, dataMode: catalog.dataMode,
      placeCount: catalog.places.length, dataVersion: catalog.dataVersion, readOnly: true, productionReady: false };
  }

  function check(scope, args, at) {
    const request = scope.prepared.request;
    require(request.requirements.length > 0, 400, 'GATEWAY_REQUIREMENTS_EMPTY');
    const value = executeAgentTool('places.get', { ids: args.ids }, catalog, { requestId: request.requestId, now: at });
    const fields = { indoor_required: ['indoor'], wheelchair: ['barrierFree', 'designations'], open_at: ['hours'], budget_cap: [] };
    const names = { indoor_required: 'indoorRequired', wheelchair: 'wheelchairRequired', open_at: 'openAt', budget_cap: 'maxVisitCostKRW' };
    const evidence = [];
    const results = value.data.items.map(place => {
      const checks = request.requirements.map(original => {
        const date = original.date ?? request.visitDate;
        const requirement = { ...original, ...(date === undefined ? {} : { date }) };
        const { verdict, ...detail } = runtimeRequirementEvidence(place, requirement, at);
        const supporting = fields[original.type].filter(field => place.attributes[field] !== undefined)
          .map(field => ({ field: `attributes.${field}`, ...copy(place.attributes[field]) }));
        evidence.push(...supporting.map(item => ({ placeId: place.id, ...item })));
        const requestedValue = original.type === 'open_at' ? { date, time: original.value } : original.value;
        return { constraint: names[original.type], requestedValue, status: verdict, ...detail,
          visitDate: date ?? null, evidence: supporting };
      });
      return { id: place.id, found: true, status: aggregate(checks), checks };
    });
    const hasUnknown = results.some(result => result.checks.some(entry => entry.status === 'unknown'));
    return { ...value, status: hasUnknown ? 'partial' : 'ok', evidence,
      data: { results, overallStatus: aggregate(results), missingIds: [],
        scope: 'registered original requirements only; not full itinerary validation',
        clock: { evaluatedAt: new Date(at).toISOString(), source: 'server_clock', timezone: 'Asia/Seoul',
          visitDate: request.visitDate ?? null } },
      warnings: [...value.warnings, 'All registered requirements and their individual dates are checked; model-supplied requirements do not replace them.',
        ...(hasUnknown ? ['Unknown must not be treated as a pass.'] : [])] };
  }

  function call(body) {
    // Reserve synchronously before validating tool arguments; invalid calls cannot
    // create unlimited retry work, and concurrent HTTP requests cannot overspend.
    const scope = lookup(body?.requestId);
    require(scope.calls < GATEWAY_TOOL_CALL_LIMIT, 429, 'GATEWAY_TOOL_BUDGET_EXHAUSTED');
    scope.calls += 1;
    require(exact(body, ['requestId', 'name', 'arguments']) && definitions.has(body.name), 400, 'INVALID_GATEWAY_TOOL_CALL');
    const schema = body.name === 'constraints.check' ? checkArguments : definitions.get(body.name).inputSchema;
    const parsed = schema.safeParse(body.arguments);
    require(parsed.success, 400, 'INVALID_GATEWAY_TOOL_ARGUMENTS');
    const args = parsed.data;
    const request = scope.prepared.request;
    const at = clock();
    require(at < scope.deadlineMs, 410, 'GATEWAY_SCOPE_EXPIRED');
    if (body.name === 'places.search') {
      require(args.maxResults <= 10, 400, 'GATEWAY_SEARCH_LIMIT_EXCEEDED');
      for (const field of ['category', 'theme', 'zone']) {
        require(args[field] === undefined || request.filters[field] === undefined
          || args[field] === request.filters[field], 400, 'GATEWAY_FILTER_CONFLICT');
        if (request.filters[field] !== undefined) args[field] = request.filters[field];
      }
      if (args.query === undefined && request.filters.text !== undefined) args.query = request.filters.text;
      // Applying additional filters to the original full candidate set computes
      // an intersection even when the two literal queries are different.
      const value = executeAgentTool(body.name, args, { ...catalog, places: scope.candidates }, { requestId: request.requestId, now: at });
      value.data.excludedIds = [...new Set([...request.excludedIds, ...(args.excludedIds ?? [])])];
      if (scope.withheldRoutes > 0) value.warnings.push(WITHHELD_ROUTES_WARNING);
      return value;
    }
    require(args.ids.every(id => scope.ids.has(id)), 403, 'GATEWAY_PLACE_OUTSIDE_SCOPE');
    if (body.name === 'constraints.check') return check(scope, args, at);
    return executeAgentTool(body.name, args, catalog, { requestId: request.requestId, now: at });
  }
  return Object.freeze({ register, prepare, validate, release, ready, call });
}
