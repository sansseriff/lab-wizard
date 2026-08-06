<script lang="ts">
	/** Another workspace's instrument tree, fetched from its daemon.
	 *
	 * Editable, because reaching its `ipc://` socket already proves same-machine.
	 * The tree and its schema come from *that* server, never from this build —
	 * it may be running a different lab_wizard, and offering a type it could not
	 * instantiate would be a form that cannot be submitted.
	 */
	import { onMount } from 'svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type {
		TreeItem as TreeNodeItem,
		TransportBadge
	} from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { fetchWithConfig } from '$lib/api';
	import { workspaceName, type LocalServer } from '$lib/types/instruments';
	import { ArrowClockwise, ArrowCounterClockwise, Plus, Trash } from 'phosphor-svelte';

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

	let { server }: { server: LocalServer } = $props();

	// Named `selected` throughout the edit helpers below; keeping the name means
	// the tab row simply supplies what a picker used to.
	const selected = $derived(server);

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

	// The parent keys this component on the server's pid, so a mount always
	// corresponds to exactly one daemon and there is nothing to re-select.
	onMount(loadTree);

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

	function when(ts: number): string {
		return new Date(ts * 1000).toLocaleTimeString();
	}
</script>

<section class="space-y-4">
	<div class="flex items-start justify-between gap-4">
		<p class="max-w-[64ch] text-xs text-muted">
			This tree belongs to <span class="mono font-medium text-ink-2"
				>{workspaceName(server.workspace_path)}</span
			>, not to this workspace. Its instrument types come from that server's build, so you cannot
			configure something it could not instantiate. Edits here change
			<span class="mono">{server.config_dir}</span>.
		</p>
		<div class="flex shrink-0 gap-2">
			<button
				class="flex items-center gap-1.5 rounded border border-line-2 px-3 py-1.5 text-xs hover:bg-surface-2 disabled:opacity-50"
				onclick={loadTree}
				disabled={loading}
			>
				<ArrowClockwise size={14} />
				Refresh
			</button>
			<button
				class="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
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
			class="rounded border p-3 text-sm {message.ok
				? 'border-ok/30 bg-ok-wash text-ok'
				: 'border-crit/30 bg-crit-wash text-crit'}"
		>
			{message.text}
		</div>
	{/if}

	{#if remote && showAdd}
		<div class="rounded-xl border border-accent/30 bg-accent-wash/40 p-4">
			<h2 class="text-sm font-medium">
				Add to <span class="font-mono">{workspaceName(selected?.workspace_path ?? '')}</span>
			</h2>
			<p class="mt-1 text-xs text-ink-2">
				Instrument types come from that server's build, not this one — so you cannot configure
				something it could not instantiate.
			</p>

			<div class="mt-3 grid gap-3 sm:grid-cols-2">
				<label class="block text-xs">
					<span class="mb-1 block text-ink-2">Instrument type</span>
					<select
						class="w-full rounded-md border border-line-2 px-2 py-1.5 text-sm"
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
						<span class="mb-1 block text-ink-2">
							Key {#if addMeta.key_hint}<span class="text-muted">({addMeta.key_hint})</span>{/if}
						</span>
						<input
							type="text"
							bind:value={addKey}
							onblur={checkDuplicate}
							placeholder={addMeta.key_hint ?? 'address or slot'}
							class="w-full rounded-md border border-line-2 px-2 py-1.5 text-sm"
						/>
					</label>
				{/if}
			</div>

			{#if requiredParents.length > 0}
				<div class="mt-3 space-y-2">
					<div class="text-xs text-ink-2">
						This is a child instrument. Pick the parents it sits under — they must already exist
						there; add a missing one as its own step first.
					</div>
					{#each requiredParents as parentType (parentType)}
						{@const candidates = nodesOfType(parentType)}
						<label class="block text-xs">
							<span class="mb-1 block text-ink-2">{parentType}</span>
							{#if candidates.length === 0}
								<span class="text-warn">
									No {parentType} configured there yet — add one first.
								</span>
							{:else}
								<select
									class="w-full rounded-md border border-line-2 px-2 py-1.5 text-sm"
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
				<div class="mt-3 rounded-md border border-line bg-surface p-2.5/40">
					<div class="text-xs font-medium">Discover</div>
					<p class="text-[11px] text-muted">
						Runs on that server, where the hardware is.
					</p>
					{#each addActions as action (action.name)}
						<div class="mt-2 flex flex-wrap items-end gap-2">
							{#each action.inputs ?? [] as inp (inp.name)}
								<label class="text-[11px]">
									<span class="mb-0.5 block text-ink-2">
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
										class="rounded border border-line-2 px-2 py-1 text-xs"
									/>
								</label>
							{/each}
							<button
								class="rounded-md border border-line-2 px-2.5 py-1 text-xs hover:bg-surface-2 disabled:opacity-50"
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
									class="block w-full rounded border border-line px-2 py-1 text-left text-xs hover:border-accent"
									onclick={() => {
										addKey = found.port ?? firstKeyField(found.key_fields);
										checkDuplicate();
									}}
								>
									<span class="font-mono">{found.port ?? firstKeyField(found.key_fields)}</span>
									{#if found.description || found.idn}
										<span class="ml-1 text-muted">{found.description ?? found.idn}</span>
									{/if}
								</button>
							{/each}
							{#each discoveryResult.children ?? [] as child}
								<button
									class="block w-full rounded border border-line px-2 py-1 text-left text-xs hover:border-accent"
									onclick={() => {
										addType = child.type;
										addKey = firstKeyField(child.key_fields);
										checkDuplicate();
									}}
								>
									<span class="font-mono">{child.type}</span>
									<span class="ml-1 text-muted">
										{firstKeyField(child.key_fields)}
										{child.idn ?? ''}
									</span>
								</button>
							{/each}
							{#if (discoveryResult.found ?? []).length === 0 && (discoveryResult.children ?? []).length === 0}
								<div class="text-[11px] text-muted">Nothing found.</div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}

			{#if duplicate?.clashes?.length}
				<!-- The cross-workspace mistake: two configs pointing at one device.
				     A warning, not a refusal — occasionally it is deliberate. -->
				<div class="mt-3 rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs">
					<div class="font-medium text-warn">
						<span class="font-mono">{duplicate.transport_key}</span> is already configured elsewhere
					</div>
					<ul class="mt-1 space-y-0.5 text-warn">
						{#each duplicate.clashes as c (c.root + (c.url ?? ''))}
							<li>
								{c.workspace_path ? workspaceName(c.workspace_path) : 'this workspace'}
								— <span class="font-mono">{c.root}</span>
								{#if c.held}<span class="font-medium">(open right now)</span>{/if}
							</li>
						{/each}
					</ul>
					<div class="mt-1 text-warn">
						One device, two owners. Whichever process opens it second will be refused.
					</div>
				</div>
			{/if}

			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2"
					onclick={resetAddForm}
					disabled={loading}>Cancel</button
				>
				<button
					class="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:brightness-110 disabled:opacity-50"
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
				class="rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs text-warn"
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
					class="relative overflow-hidden rounded-lg border border-line"
					orientation="vertical"
					viewportClasses="h-full max-h-[28rem] w-full"
				>
					<div class="p-2">
						{#if remote.tree.length === 0}
							<p class="px-2 py-3 text-sm text-muted">No instruments configured there.</p>
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
					class="max-h-[28rem] space-y-1.5 overflow-y-auto rounded-lg border border-line p-2 text-xs"
				>
					{#if remote.events.length === 0}
						<p class="px-1 py-2 text-muted">Nothing recorded yet.</p>
					{:else}
						{#each remote.events as e (e.ts + e.kind)}
							<div class="border-b border-line pb-1.5 last:border-0">
								<div class="text-ink">{e.message}</div>
								<div class="text-[10px] text-muted">
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
		<div class="w-full max-w-md rounded border border-line bg-surface p-5 shadow-2xl">
			<h3 class="text-lg font-semibold">
				{confirmAction === 'remove' ? 'Remove from' : 'Reset in'} that workspace?
			</h3>
			<p class="mt-2 text-sm text-ink-2">
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
					class="rounded-md px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2"
					onclick={() => (confirmTarget = null)}
					disabled={loading}>Cancel</button
				>
				<button
					class="flex items-center gap-1 rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-50 {confirmAction ===
					'remove'
						? 'bg-crit hover:brightness-110'
						: 'bg-warn hover:brightness-110'}"
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
