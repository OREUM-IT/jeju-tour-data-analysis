#!/usr/bin/env node
/**
 * Embed a texts file written by the app's scripts/build-place-embeddings.ts --texts-out, locally on CPU (ONNX),
 * so the app's vector file can be packed without a Hugging Face API token. Prefix, model and texts hash are
 * taken from the texts file and carried into the output for the app's consistency check.
 * Usage: node scripts/embed-texts.mjs --texts texts.json --out vectors.json [--repo Xenova/multilingual-e5-large] [--dtype fp32] [--cache-dir dir]
 */
import { readFile, writeFile } from 'node:fs/promises';
import { pipeline, env } from '@huggingface/transformers';

const args = process.argv.slice(2);
const opt = (key, fallback) => { const i = args.indexOf(`--${key}`); return i >= 0 ? args[i + 1] : fallback; };
const textsFile = opt('texts');
const outFile = opt('out');
if (!textsFile || !outFile) throw new Error('--texts and --out are required');
if (opt('cache-dir')) env.cacheDir = opt('cache-dir');
const input = JSON.parse(await readFile(textsFile, 'utf8'));
const repo = opt('repo', 'Xenova/multilingual-e5-large');
const dtype = opt('dtype', 'fp32');
const extractor = await pipeline('feature-extraction', repo, { dtype });
const vectors = [];
for (let i = 0; i < input.texts.length; i += 16) {
  const batch = input.texts.slice(i, i + 16);
  const output = await extractor(batch.map((entry) => input.docPrefix + entry.text), { pooling: 'mean', normalize: true });
  output.tolist().forEach((vector, j) => vectors.push({ id: batch[j].id, vector: vector.map((x) => Math.round(x * 1e6) / 1e6) }));
  process.stdout.write(`\r${vectors.length}/${input.texts.length}`);
}
process.stdout.write('\n');
await writeFile(outFile, JSON.stringify({ model: input.model, onnxRepo: repo, dtype, textsSha256: input.textsSha256, vectors }));
