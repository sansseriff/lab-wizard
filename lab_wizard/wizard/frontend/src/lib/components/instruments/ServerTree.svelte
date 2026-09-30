<script lang="ts">
	/** Another workspace's instrument tree, fetched from its daemon.
	 *
	 * Editable, because reaching its `ipc://` socket already proves same-machine.
	 * The tree and its schema come from *that* server, never from this build —
	 * it may be running a different lab_wizard, and offering a type it could not
	 * instantiate would be a form that cannot be submitted.
	 */
	import { onMount } from 'svelte';
	import InstrumentWorkbench from './InstrumentWorkbench.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import type { InstrumentMeta } from '$lib/types/instruments';
	import {
		parentCandidates,
		type NodePath,
		type ParamUpdate,
		type ParamResult
	} from '$lib/instruments/model';
	import type { TreeItem as TreeNodeItem, TransportBadge } from '$lib/components/TreeNode.svelte';
	import { api, errorMessage, unwrap } from '$lib/api';
	import { workspaceName, type LocalServer } from '$lib/types/instruments';
	import ArrowClockwise from 'phosphor-svelte/lib/ArrowClockwise';
	import ArrowCounterClockwise from 'phosphor-svelte/lib/ArrowCounterClockwise';
	import Trash from 'phosphor-svelte/lib/Trash';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { ask } from '$lib/confirm.svelte';
	import Select from '$lib/components/Select.svelte';

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

	let {
		server,
		dirty = $bindable(false),
		busy = $bindable(false)
	}: { server: LocalServer; dirty?: boolean; busy?: boolean } = $props();
	let addParent = $state<{ node: TreeNodeItem; path: NodePath } | null>(null);

	// Named `selected` throughout the edit helpers below; keeping the name means
	// the tab row simply supplies what a picker used to.
	const selected = $derived(server);

	let remote: RemoteTree | null = $state(null);
	let loading = $state(false);
	let message: { text: string; ok: boolean } | null = $state(null);
	let confirmTarget: TreeNodeItem | null = $state(null);
	let confirmPath = $state<NodePath>([]);
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
	let duplicate: { transport_key: string | null; clashes: DuplicateClash[] } | null = $state(null);

	/** The server's own instrument vocabulary — never this build's. */
	function metadataOf(r: RemoteTree | null): Record<string, InstrumentMeta> {
		return r?.metadata ?? {};
	}

	const addableTypes: InstrumentMeta[] = $derived(
		Object.values(metadataOf(remote))
			.filter((m) => !addParent || m.parent_type === addParent.node.type)
			.sort((a, b) => a.type.localeCompare(b.type))
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
			remote = await unwrap<RemoteTree>(api.POST('/api/remote-tree', { body: { config_dir: selected.config_dir } }));
		} catch (e) {
			remote = null;
			message = { text: errorMessage(e), ok: false };
		} finally {
			loading = false;
		}
	}

	async function edit(operation: string, payload: Record<string, any>) {
		if (!selected) return;
		loading = true;
		message = null;
		try {
			await api.POST('/api/remote-tree/edit', { body: { config_dir: selected.config_dir, operation, payload } });
			message = { text: `Applied ${operation}.`, ok: true };
			resetAddForm();
			await loadTree();
		} catch (e) {
			// The server refuses a remote peer, or a rack that is currently open.
			// Both are its decision, so its wording is what the user sees.
			message = { text: errorMessage(e), ok: false };
		} finally {
			loading = false;
			confirmTarget = null;
		}
	}

	function startAdd(node?: TreeNodeItem, path?: NodePath) {
		resetAddForm();
		addParent = node && path ? { node, path } : null;
		showAdd = true;
	}

	async function saveParams(update: ParamUpdate): Promise<ParamResult> {
		const result = await unwrap<ParamResult>(
			api.POST('/api/remote-tree/edit', {
				body: { config_dir: server.config_dir, operation: 'update', payload: update }
			})
		);
		await loadTree();
		return result;
	}

	async function refresh() {
		if (busy || (dirty && !(await ask({ title: 'Discard unsaved instrument parameters?', confirmLabel: 'Discard', tone: 'danger' })))) return;
		dirty = false;
		loadTree();
	}

	function resetAddForm() {
		showAdd = false;
		addParent = null;
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
		addParents = Object.fromEntries((addParent?.path ?? []).map((p) => [p.type, p.key]));
		discoveryResult = null;
		duplicate = null;
		const inputs: Record<string, any> = {};
		for (const action of metadataOf(remote)[type]?.discovery_actions ?? []) {
			for (const inp of action.inputs ?? []) inputs[inp.name] = inp.default ?? '';
		}
		discoveryInputs = inputs;
	}

	/** Constrain each parent to the already selected ancestor branch. */
	function nodesOfType(type: string): TreeNodeItem[] {
		const ancestors = requiredParents.slice(requiredParents.indexOf(type) + 1).reverse()
			.map((type) => ({ type, key: addParents[type] ?? '' }));
		return parentCandidates(remote?.tree ?? [], type, ancestors);
	}

	function selectParent(type: string, key: string) {
		if (!nodesOfType(type).some((node) => node.key === key)) return;
		const next = { ...addParents, [type]: key };
		for (const descendant of requiredParents.slice(0, requiredParents.indexOf(type))) {
			delete next[descendant];
		}
		addParents = next;
		discoveryResult = null;
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
			discoveryResult = await unwrap<typeof discoveryResult>(
				api.POST('/api/remote-tree/discover', {
					body: {
						config_dir: selected.config_dir,
						type: addType,
						action: actionName,
						params: discoveryInputs,
						parent_chain: parentChain
					}
				})
			);
		} catch (e) {
			message = { text: errorMessage(e), ok: false };
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
			duplicate = await unwrap<typeof duplicate>(
				api.POST('/api/transport-status/duplicate-check', {
					body: { type: addType, key: addKey.trim(), include_local: true }
				})
			);
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
				onclick={refresh}
				disabled={loading || busy}
			>
				<ArrowClockwise size={14} />
				Refresh
			</button>
		</div>
	</div>

	{#if message}
		<div
			class="rounded border p-3 text-body {message.ok
				? 'border-ok/30 bg-ok-wash text-ok'
				: 'border-crit/30 bg-crit-wash text-crit'}"
		>
			{message.text}
		</div>
	{/if}

	{#if remote && showAdd}
		<Modal
			title={addParent ? `Add under ${addParent.node.type}` : 'Add instrument'}
			onclose={resetAddForm}
		>
			<h2 class="text-body font-medium">
				Add to <span class="font-mono">{workspaceName(selected?.workspace_path ?? '')}</span>
			</h2>
			<p class="mt-1 text-xs text-ink-2">
				Instrument types come from that server's build, not this one — so you cannot configure
				something it could not instantiate.
			</p>

			<div class="mt-3 grid gap-3 sm:grid-cols-2">
				<label class="block text-xs">
					<span class="mb-1 block text-ink-2">Instrument type</span>
					<Select
						value={addType ?? ''}
						onValueChange={onPickType}
						placeholder="Choose a type…"
						options={addableTypes.map((meta) => ({ value: meta.type, label: meta.type }))}
					/>
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
							class="lw-input mono"
						/>
					</label>
				{/if}
			</div>

			{#if addParent}<p class="mt-3 text-xs text-muted">
					Parent: {addParent.path.map((p) => p.type).join(' → ')}. All ancestors are already
					selected.
				</p>{/if}
			{#if requiredParents.length > 0 && !addParent}
				<div class="mt-3 space-y-2">
					<div class="text-xs text-ink-2">
						This is a child instrument. Pick the parents it sits under — they must already exist
						there; add a missing one as its own step first.
					</div>
					{#each [...requiredParents].reverse() as parentType (parentType)}
						{@const candidates = nodesOfType(parentType)}
						<label class="block text-xs">
							<span class="mb-1 block text-ink-2">{parentType}</span>
							{#if candidates.length === 0}
								<span class="text-warn">
									Choose its ancestor above first. If it has no {parentType}, add one there.
								</span>
							{:else}
								<Select
									value={addParents[parentType] ?? ''}
									onValueChange={(v) => selectParent(parentType, v)}
									options={candidates.map((node) => ({
										value: node.key,
										label: `${node.type} (${node.key})`
									}))}
								/>
							{/if}
						</label>
					{/each}
				</div>
			{/if}

			{#if addActions.length > 0}
				<div class="mt-3 rounded-md border border-line bg-surface p-2.5/40">
					<div class="text-xs font-medium">Discover</div>
					<p class="text-fine text-muted">Runs on that server, where the hardware is.</p>
					{#each addActions as action (action.name)}
						<div class="mt-2 flex flex-wrap items-end gap-2">
							{#each action.inputs ?? [] as inp (inp.name)}
								<label class="text-fine">
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
										class="lw-input w-40"
									/>
								</label>
							{/each}
							<button
								class="lw-btn"
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
								<div class="text-fine text-muted">Nothing found.</div>
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
				<button class="lw-btn" onclick={resetAddForm} disabled={loading}>Cancel</button>
				<button
					class="lw-btn lw-btn-primary"
					onclick={submitAdd}
					disabled={!canSubmitAdd || loading}
				>
					{loading ? 'Adding…' : 'Add'}
				</button>
			</div>
		</Modal>
	{/if}

	{#if remote}
		{#if remote.config_status?.diverged}
			<div class="rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs text-warn">
				That server's config on disk no longer matches what it loaded — someone edited the files
				directly. It needs a restart to pick the change up.
			</div>
		{/if}

		<InstrumentWorkbench
			tree={remote.tree}
			metadata={remote.metadata}
									{transportBadge}
			bind:dirty
			bind:busy
			onadd={() => startAdd()}
			onaddparent={(node, path) => startAdd(node, path)}
			onsave={saveParams}
			onrefresh={loadTree}
			onremove={(node, path) => {
				confirmPath = path;
										confirmAction = 'remove';
				confirmTarget = node;
									}}
			onreset={(node, path) => {
				confirmPath = path;
										confirmAction = 'reset';
				confirmTarget = node;
									}}
								/>

			<div>
				<h2 class="mb-2 text-body font-medium">Recent activity</h2>
				<div
					class="max-h-[28rem] space-y-1.5 overflow-y-auto rounded-lg border border-line p-2 text-xs"
				>
					{#if remote.events.length === 0}
						<p class="px-1 py-2 text-muted">Nothing recorded yet.</p>
					{:else}
						{#each remote.events as e (e.ts + e.kind)}
							<div class="border-b border-line pb-1.5 last:border-0">
								<div class="text-ink">{e.message}</div>
								<div class="text-2xs text-muted">
									{when(e.ts)} · {e.actor}
								</div>
							</div>
						{/each}
					{/if}
			</div>
		</div>
	{/if}
</section>

<ConfirmDialog
	open={confirmTarget !== null}
	title="{confirmAction === 'remove' ? 'Remove from' : 'Reset in'} that workspace?"
	confirmLabel={confirmAction === 'remove' ? 'Remove' : 'Reset'}
	tone={confirmAction === 'remove' ? 'danger' : 'primary'}
	busy={loading}
	onconfirm={() =>
		edit(confirmAction, { type: confirmTarget!.type, key: confirmTarget!.key, path: confirmPath })}
	oncancel={() => (confirmTarget = null)}
>
	{#if confirmAction === 'remove'}
		This removes <strong>{confirmTarget?.type}</strong> ({confirmTarget?.key}) and its children from
		the other workspace's config — not this one.
	{:else}
		This resets <strong>{confirmTarget?.type}</strong> ({confirmTarget?.key}) to default field values
		in the other workspace's config, keeping its children.
	{/if}
</ConfirmDialog>
