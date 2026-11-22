#!/usr/bin/env -S npx tsx
/**
 * Free-form query check of the app's place-search candidate step with and without the embedding fill.
 * Queries and their relevance rules come from a local file (.local); relevance is computed from the agent catalog
 * by rule (category OR theme >= 0.5, AND zone), never chosen by a model. Gemini steps are NOT run: ruleIntent stands in
 * for the Gemini intent, so this measures the keyword fallback and the candidate list given to the Gemini pick step.
 *   (cd <jeju-tour>/prototype && npx tsx <this> --app . --places <places.json> --vectors data/place_embeddings.json
 *     --catalog <.local/agent-catalog.json> --queries <.local/free-queries.json> --report <reports/x.json> [--cache-dir dir])
 */
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pipeline, env } from '@huggingface/transformers';

const args = process.argv.slice(2);
const opt = (key: string): string | undefined => { const i = args.indexOf(`--${key}`); return i >= 0 ? args[i + 1] : undefined; };
for (const key of ['app', 'places', 'vectors', 'catalog', 'queries', 'report']) if (!opt(key)) throw new Error(`--${key} is required`);
if (path.resolve(opt('report')!).split(path.sep).includes('.local')) throw new Error('--report must be outside .local');
if (!path.resolve(opt('queries')!).split(path.sep).includes('.local')) throw new Error('--queries must be inside .local');
const app = path.resolve(opt('app')!);
const search = await import(path.join(app, 'src', 'placesearch.ts'));
const embeddings = await import(path.join(app, 'src', 'place-embeddings.ts'));
const places = JSON.parse(await readFile(opt('places')!, 'utf8')).data.places;
const catalog = JSON.parse(await readFile(opt('catalog')!, 'utf8'));
const querySet = JSON.parse(await readFile(opt('queries')!, 'utf8'));
const vectorFile = JSON.parse(await readFile(opt('vectors')!, 'utf8'));
process.env.PLACE_EMBEDDING_FILE = path.resolve(opt('vectors')!);
if (opt('cache-dir')) env.cacheDir = opt('cache-dir')!;
const extractor = await pipeline('feature-extraction', 'Xenova/multilingual-e5-large', { dtype: 'fp32' });

type Rule = { q: string; categories?: string[]; themes?: string[]; zone?: string };
export function relevantIds(rule: Rule, catalogPlaces: { id: string; category: string; zone: string | null; themes: Record<string, number> }[]): Set<string> {
  return new Set(catalogPlaces.filter((p) => p.category !== 'stay'
    && ((rule.categories ?? []).includes(p.category) || (rule.themes ?? []).some((t) => (p.themes?.[t] ?? 0) >= 0.5))
    && (rule.zone === undefined || p.zone === rule.zone)).map((p) => p.id));
}
const mean = (values: number[]): number | null => values.length ? Math.round(values.reduce((a, v) => a + v, 0) / values.length * 10000) / 10000 : null;
function metrics(ranked: string[], relevant: Set<string>, k: number) {
  const top = ranked.slice(0, k);
  const first = ranked.findIndex((id) => relevant.has(id));
  return { precision: top.filter((id) => relevant.has(id)).length / k, mrr: first < 0 ? 0 : 1 / (first + 1),
    relevantFound: ranked.filter((id) => relevant.has(id)).length };
}
const groups: Record<string, Record<string, Record<string, number[]>>> = {};
const push = (group: string, system: string, key: string, value: number) => { (((groups[group] ??= {})[system] ??= {})[key] ??= []).push(value); };
let keywordEmpty20 = 0; let keywordShort20 = 0; let keywordShort60 = 0;
for (const rule of querySet.queries as Rule[]) {
  const relevant = relevantIds(rule, catalog.places);
  if (!relevant.size) continue;
  const it = search.ruleIntent(rule.q);
  const [vector] = (await extractor([vectorFile.queryPrefix + rule.q], { pooling: 'mean', normalize: true })).tolist();
  const sims = embeddings.semanticScores(Float32Array.from(vector), places);
  const keyword20 = search.rank(places, it, 20);
  const keyword60 = search.rank(places, it, 60);
  if (!keyword20.length) keywordEmpty20++;
  if (keyword20.length < 20) keywordShort20++;
  if (keyword60.length < 60) keywordShort60++;
  const lists: Record<string, { results: string[]; candidates: string[] }> = {
    keyword: { results: keyword20.map((x: any) => x.p.id), candidates: keyword60.map((x: any) => x.p.id) },
    blended: { results: search.blendSemantic(places, it, keyword20, sims, 20).map((x: any) => x.p.id),
      candidates: search.blendSemantic(places, it, keyword60, sims, 60).map((x: any) => x.p.id) },
  };
  const fillActive = keyword60.length < 60;
  for (const [system, list] of Object.entries(lists)) {
    for (const group of ['all', ...(fillActive ? ['fillActive'] : [])]) {
      const r = metrics(list.results, relevant, 10);
      const c = metrics(list.candidates, relevant, 60);
      push(group, system, 'resultsPrecisionAt10', r.precision); push(group, system, 'resultsMrr', r.mrr);
      push(group, system, 'resultsEmpty', list.results.length === 0 ? 1 : 0);
      push(group, system, 'candidatesRelevantFound', c.relevantFound); push(group, system, 'candidatesCount', list.candidates.length);
    }
  }
}
const report = {
  schemaVersion: '1.0', kind: 'jeju-app-place-search-free-query-check', resultType: 'offline_rule_intent_proxy_author_written_queries',
  querySetVersion: querySet.version, queries: querySet.queries.length,
  relevance: querySet.relevance, places: places.length,
  embedding: { model: vectorFile.model, source: vectorFile.source, queryVectors: 'local fp32 ONNX (Xenova/multilingual-e5-large)' },
  keywordStage: { emptyAt20: keywordEmpty20, shortOf20: keywordShort20, shortOf60: keywordShort60 },
  note: 'Queries were written by the assistant, not users. ruleIntent replaces the Gemini intent step and the Gemini pick step is not run. candidatesRelevantFound is the mean number of rule-relevant places among the up-to-60 candidates.',
  groups: Object.fromEntries(Object.entries(groups).map(([group, systems]) => [group, Object.fromEntries(Object.entries(systems).map(([system, values]) => [system,
    { items: values.resultsMrr.length, ...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, mean(v)])) }]))])),
};
await writeFile(opt('report')!, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
console.log(JSON.stringify({ keywordStage: report.keywordStage, groups: report.groups }));
