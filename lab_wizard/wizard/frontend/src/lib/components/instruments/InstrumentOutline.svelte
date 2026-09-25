<script lang="ts">
	import CaretDown from 'phosphor-svelte/lib/CaretDown';
	import CaretRight from 'phosphor-svelte/lib/CaretRight';
	import Plus from 'phosphor-svelte/lib/Plus';
	import Self from './InstrumentOutline.svelte';
	import type { TreeItem, InstrumentMeta } from '$lib/types/instruments';
	import type { TransportBadge } from '$lib/components/TreeNode.svelte';
	import {
		pathKey,
		nodeAddress,
		matchesTree,
		childTypes,
		sortedChildren,
		type NodePath
	} from '$lib/instruments/model';
	let {
		node,
		metadata,
		path = [],
		selected,
		query,
		onselect,
		onadd,
		transportBadge,
		disabled = false
	}: {
		node: TreeItem;
		metadata: Record<string, InstrumentMeta>;
		path?: NodePath;
		selected: string;
		query: string;
		onselect: (path: NodePath) => void;
		onadd: (node: TreeItem, path: NodePath) => void;
		transportBadge?: (node: TreeItem) => TransportBadge | null;
		disabled?: boolean;
	} = $props();
	let collapsed = $state(false);
	const currentPath = $derived([...path, { type: node.type, key: node.key }]);
	const children = $derived(sortedChildren(node));
	const addable = $derived(childTypes(metadata, node).length > 0);
	const badge = $derived(path.length === 0 ? transportBadge?.(node) : null);
</script>

{#if matchesTree(node, query)}
	<div class="instrument-branch">
		<div class="instrument-row" class:selected={selected === pathKey(currentPath)}>
			{#if children.length}
				<button
					class="instrument-toggle"
					aria-label="{collapsed ? 'Expand' : 'Collapse'} {node.type}"
					aria-expanded={!collapsed || !!query}
					onclick={() => (collapsed = !collapsed)}
				>
					{#if collapsed && !query}<CaretRight size={14} />{:else}<CaretDown size={14} />{/if}
				</button>
			{:else}<span class="instrument-toggle"></span>{/if}
			<button
				class="instrument-select"
				aria-pressed={selected === pathKey(currentPath)}
				{disabled}
				onclick={() => onselect(currentPath)}
			>
				<span class="font-medium">{node.type}</span>
				<span class="instrument-address">{nodeAddress(node)}</span>
				{#if badge}<span class="instrument-badge"
						>{badge.held_by_server ? 'In use' : badge.transport_sharing}</span
					>{/if}
			</button>
			{#if addable}<button
					class="instrument-add"
					title="Add instrument under {node.type} ({nodeAddress(node)})"
					aria-label="Add instrument under {node.type} ({nodeAddress(node)})"
					{disabled}
					onclick={() => {
						collapsed = false;
						onadd(node, currentPath);
					}}><Plus size={14} /><span>Add</span></button
				>{/if}
		</div>
		{#if (!collapsed || query) && children.length}
			<div class="instrument-children">
				{#each children as child (child.key)}
					<Self
						node={child}
						{metadata}
						path={currentPath}
						{selected}
						{query}
						{onselect}
						{onadd}
						{transportBadge}
						{disabled}
					/>
				{/each}
			</div>
		{/if}
	</div>
{/if}
