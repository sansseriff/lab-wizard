import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '../../api';

export type TreeItem = {
	type: string;
	key: string;
	fields: Record<string, any>;
	children: Record<string, TreeItem>;
};

export type DiscoveryInput = {
	name: string;
	type: 'text' | 'number';
	label: string;
	default?: any;
};

export type DiscoveryAction = {
	name: string;
	label: string;
	description: string;
	inputs: DiscoveryInput[];
	parent_dep?: string;
	result_type: 'probe' | 'children' | 'self_candidates';
};

export type ProbeResult = {
	result_type: 'probe';
	found: { port: string; description?: string }[];
};

export type ChildrenResult = {
	result_type: 'children';
	children: { type: string; key_fields: Record<string, string>; idn?: string }[];
	parent_key: string | null;
	warnings?: string[];
};

export type SelfCandidatesResult = {
	result_type: 'self_candidates';
	found: { key_fields: Record<string, string>; idn?: string }[];
};

export type DiscoveryResult = ProbeResult | ChildrenResult | SelfCandidatesResult;

export type ChainStep = {
	type: string;
	key: string;
	action: 'create_new' | 'use_existing';
	resolved: boolean;
	extra?: Record<string, any>;
};

export type InstrumentMeta = {
	type: string;
	class_name: string;
	module: string;
	is_top_level: boolean;
	is_child: boolean;
	parent_type: string | null;
	parent_chain: string[];
	child_types: string[];
	defaults: Record<string, any>;
	key_hint: string | null;
	discovery_actions: DiscoveryAction[];
};

export type ManageData = {
	tree: TreeItem[];
	metadata: Record<string, InstrumentMeta>;
};

export type RootTransport = {
	root: string;
	transport_sharing: 'exclusive' | 'shared';
	state_authority: 'inferred' | 'subscribed';
	transport_key: string | null;
	held_by_server: boolean;
	held_by: string | null;
};

export const load: PageLoad = async () => {
	if (!browser) {
		return {
			tree: [] as TreeItem[],
			metadata: {} as Record<string, InstrumentMeta>,
			roots: {} as Record<string, RootTransport>,
			hardwareOwner: 'wizard' as 'wizard' | 'server'
		};
	}
	const data: ManageData = await fetchWithConfig('/api/manage-instruments', 'GET');

	// Transport status is best-effort: the tree must still render if no server
	// is around to ask, so a failure here costs badges, not the page.
	let roots: Record<string, RootTransport> = {};
	let hardwareOwner: 'wizard' | 'server' = 'wizard';
	let otherWorkspaces = 0;
	try {
		const [status, owner, servers] = await Promise.all([
			fetchWithConfig<{ roots: Record<string, RootTransport> }>(
				'/api/transport-status',
				'GET'
			),
			fetchWithConfig<{ owner: 'wizard' | 'server' }>('/api/hardware-owner', 'GET'),
			fetchWithConfig<{ servers: { is_this_workspace: boolean }[] }>(
				'/api/local-servers',
				'GET'
			)
		]);
		roots = status.roots ?? {};
		hardwareOwner = owner.owner;
		// This page only ever shows *this* workspace's tree. Other workspaces'
		// servers have their own, which is a common point of confusion.
		otherWorkspaces = (servers.servers ?? []).filter((s) => !s.is_this_workspace).length;
	} catch {
		// leave defaults
	}

	return {
		tree: data.tree ?? [],
		metadata: data.metadata ?? {},
		roots,
		hardwareOwner,
		otherWorkspaces
	};
};

export const prerender = true;
export const ssr = false;
