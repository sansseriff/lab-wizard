<!-- The "⋯" button at the end of a row: its less common actions, one click
     away without a row of buttons. -->
<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import DotsThreeIcon from 'phosphor-svelte/lib/DotsThree';
	import MenuItems from './MenuItems.svelte';
	import type { MenuAction } from './items';

	let { items, label, small = false }: { items: MenuAction[]; label: string; small?: boolean } =
		$props();
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger class="lw-btn {small ? 'lw-btn-sm' : ''} px-1.5" aria-label={label}>
		<DotsThreeIcon size={16} weight="bold" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Portal>
		<DropdownMenu.Content class="lw-menu" align="end" sideOffset={4}>
			<MenuItems {items} />
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>
