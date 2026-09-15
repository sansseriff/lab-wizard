import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';

export type ProcedureSummary = {
	name: string;
	origin: 'workspace' | 'builtin' | null;
	overrides_builtin: boolean;
	description: string;
	roles: Record<string, string>;
	records: string[];
	presets: string[];
	problems: string[];
};

export const load = async () => {
	if (!browser) return { procedures: [] as ProcedureSummary[], error: null as string | null };
	try {
		const data = await fetchWithConfig<{ procedures: ProcedureSummary[] }>('/api/procedures', 'GET');
		return { procedures: data.procedures, error: null };
	} catch (e) {
		return { procedures: [] as ProcedureSummary[], error: e instanceof Error ? e.message : String(e) };
	}
};

export const prerender = true;
export const ssr = false;
