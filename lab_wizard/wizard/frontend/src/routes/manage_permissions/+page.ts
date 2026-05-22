import { browser } from '$app/environment';
import { fetchWithConfig } from '../../api';

export type TreeItem = {
	type: string;
	key: string;
	fields: Record<string, any>;
	children: Record<string, TreeItem>;
};

/** An addressable instrument node, with the vocabulary the rule builder offers. */
export type PermInstrument = {
	path: string;
	attribute: string | null;
	type_hint: string | null;
	behavior_abc: string | null;
	state_keys: string[];
	methods: string[];
};

export type Operator = 'equals' | 'not_equals' | 'greater_than' | 'less_than' | 'in';

export type Condition = {
	// leaf
	path?: string;
	attribute?: string;
	key?: string;
	equals?: any;
	not_equals?: any;
	greater_than?: number;
	less_than?: number;
	in?: any[];
	// composite
	all?: Condition[];
	any?: Condition[];
	not?: Condition;
};

export type DenyClause = {
	path?: string;
	path_glob?: string;
	attribute?: string;
	methods: string[];
};

export type Rule = {
	id: string;
	description?: string;
	when: Condition;
	deny: DenyClause[];
	message?: string;
};

export type Permissions = {
	state_defaults?: Record<string, Record<string, any>>;
	rules: Rule[];
};

export type ServerStatus = {
	running: boolean;
	pid: number | null;
	detached: boolean;
	bind: string | null;
	rule_count: number;
	has_config: boolean;
};

export type PermissionsData = {
	tree: TreeItem[];
	instruments: PermInstrument[];
	permissions: Permissions;
	serverStatus: ServerStatus | null;
};

export const load = async () => {
	if (!browser) {
		return {
			tree: [],
			instruments: [],
			permissions: { rules: [] },
			serverStatus: null
		} as PermissionsData;
	}
	const data = await fetchWithConfig<PermissionsData>('/api/permissions', 'GET');
	let serverStatus: ServerStatus | null = null;
	try {
		serverStatus = await fetchWithConfig<ServerStatus>('/api/server/status', 'GET');
	} catch {
		serverStatus = null;
	}
	return {
		tree: data?.tree ?? [],
		instruments: data?.instruments ?? [],
		permissions: data?.permissions ?? { rules: [] },
		serverStatus
	};
};

export const prerender = true;
export const ssr = false;
