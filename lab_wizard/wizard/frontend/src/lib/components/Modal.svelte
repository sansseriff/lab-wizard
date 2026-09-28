<script lang="ts">
	/** A centred dialog over a dimmed page.
	 *
	 * The add-instrument flow used to render inline *below* the instrument tree,
	 * which meant a multi-step form began somewhere off the bottom of a page that
	 * was already several screens tall. A dialog puts the step you are on where
	 * you are looking, and it keeps its own scroll so a long list of instrument
	 * types cannot push the buttons out of reach.
	 */
	import { Dialog } from 'bits-ui';
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

	/** Focus the first field rather than the close button, so typing works
	 *  immediately — the type picker's search field is the first thing in the
	 *  dialog for exactly this reason. */
	let content = $state<HTMLElement | null>(null);
	function focusFirstField(event: Event) {
		const field = content?.querySelector<HTMLElement>(
			'[data-modal-body] :is(input:not([type=checkbox]), select, textarea, button)'
		);
		if (field) {
			event.preventDefault();
			field.focus();
		}
	}
</script>

<Dialog.Root
	open
	onOpenChange={(open) => {
		if (!open) onclose();
	}}
>
	<Dialog.Portal>
		<Dialog.Overlay class="fixed inset-0 z-50 bg-black/50" />
		<Dialog.Content
			bind:ref={content}
			onOpenAutoFocus={focusFirstField}
			class="fixed left-1/2 top-4 z-50 flex w-[calc(100vw-2rem)] {width} max-h-[calc(100vh-2rem)] -translate-x-1/2 flex-col rounded-lg border border-line bg-surface shadow-2xl sm:top-8 sm:max-h-[calc(100vh-4rem)]"
		>
			<div class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
				<div class="min-w-0">
					<Dialog.Title class="text-title font-semibold">{title}</Dialog.Title>
					{#if subtitle}
						<Dialog.Description class="mt-0.5 text-xs text-muted">{subtitle}</Dialog.Description>
					{/if}
				</div>
				<Dialog.Close
					class="shrink-0 rounded p-1 text-muted transition-colors hover:bg-surface-2 hover:text-ink"
					aria-label="Close"
				>
					<XIcon size={16} />
				</Dialog.Close>
			</div>

			<div class="min-h-0 flex-1 overflow-y-auto px-4 py-3.5" data-modal-body>
				{@render children()}
			</div>

			{#if footer}
				<div class="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
					{@render footer()}
				</div>
			{/if}
		</Dialog.Content>
	</Dialog.Portal>
</Dialog.Root>
