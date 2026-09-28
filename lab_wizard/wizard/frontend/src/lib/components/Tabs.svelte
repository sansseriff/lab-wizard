<!--
  The one tab bar: an underlined row of tabs, arrow keys and Home/End to move
  between them, and a single panel below for whichever is active. Only the
  active tab's content is rendered — callers branch on `value` inside
  `children` — so a heavy tab costs nothing until it is opened.

  `onValueChange` decides whether the switch happens: the bar shows `value`,
  not the tab last clicked, so a caller can ask first ("discard changes?") and
  simply not update.
-->
<script lang="ts">
	import { Tabs } from 'bits-ui';
	import type { Snippet } from 'svelte';

	let {
		value,
		onValueChange,
		tabs,
		label,
		size = 'md',
		lead = undefined,
		actions = undefined,
		class: className = '',
		panelClass = '',
		children
	}: {
		value: string;
		onValueChange: (value: string) => void;
		/** A disabled tab stays in the bar, greyed; `title` can say why. */
		tabs: { value: string; label: string; disabled?: boolean; title?: string }[];
		/** Names the tab list for screen readers. */
		label: string;
		/** `sm` for a bar inside a panel rather than across a page. */
		size?: 'md' | 'sm';
		/** A heading at the left of the bar; the tabs then sit to its right. */
		lead?: Snippet;
		/** Controls at the right-hand end of the bar. */
		actions?: Snippet;
		class?: string;
		panelClass?: string;
		children: Snippet;
	} = $props();

	const uid = $props.id();
	const triggerId = (tab: string) => `${uid}-tab-${tab}`;
</script>

<Tabs.Root bind:value={() => value, (next) => next !== value && onValueChange(next)} class={className}>
	<div class="lw-tabs {size === 'sm' ? 'lw-tabs-sm' : ''}">
		{#if lead}<div class="min-w-0 flex-1">{@render lead()}</div>{/if}
		<Tabs.List aria-label={label} class="flex min-w-0 flex-wrap">
			{#each tabs as tab (tab.value)}
				<Tabs.Trigger
					id={triggerId(tab.value)}
					value={tab.value}
					disabled={tab.disabled}
					title={tab.title}
					class="lw-tab">{tab.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
		{#if actions}<div class="{lead ? '' : 'ml-auto'} flex shrink-0 items-center gap-2">{@render actions()}</div>{/if}
	</div>
	<div role="tabpanel" aria-labelledby={triggerId(value)} class={panelClass}>
		{@render children()}
	</div>
</Tabs.Root>
