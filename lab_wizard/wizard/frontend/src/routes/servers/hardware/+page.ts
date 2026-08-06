import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';

export type RootStatus = {
	root: string;
	transport_sharing: 'exclusive' | 'shared';
	state_authority: 'inferred' | 'subscribed';
	transport_key: string | null;
	held_by_server: boolean;
	held_by: string | null;
};

export type LocalServer = {
	bind: string | null;
	ipc: string | null;
	pid: number;
	workspace_path: string;
	config_dir: string;
	started_at: number;
	endpoints: string[];
};

export type TransportStatus = {
	server_running: boolean;
	local_servers: {
		url: string;
		workspace_path: string | null;
		config_dir: string | null;
		pid: number | null;
		held_roots: string[];
	}[];
	roots: Record<string, RootStatus>;
	duplicate_transports: Record<string, string[]>;
};

export type HardwareOwner = { owner: 'server' | 'wizard'; url: string | null };

export type HardwareStatusData = {
	owner: HardwareOwner;
	transport: TransportStatus;
	servers: LocalServer[];
	error?: string;
};

/** Nothing here exists at build time.
 *
 * Every fact on this page — who owns a transport, which daemons are up — comes
 * from processes running on the user's machine, so prerendering can only report
 * an empty workstation. The guard keeps that from being attempted (and from
 * filling the build log with caught fetch failures).
 */
const EMPTY: HardwareStatusData = {
	owner: { owner: 'wizard', url: null },
	transport: {
		server_running: false,
		local_servers: [],
		roots: {},
		duplicate_transports: {}
	},
	servers: []
};

export const load: PageLoad = async () => {
	if (!browser) return EMPTY;
	try {
		// Fetched together so the page shows one coherent picture rather than
		// three panels that can disagree with each other.
		const [owner, transport, servers] = await Promise.all([
			fetchWithConfig<HardwareOwner>('/api/hardware-owner', 'GET'),
			fetchWithConfig<TransportStatus>('/api/transport-status', 'GET'),
			fetchWithConfig<{ servers: LocalServer[] }>('/api/local-servers', 'GET')
		]);
		return { owner, transport, servers: servers.servers } satisfies HardwareStatusData;
	} catch (e) {
		return {
			...EMPTY,
			error: e instanceof Error ? e.message : String(e)
		} satisfies HardwareStatusData;
	}
};
