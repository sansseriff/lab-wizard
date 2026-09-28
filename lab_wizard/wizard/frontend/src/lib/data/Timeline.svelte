<script lang="ts">
	/** What a run did, in order: every step execution as a bar on the run's time axis.
	 *
	 * Nesting is the procedure's own (a sweep's iterations sit under it, each
	 * with its #n), so this reconstructs the procedure's flow from the record.
	 * The steps a clicked point came from are highlighted.
	 *
	 * `follow` is for a run still going: steps still running grow with `now`,
	 * and the newest step is kept in view. A long run then shows its latest
	 * steps rather than its first, with every step still open above them, so
	 * the branch the run is in never scrolls away.
	 */
	import { duration, timeline, type Step, type TimelineRow } from './model';

	let {
		steps,
		highlight = new Set<string>(),
		follow = false,
		now = undefined
	}: { steps: Step[]; highlight?: Set<string>; follow?: boolean; now?: number } = $props();

	// A long sweep can execute tens of thousands of steps; the first ones show
	// the shape, and the full list is in the exported steps.csv.
	const LIMIT = 2000;
	const shown = $derived.by(() => {
		if (steps.length <= LIMIT) return steps;
		if (!follow) return steps.slice(0, LIMIT);
		const tail = steps.slice(-LIMIT);
		const open = steps.slice(0, -LIMIT).filter((s) => !s.ended_at);
		return [...open, ...tail];
	});
	const rows = $derived(timeline(shown, now ?? Date.now()));
	let list: HTMLDivElement | undefined = $state();

	// Following a run: keep its newest step in view as steps arrive.
	$effect(() => {
		if (!follow || !list || !steps.length) return;
		void steps.length;
		const all = list.querySelectorAll('[data-row]');
		all[all.length - 1]?.scrollIntoView({ block: 'nearest' });
	});

	// Bring the clicked point's own step into view: the deepest one lit.
	$effect(() => {
		if (!list || !highlight.size || !rows.length) return;
		const lit = list.querySelectorAll('[data-lit="true"]');
		lit[lit.length - 1]?.scrollIntoView({ block: 'center' });
	});

	function tip(row: TimelineRow): string {
		const lines = [row.path, `${row.status ?? 'running'} · ${duration(row.started_at, row.ended_at)}`];
		if (row.error) lines.push(row.error);
		return lines.join('\n');
	}

	const bar: Record<string, string> = {
		success: 'bg-accent/60',
		failed: 'bg-crit',
		aborted: 'bg-warn'
	};
</script>

{#if !steps.length}
	<p class="p-4 text-xs text-muted">This run recorded no steps.</p>
{:else}
	<div class="text-xs" role="table" aria-label="Step timeline" bind:this={list}>
		{#each rows as row, i (i)}
			{@const lit = highlight.has(row.path)}
			<div
				role="row"
				data-row
				data-lit={lit}
				class="grid grid-cols-[minmax(10rem,40%)_1fr] items-center gap-2 border-b border-line/60 px-2 py-[3px] {lit
					? 'bg-accent-wash'
					: ''}"
				title={tip(row)}
			>
				<span role="cell" class="mono truncate {lit ? 'font-semibold text-accent-strong' : 'text-ink-2'}" style="padding-left: {row.depth * 12}px"
					>{row.name}</span
				>
				<span role="cell" class="relative h-3">
					<span
						class="absolute top-0 h-3 rounded-sm {row.status ? (bar[row.status] ?? 'bg-muted') : 'animate-pulse bg-accent'}"
						style="left: {row.left * 100}%; width: {row.width * 100}%"
					></span>
				</span>
			</div>
			{#if row.error}
				<p class="px-2 py-1 text-fine text-crit" style="padding-left: {row.depth * 12 + 8}px">{row.error}</p>
			{/if}
		{/each}
		{#if steps.length > LIMIT}
			<p class="p-2 text-xs text-muted">
				{follow ? 'The latest' : 'The first'}
				{LIMIT} of {steps.length} steps. Export the run for all of them.
			</p>
		{/if}
	</div>
{/if}
