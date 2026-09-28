<script lang="ts">
	import { ask, guardNavigation } from '$lib/confirm.svelte';
	import IconButton from '$lib/components/IconButton.svelte';
	import '$lib/procedures/composer.css';
	import '$lib/instruments/editor.css';
	import Panel from '$lib/components/Panel.svelte';
	import Plus from 'phosphor-svelte/lib/Plus';
	import ArrowClockwise from 'phosphor-svelte/lib/ArrowClockwise';
	import InstrumentOutline from './InstrumentOutline.svelte';
	import InstrumentInspector from './InstrumentInspector.svelte';
	import type { TreeItem, InstrumentMeta } from '$lib/types/instruments';
	import type { TransportBadge } from '$lib/components/TreeNode.svelte';
	import {
		nodeAt,
		pathKey,
		childTypes,
		matchesTree,
		type NodePath,
		type ParamUpdate,
		type ParamResult
	} from '$lib/instruments/model';
	let {
		tree,
		metadata,
		onadd,
		onaddparent,
		onreset,
		onremove,
		onsave,
		onrefresh,
		transportBadge,
		dirty = $bindable(false),
		busy = $bindable(false)
	}: {
		tree: TreeItem[];
		metadata: Record<string, InstrumentMeta>;
		onadd: () => void;
		onaddparent: (node: TreeItem, path: NodePath) => void;
		onreset: (node: TreeItem, path: NodePath) => void;
		onremove: (node: TreeItem, path: NodePath) => void;
		onsave: (update: ParamUpdate) => Promise<ParamResult>;
		onrefresh: () => Promise<void>;
		transportBadge?: (node: TreeItem) => TransportBadge | null;
		dirty?: boolean;
		busy?: boolean;
	} = $props();
	let selectedPath = $state<NodePath>([]);
	let query = $state('');
	let revision = $state(0);
	let refreshError = $state('');
	async function refresh() {
		if (!(await canLeave())) return;
		dirty = false;
		revision++;
		busy = true;
		refreshError = '';
		try {
			await onrefresh();
		} catch (e) {
			refreshError = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
	const selected = $derived(nodeAt(tree, selectedPath));
	const visible = $derived(tree.filter((node) => matchesTree(node, query)));
	async function canLeave() {
		return !busy && (!dirty || (await ask({ title: 'Discard unsaved instrument parameters?', confirmLabel: 'Discard', tone: 'danger' })));
	}
	async function select(path: NodePath) {
		if (pathKey(path) === pathKey(selectedPath) || !(await canLeave())) return;
		dirty = false;
		selectedPath = path;
	}
	async function action(run: () => void) {
		if (await canLeave()) {
			dirty = false;
			revision++;
			run();
		}
	}
	guardNavigation(() => dirty, { title: 'Discard unsaved instrument parameters?', confirmLabel: 'Discard', tone: 'danger' });
</script>

<div class="instrument-editor">
	<div class="editor-grid">
		<Panel
			title="Instrument tree"
			description="Select an instrument to edit its settings. Add modules directly under their parent."
			flush
		>
			{#snippet actions()}<IconButton label="Reload saved instruments" disabled={busy} onclick={refresh}><ArrowClockwise size={14} /></IconButton><button class="lw-btn lw-btn-primary" disabled={busy} onclick={() => action(onadd)}
					><Plus size={14} /> Add instrument</button
				>{/snippet}
			<div class="border-b border-line p-3">
				<input
					class="lw-input"
					type="search"
					aria-label="Find instruments"
					placeholder="Find by name, type, or address…"
					bind:value={query}
				/>
			</div>
			{#if refreshError}<p class="p-3 text-xs text-crit" role="alert">{refreshError}</p>{/if}
			<div class="p-2">
				{#each visible as node (node.key)}
					<InstrumentOutline
						{node}
						{metadata}
						selected={pathKey(selectedPath)}
						{query}
						onselect={select}
						onadd={(parent, path) => action(() => onaddparent(parent, path))}
						onreset={(target, path) => action(() => onreset(target, path))}
						onremove={(target, path) => action(() => onremove(target, path))}
						{transportBadge}
						disabled={busy}
					/>
				{/each}
				{#if !visible.length}<div class="editor-empty">
						{tree.length
							? 'No matching instruments. Try another name or address.'
							: 'No instruments yet. Add a controller, rack, or standalone instrument to get started.'}
					</div>{/if}
			</div>
		</Panel>
		<aside class="editor-detail" aria-label="Instrument inspector">
			{#if selected}
				{#key pathKey(selectedPath) + JSON.stringify(selected.fields) + revision}
					<InstrumentInspector
						node={selected}
						path={selectedPath}
						meta={metadata[selected.type]}
						{onsave}
						bind:dirty
						bind:busy
					/>
				{/key}
				<div class="editor-actions border-t border-line pt-4">
					{#if childTypes(metadata, selected).length}<button
							class="lw-btn"
							disabled={busy}
							onclick={() => action(() => onaddparent(selected!, selectedPath))}
							><Plus size={14} /> Add child</button
						>{/if}
					<button
						class="lw-btn"
						disabled={busy}
						onclick={() => action(() => onreset(selected!, selectedPath))}>Reset defaults</button
					>
					<button
						class="lw-btn text-crit"
						disabled={busy}
						onclick={() => action(() => onremove(selected!, selectedPath))}>Remove</button
					>
				</div>
			{:else}
				<p class="mb-2 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
					Instrument settings
				</p>
				<h3>Select an instrument</h3>
				<p class="mt-2 text-body leading-relaxed text-muted">
					Inspect its connection, edit parameters and channel settings, or view its saved YAML.
				</p>
				<p class="mt-4 text-xs text-muted">
					Use <strong>Add</strong> beside a parent to attach a compatible module.
				</p>
			{/if}
		</aside>
	</div>
</div>
