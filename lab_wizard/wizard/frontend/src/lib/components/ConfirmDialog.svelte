<!--
  The one "are you sure?" dialog. Escape, the Cancel button and a click outside
  all cancel; focus is trapped inside while it is open and returns to whatever
  opened it. For a plain question use `ask()` from `$lib/confirm.svelte`; use
  this directly when the body needs more than a sentence (a list of what the
  removal will break, say) or the action is slow and should keep it open.
-->
<script lang="ts">
	import { AlertDialog } from 'bits-ui';
	import type { Snippet } from 'svelte';

	let {
		open,
		title,
		description = undefined,
		confirmLabel = 'Confirm',
		cancelLabel = 'Cancel',
		tone = 'primary',
		busy = false,
		onconfirm,
		oncancel,
		children = undefined
	}: {
		open: boolean;
		title: string;
		description?: string;
		confirmLabel?: string;
		cancelLabel?: string;
		/** `danger` for anything that deletes or discards. */
		tone?: 'primary' | 'danger';
		/** Disables both buttons while the action runs; the dialog stays up. */
		busy?: boolean;
		onconfirm: () => void;
		oncancel: () => void;
		children?: Snippet;
	} = $props();
</script>

<AlertDialog.Root
	{open}
	onOpenChange={(next) => {
		if (!next && !busy) oncancel();
	}}
>
	<AlertDialog.Portal>
		<AlertDialog.Overlay class="fixed inset-0 z-50 bg-black/50" />
		<AlertDialog.Content
			class="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded border border-line bg-surface p-5 shadow-lg"
		>
			<AlertDialog.Title class="text-title font-semibold">{title}</AlertDialog.Title>
			{#if description}
				<AlertDialog.Description class="mt-2 text-body text-ink-2">{description}</AlertDialog.Description>
			{/if}
			{#if children}<div class="mt-2 text-body text-ink-2">{@render children()}</div>{/if}
			<div class="mt-4 flex justify-end gap-2">
				<AlertDialog.Cancel class="lw-btn" disabled={busy}>{cancelLabel}</AlertDialog.Cancel>
				<AlertDialog.Action
					class="lw-btn {tone === 'danger' ? 'lw-btn-danger' : 'lw-btn-primary'}"
					disabled={busy}
					onclick={onconfirm}>{busy ? 'Working…' : confirmLabel}</AlertDialog.Action
				>
			</div>
		</AlertDialog.Content>
	</AlertDialog.Portal>
</AlertDialog.Root>
