#!/usr/bin/env node
/**
 * Compare open embedding models with a lexical baseline for Jeju place retrieval.
 * Inputs are the local catalog and evaluation set (.local only). Gold relevantIds are read only to score rankings;
 * they never enter a document or a query. Models are downloaded from Hugging Face on first use and then cached.
 * Writes item-level rankings to .local and a publishable aggregate report (no place names, queries or IDs).
 */
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { MODELS, createEncoder, dot } from '../src/embeddings.mjs';
import { candidatePlaces } from '../../jeju-mcp/src/agent-tools.mjs';
import { inspectValidatedRequest } from '../../jeju-mcp/src/validated-request-adapter.mjs';
import { PLACE_TEXT_VERSION, createBm25, placeText, rankIds, rankingMetrics } from '../src/place-text.mjs';

const USAGE = `Usage: node scripts/compare-embeddings.mjs --catalog <.local/catalog.json> --set <.local/evaluation-set.json>
  --details <.local/embedding-details.json> --report <reports/x.json> [--models a,b] [--cache-dir <dir>] [--vectors-dir <.local/dir>]`;
const local = (file) => path.resolve(file).split(path.sep).includes('.local');
const sha = (value) => createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');

function parse(args) {
  const out = {};
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i]?.startsWith('--') ? args[i].slice(2) : null;
    if (!key || !['catalog', 'set', 'details', 'report', 'models', 'cache-dir', 'vectors-dir'].includes(key) || args[i + 1] === undefined) throw new Error(USAGE);
    out[key] = args[i + 1];
  }
  for (const key of ['catalog', 'set', 'details', 'report']) if (!out[key]) throw new Error(USAGE);
  for (const key of ['catalog', 'set', 'details']) if (!local(out[key])) throw new Error(`--${key} must be inside .local`);
  if (out['vectors-dir'] && !local(out['vectors-dir'])) throw new Error('--vectors-dir must be inside .local');
  if (local(out.report)) throw new Error('--report is the publishable aggregate; keep it outside .local');
  out.models = (out.models ?? Object.keys(MODELS).join(',')).split(',');
  if (out.models.some((name) => !MODELS[name])) throw new Error(`Unknown model. Known: ${Object.keys(MODELS).join(', ')}`);
  return out;
}

const mean = (values) => values.length ? values.reduce((a, v) => a + v, 0) / values.length : null;
const round = (value) => value === null ? null : Math.round(value * 10000) / 10000;

/** Two settings: rank the whole catalog, and rank inside the candidate set the structured request grammar allows. */
function evaluate(items, ids, scoreFor, catalog) {
  const indexOf = new Map(ids.map((id, index) => [id, index]));
  const rows = items.map((item) => {
    const relevant = new Set(item.gold.relevantIds);
    const scores = scoreFor(item);
    const open = rankIds(ids, scores);
    let filtered = null;
    try {
      const { request } = inspectValidatedRequest({ query: item.query.template, context: item.context }, { catalog, requestId: 'embedding-eval' });
      const pool = candidatePlaces(catalog, { ...request.filters, excludedIds: request.excludedIds }).map((place) => place.id);
      filtered = { poolSize: pool.length, ranked: rankIds(pool, pool.map((id) => scores[indexOf.get(id)])) };
    } catch { /* Outside the closed template grammar: only the open setting applies. */ }
    return { itemId: item.id, relevant, open, filtered };
  });
  const scored = rows.filter((row) => row.relevant.size > 0);
  const summarize = (pick) => {
    const metrics = scored.map(pick).filter(Boolean);
    return { items: metrics.length, ...Object.fromEntries(['precisionAtK', 'ndcgAtK', 'mrr', 'recallAtDepth']
      .map((key) => [key, round(mean(metrics.map((m) => m[key])))])) };
  };
  return {
    rows,
    open: summarize((row) => rankingMetrics(row.open, row.relevant)),
    filtered: summarize((row) => row.filtered && rankingMetrics(row.filtered.ranked, row.relevant)),
  };
}

