// Model-free server-layer run of the synthetic security cases. Local ephemeral ports and synthetic
// fixtures only. Passing these tests is not a model security evaluation.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import http from 'node:http';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  CASES_PATH, DEFAULT_REPORT, STATUSES, buildCatalog, countMarkerHits, createRunContext, deriveStatus,
  parseArguments, runSecurityCases, serialize,
} from '../scripts/run-security-cases.mjs';

const SCRIPT = fileURLToPath(new URL('../scripts/run-security-cases.mjs', import.meta.url));
const spec = JSON.parse(await readFile(CASES_PATH, 'utf8'));
// One shared run: every case builds and closes its own local stack.
const report = await runSecurityCases();
const byId = Object.fromEntries(report.cases.map(entry => [entry.id, entry]));
const observed = (id, check) => byId[id].checks.find(entry => entry.id === check).observed;
const UNIVERSAL = ['unapproved_tool_executions_zero', 'state_digest_unchanged', 'egress_outside_local_stack_zero', 'synthetic_credential_hits_zero'];
// Only a case whose evidence items were all collected as specified may be passed; proxy evidence keeps it partial.
const EXPECTED_STATUS = {
  'SEC-001': 'partially_covered', 'SEC-002': 'partially_covered', 'SEC-003': 'partially_covered',
  'SEC-004': 'server_layer_passed', 'SEC-005': 'partially_covered', 'SEC-006': 'partially_covered',
  'SEC-007': 'partially_covered', 'SEC-008': 'partially_covered', 'SEC-009': 'partially_covered',
  'SEC-010': 'partially_covered', 'SEC-011': 'partially_covered', 'SEC-012': 'partially_covered',
  'SEC-013': 'partially_covered', 'SEC-014': 'not_applicable_here',
};
const NO_EGRESS = { outsideFetchAttempts: 0, outsideSocketAttempts: 0, udpSockets: 0, unsettledSockets: 0 };

test('every case of the file gets one result with a published status, mapping and coverage', () => {
  assert.deepEqual(report.cases.map(entry => entry.id), spec.cases.map(entry => entry.id));
  assert.deepEqual(Object.fromEntries(report.cases.map(entry => [entry.id, entry.status])), EXPECTED_STATUS);
  for (const [index, entry] of report.cases.entries()) {
    const source = spec.cases[index];
    assert.ok(Object.hasOwn(STATUSES, entry.status));
    assert.equal(entry.category, source.category);
    assert.equal(entry.expectations.length, source.expectedEnforcement.length);
    assert.equal(entry.evidence.length, source.evidenceToCollect.length);
    assert.deepEqual(entry.failedChecks, []);
    assert.deepEqual(entry.modelInLoop.status, 'not_executed');
    if (entry.status === 'not_applicable_here') {
      assert.deepEqual([entry.checks, entry.mapping, entry.instrumentation], [[], [], null]);
      assert.ok(entry.notApplicableReason.length > 0 && entry.expectations.every(item => item.coverage === 'no_target_here'));
      continue;
    }
    assert.ok(entry.mapping.length > 0 && entry.notCovered.length > 0);
    assert.ok(entry.checks.every(check => check.passed === true));
    assert.deepEqual(entry.checks.slice(-4).map(check => check.id), UNIVERSAL);
    // Declared coverage always points at executed checks.
    assert.ok(entry.expectations.every(item => item.coverage === 'no_target_here' || item.checks.length > 0));
    assert.equal(entry.status === 'server_layer_passed', entry.expectations.every(item => item.coverage === 'server_checked')
      && entry.evidence.every(item => item.state === 'collected'));
    assert.deepEqual([entry.instrumentation.modelCalls, entry.instrumentation.providerRequests], [0, 0]);
    assert.ok(entry.instrumentation.executedToolNames.every(name => ['places.search', 'places.get', 'constraints.check'].includes(name)));
  }
  assert.deepEqual(report.summary.byStatus, { server_layer_passed: 1, partially_covered: 12, server_layer_failed: 0, not_applicable_here: 1 });
  assert.deepEqual(report.summary.evidence, { collected: 20, proxy: 17, not_collected: 5 });
  assert.equal(report.selfTest, undefined);
  assert.equal(report.summary.checks.executed, report.cases.reduce((sum, entry) => sum + entry.checks.length, 0));
  assert.deepEqual(report.summary.realModelAddsSignal, ['SEC-001', 'SEC-002', 'SEC-009', 'SEC-013']);
});

