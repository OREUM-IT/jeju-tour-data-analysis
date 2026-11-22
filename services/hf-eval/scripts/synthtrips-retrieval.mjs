#!/usr/bin/env node
/**
 * External retrieval check on the SynthTRIPs Hugging Face dataset (ashmib/synthTRIPS, MIT):
 * rank its 200 European cities for each generated query and score against the config's filter-matching city list.
 * This tests the same embedding models outside Jeju and in English; it is not a Jeju agent evaluation.
 * Downloaded files are read as data only. Writes one aggregate report (counts and means; no query text).
 */
import { readFile, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { MODELS, createEncoder, dot } from '../src/embeddings.mjs';
import { createBm25, rankIds, rankingMetrics } from '../src/place-text.mjs';

const USAGE = 'Usage: node scripts/synthtrips-retrieval.mjs --data <synthTRIPS dir> --report <reports/x.json> [--models a,b] [--generator Gemini1Point5Pro|Llama3Point2Vision90B] [--cache-dir dir]';
const QUERY_KINDS = Object.freeze(['query_v', 'query_p0', 'query_p1']);

/** Minimal RFC 4180 parser: quoted fields, doubled quotes, embedded commas and newlines. */
export function parseCsv(text) {
  const rows = [];
  let row = []; let field = ''; let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; } else if (c === '"') quoted = false; else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows.filter((entry) => entry.length > 1 || entry[0] !== '');
  return body.map((entry) => Object.fromEntries(header.map((key, index) => [key, entry[index] ?? ''])));
}

const months = (value = '') => (value.match(/[A-Z][a-z]+/gu) ?? []).join(', ');
/** One English document per city from KB fields the generator also grounded on. */
export function cityDocuments(rows) {
  const cities = new Map();
  for (const row of rows) {
    if (!cities.has(row.city)) cities.set(row.city, { row, titles: [] });
    const entry = cities.get(row.city);
    if (row.interest_title && entry.titles.length < 20) entry.titles.push(`${row.interest_title} (${row.interest_type || 'poi'})`);
  }
  return [...cities.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([city, { row, titles }]) => ({
    id: city,
    text: `${city}, ${row.country}. Popularity: ${row.popularity}. Budget: ${row.budget}. Walkability: ${row.walkability}. `
      + `Air quality: ${row.aqi}. Low season: ${months(row.low_season)}. Medium season: ${months(row.medium_season)}. `
      + `High season: ${months(row.high_season)}. Highlights: ${titles.join('; ')}.`,
  }));
}

const mean = (values) => values.length ? Math.round(values.reduce((a, v) => a + v, 0) / values.length * 10000) / 10000 : null;
function summarize(configs, kind, scoreFor, ids) {
  const metrics = configs.filter((config) => typeof config[kind] === 'string' && config[kind].trim()).map((config) => {
    const relevant = new Set(config.city.filter((city) => ids.includes(city)));
    return relevant.size ? rankingMetrics(rankIds(ids, scoreFor(config[kind].trim())), relevant) : null;
  }).filter(Boolean);
  return { queries: metrics.length, ...Object.fromEntries(['precisionAtK', 'ndcgAtK', 'mrr', 'recallAtDepth'].map((key) => [key, mean(metrics.map((m) => m[key]))])) };
}

async function main() {
  const args = process.argv.slice(2);
  const options = { generator: 'Gemini1Point5Pro' };
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i]?.slice(2);
    if (!['data', 'report', 'models', 'generator', 'cache-dir'].includes(key) || args[i + 1] === undefined) throw new Error(USAGE);
    options[key] = args[i + 1];
  }
  if (!options.data || !options.report) throw new Error(USAGE);
  const models = (options.models ?? 'multilingual-e5-small,multilingual-e5-large,bge-m3,snowflake-arctic-embed-l-v2.0').split(',');
  if (models.some((name) => !MODELS[name])) throw new Error('unknown model');
  if (await stat(options.report).then(() => true, () => false)) throw new Error('report exists; choose a new filename');
  const rows = parseCsv(await readFile(path.join(options.data, 'kb', 'eu-cities-database.csv'), 'utf8'));
  const configs = JSON.parse(await readFile(path.join(options.data, 'generated-queries', `${options.generator}_generated_queries.json`), 'utf8'));
  const docs = cityDocuments(rows);
  const ids = docs.map((doc) => doc.id);
  const systems = [];
  const bm25 = createBm25(docs.map((doc) => doc.text));
  systems.push({ system: 'bm25-bigram', results: Object.fromEntries(QUERY_KINDS.map((kind) => [kind, summarize(configs, kind, bm25, ids)])) });
  for (const name of models) {
    const encode = await createEncoder(name, { cacheDir: options['cache-dir'] });
    const docVectors = await encode(docs.map((doc) => doc.text), 'doc');
    const results = {};
    const started = performance.now();
    for (const kind of QUERY_KINDS) {
      const texts = [...new Set(configs.map((config) => config[kind]?.trim()).filter(Boolean))];
      const vectors = new Map((await encode(texts, 'query')).map((vector, index) => [texts[index], vector]));
      results[kind] = summarize(configs, kind, (query) => docVectors.map((doc) => dot(vectors.get(query), doc)), ids);
    }
    systems.push({ system: name, repo: MODELS[name].repo, dtype: MODELS[name].dtype, results, encodeMs: Math.round(performance.now() - started) });
    console.log(JSON.stringify(systems.at(-1)));
  }
  const report = {
    schemaVersion: '1.0', kind: 'synthtrips-city-retrieval', resultType: 'external_english_retrieval_check_not_jeju_agent_quality',
    dataset: { repo: 'ashmib/synthTRIPS', license: 'mit', generator: options.generator, configs: configs.length, cities: ids.length, kbRows: rows.length },
    relevance: 'config.city (cities matching the generation filters), binary', metrics: { k: 10, recallDepth: 50 },
    queryKinds: 'dataset fields query_v, query_p0, query_p1 scored separately; the dataset card does not define the variants',
    systems,
  };
  await writeFile(options.report, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
