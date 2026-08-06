<script lang="ts">
	/** A small state chip.
	 *
	 * `tone` is semantic, never decorative: `ok` / `warn` / `crit` mean something
	 * is respectively fine, contended, or refused. `accent` marks a selection,
	 * which is why it is a separate tone from `ok` — "chosen" and "healthy" are
	 * different facts and must not share a colour.
	 */
	import type { Snippet } from 'svelte';

	let {
		tone = 'neutral',
		dot = false,
		title = undefined,
		children
	}: {
		tone?: 'neutral' | 'ok' | 'warn' | 'crit' | 'accent';
		dot?: boolean;
		title?: string | undefined;
		children: Snippet;
	} = $props();

	const tones: Record<string, string> = {
		neutral: 'bg-surface-2 text-muted border-line',
		ok: 'bg-ok-wash text-ok border-ok/30',
		warn: 'bg-warn-wash text-warn border-warn/30',
		crit: 'bg-crit-wash text-crit border-crit/30',
		accent: 'bg-accent-wash text-accent-strong border-accent/30'
	};
</script>

<span
	{title}
	class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold {tones[
		tone
	]}"
>
	{#if dot}
		<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-current"></span>
	{/if}
	{@render children()}
</span>