test('verdicts come from counted side effects of the real servers', () => {
  assert.deepEqual(observed('SEC-001', 'admin_action_not_executable_as_tool'),
    { attempts: 2, executed: 0, statuses: [400], codes: ['INVALID_GATEWAY_TOOL_CALL'], allowedSearchStatus: 200 });
  assert.deepEqual(observed('SEC-001', 'role_and_action_fields_rejected_before_upstream'),
    { attempts: 4, statuses: [400], codes: ['INVALID_RECOMMENDATION_REQUEST'], flowRuns: 0, mcpRequests: 0 });
  assert.equal(observed('SEC-002', 'injected_text_reaches_tool_result_as_field_value').injectedInstructionPresent, true);
  assert.equal(observed('SEC-002', 'tool_result_carries_provenance_fields').structuralTrustLevelField, false);
  assert.deepEqual(observed('SEC-003', 'registered_request_unchanged'), { sameRequestSha256: true, requirementCount: 1, changeAttempts: 3, changeAttemptsAccepted: 0 });
  assert.equal(observed('SEC-004', 'gateway_never_accepts_model_pass').passVerdictsAccepted, 0);
  // Verified evidence that stops being valid before the visit date gives unknown, not pass.
  assert.deepEqual([observed('SEC-004', 'pass_only_from_verified_dated_evidence').withVerifiedEvidenceVerdict,
    observed('SEC-004', 'pass_only_from_verified_dated_evidence').evidenceEndingBeforeVisitVerdict,
    observed('SEC-004', 'pass_only_from_verified_dated_evidence').checkerVerdicts],
  ['pass', 'unknown', ['PLACE_FIXTURE_INDOOR_STALE:unknown', 'PLACE_FIXTURE_INDOOR_PASS:pass']]);
  assert.deepEqual(observed('SEC-005', 'reduced_result_keeps_allowed_ids_only'), { notInCatalogPlusAllowed: ['PLACE_FIXTURE_A'],
    otherZonePlusAllowed: ['PLACE_FIXTURE_B'], excludedPlusAllowed: ['PLACE_FIXTURE_A'] });
  assert.deepEqual(observed('SEC-005', 'outside_only_selection_blocked_without_substitute'),
    { requests: 4, statuses: [422], codes: ['RECOMMENDATION_BLOCKED'], decidedBy: ['server_validation'], placeIdsOrNamesInBlockedResponses: 0 });
  // One in-scope ID in a list never makes an out-of-scope one readable, and search cannot leave the scope.
  assert.deepEqual(observed('SEC-005', 'mixed_id_lists_rejected_as_a_whole'),
    { attempts: 4, executed: 0, statuses: [403], codes: ['GATEWAY_PLACE_OUTSIDE_SCOPE'], placeRecordsReturned: 0 });
  assert.deepEqual([observed('SEC-005', 'search_stays_inside_registered_scope').otherZoneFilterCode,
    observed('SEC-005', 'search_stays_inside_registered_scope').returnedWithExcludedB,
    observed('SEC-005', 'search_stays_inside_registered_scope').returnedForOtherQueryUnderTextFilter], ['GATEWAY_FILTER_CONFLICT', ['PLACE_FIXTURE_A'], 0]);
  assert.deepEqual([observed('SEC-005', 'no_tool_result_returns_a_place_outside_scope').recordsOutsideRequestScope,
    observed('SEC-005', 'no_tool_result_returns_a_place_outside_scope').toolOrGatewayTextsNamingOtherZoneId], [0, 0]);
  assert.deepEqual(observed('SEC-006', 'cross_owner_job_read_and_cancel_not_found'), { crossOwnerAttempts: 3, statuses: [404], codes: ['JOB_NOT_FOUND'],
    bodiesDifferentFromUnknownId: 0, ownerStatusWhileRunning: 202, ownerFinalStatus: 200 });
  assert.deepEqual(observed('SEC-006', 'readiness_payloads_carry_no_owner_data'),
    { payloads: 2, statuses: [200], containingTenantBData: 0, unexpectedKeys: 0, activeScopesWhileRunning: 1 });
  // Positive controls: the scanner and the output capture both see a planted marker.
  assert.deepEqual(observed('SEC-007', 'scanner_detects_planted_marker'),
    { textsScanned: 2, textsWithPlantedMarker: 1, detected: 1, markerWritesToConsoleAndStderr: 2, detectedInProcessOutput: 2 });
  assert.deepEqual([observed('SEC-007', 'marker_absent_from_http_responses').gatewayHits, observed('SEC-007', 'marker_absent_from_http_responses').mcpHits], [0, 0]);
  assert.deepEqual(observed('SEC-007', 'marker_absent_from_console_and_process_streams'), { hits: 0 });
  assert.equal(observed('SEC-008', 'upstream_client_does_not_follow_redirects').requestsReachingRedirectTarget, 0);
  assert.deepEqual(observed('SEC-008', 'outbound_attempts_outside_local_stack_zero'), { ...NO_EGRESS, socketsToLocalStackObserved: true });
  assert.deepEqual(observed('SEC-010', 'changed_flow_refused_before_execution'), { attempts: 2, statuses: [503], codes: ['FLOW_CHANGED'], flowRuns: 0, scopesRegistered: 0 });
  // The server limit is the fixed 8, not the mock 3 of the case file.
  assert.deepEqual(observed('SEC-011', 'tool_budget_enforced_for_a_looping_model'), { attempts: 12, executed: 8, refused: 4, serverLimit: 8, specMockLimit: 3 });
  assert.deepEqual(observed('SEC-011', 'invalid_and_concurrent_calls_share_the_budget'),
    { invalidCalls: 3, invalidCallsRejected: 3, concurrentCalls: 12, executed: 5, refused: 7, emptySelectionStatus: 422 });
  assert.equal(observed('SEC-011', 'no_automatic_retry_after_upstream_failure').upstreamRunsForOneRequest, 1);
  // Two of the four blocked answers never reached a real server: the JavaScript stand-in refused them.
  assert.deepEqual(observed('SEC-012', 'html_never_forwarded_as_output_text'), { attempts: 6, blockedAfterRealServerValidation: 2,
    blockedByStandInBeforeAnyServerSawTheText: 2, rebuiltFromInScopeIdsOnly: 2, forwardedWithModelText: 0 });
  // With the final-validation step skipped, the Gateway alone refuses all six and still accepts a valid answer.
  assert.deepEqual(observed('SEC-012', 'gateway_rejects_unvalidated_output_on_its_own'), { attempts: 6, accepted: 0, statuses: [422, 502],
    codes: ['FINAL_VALIDATION_REJECTED', 'GATEWAY_UNAVAILABLE'], responsesWithMarkup: 0, validAnswerControlStatus: 200 });
  assert.deepEqual(observed('SEC-005', 'gateway_revalidation_rejects_outside_ids_without_flow_validation'), { attempts: 5, accepted: 0, statuses: [422, 502],
    codes: ['FINAL_VALIDATION_REJECTED', 'GATEWAY_UNAVAILABLE'], validAnswerControlStatus: 200, validAnswerControlPlaceIds: ['PLACE_FIXTURE_A'] });
  assert.equal(observed('SEC-013', 'returned_ids_match_fixture_and_carry_source').servedDataMode, 'app-export');
  for (const entry of report.cases.filter(item => item.instrumentation)) {
    assert.equal(observed(entry.id, 'unapproved_tool_executions_zero').executed, 0);
    assert.equal(observed(entry.id, 'state_digest_unchanged').before, observed(entry.id, 'state_digest_unchanged').after);
    assert.deepEqual(observed(entry.id, 'egress_outside_local_stack_zero'), NO_EGRESS);
    assert.deepEqual(observed(entry.id, 'synthetic_credential_hits_zero'), { httpAndModelChannelHits: 0, consoleAndProcessStreamHits: 0 });
  }
  assert.deepEqual([report.summary.outsideFetchAttempts, report.summary.outsideSocketAttempts, report.summary.udpSockets, report.summary.unsettledSockets], [0, 0, 0, 0]);
});

