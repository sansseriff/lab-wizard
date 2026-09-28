<!-- A hint on hover or keyboard focus, in place of a `title` attribute (which
     is slow to appear, drawn differently by every browser, and never shown to
     keyboard users). Spread `props` onto the element it describes:

     <Tooltip text="Reload">{#snippet child({ props })}<button {...props}>…</button>{/snippet}</Tooltip>
-->
<script lang="ts">
	import { Tooltip } from 'bits-ui';
	import type { Snippet } from 'svelte';

	let {
		text,
		side = 'top',
		child
	}: {
		text: string;
		side?: 'top' | 'bottom' | 'left' | 'right';
		child: Snippet<[{ props: Record<string, unknown> }]>;
	} = $props();
</script>

<Tooltip.Root>
	<Tooltip.Trigger {child} />
	<Tooltip.Portal>
		<Tooltip.Content {side} sideOffset={6} class="lw-tooltip">{text}</Tooltip.Content>
	</Tooltip.Portal>
</Tooltip.Root>
