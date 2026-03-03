<script module lang="ts">
	export type TreeItem = {
		type: string;
		key: string;
		fields: Record<string, any>;
		children: Record<string, TreeItem>;
	};

	export type TreePathRef = {
		type: string;
		key: string;
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

	function handleSelect() {
		if (!isSelectable || !compatible || !onSelect) return;
		onSelect(node, currentPath);
	}
</script>

<div class="relative" style="padding-left: {depth > 0 ? 1.25 : 0}rem;">
	{#if depth > 0}
		<div
			class="absolute top-0 bottom-0 left-0 w-px bg-gray-300 dark:bg-gray-600"
			style="left: 0.125rem;"
		></div>
	{/if}

	<div
		class="group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition hover:bg-gray-100 dark:hover:bg-gray-800 {isSelectable && !compatible ? 'opacity-45' : ''} {selected ? 'bg-gray-200 dark:bg-gray-700' : ''} {isSelectable && compatible ? 'cursor-pointer' : ''}"
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
				class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
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

		<span class="font-medium text-gray-900 dark:text-gray-100">{node.type}</span>
		<span class="text-xs text-gray-500 dark:text-gray-400">({node.key})</span>
		{#if selectBadge}
			<span
				class="rounded px-1.5 py-0.5 text-[10px] bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300"
			>
				{selectBadge}
			</span>
		{/if}

		<div class="ml-auto flex gap-1 opacity-0 transition group-hover:opacity-100">
			{#if onReset}
				<button
					class="rounded p-1 text-gray-500 hover:bg-gray-200 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200"
					title="Reset to defaults"
					onclick={() => onReset?.(node)}
				>
					<ArrowCounterClockwise size={14} />
				</button>
			{/if}
			{#if onRemove}
				<button
					class="rounded p-1 text-gray-500 hover:bg-red-100 hover:text-red-600 dark:text-gray-400 dark:hover:bg-red-900/40 dark:hover:text-red-400"
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
					path={currentPath}
				/>
			{/each}
		</div>
	{/if}
</div>
