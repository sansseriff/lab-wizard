<!-- The items of a row menu. DropdownMenu and ContextMenu share bits-ui's menu
     items, so this renders inside either. Dangerous actions go last, after a
     divider, so they are never the first thing under the pointer. -->
<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { goto } from '$app/navigation';
	import type { MenuAction } from './items';

	let { items }: { items: MenuAction[] } = $props();
	const safe = $derived(items.filter((item) => !item.danger));
	const danger = $derived(items.filter((item) => item.danger));
</script>

{#snippet entry(item: MenuAction)}
	<DropdownMenu.Item
		class="lw-menu-item {item.danger ? 'text-crit' : ''}"
		disabled={item.disabled}
		onSelect={() => (item.href ? goto(item.href) : item.onselect?.())}>{item.label}</DropdownMenu.Item
	>
{/snippet}

{#each safe as item (item.label)}{@render entry(item)}{/each}
{#if safe.length && danger.length}<DropdownMenu.Separator class="my-1 h-px bg-line" />{/if}
{#each danger as item (item.label)}{@render entry(item)}{/each}
