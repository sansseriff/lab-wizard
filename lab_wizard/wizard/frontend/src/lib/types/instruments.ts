/** Shapes shared by every surface that renders an instrument tree.
 *
 * These live outside `routes/` because two different components now render
 * trees — this workspace's config and another workspace's daemon — and a type
 * owned by one route's loader would make the other import across the app's
 * navigation structure for a reason that has nothing to do with navigation.
 *
 * Note that `InstrumentMeta` is *not* a constant of this build. A same-machine
 * server reports its own vocabulary via `schema_get`, and it may be running a
 * different lab_wizard than we are. Anything rendering a server's tree must use
 * that server's metadata, never ours.
 */

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
	behavior_abc?: string | null;
	channel_behavior_abc?: string | null;
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

/** An instrument server advertised by another workspace on this machine.
 *
 * Same-machine daemons are reachable over `ipc://`, and reaching that socket is
 * itself the proof of same-machine — which is why their trees are editable
 * while a server on another computer's are not.
 */
export type LocalServer = {
	pid: number;
	workspace_path: string;
	config_dir: string;
	endpoints: string[];
	is_this_workspace?: boolean;
};

/** Trailing path segment of a workspace, which is what people call it. */
export function workspaceName(path: string | null | undefined): string {
	if (!path) return 'workspace';
	const parts = path.replace(/[/\\]$/, '').split(/[/\\]/);
	return parts[parts.length - 1] || path;
}
