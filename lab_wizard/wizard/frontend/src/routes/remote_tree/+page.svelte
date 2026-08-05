<script lang="ts">
	import { onMount } from 'svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type {
		TreeItem as TreeNodeItem,
		TransportBadge
	} from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { fetchWithConfig } from '../../api';
	import { ArrowClockwise, ArrowCounterClockwise, Plus, Trash } from 'phosphor-svelte';

	type LocalServer = {
		pid: number;
		workspace_path: string;
		config_dir: string;
		endpoints: string[];
		is_this_workspace?: boolean;
	};
	type RootTransport = {
		transport_sharing: 'exclusive' | 'shared';
		state_authority: 'inferred' | 'subscribed';
	};
	type DiscoveryInput = { name: string; label?: string; default?: any };
	type DiscoveryAction = {
		name: string;
		label: string;
		description: string;
		inputs: DiscoveryInput[];
		parent_dep?: string;
		result_type: 'probe' | 'children' | 'self_candidates';
	};
	type InstrumentMeta = {
		type: string;
		class_name: string;
		is_top_level: boolean;
		is_child: boolean;
		parent_type: string | null;
		parent_chain: string[];
		child_types: string[];
		key_hint: string | null;
		discovery_actions?: DiscoveryAction[];
	};
	type RemoteTree = {
		tree: TreeNodeItem[];
		roots: Record<string, RootTransport>;
		held_roots: string[];
		config_dir: string;
		metadata: Record<string, InstrumentMeta>;
		events: { ts: number; kind: string; message: string; actor: string }[];
		config_status: { diverged?: boolean };
	};
	type DuplicateClash = {
		root: string;
		workspace_path: string | null;
		url: string | null;
		held: boolean;
	};

	let servers: LocalServer[] = $state([]);
	let selected: LocalServer | null = $state(null);
	let remote: RemoteTree | null = $state(null);
	let loading = $state(false);
	let message: { text: string; ok: boolean } | null = $state(null);
	let confirmTarget: TreeNodeItem | null = $state(null);
	let confirmAction: 'remove' | 'reset' = $state('remove');

	// --- Add flow -----------------------------------------------------------
	//
	// Deliberately narrower than Manage Instruments: ancestors must already
	// exist on that server. Building a whole chain remotely would mean saving
	// partial parents on someone else's config to learn their hash keys, and
	// leaving debris there if the user backs out. Adding the root first, then
	// its children, keeps every write complete on its own.
	let showAdd = $state(false);
	let addType: string | null = $state(null);
	let addKey = $state('');
	let addParents: Record<string, string> = $state({});
	let discoveryResult: any = $state(null);
	let discoveryLoading = $state(false);
	let discoveryInputs: Record<string, any> = $state({});
	let duplicate: { transport_key: string | null; clashes: DuplicateClash[] } | null =
		$state(null);

	/** The server's own instrument vocabulary — never this build's. */
	function metadataOf(r: RemoteTree | null): Record<string, InstrumentMeta> {
		return r?.metadata ?? {};
	}

	const addableTypes: InstrumentMeta[] = $derived(
		Object.values(metadataOf(remote)).sort((a, b) => a.type.localeCompare(b.type))
	);
	const addMeta: InstrumentMeta | null = $derived(
		addType ? (metadataOf(remote)[addType] ?? null) : null
	);
	const addActions: DiscoveryAction[] = $derived(addMeta?.discovery_actions ?? []);
	// parent_chain is nearest-parent first; every ancestor must already be there.
	const requiredParents: string[] = $derived(addMeta?.parent_chain ?? []);
	const parentsSatisfied: boolean = $derived(
		requiredParents.every((t: string) => Boolean(addParents[t]))
	);
	const canSubmitAdd: boolean = $derived(
		Boolean(addType) && Boolean(addKey.trim()) && parentsSatisfied
	);

	/** First value of a discovery result's key_fields, as the key to prefill. */
	function firstKeyField(fields: Record<string, string> | undefined): string {
		const values = Object.values(fields ?? {});
		return values.length > 0 ? String(values[0]) : '';
	}

	onMount(loadServers);

	async function loadServers() {
		try {
			const res = await fetchWithConfig<{ servers: LocalServer[] }>(
				'/api/local-servers',
				'GET'
			);
			// This workspace's own tree is on Manage Instruments; showing it here
			// as "another workspace" would be two names for one thing.
			servers = res.servers.filter((s) => !s.is_this_workspace);
			if (servers.length === 1) await select(servers[0]);
		} catch (e) {
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		}
	}

	async function select(server: LocalServer) {
		selected = server;
		resetAddForm();
		await loadTree();
	}

	async function loadTree() {
		if (!selected) return;
		loading = true;
		message = null;
		try {
			remote = await fetchWithConfig<RemoteTree>('/api/remote-tree', 'POST', {
				config_dir: selected.config_dir
			});
		} catch (e) {
			remote = null;
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		} finally {
			loading = false;
		}
	}

	async function edit(operation: string, payload: Record<string, any>) {
		if (!selected) return;
		loading = true;
		message = null;
		try {
			await fetchWithConfig('/api/remote-tree/edit', 'POST', {
				config_dir: selected.config_dir,
				operation,
				payload
			});
			message = { text: `Applied ${operation}.`, ok: true };
			resetAddForm();
			await loadTree();
		} catch (e) {
			// The server refuses a remote peer, or a rack that is currently open.
			// Both are its decision, so its wording is what the user sees.
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		} finally {
			loading = false;
			confirmTarget = null;
		}
	}

	function resetAddForm() {
		showAdd = false;
		addType = null;
		addKey = '';
		addParents = {};
		discoveryResult = null;
		discoveryInputs = {};
		duplicate = null;
	}

	function onPickType(type: string) {
		addType = type;
		addKey = '';
		addParents = {};
		discoveryResult = null;
		duplicate = null;
		const inputs: Record<string, any> = {};
		for (const action of metadataOf(remote)[type]?.discovery_actions ?? []) {
			for (const inp of action.inputs ?? []) inputs[inp.name] = inp.default ?? '';
		}
		discoveryInputs = inputs;
	}

	/** Nodes of a given type anywhere in the server's tree, as parent candidates. */
	function nodesOfType(type: string): TreeNodeItem[] {
		const out: TreeNodeItem[] = [];
		const walk = (nodes: TreeNodeItem[]) => {
			for (const n of nodes) {
				if (n.type === type) out.push(n);
				walk(Object.values(n.children ?? {}));
			}
		};
		walk(remote?.tree ?? []);
		return out;
	}

	async function runDiscovery(actionName: string) {
		if (!selected || !addType) return;
		discoveryLoading = true;
		discoveryResult = null;
		try {
			// Root-first, as the server's discover RPC expects, and only the
			// ancestors the user has actually chosen.
			const parentChain = [...requiredParents]
				.reverse()
				.filter((t) => addParents[t])
				.map((t) => ({ type: t, key: addParents[t] }));
			discoveryResult = await fetchWithConfig('/api/remote-tree/discover', 'POST', {
				config_dir: selected.config_dir,
				type: addType,
				action: actionName,
				params: discoveryInputs,
				parent_chain: parentChain
			});
		} catch (e) {
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		} finally {
			discoveryLoading = false;
		}
	}

	/** Ask before writing whether this would be the same device configured twice. */
	async function checkDuplicate() {
		if (!addType || !addKey.trim()) {
			duplicate = null;
			return;
		}
		try {
			duplicate = await fetchWithConfig('/api/transport-status/duplicate-check', 'POST', {
				type: addType,
				key: addKey.trim(),
				include_local: true
			});
		} catch {
			duplicate = null;
		}
	}

	async function submitAdd() {
		if (!addType || !canSubmitAdd) return;
		// Leaf-first, as add_instrument_chain expects.
		const chain: Record<string, any>[] = [
			{ type: addType, key: addKey.trim(), action: 'create_new', extra: {} }
		];
		for (const parentType of requiredParents) {
			chain.push({
				type: parentType,
				key: addParents[parentType],
				action: 'use_existing',
				extra: {}
			});
		}
		await edit('add', { chain });
	}

	function transportBadge(node: TreeNodeItem): TransportBadge | null {
		const info = remote?.roots?.[`inst://${node.key}`];
		if (!info) return null;
		return {
			transport_sharing: info.transport_sharing,
			state_authority: info.state_authority,
			held_by_server: (remote?.held_roots ?? []).includes(`inst://${node.key}`)
		};
	}

	function workspaceName(path: string): string {
		const parts = path.replace(/\/$/, '').split('/');
		return parts[parts.length - 1] || path;
	}

	function when(ts: number): string {
		return new Date(ts * 1000).toLocaleTimeString();
	}
