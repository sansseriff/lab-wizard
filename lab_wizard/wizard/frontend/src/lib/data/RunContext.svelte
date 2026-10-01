<script lang="ts">
	/** Where a run was at one moment: its path in words and its loops' progress.
	 *
	 * Built by ``contextAt`` in activity.ts. Each level of loop worth a row
	 * (``rows``, from ``readableLevels``) has one at every moment, greyed while
	 * the run is between its rounds, so rows never come and go as the run moves
	 * in and out of a loop. While a run goes, the line leaves out what changes
	 * too fast to read: loops without a row, and steps quicker than half a
	 * second; the close-up below shows those going by.
	 */
	import { READABLE_MS, span, type Context } from './activity';

	let { context, rows = [], live = false }: { context: Context; rows?: boolean[]; live?: boolean } = $props();

	const fast = $derived(live && context.step.mean !== null && context.step.mean < READABLE_MS);
	const crumbs = $derived(
		live
			? context.crumbs.filter((c, i) => (c.depth === undefined ? !(fast && i === context.crumbs.length - 1) : rows[c.depth] !== false))
			: context.crumbs
	);
	const whole = $derived(crumbs.map((c) => c.label).join(' › '));
	const levels = $derived(context.levels.map((level, depth) => ({ level, depth })).filter(({ depth }) => rows[depth] !== false));
</script>

<div class="flex flex-col gap-2">
	<!-- One line whatever the width: wrapping onto a second would move everything below it. -->
	<p class="truncate text-xs" title={whole}>
		<span class="mono font-medium text-ink">
			{#each crumbs as crumb, i (i)}{#if i}<span class="mx-1 text-muted">›</span>{/if}<span>{crumb.label}</span>{/each}
		</span>
		{#if context.inStep !== null && !fast}
			<span class="ml-1.5 tabular-nums text-muted">· {span(context.inStep)} {live ? 'in this step' : 'into this step'}</span>
		{/if}
		{#if context.remaining !== null}
			<span class="ml-1.5 tabular-nums text-muted">· about {span(context.remaining)} left</span>
		{/if}
	</p>

	{#if levels.length}
		<div class="grid grid-cols-[7rem_minmax(0,1fr)_12rem] items-center gap-x-2 gap-y-1">
			{#each levels as { level, depth } (depth)}
				<span
					class="truncate text-right text-fine {level.active ? 'text-muted' : 'text-muted/60'}"
					title={level.active ? level.label : `${level.label}: between rounds`}>{level.label}</span
				>
				<div class="h-1.5 overflow-hidden rounded-full bg-surface-3">
					{#if level.total && level.index >= 0}
						<div
							class="h-full rounded-full {level.active ? 'bg-accent' : 'bg-line-2'}"
							style:width="{((level.index + 1) / level.total) * 100}%"
						></div>
					{/if}
				</div>
				<span class="text-fine tabular-nums {level.active ? 'text-ink-2' : 'text-muted/60'}">
					{#if level.index < 0}
						not begun
					{:else}
						{level.index + 1}{level.total !== null ? ` of ${level.total}` : ''}
					{/if}
					{#if level.remaining !== null}<span class="text-muted"> · {span(level.remaining)} left</span>{/if}
				</span>
			{/each}
		</div>
	{/if}
</div>
