// Open embedding models compared for Jeju place retrieval, and a CPU encoder built on transformers.js (ONNX).
import { pipeline, env } from '@huggingface/transformers';

export const MODELS = Object.freeze({
  'multilingual-e5-small': { repo: 'Xenova/multilingual-e5-small', dtype: 'q8', pooling: 'mean', query: 'query: ', doc: 'passage: ', license: 'mit' },
  'multilingual-e5-base': { repo: 'Xenova/multilingual-e5-base', dtype: 'q8', pooling: 'mean', query: 'query: ', doc: 'passage: ', license: 'mit' },
  'multilingual-e5-large': { repo: 'Xenova/multilingual-e5-large', dtype: 'q8', pooling: 'mean', query: 'query: ', doc: 'passage: ', license: 'mit' },
  'bge-m3': { repo: 'Xenova/bge-m3', dtype: 'q8', pooling: 'cls', query: '', doc: '', license: 'mit' },
  'snowflake-arctic-embed-l-v2.0': { repo: 'Snowflake/snowflake-arctic-embed-l-v2.0', dtype: 'q8', pooling: 'cls', query: 'query: ', doc: '', license: 'apache-2.0' },
  // Last-token pooling is only correct without padding, so this model is encoded one text at a time.
  'qwen3-embedding-0.6b': { repo: 'onnx-community/Qwen3-Embedding-0.6B-ONNX', dtype: 'q8', pooling: 'last_token', batch: 1,
    query: 'Instruct: Given a Korean travel request, retrieve matching Jeju places\nQuery:', doc: '', license: 'apache-2.0' },
});

/** Returns encode(texts, role) where role is 'query' or 'doc'; vectors are L2-normalized plain arrays. */
export async function createEncoder(name, { cacheDir } = {}) {
  const config = MODELS[name];
  if (!config) throw new Error(`Unknown embedding model ${name}`);
  if (cacheDir) env.cacheDir = cacheDir;
  const extractor = await pipeline('feature-extraction', config.repo, { dtype: config.dtype });
  const batch = config.batch ?? 16;
  return async (texts, role) => {
    const prefix = role === 'query' ? config.query : config.doc;
    const vectors = [];
    for (let i = 0; i < texts.length; i += batch) {
      const output = await extractor(texts.slice(i, i + batch).map((text) => prefix + text), { pooling: config.pooling, normalize: true });
      vectors.push(...output.tolist());
    }
    return vectors;
  };
}

export const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
