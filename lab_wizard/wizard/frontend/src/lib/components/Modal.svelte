<script lang="ts">
	/** A centred dialog over a dimmed page.
	 *
	 * The add-instrument flow used to render inline *below* the instrument tree,
	 * which meant a multi-step form began somewhere off the bottom of a page that
	 * was already several screens tall. A dialog puts the step you are on where
	 * you are looking, and it keeps its own scroll so a long list of instrument
	 * types cannot push the buttons out of reach.
	 */
	import type { Snippet } from 'svelte';
	import XIcon from 'phosphor-svelte/lib/X';

	let {
		title,
		subtitle = undefined,
		/** Escape and backdrop clicks route here, same as the close button. */
		onclose,
		/** Sticky row at the bottom — the step's own controls. */
		footer = undefined,
		width = 'max-w-2xl',
		children
	}: {
		title: string;
		subtitle?: string | undefined;
		onclose: () => void;
		footer?: Snippet | undefined;
		width?: string;
		children: Snippet;
	} = $props();

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.stopPropagation();
			onclose();
		}
	}

	/** Focus the first control so typing works immediately — the type picker's
	 *  search field is the first thing in the dialog for exactly this reason. */
	function autofocus(node: HTMLElement) {
		const target = node.querySelector<HTMLElement>(
			'input:not([type=checkbox]), select, textarea, button'
		);
		target?.focus();
	}
</script>

<svelte:window {onkeydown} />

<div class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8">
	<!-- Backdrop click closes. The dialog stops propagation so a click inside
	     never reaches it. -->
	<button
		class="fixed inset-0 cursor-default"
		aria-label="Close dialog"
		tabindex="-1"
		onclick={onclose}
	></button>

	<div
		class="relative flex w-full {width} max-h-[calc(100vh-4rem)] flex-col rounded-lg border border-line bg-surface shadow-2xl"
		role="dialog"
		aria-modal="true"
		aria-label={title}
		use:autofocus
	>
		<div class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
			<div class="min-w-0">
				<h2 class="text-[15px] font-semibold">{title}</h2>
				{#if subtitle}
					<p class="mt-0.5 text-xs text-muted">{subtitle}</p>
				{/if}
			</div>
			<button
				class="shrink-0 rounded p-1 text-muted transition-colors hover:bg-surface-2 hover:text-ink"
				title="Close"
				onclick={onclose}
			>
				<XIcon size={16} />
			</button>
		</div>

		<div class="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
			{@render children()}
		</div>

		{#if footer}
			<div class="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
				{@render footer()}
			</div>
		{/if}
	</div>
</div>
