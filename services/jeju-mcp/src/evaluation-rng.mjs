// Deterministic sampling for the evaluation-set generator. No clock and no Math.random: every stream is derived
// from a text label, so the same seed, persona and slot always draw the same values on any Node.js version.
import { createHash } from 'node:crypto';

/**
 * sfc32 seeded from SHA-256(label). Only 32-bit integer arithmetic and one division are used, so the sequence
 * does not depend on the JavaScript engine's Math.random or on floating-point library functions.
 */
export function createRng(label) {
  if (typeof label !== 'string' || label.length === 0) throw new TypeError('rng label must be a non-empty string');
  const digest = createHash('sha256').update(label, 'utf8').digest();
  let a = digest.readUInt32LE(0);
  let b = digest.readUInt32LE(4);
  let c = digest.readUInt32LE(8);
  let d = digest.readUInt32LE(12);
  const next = () => {
    const t = (((a + b) | 0) + d) | 0;
    d = (d + 1) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    c = (c + t) | 0;
    return (t >>> 0) / 4294967296;
  };
  for (let i = 0; i < 16; i++) next();
  const int = (bound) => {
    if (!Number.isInteger(bound) || bound <= 0) throw new RangeError('rng.int needs a positive integer bound');
    return Math.floor(next() * bound);
  };
  return Object.freeze({
    next,
    int,
    /** Uniform choice. The list order is part of the contract: callers pass canonically ordered lists. */
    pick(items) {
      if (!Array.isArray(items) || items.length === 0) throw new RangeError('rng.pick needs a non-empty array');
      return items[int(items.length)];
    },
    /** Choice proportional to positive weights given as [value, weight] pairs, in the order supplied. */
    weighted(entries) {
      if (!Array.isArray(entries) || entries.length === 0) throw new RangeError('rng.weighted needs entries');
      let total = 0;
      for (const [, weight] of entries) {
        if (typeof weight !== 'number' || !Number.isFinite(weight) || weight <= 0) throw new RangeError('rng.weighted needs positive finite weights');
        total += weight;
      }
      let target = next() * total;
      for (const [value, weight] of entries) {
        target -= weight;
        if (target < 0) return value;
      }
      return entries[entries.length - 1][0];
    },
    /** Fisher-Yates on a copy; the input is not modified. */
    shuffle(items) {
      const copy = [...items];
      for (let i = copy.length - 1; i > 0; i--) {
        const j = int(i + 1);
        [copy[i], copy[j]] = [copy[j], copy[i]];
      }
      return copy;
    },
  });
}

/** Hex SHA-256 of a canonical JSON rendering (object keys sorted), for content addressing of briefs and sets. */
export function canonicalSha256(value) {
  return createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
}

export function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('canonical JSON cannot hold a non-finite number');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  throw new TypeError(`canonical JSON cannot hold a ${typeof value}`);
}