async function main() {
  const options = parse(process.argv.slice(2));
  const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
  const [catalog, set] = await Promise.all([readJson(options.catalog), readJson(options.set)]);
  const ids = catalog.places.map((place) => place.id);
  const documents = catalog.places.map(placeText);
  const items = set.items;
  const queries = items.map((item) => item.query.template);
  const systems = [];
  const details = {};

  const bm25 = createBm25(documents);
  const lexical = evaluate(items, ids, (item) => bm25(item.query.template), catalog);
  systems.push({ system: 'bm25-bigram', kind: 'lexical', open: lexical.open, filtered: lexical.filtered });
  details['bm25-bigram'] = lexical.rows.map(({ itemId, open, filtered }) => ({ itemId, top10: open.slice(0, 10), filteredTop10: filtered?.ranked.slice(0, 10) ?? null }));

  for (const name of options.models) {
    const config = MODELS[name];
    const started = performance.now();
    const encode = await createEncoder(name, { cacheDir: options['cache-dir'] });
    const loadedMs = performance.now() - started;
    const docStart = performance.now();
    const docVectors = await encode(documents, 'doc');
    const docMs = performance.now() - docStart;
    const queryStart = performance.now();
    const queryVectors = await encode(queries, 'query');
    const queryMs = performance.now() - queryStart;
    const byItem = new Map(items.map((item, index) => [item.id, queryVectors[index]]));
    const result = evaluate(items, ids, (item) => docVectors.map((vector) => dot(byItem.get(item.id), vector)), catalog);
    systems.push({ system: name, kind: 'embedding', repo: config.repo, dtype: config.dtype, pooling: config.pooling, license: config.license,
      dimensions: docVectors[0].length, timing: { loadMs: Math.round(loadedMs), documentsMs: Math.round(docMs), queryMeanMs: Math.round(queryMs / queries.length) },
      open: result.open, filtered: result.filtered });
    details[name] = result.rows.map(({ itemId, open, filtered }) => ({ itemId, top10: open.slice(0, 10), filteredTop10: filtered?.ranked.slice(0, 10) ?? null }));
    if (options['vectors-dir']) {
      await mkdir(options['vectors-dir'], { recursive: true, mode: 0o700 });
      await writeFile(path.join(options['vectors-dir'], `${name}.json`), JSON.stringify({
        model: config.repo, dtype: config.dtype, placeTextVersion: PLACE_TEXT_VERSION, catalogSha256: sha(catalog), ids,
        vectors: docVectors.map((vector) => vector.map((x) => Math.round(x * 1e6) / 1e6)),
      }), { mode: 0o600 });
    }
    console.log(JSON.stringify({ model: name, open: result.open, filtered: result.filtered, timing: systems.at(-1).timing }));
  }

  const report = {
    schemaVersion: '1.0', kind: 'jeju-embedding-retrieval-comparison',
    resultType: 'rule_gold_retrieval_proxy_not_user_relevance',
    placeTextVersion: PLACE_TEXT_VERSION, catalogSha256: sha(catalog), goldSha256: set.contentSha256?.gold ?? null,
    places: ids.length, items: items.length, itemsWithRelevantPlaces: items.filter((item) => item.gold.relevantIds.length > 0).length,
    query: 'template', metrics: { k: 10, recallDepth: 50, relevance: 'binary gold.relevantIds' },
    settings: { open: 'rank all catalog places', filtered: 'rank inside the closed-template candidate set (zone/theme/category/exclusions)' },
    runtime: { node: process.version, library: '@huggingface/transformers 4.3.2', device: 'cpu' },
    systems,
  };
  await mkdir(path.dirname(options.report), { recursive: true });
  for (const [file, value] of [[options.details, details], [options.report, report]]) {
    if (await stat(file).then(() => true, () => false)) throw new Error(`${file} already exists; choose a new filename.`);
  }
  await writeFile(options.details, JSON.stringify(details), { mode: 0o600, flag: 'wx' });
  await writeFile(options.report, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