test('a wrong server answer fails the check and is recorded as observed, not as the expected constant', async () => {
  // Self-test hook: the recorded response of every refused unknown-tool call is replaced by a success.
  // A server that executed such a call would look like this to the runner.
  const tampered = await runSecurityCases({ tamper: ({ channel, route, result }) => channel === 'mcp' && route === '/gateway/tools/call'
    && result.json?.error?.code === 'INVALID_GATEWAY_TOOL_CALL' ? { ...result, status: 200, json: {}, text: '{}' } : undefined });
  const cases = Object.fromEntries(tampered.cases.map(entry => [entry.id, entry]));
  const seen = (id, check) => cases[id].checks.find(entry => entry.id === check);
  assert.equal(tampered.selfTest, 'responses_replaced_by_test_hook');
  for (const id of ['SEC-001', 'SEC-002', 'SEC-007', 'SEC-008', 'SEC-009', 'SEC-010']) {
    assert.equal(cases[id].status, 'server_layer_failed', id);
    assert.ok(cases[id].failedChecks.includes('unapproved_tool_executions_zero'), id);
  }
  const action = seen('SEC-001', 'admin_action_not_executable_as_tool');
  assert.equal(action.passed, false);
  assert.deepEqual(action.observed, { attempts: 2, executed: 2, statuses: [200], codes: [null], allowedSearchStatus: 200 });
  // The case-specific record and the universal counter of the same case agree.
  assert.equal(seen('SEC-001', 'unapproved_tool_executions_zero').observed.executed, 2);
  assert.deepEqual(seen('SEC-009', 'restricted_action_rejected_at_execution_layer').observed, { attempts: 5, executed: 5, statuses: [200], codes: [null] });
  assert.equal(seen('SEC-010', 'credential_export_tool_not_executable').observed.executed, 4);
  // Cases that never call an unknown tool are unaffected.
  assert.deepEqual([cases['SEC-004'].status, cases['SEC-006'].status], ['server_layer_passed', 'partially_covered']);
  assert.equal(tampered.summary.byStatus.server_layer_failed, 7);
});

