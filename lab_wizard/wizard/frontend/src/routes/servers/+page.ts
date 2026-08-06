import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';
import type { ServerStatus } from '$lib/stores/workstation.svelte';

export type ServersData = {
	serverStatus: ServerStatus | null;
	suggestedBind: string;
};

export const load: PageLoad = async () => {
	const fallbackBind = 'tcp://0.0.0.0:12300';
	if (!browser) {
		return { serverStatus: null, suggestedBind: fallbackBind } satisfies ServersData;
	}

	let serverStatus: ServerStatus | null = null;
	try {
		serverStatus = await fetchWithConfig<ServerStatus>('/api/server/status', 'GET');
	} catch {
		serverStatus = null;
	}

	// A known-good address to pre-fill with: the conventional port when it is
	// free, otherwise one that is. Offering a port already in use as the default
	// would make the first thing the user tries the thing that fails.
	let suggestedBind = fallbackBind;
	try {
		const r = await fetchWithConfig<{ bind: string }>(
			'/api/server/suggest-port?prefer_default=true',
			'GET'
		);
		suggestedBind = r.bind;
	} catch {
		/* keep fallback */
	}

	return { serverStatus, suggestedBind } satisfies ServersData;
};

export const prerender = true;
export const ssr = false;
