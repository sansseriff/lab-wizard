import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';
import { type Catalog, type Definition, blankDefinition } from '$lib/procedures/model';

/** `?name=` edits a procedure, `?from=` starts a copy of one, neither starts blank. */
export const load = async ({ url }: { url: URL }) => {
	if (!browser) return { catalog: null, definition: null, origin: null, loadedName: null, hasBuiltin: false, error: null };

	const name = url.searchParams.get('name');
	const from = url.searchParams.get('from');
	try {
		const catalog = await fetchWithConfig<Catalog>('/api/procedures/catalog', 'GET');
		if (!name && !from) {
			return { catalog, definition: blankDefinition(), origin: null, loadedName: null, hasBuiltin: false, error: null };
		}
		const detail = await fetchWithConfig<{
			name: string;
			origin: 'workspace' | 'builtin';
			has_builtin: boolean;
			definition: Definition;
		}>(
			`/api/procedures/${encodeURIComponent((name ?? from)!)}`,
			'GET'
		);
		const definition = { description: '', roles: {}, params: {}, ...(detail.definition as Partial<Definition>) } as Definition;
		if (from) {
			definition.name = `${detail.name}_copy`;
			return { catalog, definition, origin: null, loadedName: null, hasBuiltin: false, error: null };
		}
		return { catalog, definition, origin: detail.origin, loadedName: detail.name, hasBuiltin: detail.has_builtin, error: null };
	} catch (e) {
		return {
			catalog: null,
			definition: null,
			origin: null,
			loadedName: null,
			hasBuiltin: false,
			error: e instanceof Error ? e.message : String(e)
		};
	}
};

export const prerender = true;
export const ssr = false;