</script>

<section class="space-y-4">
	<div class="flex items-start justify-between">
		<div>
			<h1 class="text-2xl font-semibold">Another Workspace's Instruments</h1>
			<p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
				Servers on this machine expose their instrument tree. Because they are local, you can also
				edit them — the tree and its schema come from that server, never from this workspace.
			</p>
		</div>
		<div class="flex gap-2">
			<button
				class="flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-800"
				onclick={loadTree}
				disabled={loading || !selected}
			>
				<ArrowClockwise size={14} />
				Refresh
			</button>
			<button
				class="flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
				onclick={() => (showAdd = !showAdd)}
				disabled={loading || !remote}
			>
				<Plus size={14} />
				Add instrument
			</button>
		</div>
	</div>

	{#if message}
		<div
			class="rounded-md border p-3 text-sm {message.ok
				? 'border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300'
				: 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300'}"
		>
			{message.text}
		</div>
	{/if}

	{#if servers.length === 0}
		<p class="text-sm text-gray-500 dark:text-gray-400">
			No other workspace is running an instrument server on this machine.
			<a class="text-indigo-600 hover:underline" href="/hardware_status">Hardware &amp; Servers →</a>
		</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			{#each servers as s (s.pid)}
				<button
					class="rounded-md border px-3 py-1.5 text-sm {selected?.pid === s.pid
						? 'border-indigo-400 bg-indigo-50 text-indigo-900 dark:border-indigo-600 dark:bg-indigo-950/30 dark:text-indigo-300'
						: 'border-gray-300 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-800'}"
					onclick={() => select(s)}
				>
					{workspaceName(s.workspace_path)}
					<span class="ml-1 text-xs text-gray-500">pid {s.pid}</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if remote && showAdd}
		<div class="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4 dark:border-indigo-900/50 dark:bg-indigo-950/20">
			<h2 class="text-sm font-medium">
				Add to <span class="font-mono">{workspaceName(selected?.workspace_path ?? '')}</span>
			</h2>
			<p class="mt-1 text-xs text-gray-600 dark:text-gray-400">
				Instrument types come from that server's build, not this one — so you cannot configure
				something it could not instantiate.
			</p>

			<div class="mt-3 grid gap-3 sm:grid-cols-2">
				<label class="block text-xs">
					<span class="mb-1 block text-gray-600 dark:text-gray-300">Instrument type</span>
					<select
						class="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
						value={addType ?? ''}
						onchange={(e) => onPickType((e.target as HTMLSelectElement).value)}
					>
						<option value="" disabled>Choose a type…</option>
						{#each addableTypes as meta (meta.type)}
							<option value={meta.type}>{meta.type}</option>
						{/each}
					</select>
				</label>

				{#if addMeta}
					<label class="block text-xs">
						<span class="mb-1 block text-gray-600 dark:text-gray-300">
							Key {#if addMeta.key_hint}<span class="text-gray-400">({addMeta.key_hint})</span>{/if}
						</span>
						<input
							type="text"
							bind:value={addKey}
							onblur={checkDuplicate}
							placeholder={addMeta.key_hint ?? 'address or slot'}
							class="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
						/>
					</label>
				{/if}
			</div>

			{#if requiredParents.length > 0}
				<div class="mt-3 space-y-2">
					<div class="text-xs text-gray-600 dark:text-gray-300">
						This is a child instrument. Pick the parents it sits under — they must already exist
						there; add a missing one as its own step first.
					</div>
					{#each requiredParents as parentType (parentType)}
						{@const candidates = nodesOfType(parentType)}
						<label class="block text-xs">
							<span class="mb-1 block text-gray-600 dark:text-gray-300">{parentType}</span>
							{#if candidates.length === 0}
								<span class="text-amber-700 dark:text-amber-400">
									No {parentType} configured there yet — add one first.
								</span>
							{:else}
								<select
									class="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
									value={addParents[parentType] ?? ''}
									onchange={(e) =>
										(addParents = {
											...addParents,
											[parentType]: (e.target as HTMLSelectElement).value
										})}
								>
									<option value="" disabled>Choose…</option>
									{#each candidates as node (node.key)}
										<option value={node.key}>{node.type} ({node.key})</option>
									{/each}
								</select>
							{/if}
						</label>
					{/each}
				</div>
			{/if}

			{#if addActions.length > 0}
				<div class="mt-3 rounded-md border border-gray-200 bg-white/60 p-2.5 dark:border-gray-700 dark:bg-gray-900/40">
					<div class="text-xs font-medium">Discover</div>
					<p class="text-[11px] text-gray-500">
						Runs on that server, where the hardware is.
					</p>
					{#each addActions as action (action.name)}
						<div class="mt-2 flex flex-wrap items-end gap-2">
							{#each action.inputs ?? [] as inp (inp.name)}
								<label class="text-[11px]">
									<span class="mb-0.5 block text-gray-600 dark:text-gray-300">
										{inp.label ?? inp.name}
									</span>
									<input
										type="text"
										value={discoveryInputs[inp.name] ?? ''}
										oninput={(e) =>
											(discoveryInputs = {
												...discoveryInputs,
												[inp.name]: (e.target as HTMLInputElement).value
											})}
										class="rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-900"
									/>
								</label>
							{/each}
							<button
								class="rounded-md border border-gray-300 px-2.5 py-1 text-xs hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-800"
								onclick={() => runDiscovery(action.name)}
								disabled={discoveryLoading}
							>
								{discoveryLoading ? 'Scanning…' : action.label || action.name}
							</button>
						</div>
					{/each}

					{#if discoveryResult}
						<div class="mt-2 space-y-1">
							{#each discoveryResult.found ?? [] as found}
								<button
									class="block w-full rounded border border-gray-200 px-2 py-1 text-left text-xs hover:border-indigo-300 dark:border-gray-600"
									onclick={() => {
										addKey = found.port ?? firstKeyField(found.key_fields);
										checkDuplicate();
									}}
								>
									<span class="font-mono">{found.port ?? firstKeyField(found.key_fields)}</span>
									{#if found.description || found.idn}
										<span class="ml-1 text-gray-500">{found.description ?? found.idn}</span>
									{/if}
								</button>
							{/each}
							{#each discoveryResult.children ?? [] as child}
								<button
									class="block w-full rounded border border-gray-200 px-2 py-1 text-left text-xs hover:border-indigo-300 dark:border-gray-600"
									onclick={() => {
										addType = child.type;
										addKey = firstKeyField(child.key_fields);
										checkDuplicate();
									}}
								>
									<span class="font-mono">{child.type}</span>
									<span class="ml-1 text-gray-500">
										{firstKeyField(child.key_fields)}
										{child.idn ?? ''}
									</span>
								</button>
							{/each}
							{#if (discoveryResult.found ?? []).length === 0 && (discoveryResult.children ?? []).length === 0}
								<div class="text-[11px] text-gray-500">Nothing found.</div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}

			{#if duplicate?.clashes?.length}
				<!-- The cross-workspace mistake: two configs pointing at one device.
				     A warning, not a refusal — occasionally it is deliberate. -->
				<div class="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs dark:border-amber-900 dark:bg-amber-950/20">
					<div class="font-medium text-amber-900 dark:text-amber-300">
						<span class="font-mono">{duplicate.transport_key}</span> is already configured elsewhere
					</div>
					<ul class="mt-1 space-y-0.5 text-amber-800 dark:text-amber-400">
						{#each duplicate.clashes as c (c.root + (c.url ?? ''))}
							<li>
								{c.workspace_path ? workspaceName(c.workspace_path) : 'this workspace'}
								— <span class="font-mono">{c.root}</span>
								{#if c.held}<span class="font-medium">(open right now)</span>{/if}
							</li>
						{/each}
					</ul>
					<div class="mt-1 text-amber-700 dark:text-amber-500">
						One device, two owners. Whichever process opens it second will be refused.
					</div>
				</div>
			{/if}

			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
					onclick={resetAddForm}
					disabled={loading}>Cancel</button
				>
				<button
					class="rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
					onclick={submitAdd}
					disabled={!canSubmitAdd || loading}
				>
					{loading ? 'Adding…' : 'Add'}
				</button>
			</div>
		</div>
	{/if}

	{#if remote}
		{#if remote.config_status?.diverged}
			<div
				class="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
			>
				That server's config on disk no longer matches what it loaded — someone edited the files
				directly. It needs a restart to pick the change up.
			</div>
		{/if}

		<div class="grid gap-4 lg:grid-cols-3">
			<div class="lg:col-span-2">
				<h2 class="mb-2 text-sm font-medium">Instrument tree</h2>
				<ScrollArea
					type="hover"
					class="relative overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
					orientation="vertical"
					viewportClasses="h-full max-h-[28rem] w-full"
				>
					<div class="p-2">
						{#if remote.tree.length === 0}
							<p class="px-2 py-3 text-sm text-gray-500">No instruments configured there.</p>
						{:else}
							{#each remote.tree as node (node.key)}
								<TreeNode
									{node}
									{transportBadge}
									onRemove={(n) => {
										confirmAction = 'remove';
										confirmTarget = n;
									}}
									onReset={(n) => {
										confirmAction = 'reset';
										confirmTarget = n;
									}}
								/>
							{/each}
						{/if}
					</div>
				</ScrollArea>
			</div>

			<div>
				<h2 class="mb-2 text-sm font-medium">Recent activity</h2>
				<div
					class="max-h-[28rem] space-y-1.5 overflow-y-auto rounded-lg border border-gray-200 p-2 text-xs dark:border-gray-700"
				>
					{#if remote.events.length === 0}
						<p class="px-1 py-2 text-gray-500">Nothing recorded yet.</p>
					{:else}
						{#each remote.events as e (e.ts + e.kind)}
							<div class="border-b border-gray-100 pb-1.5 last:border-0 dark:border-gray-800">
								<div class="text-gray-800 dark:text-gray-200">{e.message}</div>
								<div class="text-[10px] text-gray-500">
									{when(e.ts)} · {e.actor}
								</div>
							</div>
						{/each}
					{/if}
				</div>
			</div>
		</div>
	{/if}
</section>

{#if confirmTarget}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
		<div class="w-full max-w-md rounded-lg bg-white p-5 shadow-xl dark:bg-gray-900">
			<h3 class="text-lg font-semibold">
				{confirmAction === 'remove' ? 'Remove from' : 'Reset in'} that workspace?
			</h3>
			<p class="mt-2 text-sm text-gray-600 dark:text-gray-300">
				{#if confirmAction === 'remove'}
					This removes <strong>{confirmTarget.type}</strong> ({confirmTarget.key}) and its children
					from the other workspace's config — not this one.
				{:else}
					This resets <strong>{confirmTarget.type}</strong> ({confirmTarget.key}) to default field
					values in the other workspace's config, keeping its children.
				{/if}
			</p>
			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
					onclick={() => (confirmTarget = null)}
					disabled={loading}>Cancel</button
				>
				<button
					class="flex items-center gap-1 rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-50 {confirmAction ===
					'remove'
						? 'bg-red-600 hover:bg-red-700'
						: 'bg-amber-600 hover:bg-amber-700'}"
					onclick={() =>
						edit(confirmAction, { type: confirmTarget!.type, key: confirmTarget!.key })}
					disabled={loading}
				>
					{#if confirmAction === 'remove'}
						<Trash size={14} /> Remove
					{:else}
						<ArrowCounterClockwise size={14} /> Reset
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
