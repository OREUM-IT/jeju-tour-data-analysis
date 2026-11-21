// Place documents and lexical baseline for retrieval experiments. Pure functions: no model, file or network access.
// The document uses only catalog fields a production search index would also have. Theme weights and evaluation
// gold are never part of the text, so an embedding score is not a copy of the rule that produced the gold set.

export const CATEGORY_LABELS = Object.freeze({
  herit: '역사·문화 유적', art: '미술·전시', leisure: '레저·스포츠', heal: '숲·정원·명승', activity: '테마파크·체험',
  sea: '바다·해변', food: '식당·시장', cafe: '카페·디저트', stay: '숙소', hike: '오름·올레·탐방로',
});
export const ZONE_LABELS = Object.freeze({
  JC: '제주시 원도심', NE: '조천·구좌', E: '성산·우도', SE: '표선·남원', S: '서귀포 시내', SW: '중문', W2: '안덕·대정', W: '한림·애월',
});
export const PLACE_TEXT_VERSION = 'place-text-v1';

const clean = (value) => typeof value === 'string' ? value.replace(/\s+/gu, ' ').trim() : '';

/** One line per place: name, kind, category label, zone label and a short source note when present. */
export function placeText(place) {
  const note = clean(typeof place.attributes?.note?.value === 'string' ? place.attributes.note.value : '').slice(0, 160);
  return [clean(place.name), clean(place.kind), CATEGORY_LABELS[place.category] ?? '', ZONE_LABELS[place.zone] ?? '', note]
    .filter(Boolean).join(' · ');
}

/** Character bigrams of Hangul/alphanumeric runs: a dependency-free Korean lexical baseline. */
export function bigrams(text) {
  const runs = text.normalize('NFC').toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  const grams = [];
  for (const run of runs) {
    if (run.length === 1) grams.push(run);
    for (let i = 0; i + 1 < run.length; i++) grams.push(run.slice(i, i + 2));
  }
  return grams;
}

/** Okapi BM25 (k1 1.2, b 0.75) over bigram documents. Returns a scorer for query strings. */
export function createBm25(documents, { k1 = 1.2, b = 0.75 } = {}) {
  const docs = documents.map((text) => {
    const tf = new Map();
    const grams = bigrams(text);
    for (const gram of grams) tf.set(gram, (tf.get(gram) ?? 0) + 1);
    return { tf, length: grams.length };
  });
  const avg = docs.reduce((sum, doc) => sum + doc.length, 0) / Math.max(1, docs.length);
  const df = new Map();
  for (const doc of docs) for (const gram of doc.tf.keys()) df.set(gram, (df.get(gram) ?? 0) + 1);
  const idf = (gram) => Math.log(1 + (docs.length - (df.get(gram) ?? 0) + 0.5) / ((df.get(gram) ?? 0) + 0.5));
  return (query) => {
    const grams = [...new Set(bigrams(query))];
    return docs.map((doc) => grams.reduce((sum, gram) => {
      const f = doc.tf.get(gram) ?? 0;
      return f === 0 ? sum : sum + idf(gram) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * doc.length / avg));
    }, 0));
  };
}

/** Stable descending order by score, ties broken by ID so repeated runs give identical rankings. */
export function rankIds(ids, scores) {
  return ids.map((id, index) => ({ id, score: scores[index] }))
    .sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)).map((entry) => entry.id);
}

/** Binary-relevance ranking metrics. Items with an empty relevant set are excluded by the caller. */
export function rankingMetrics(ranked, relevant, { k = 10, recallDepth = 50 } = {}) {
  const hits = ranked.map((id) => relevant.has(id));
  const dcg = hits.slice(0, k).reduce((sum, hit, i) => sum + (hit ? 1 / Math.log2(i + 2) : 0), 0);
  const ideal = Array.from({ length: Math.min(k, relevant.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, v) => a + v, 0);
  const first = hits.indexOf(true);
  return {
    precisionAtK: hits.slice(0, k).filter(Boolean).length / k,
    ndcgAtK: ideal === 0 ? 0 : dcg / ideal,
    mrr: first < 0 ? 0 : 1 / (first + 1),
    recallAtDepth: hits.slice(0, recallDepth).filter(Boolean).length / relevant.size,
  };
}
