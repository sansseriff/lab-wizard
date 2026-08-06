<script module lang="ts">
	export type TreeItem = {
		type: string;
		key: string;
		fields: Record<string, any>;
		children: Record<string, TreeItem>;
		// Hardware channel count for channel providers (class-level fact);
		// absent for instruments without channels.
		num_channels?: number;
	};

	export type TreePathRef = {
		type: string;
		key: string;
	};

	// Transport facts for a root, shown as badges. Only roots own a transport,
	// so children and channels inherit their root's answer and are not badged.
	export type TransportBadge = {
		transport_sharing: 'exclusive' | 'shared';
		state_authority: 'inferred' | 'subscribed';
		held_by_server: boolean;
	};
</script>

<script lang="ts">
	import { CaretDown, CaretRight, ArrowCounterClockwise, Trash } from 'phosphor-svelte';
	import Self from './TreeNode.svelte';

	type Props = {
		node: TreeItem;
		depth?: number;
		onReset?: (node: TreeItem) => void;
		onRemove?: (node: TreeItem) => void;
		onSelect?: (node: TreeItem, path: TreePathRef[]) => void;
		isSelectable?: boolean;
		isCompatible?: (node: TreeItem, path: TreePathRef[]) => boolean;
		isSelected?: (node: TreeItem, path: TreePathRef[]) => boolean;
		selectionLabel?: (node: TreeItem, path: TreePathRef[]) => string | null;
		// Optional, so every existing caller renders unchanged.
		transportBadge?: (node: TreeItem, path: TreePathRef[]) => TransportBadge | null;
		path?: TreePathRef[];
	};

	let {
		node,
		depth = 0,
		onReset,
		onRemove,
		onSelect,
		isSelectable = false,
		isCompatible,
		isSelected,
		selectionLabel,
		transportBadge,
		path = []
	}: Props = $props();

	let expanded = $state(true);
	const childEntries = $derived(Object.entries(node.children ?? {}));
	const hasChildren = $derived(childEntries.length > 0);
	const currentPath = $derived([...path, { type: node.type, key: node.key }]);
	const compatible = $derived(
		isSelectable ? (isCompatible ? isCompatible(node, currentPath) : true) : true
	);
	const selected = $derived(
		isSelectable ? (isSelected ? isSelected(node, currentPath) : false) : false
	);
	const selectBadge = $derived(selectionLabel ? selectionLabel(node, currentPath) : null);
	// Depth 0 is a root — the only node that owns a transport.
	const transport = $derived(
		depth === 0 && transportBadge ? transportBadge(node, currentPath) : null
	);

	function handleSelect() {
		if (!isSelectable || !compatible || !onSelect) return;
		onSelect(node, currentPath);
	}
</script>

<div class="relative" style="padding-left: {depth > 0 ? 1.25 : 0}rem;">
	{#if depth > 0}
		<div
			class="absolute top-0 bottom-0 left-0 w-px bg-surface-3"
			style="left: 0.125rem;"
		></div>
	{/if}

	<div
		class="group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition hover:bg-surface-2 {isSelectable && !compatible ? 'opacity-45' : ''} {selected ? 'bg-surface-2' : ''} {isSelectable && compatible ? 'cursor-pointer' : ''}"
		role={isSelectable ? 'button' : undefined}
		aria-disabled={isSelectable && !compatible}
		onclick={handleSelect}
		onkeydown={(e) => {
			if ((e.key === 'Enter' || e.key === ' ') && isSelectable && compatible) {
				e.preventDefault();
				handleSelect();
			}
		}}
	>
		{#if hasChildren}
			<button
				class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted hover:text-ink"
				onclick={() => (expanded = !expanded)}
			>
				{#if expanded}
					<CaretDown size={14} />
				{:else}
					<CaretRight size={14} />
				{/if}
			</button>
		{:else}
			<span class="h-5 w-5 shrink-0"></span>
		{/if}

		<span class="font-medium text-ink">{node.type}</span>
		<span class="text-xs text-muted">({node.key})</span>
		{#if selectBadge}
			<span
				class="rounded px-1.5 py-0.5 text-[10px] bg-accent-wash text-accent-strong"
			>
				{selectBadge}
			</span>
		{/if}

		{#if transport}
			<span
				class="rounded px-1.5 py-0.5 text-[10px] font-medium {transport.transport_sharing ===
				'shared'
					? 'bg-ok-wash text-ok'
					: 'bg-warn-wash text-warn'}"
				title={transport.transport_sharing === 'shared'
					? 'Behind a server that already multiplexes it — several programs may use it at once'
					: 'One process at a time can hold this transport'}
			>
				{transport.transport_sharing}
			</span>
			{#if transport.state_authority === 'subscribed'}
				<span
					class="rounded bg-accent-wash px-1.5 py-0.5 text-[10px] font-medium text-accent-strong"
					title="State is read from the process that owns this hardware, not inferred from our own commands"
				>
					subscribed
				</span>
			{/if}
			{#if transport.held_by_server}
				<span
					class="rounded bg-crit-wash px-1.5 py-0.5 text-[10px] font-medium text-crit"
					title="A server has this hardware open right now"
				>
					in use
				</span>
			{/if}
		{/if}

		<div class="ml-auto flex gap-1 opacity-0 transition group-hover:opacity-100">
			{#if onReset}
				<button
					class="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink-2"
					title="Reset to defaults"
					onclick={() => onReset?.(node)}
				>
					<ArrowCounterClockwise size={14} />
				</button>
			{/if}
			{#if onRemove}
				<button
					class="rounded p-1 text-muted hover:bg-crit-wash hover:text-crit"
					title="Remove"
					onclick={() => onRemove?.(node)}
				>
					<Trash size={14} />
				</button>
			{/if}
		</div>
	</div>

	{#if expanded && hasChildren}
		<div>
			{#each childEntries as [_childKey, child]}
				<Self
					node={child}
					depth={depth + 1}
					{onReset}
					{onRemove}
					{onSelect}
					{isSelectable}
					{isCompatible}
					{isSelected}
					{selectionLabel}
					{transportBadge}
					path={currentPath}
				/>
			{/each}
		</div>
	{/if}
</div>
