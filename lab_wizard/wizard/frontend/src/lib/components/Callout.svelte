<script lang="ts">
	/** A tinted box for something the user needs to know before acting.
	 *
	 * Tone is load-bearing, not decorative:
	 *   `info`  — context; nothing is wrong.
	 *   `ok`    — an operation succeeded.
	 *   `warn`  — it works, but something will bite later (a rule that is saved
	 *             but not loaded; a transport two configs both claim).
	 *   `crit`  — it failed, or will fail on the next attempt.
	 *
	 * Most of this app's failure modes are of the `warn` kind — nothing is
	 * broken *yet* — which is exactly why they need somewhere consistent to live
	 * rather than being folded into body copy.
	 */
	import type { Snippet } from 'svelte';
	import WarningIcon from 'phosphor-svelte/lib/Warning';
	import InfoIcon from 'phosphor-svelte/lib/Info';
	import CheckCircleIcon from 'phosphor-svelte/lib/CheckCircle';
	import XCircleIcon from 'phosphor-svelte/lib/XCircle';

	let {
		tone = 'info',
		title = undefined,
		children
	}: {
		tone?: 'info' | 'ok' | 'warn' | 'crit';
		title?: string | undefined;
		children: Snippet;
	} = $props();

	const styles: Record<string, string> = {
		info: 'border-line bg-surface-2 text-ink-2',
		ok: 'border-ok/30 bg-ok-wash text-ok',
		warn: 'border-warn/30 bg-warn-wash text-warn',
		crit: 'border-crit/30 bg-crit-wash text-crit'
	};
	const icons: Record<string, any> = {
		info: InfoIcon,
		ok: CheckCircleIcon,
		warn: WarningIcon,
		crit: XCircleIcon
	};
	const Icon = $derived(icons[tone]);
</script>

<div class="flex gap-2.5 rounded border px-3 py-2 text-[12.5px] leading-relaxed {styles[tone]}">
	<Icon size={15} class="mt-px shrink-0" />
	<div class="min-w-0">
		{#if title}
			<strong class="font-semibold">{title}</strong>
		{/if}
		{@render children()}
	</div>
</div>
