#!/usr/bin/env -S npx tsx
/**
 * Offline check of the app's place-search candidate step with and without the embedding blend.
 * Uses the app's own ruleIntent/rank/blendSemantic (pinned jeju-tour checkout, read-only) on the 96 template queries;
 * the Gemini intent and pick steps are NOT run, so this measures the keyword fallback and candidate recall only.
 * Gold relevantIds are read only for scoring. Run with the app's tsx:
 *   (cd <jeju-tour>/prototype && npx tsx <this> --app . --places <places.json> --vectors data/place_embeddings.json
 *     --set <.local/evaluation-set.json> --report <reports/x.json> [--cache-dir dir])
 */
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pipeline, env } from '@huggingface/transformers';

const args = process.argv.slice(2);
const opt = (key: string): string | undefined => { const i = args.indexOf(`--${key}`); return i >= 0 ? args[i + 1] : undefined; };
for (const key of ['app', 'places', 'vectors', 'set', 'report']) if (!opt(key)) throw new Error(`--${key} is required`);
if (path.resolve(opt('report')!).split(path.sep).includes('.local')) throw new Error('--report must be outside .local');
const app = path.resolve(opt('app')!);
const search = await import(path.join(app, 'src', 'placesearch.ts'));
const embeddings = await import(path.join(app, 'src', 'place-embeddings.ts'));
const places = JSON.parse(await readFile(opt('places')!, 'utf8')).data.places;
const set = JSON.parse(await readFile(opt('set')!, 'utf8'));
const vectorFile = JSON.parse(await readFile(opt('vectors')!, 'utf8'));
process.env.PLACE_EMBEDDING_FILE = path.resolve(opt('vectors')!);
if (opt('cache-dir')) env.cacheDir = opt('cache-dir')!;
// Same weights as the hosted model; the local fp32 ONNX export stands in for the Inference Providers call.
const extractor = await pipeline('feature-extraction', 'Xenova/multilingual-e5-large', { dtype: 'fp32' });

const mean = (values: number[]): number | null => values.length ? Math.round(values.reduce((a, v) => a + v, 0) / values.length * 10000) / 10000 : null;
function metrics(ranked: string[], relevant: Set<string>, k: number) {
  const top = ranked.slice(0, k);
  const hits = top.filter((id) => relevant.has(id)).length;
  const first = ranked.findIndex((id) => relevant.has(id));
  return { precision: top.length ? hits / k : 0, recall: hits / relevant.size, mrr: first < 0 ? 0 : 1 / (first + 1), empty: ranked.length === 0 ? 1 : 0 };
}
const rows: Record<string, Record<string, number[]>> = {};
const push = (system: string, key: string, value: number) => { ((rows[system] ??= {})[key] ??= []).push(value); };
for (const item of set.items) {
  const relevant = new Set<string>(item.gold.relevantIds);
  if (!relevant.size) continue;
  const q = item.query.template;
  const it = search.ruleIntent(q);
  const [vector] = (await extractor([vectorFile.queryPrefix + q], { pooling: 'mean', normalize: true })).tolist();
  const sims = embeddings.semanticScores(Float32Array.from(vector), places);
  const variants: Record<string, (n: number) => string[]> = {
    keyword: (n) => search.rank(places, it, n).map((x: { p: { id: string } }) => x.p.id),
    blended: (n) => search.blendSemantic(places, it, search.rank(places, it, n), sims, n).map((x: { p: { id: string } }) => x.p.id),
  };
  for (const [name, run] of Object.entries(variants)) {
    // Keyword-fallback results (20) and the 60 candidates given to the Gemini pick step; semantic places only fill free slots.
    const results = metrics(run(20), relevant, 10);
    const candidates = metrics(run(60), relevant, 60);
    push(name, 'resultsPrecisionAt10', results.precision); push(name, 'resultsMrr', results.mrr); push(name, 'resultsEmpty', results.empty);
    push(name, 'candidatesRecallAt60', candidates.recall); push(name, 'candidatesEmpty', candidates.empty);
  }
}
const report = {
  schemaVersion: '1.0', kind: 'jeju-app-place-search-blend-check', resultType: 'offline_rule_intent_proxy_not_live_search',
  goldSha256: set.contentSha256?.gold ?? null, places: places.length, embedding: { model: vectorFile.model, source: vectorFile.source, textVersion: vectorFile.textVersion },
  scoredItems: rows.keyword?.resultsMrr?.length ?? 0,
  note: 'ruleIntent replaces the Gemini intent step; the Gemini pick step is not run. resultsEmpty/candidatesEmpty are shares of queries with no result at all.',
  systems: Object.fromEntries(Object.entries(rows).map(([name, values]) => [name, Object.fromEntries(Object.entries(values).map(([k, v]) => [k, mean(v)]))])),
};
await writeFile(opt('report')!, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
console.log(JSON.stringify(report.systems));