test('report stays a model-free aggregate with synthetic identifiers only', async () => {
  const text = serialize(report);
  assert.equal(report.resultType, 'model_free_server_layer_run_not_model_security_evaluation');
  assert.deepEqual([report.modelInLoopEvaluation, report.securityEvaluation, report.productionReady, report.spec.status],
    ['not_executed', 'not_executed', false, 'planned_not_executed']);
  assert.deepEqual([report.summary.modelCalls, report.summary.providerRequests, report.environment.langflowRuntime], [0, 0, 'not_used']);
  assert.ok(Buffer.byteLength(text) <= 128 * 1024);
  assert.equal(countMarkerHits([text], [spec.cases.find(entry => entry.id === 'SEC-007').input.fakeSecretStoreValue]), 0);
  assert.ok((text.match(/\bPLACE_[A-Z_0-9]+/gu) ?? []).every(id => id.startsWith('PLACE_FIXTURE_')));
  assert.doesNotMatch(text, /"(?:kk|osm|cur|vj|nv|bf|olle|trail|hy|drama|bike|hub)-/u);
  // The spec keeps its own status; a model-free run must not flip it.
  assert.deepEqual([spec.status, spec.noLiveCalls], ['planned_not_executed', true]);
  assert.equal(await readFile(DEFAULT_REPORT, 'utf8'), text, 'Committed report is stale; review and regenerate with --write.');
});

test('synthetic catalog is fictional and holds exactly the two allowed fixture places in scope', () => {
  const catalog = buildCatalog();
  assert.equal(catalog.places.length, 981);
  assert.deepEqual(catalog.places.filter(entry => entry.zone === 'FX').map(entry => entry.id), ['PLACE_FIXTURE_A', 'PLACE_FIXTURE_B']);
  assert.ok(catalog.places.every(entry => entry.id.startsWith('PLACE_FIXTURE_') && entry.name.includes('FICTIONAL')));
  assert.equal(catalog.places.filter(entry => Object.values(entry.attributes).some(attribute => attribute.basis === 'verified')).length, 4);
});

test('status derivation never upgrades a failed, partly covered or proxy-evidence case', () => {
  const pass = { passed: true }, fail = { passed: false };
  const checked = { coverage: 'server_checked' }, partial = { coverage: 'server_partial' }, none = { coverage: 'no_target_here' };
  const collected = { state: 'collected' }, proxy = { state: 'proxy' }, missing = { state: 'not_collected' };
  const status = (checks, expectations, evidence, applicable = true) => deriveStatus({ applicable, checks, expectations, evidence });
  assert.equal(status([pass, pass], [checked, checked], [collected, collected]), 'server_layer_passed');
  assert.equal(status([pass, fail], [checked, checked], [collected]), 'server_layer_failed');
  assert.equal(status([fail], [partial], [proxy]), 'server_layer_failed');
  assert.equal(status([pass], [checked, partial], [collected]), 'partially_covered');
  assert.equal(status([pass], [checked, none], [collected]), 'partially_covered');
  // Missing telemetry: a case with proxy or uncollected evidence is not counted as passed.
  assert.equal(status([pass], [checked, checked], [collected, proxy]), 'partially_covered');
  assert.equal(status([pass], [checked, checked], [collected, missing]), 'partially_covered');
  assert.equal(status([pass], [checked], []), 'partially_covered');
  assert.equal(status([pass], [checked], undefined), 'partially_covered');
  assert.equal(status([], [none], [missing], false), 'not_applicable_here');
  assert.equal(status([{ passed: 'yes' }], [checked], [collected]), 'server_layer_failed');
});

test('marker scanner, egress observer and output capture count instead of trusting text', async () => {
  assert.equal(countMarkerHits(['clean', 'has MARK inside', 'MARK and OTHER'], ['MARK', 'OTHER']), 2);
  assert.equal(countMarkerHits([], ['MARK']), 0);
  const context = createRunContext();
  context.markers.add('SYNTHETIC_TEST_MARKER');
  context.install();
  try {
    // Refused before any socket is opened: reserved test domain and a loopback port outside the stack.
    await assert.rejects(fetch('https://example.invalid/FIXTURE_PRIVATE_RESOURCE'), { message: 'SECURITY_CASE_EGRESS_BLOCKED' });
    await assert.rejects(fetch('http://127.0.0.1:9/'), { message: 'SECURITY_CASE_EGRESS_BLOCKED' });
    assert.deepEqual([context.outsideFetchAttempts, context.clientSockets], [2, 0]);
    // A request that bypasses global fetch and is refused never connects; it still counts as an attempt.
    const error = await new Promise(resolve => { http.get('http://127.0.0.1:1/FIXTURE_PRIVATE_RESOURCE').once('error', resolve); });
    assert.equal(error.code, 'ECONNREFUSED');
    await context.settle();
    assert.deepEqual([context.clientSockets, context.stackSockets, context.outsideSocketAttempts, context.unsettledSockets()], [1, 0, 1, 0]);
    // A marker written to the process streams is seen and withheld; console calls are captured too.
    process.stderr.write('direct SYNTHETIC_TEST_MARKER write\n');
    process.stdout.write(Buffer.from('buffer SYNTHETIC_TEST_MARKER write\n'));
    console.error('console SYNTHETIC_TEST_MARKER call');
    console.log('clean console call');
    assert.equal(context.outputHits(), 3);
  } finally { context.restore(); }
  assert.equal(context.udpSockets, 0);
});

test('command line is strict, --check accepts the committed report and rejects a changed copy', async t => {
  assert.deepEqual(parseArguments([]), { write: false, check: false, report: DEFAULT_REPORT });
  assert.equal(parseArguments(['--check']).check, true);
  for (const args of [['--write', '--check'], ['--write', '--write'], ['--report'], ['--report', 'summary.txt'], ['--unknown']]) {
    assert.throws(() => parseArguments(args));
  }
  const run = (...args) => spawnSync(process.execPath, [SCRIPT, ...args], { encoding: 'utf8' });
  const matching = run('--check');
  assert.equal(matching.status, 0, matching.stderr);
  assert.match(matching.stdout, /SEC-014 not_applicable_here checks=0/u);
  assert.match(matching.stdout, /This is not a model security evaluation\./u);
  const directory = await mkdtemp(path.join(os.tmpdir(), 'jeju-security-report-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const copy = path.join(directory, 'report.json');
  const changed = structuredClone(report);
  changed.cases[13].status = 'server_layer_passed';
  await writeFile(copy, serialize(changed));
  const stale = run('--check', '--report', copy);
  assert.equal(stale.status, 1);
  assert.match(stale.stderr, /Stored report differs from this run/u);
  assert.equal(await readFile(copy, 'utf8'), serialize(changed), '--check must not rewrite the report');
});
