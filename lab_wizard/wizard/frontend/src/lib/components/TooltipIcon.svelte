<script lang="ts">
	import type { SvelteHTMLElements } from 'svelte/elements';
	let {
		label = '',
		href = undefined,
		class: className = '',
		children,
		...rest
	} = $props<
		{
			label?: string;
			href?: string;
			class?: string;
			children?: any;
		} & SvelteHTMLElements['a']
	>();
</script>

{#if href}
	<a
		{href}
		class={`group inline-flex h-9 w-9 items-center justify-center rounded-md border border-transparent bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 ${className}`}
		{...rest}
		aria-label={label}
		title={label}
	>
		{@render children?.()}
	</a>
{:else}
	<button
		class={`group inline-flex h-9 w-9 items-center justify-center rounded-md border border-transparent bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 ${className}`}
		{...rest}
		aria-label={label}
		title={label}
	>
		{@render children?.()}
	</button>
{/if}
