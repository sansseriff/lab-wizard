/** Where you are on a page — which tab, which item — lives in the query string.
 *
 * It is written with `replaceState`, never pushed, so Back leaves the page
 * rather than stepping through every tab clicked on it; but reloading, sharing
 * the link, or coming Back to the page lands on the same view.
 */
import { replaceState } from '$app/navigation';
import { page } from '$app/state';

/** Set (or, for null/empty, remove) query parameters on the current URL. */
export function setQuery(values: Record<string, string | null | undefined>) {
	const url = new URL(page.url);
	for (const [key, value] of Object.entries(values)) {
		if (value) url.searchParams.set(key, value);
		else url.searchParams.delete(key);
	}
	if (url.href !== page.url.href) replaceState(url, page.state);
}

/** A query parameter if it is one of `allowed`, else `fallback`. */
export function queryChoice<T extends string>(name: string, allowed: readonly T[], fallback: T): T {
	const value = page.url.searchParams.get(name);
	return allowed.includes(value as T) ? (value as T) : fallback;
}
