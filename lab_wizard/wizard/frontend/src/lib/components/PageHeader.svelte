<script lang="ts">
	/** Title, one-paragraph explanation, and the page's primary action.
	 *
	 * The lede is capped near 64 characters. These pages explain machinery that
	 * needs real sentences — which process owns a transport, why a rule is inert
	 * — and a full-width line of body text at this size is genuinely harder to
	 * read than a narrow one.
	 */
	import type { Snippet } from 'svelte';

	let {
		title,
		lede = undefined,
		actions = undefined,
		children = undefined
	}: {
		title: string;
		lede?: string | undefined;
		actions?: Snippet | undefined;
		/** Richer lede content when a plain string will not do (links, code). */
		children?: Snippet | undefined;
	} = $props();
</script>

<div class="flex items-start justify-between gap-4">
	<div class="min-w-0">
		<h1 class="text-[22px] font-semibold tracking-tight">{title}</h1>
		{#if lede}
			<p class="mt-1 max-w-[64ch] text-[13px] text-muted">{lede}</p>
		{/if}
		{#if children}
			<div class="mt-1 max-w-[64ch] text-[13px] text-muted">{@render children()}</div>
		{/if}
	</div>
	{#if actions}
		<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>
	{/if}
</div>
