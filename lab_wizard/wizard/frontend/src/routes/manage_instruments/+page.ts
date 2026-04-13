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
	type: string;
	label: string;
	default?: any;
};

export type DiscoveryAction = {
	name: string;
	label: string;
	description: string;
	inputs: DiscoveryInput[];
	parent_dep?: string;
	result_type: 'probe' | 'children' | 'self_candidates' | 'generic';
};

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

export const load: PageLoad = async () => {
	if (!browser) {
		return { tree: [] as TreeItem[], metadata: {} as Record<string, InstrumentMeta> };
	}
	const data: ManageData = await fetchWithConfig('/api/manage-instruments', 'GET');
	return { tree: data.tree ?? [], metadata: data.metadata ?? {} };
};

export const prerender = true;
export const ssr = false;
