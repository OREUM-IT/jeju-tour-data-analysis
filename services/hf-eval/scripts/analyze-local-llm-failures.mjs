#!/usr/bin/env node
/**
 * Failure breakdown of a scored local LLM run: expected vs returned action, whether the server could have known the action
 * from runtime facts (candidate pool, request date conflict), and retrieval coverage of the shown candidates.
 * Gold is read only to label outcomes. Writes a publishable aggregate (counts only, no queries or IDs).
 * Usage: node scripts/analyze-local-llm-failures.mjs --set <.local/set.json> --plan <.local/plan.json> --catalog <.local/catalog.json>
 *   --diagnostics <.local/diag.json> --scores <.local/scores.json> --report <reports/x.json> [--label name]
 */
import { readFile, writeFile } from 'node:fs/promises';
import { inspectValidatedRequest } from '../../jeju-mcp/src/validated-request-adapter.mjs';
import { candidatePlaces } from '../../jeju-mcp/src/agent-tools.mjs';

const args = process.argv.slice(2);
const opt = (key) => { const i = args.indexOf(`--${key}`); return i >= 0 ? args[i + 1] : undefined; };
for (const key of ['set', 'plan', 'catalog', 'diagnostics', 'scores', 'report']) if (!opt(key)) throw new Error(`--${key} is required`);
if (opt('report').split('/').includes('.local')) throw new Error('--report must be outside .local');
const read = async (key) => JSON.parse(await readFile(opt(key), 'utf8'));
const [set, plan, catalog, diag, scores] = await Promise.all(['set', 'plan', 'catalog', 'diagnostics', 'scores'].map(read));
const items = new Map(set.items.map((item) => [item.id, item]));
const cases = new Map(plan.cases.map((entry) => [entry.caseId, entry]));
const scored = new Map(scores.map((entry) => [entry.caseId, entry]));
const count = (values) => Object.fromEntries([...values.reduce((m, v) => m.set(v, (m.get(v) ?? 0) + 1), new Map())].sort());

const rows = diag.map((d) => {
  const entry = cases.get(d.caseId);
  const gold = items.get(entry.itemId).gold;
  let audit = {}; let pool = null;
  try {
    const inspected = inspectValidatedRequest(entry.request, { catalog, requestId: 'analysis-0001' });
    audit = inspected.audit;
    // Recomputed from the request, so a run that skipped retrieval (server-decided action) is counted the same way.
    pool = candidatePlaces(catalog, { ...inspected.request.filters, excludedIds: inspected.request.excludedIds }).length;
  } catch { audit = { unsupported: true }; }
  const relevant = new Set(gold.relevantIds);
  const shown = d.raw?.retrieval?.shownIds ?? [];
  return {
    expected: gold.expectedAction, returned: d.modelAction ?? 'none', outcome: d.outcome,
    correct: scored.get(d.caseId)?.score?.actionCorrect === true,
    pool, dateConflict: audit.dateConflict ?? null, relevant: relevant.size, relevantShown: shown.filter((id) => relevant.has(id)).length, shown: shown.length,
  };
});
const byExpected = Object.fromEntries(['recommend', 'cannot_confirm', 'ask_clarification', 'impossible'].map((action) => {
  const rs = rows.filter((r) => r.expected === action);
  return [action, { items: rs.length, actionCorrect: rs.filter((r) => r.correct).length,
    returned: count(rs.map((r) => `${r.returned}:${r.outcome}`)),
    runtimeDateConflict: rs.filter((r) => r.dateConflict).length, emptyPool: rs.filter((r) => r.pool === 0).length }];
}));
const placeItems = rows.filter((r) => ['recommend', 'cannot_confirm'].includes(r.expected) && r.relevant > 0 && r.shown > 0);
const report = {
  schemaVersion: '1.0', kind: 'jeju-local-llm-failure-breakdown', label: opt('label') ?? null, resultType: 'diagnostic_counts_not_quality_claim',
  goldSha256: set.contentSha256?.gold ?? null, planSha256: plan.planSha256, cases: rows.length,
  actionCorrect: rows.filter((r) => r.correct).length, byExpected,
  retrieval: { itemsWithShownCandidatesAndRelevant: placeItems.length, atLeastOneRelevantShown: placeItems.filter((r) => r.relevantShown > 0).length,
    meanRelevantShareOfShown: placeItems.length ? Math.round(placeItems.reduce((a, r) => a + r.relevantShown / r.shown, 0) / placeItems.length * 1000) / 1000 : null },
  note: 'runtimeDateConflict counts items whose request adapter audit already reports a date conflict; emptyPool counts items with no catalog candidate after filters and exclusions.',
};
await writeFile(opt('report'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
console.log(JSON.stringify({ actionCorrect: report.actionCorrect, byExpected: Object.fromEntries(Object.entries(byExpected).map(([k, v]) => [k, `${v.actionCorrect}/${v.items}`])), retrieval: report.retrieval }));
