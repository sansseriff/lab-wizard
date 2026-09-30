import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { api, errorMessage, unwrap } from '$lib/api';

export type Project = {
	name: string;
	path: string;
	measurement: string | null;
	schema_version: number | null;
	created: string | null;
	instruments: string[];
	/** What a run produces besides its database record. */
	outputs: { files: boolean; live_plot: 'none' | 'window' | 'web'; plot: string };
	setup_file: string | null;
	/** False when the project YAML could not be parsed. The directory is still
	 *  listed — one visible on disk but missing here would look like data loss. */
	readable: boolean;
};

export const load: PageLoad = async () => {
	if (!browser) return { projects: [] as Project[], error: null as string | null };
	try {
		const res = await unwrap<{ projects: Project[] }>(api.GET('/api/projects'));
		return { projects: res.projects ?? [], error: null as string | null };
	} catch (e) {
		return {
			projects: [] as Project[],
			error: errorMessage(e) || 'Could not read the projects directory.'
		};
	}
};

export const prerender = true;
export const ssr = false;
