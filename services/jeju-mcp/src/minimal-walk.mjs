// The one walking condition the Gateway scope enforces, and the catalog places it withholds for it.
// A closed rule, not intent parsing: only the exact sentence of the published request template counts.
// It can only narrow what the server offers; it never selects, adds or ranks a place.

/** The template sentence for walk: minimal (evaluation-set.mjs templateQuery; the validated adapter's walk clause). */
export const MINIMAL_WALK_SENTENCE = '거의 걷지 않으려고 합니다.';

/** True when the registered query states minimal walking with exactly that sentence, anywhere in the text. */
export function asksMinimalWalk(query) {
  return typeof query === 'string' && query.normalize('NFC').includes(MINIMAL_WALK_SENTENCE);
}

/**
 * A route place: an official trail, Olle course or bike-route stop whose visit is walking or riding the route
 * (catalog v2 attributes.route, official route data). The evaluation gold's hasRoute is the same test.
 */
export function isRoutePlace(place) {
  return place?.attributes?.route !== undefined;
}
