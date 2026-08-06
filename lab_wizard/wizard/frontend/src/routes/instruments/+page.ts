import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';
import type {
	InstrumentMeta,
	LocalServer,
	ManageData,
	RootTransport,
	TreeItem
} from '$lib/types/instruments';

export type { InstrumentMeta, LocalServer, RootTransport, TreeItem };

export const load: PageLoad = async () => {
	if (!browser) {
		return {
			tree: [] as TreeItem[],
			metadata: {} as Record<string, InstrumentMeta>,
			roots: {} as Record<string, RootTransport>,
			servers: [] as LocalServer[]
		};
	}
	const data: ManageData = await fetchWithConfig('/api/manage-instruments', 'GET');

	// Transport status is best-effort: the tree must still render if no server
	// is around to ask, so a failure here costs badges, not the page.
	//
	// `/api/hardware-owner` is deliberately *not* fetched here. A load function
	// blocks navigation until it resolves, and that endpoint has to probe for a
	// server — which is slowest in exactly the case where the answer is least
	// interesting (no server running). Clicking Instruments then appeared to do
	// nothing at all for several seconds. The layout's workstation store already
	// holds the same fact and fetches it without blocking anything.
	let roots: Record<string, RootTransport> = {};
	let servers: LocalServer[] = [];
	try {
		const [status, local] = await Promise.all([
			fetchWithConfig<{ roots: Record<string, RootTransport> }>('/api/transport-status', 'GET'),
			fetchWithConfig<{ servers: LocalServer[] }>('/api/local-servers', 'GET')
		]);
		roots = status.roots ?? {};
		// This workspace's own daemon serves the very tree already shown on the
		// first tab. Offering it as a second tab would ask the user to choose
		// between two names for one instrument.
		servers = (local.servers ?? []).filter((s) => !s.is_this_workspace);
	} catch {
		// leave defaults
	}

	return {
		tree: data.tree ?? [],
		metadata: data.metadata ?? {},
		roots,
		servers
	};
};

export const prerender = true;
export const ssr = false;
