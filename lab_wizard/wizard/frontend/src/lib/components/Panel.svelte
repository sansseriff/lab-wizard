<script lang="ts">
	/** A bordered card with an optional header.
	 *
	 * The header row exists so a panel's title, its one-line explanation and its
	 * primary action share a baseline across every page — that alignment is most
	 * of what makes a set of panels read as one system rather than as a stack of
	 * separately-built boxes.
	 */
	import type { Snippet } from 'svelte';

	let {
		title = undefined,
		description = undefined,
		/** Controls rendered at the right of the header. */
		actions = undefined,
		/** Drop the body padding — for tables and lists that own their own. */
		flush = false,
		class: klass = '',
		children
	}: {
		title?: string | undefined;
		description?: string | undefined;
		actions?: Snippet | undefined;
		flush?: boolean;
		class?: string;
		children: Snippet;
	} = $props();
</script>

<div class="rounded border border-line bg-surface {klass}">
	{#if title || actions}
		<div
			class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3.5 py-2.5"
		>
			<div class="min-w-0">
				{#if title}
					<h2 class="text-[13.5px] font-semibold">{title}</h2>
				{/if}
				{#if description}
					<p class="mt-0.5 text-xs text-muted">{description}</p>
				{/if}
			</div>
			{#if actions}
				<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>
			{/if}
		</div>
	{/if}

	<div class={flush ? '' : 'p-3.5'}>
		{@render children()}
	</div>
</div>
