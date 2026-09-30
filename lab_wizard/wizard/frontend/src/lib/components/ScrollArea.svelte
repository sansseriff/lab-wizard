<!--
  The one scroll container: its scrollbar is drawn by bits-ui over the content
  and shows only while the pointer is over it, as in Figma. Native scrollbars
  cannot do this in the desktop window: WebKit does not repaint a scrollbar
  restyled on :hover, so one hovered once stays visible.

  To fill a flex column, give it `min-h-0 flex-1`; to cap a list, pass a
  `max-h-…` in `viewportClasses`.
-->
<script lang="ts">
	import { ScrollArea, type WithoutChild } from 'bits-ui';

	type Props = WithoutChild<ScrollArea.RootProps> & {
		orientation?: 'vertical' | 'horizontal' | 'both';
		viewportClasses?: string;
	};

	let {
		ref = $bindable(null),
		orientation = 'vertical',
		type = 'hover',
		viewportClasses = '',
		class: className = '',
		children,
		...restProps
	}: Props = $props();
</script>

{#snippet Scrollbar({ orientation }: { orientation: 'vertical' | 'horizontal' })}
	<ScrollArea.Scrollbar
		{orientation}
		class="z-10 flex touch-none select-none p-px {orientation === 'vertical' ? 'w-2.5' : 'h-2.5'}"
	>
		<ScrollArea.Thumb class="flex-1 rounded-full bg-line-2 hover:bg-muted" />
	</ScrollArea.Scrollbar>
{/snippet}

<ScrollArea.Root bind:ref {type} class="relative overflow-hidden {className}" {...restProps}>
	<ScrollArea.Viewport class="size-full {viewportClasses}">
		{@render children?.()}
	</ScrollArea.Viewport>
	{#if orientation === 'vertical' || orientation === 'both'}
		{@render Scrollbar({ orientation: 'vertical' })}
	{/if}
	{#if orientation === 'horizontal' || orientation === 'both'}
		{@render Scrollbar({ orientation: 'horizontal' })}
	{/if}
	<ScrollArea.Corner />
</ScrollArea.Root>
