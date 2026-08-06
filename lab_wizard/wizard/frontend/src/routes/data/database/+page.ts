import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';

export type SaverItem = {
	type: string;
	key: string;
	fields: Record<string, any>;
};

/** Database savers only. A file saver would land in the same config directory
 *  but has nothing to browse, so it belongs on Savers rather than here. */
export const load: PageLoad = async () => {
	if (!browser) return { savers: [] as SaverItem[], error: null as string | null };
	try {
		const res = await fetchWithConfig<{ tree: SaverItem[] }>('/api/manage-savers', 'GET');
		return {
			savers: (res.tree ?? []).filter((s) => s.type === 'database_saver'),
			error: null as string | null
		};
	} catch (e) {
		return {
			savers: [] as SaverItem[],
			error: e instanceof Error ? e.message : 'Could not read saver configuration.'
		};
	}
};

export const prerender = true;
export const ssr = false;
