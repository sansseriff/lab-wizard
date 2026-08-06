import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';
import type { LocalServer, RootTransport, TreeItem } from '$lib/types/instruments';

export type OverviewData = {
	instrumentCount: number;
	roots: RootTransport[];
	duplicates: [string, string[]][];
	saverCount: number;
	plotterCount: number;
	remoteCount: number;
	otherServers: LocalServer[];
	projects: { name: string; measurement: string | null; created: string | null }[];
	failed: boolean;
};

/** Every node in the tree, not just the roots — "18 instruments" means the
 * things you can bind a measurement role to, and those are mostly children. */
function countNodes(nodes: TreeItem[]): number {
	let total = 0;
	for (const node of nodes) {
		total += 1 + countNodes(Object.values(node.children ?? {}));
	}
	return total;
}

export const load: PageLoad = async () => {
	const empty: OverviewData = {
		instrumentCount: 0,
		roots: [],
		duplicates: [],
		saverCount: 0,
		plotterCount: 0,
		remoteCount: 0,
		otherServers: [],
		projects: [],
		failed: false
	};
	if (!browser) return empty;

	// Each of these is independently allowed to fail. An overview that refuses to
	// render because one subsystem is unhappy is worse than one that renders with
	// a gap: the whole point of the page is to say which subsystem is unhappy.
	const settle = async <T>(p: Promise<T>, fallback: T): Promise<T> => {
		try {
			return await p;
		} catch {
			return fallback;
		}
	};

	const [instruments, transport, savers, plotters, remotes, locals, projects] = await Promise.all([
		settle(fetchWithConfig<{ tree: TreeItem[] }>('/api/manage-instruments', 'GET'), { tree: [] }),
		settle(
			fetchWithConfig<{
				roots: Record<string, RootTransport>;
				duplicate_transports: Record<string, string[]>;
			}>('/api/transport-status', 'GET'),
			{ roots: {}, duplicate_transports: {} }
		),
		settle(fetchWithConfig<{ tree: unknown[] }>('/api/manage-savers', 'GET'), { tree: [] }),
		settle(fetchWithConfig<{ tree: unknown[] }>('/api/manage-plotters', 'GET'), { tree: [] }),
		settle(fetchWithConfig<{ servers: unknown[] }>('/api/remote-servers', 'GET'), { servers: [] }),
		settle(fetchWithConfig<{ servers: LocalServer[] }>('/api/local-servers', 'GET'), {
			servers: []
		}),
		settle(fetchWithConfig<{ projects: OverviewData['projects'] }>('/api/projects', 'GET'), {
			projects: []
		})
	]);

	return {
		instrumentCount: countNodes(instruments.tree ?? []),
		roots: Object.values(transport.roots ?? {}),
		duplicates: Object.entries(transport.duplicate_transports ?? {}),
		saverCount: (savers.tree ?? []).length,
		plotterCount: (plotters.tree ?? []).length,
		remoteCount: (remotes.servers ?? []).length,
		otherServers: (locals.servers ?? []).filter((s) => !s.is_this_workspace),
		projects: (projects.projects ?? []).slice(0, 3),
		failed: false
	} satisfies OverviewData;
};

export const prerender = true;
export const ssr = false;
