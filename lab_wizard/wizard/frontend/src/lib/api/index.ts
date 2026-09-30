/** The wizard's HTTP API, typed from the backend's own OpenAPI schema.
 *
 * `schema.d.ts` is generated (`bun run api`) from the FastAPI app, so a path,
 * a path parameter or a request body the backend does not accept is a type
 * error here, not a 422 at run time:
 *
 *     const settings = await unwrap(api.GET('/api/projects/{name}/settings', { params: { path: { name } } }));
 *
 * Every failure is thrown as an `ApiError` carrying the backend's message, and
 * `problems` saying where, when a request had fixable parts
 * (`backend/errors.py`). There is nothing to strip or parse at a call site:
 * `error.message` is the sentence to show.
 *
 * A route with a `response_model` gives its result a type of its own. One
 * without says `unknown`, so its call site names the type it expects —
 * `unwrap<Foo>(...)` — which marks it as a route still to type in the backend.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/** One fixable thing wrong with a request, and where it is: `["measurement", "params", "settle_s"]`. */
export type Problem = { path: (string | number)[]; message: string };

export class ApiError extends Error {
	constructor(
		readonly status: number,
		message: string,
		readonly problems: Problem[] = []
	) {
		super(message);
		this.name = 'ApiError';
	}
}

const throwOnError: Middleware = {
	async onResponse({ response }) {
		if (response.ok) return undefined;
		let body: { detail?: unknown; problems?: unknown } | null = null;
		try {
			body = await response.clone().json();
		} catch {
			// Not JSON: a proxy's error page, or nothing at all.
		}
		const detail =
			typeof body?.detail === 'string'
				? body.detail
				: (await response.clone().text().catch(() => '')) || `HTTP ${response.status}`;
		throw new ApiError(response.status, detail, Array.isArray(body?.problems) ? (body.problems as Problem[]) : []);
	},
	onError({ error }) {
		// The request never got an answer: the wizard is not running, or restarting.
		return new ApiError(0, `The wizard did not answer (${error instanceof Error ? error.message : String(error)})`);
	}
};

// The page's own origin: the wizard serves the API beside it. Absolute, so a
// request can also be built where there is no page (tests). `fetch` is looked
// up on each call rather than captured once, so a replaced global is honoured.
export const api = createClient<paths>({
	baseUrl: typeof location === 'undefined' ? 'http://localhost' : location.origin,
	fetch: (request) => globalThis.fetch(request)
});
api.use(throwOnError);

/** The body of a successful call. Name `T` only for a route without a response model. */
export async function unwrap<T = never, D = unknown>(call: Promise<{ data?: D }>): Promise<[T] extends [never] ? D : T> {
	const { data } = await call;
	return data as [T] extends [never] ? D : T;
}

/** What to show for anything a call threw. */
export function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
