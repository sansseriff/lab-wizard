<!-- Right-click on a row for the same actions as its "⋯" button (or, where a
     row has no room for one, as a shortcut to the buttons elsewhere). Wraps
     its children without adding a box of its own; for a table row, use the
     `child` snippet and spread `props` onto the <tr>. -->
<script lang="ts">
	import { ContextMenu } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import MenuItems from './MenuItems.svelte';
	import type { MenuAction } from './items';

	let {
		items,
		disabled = false,
		child = undefined,
		children = undefined
	}: {
		items: MenuAction[];
		disabled?: boolean;
		child?: Snippet<[{ props: Record<string, unknown> }]>;
		children?: Snippet;
	} = $props();
</script>

<ContextMenu.Root>
	{#if child}
		<ContextMenu.Trigger {disabled} {child} />
	{:else}
		<ContextMenu.Trigger {disabled} class="contents">{@render children?.()}</ContextMenu.Trigger>
	{/if}
	<ContextMenu.Portal>
		<ContextMenu.Content class="lw-menu">
			<MenuItems {items} />
		</ContextMenu.Content>
	</ContextMenu.Portal>
</ContextMenu.Root>
