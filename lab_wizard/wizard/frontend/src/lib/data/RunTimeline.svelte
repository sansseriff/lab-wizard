<script lang="ts">
	/** A run's timeline: the whole run at a glance, and where it was at one moment.
	 *
	 * Two arrangements of the same two views. Watching a run (``live``), the
	 * question is what it is doing: the moment is now, so its context comes
	 * first and the chart below shows how far through it is, with what is
	 * expected yet. Looking back at a run, the question is what it did: the
	 * chart comes first, and the context below follows the pointer over it, or
	 * the moment kept by a click there or by a point chosen on the plot.
	 */
	import { activity as build, closeUpSpan, contextAt, expected, kindColor, readableLevels, span, timeByKind, type Expected } from './activity';
	import ActivityChart from './ActivityChart.svelte';
	import RunContext from './RunContext.svelte';
	import type { Loops, Step } from './model';

	let {
		steps,
		loops = {},
		live = false,
		now = Date.now(),
		cursor = null
	}: {
		steps: Step[];
		loops?: Loops;
		live?: boolean;
		now?: number;
		/** A moment to show, such as when a point on the plot was recorded. */
		cursor?: number | null;
	} = $props();

	const run = $derived(build(steps, loops));
	let hover = $state<number | null>(null);
	let kept = $state<number | null>(null);
	// A moment chosen from outside (a plot point) replaces one kept here.
	$effect(() => {
		kept = cursor;
	});

	// The page's clock ticks twice a second and steps arrive in between, so a
	// running run's clock is never behind its latest step: a step just begun is
	// now, not the future.
	const clock = $derived(live ? Math.max(now, run.end) : now);
	const liveContext = $derived(live ? contextAt(run, clock, clock) : null);
	const estimatedEnd = $derived(liveContext?.remaining != null ? clock + liveContext.remaining : null);
	const moment = $derived(hover ?? kept);
	const context = $derived(live && moment === null ? liveContext : moment !== null ? contextAt(run, moment, clock) : null);
	const shares = $derived(timeByKind(run, clock).filter((k) => k.share >= 0.005));

	// Which levels of loop get a row. Each is settled once, as soon as it can
	// be told, and kept, so rows do not come and go; until then a level has one.
	let settled: (boolean | undefined)[] = [];
	let settledFor = NaN;
	const rows = $derived.by(() => {
		if (settledFor !== run.start) {
			settled = [];
			settledFor = run.start;
		}
		return readableLevels(run).map((worth, d) => {
			if (settled[d] !== undefined) return settled[d]!;
			if (worth === null) return true;
			settled[d] = worth;
			return worth;
		});
	});

	// The close-up: travelling with now while a run goes and nothing else is
	// chosen, else centred on the chosen moment.
	const following = $derived(live && moment === null);
	// One width for the whole run, kept once settled, so the close-up never zooms.
	let settledSpan: number | null = null;
	let spanFor = NaN;
	const runSpan = $derived.by(() => {
		if (spanFor !== run.start) {
			settledSpan = null;
			spanFor = run.start;
		}
		if (settledSpan !== null) return settledSpan;
		const { span: width, settled } = closeUpSpan(run);
		if (settled) settledSpan = width;
		return width;
	});
	const closeSpan = $derived(following || moment !== null ? runSpan : null);
	// What is expected next is kept while the run keeps to it: a new step that
	// starts close to where one of its kind was expected leaves it be, so it
	// travels steadily. It is worked out afresh only when the run has drifted
	// from it, or there is less than half a close-up of it left.
	let plan: Expected[] = [];
	const upcoming = $derived.by(() => {
		if (!following || closeSpan === null) return (plan = []);
		const latest = run.execs[run.leaves.at(-1) ?? -1];
		const close = closeSpan * 0.03;
		const onTrack =
			!!latest &&
			plan.length > 0 &&
			(plan[0].t0 > latest.t0 || plan.some((e) => e.name === latest.name && Math.abs(e.t0 - latest.t0) <= close));
		const enough = plan.length > 0 && (plan.at(-1)!.t1 ?? 0) > clock + closeSpan / 2;
		if (!onTrack || !enough) plan = expected(run, closeSpan);
		return plan;
	});
	const failed = $derived(run.failures.length);
</script>

{#snippet chart()}
	<ActivityChart
		activity={run}
		now={clock}
		{live}
		{estimatedEnd}
		cursor={kept}
		onhover={(at) => (hover = at)}
		onpick={(at) => (kept = kept !== null && Math.abs(at - kept) < 1 ? null : at)}
	/>
	<div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-[7.5rem] text-fine text-muted">
		{#each shares as { kind, share } (kind)}
			<span class="inline-flex items-center gap-1">
				<span class="size-2 rounded-sm" style:background={kindColor(kind)}></span>
				<span class="mono text-ink-2">{run.kinds[kind]}</span>
				<span class="tabular-nums">{Math.round(share * 100)}%</span>
			</span>
		{/each}
		{#if failed}
			<span class="text-crit">{failed} step{failed === 1 ? '' : 's'} failed or aborted</span>
		{/if}
	</div>
{/snippet}

{#snippet moment_()}
	{#if context}
		<RunContext {context} {rows} live={following} />
		{#if closeSpan !== null}
			<div>
				<p class="mb-1 pl-[7.5rem] text-fine text-muted">
					Close up, {span(closeSpan)} around {following ? 'now' : 'that moment'}. Point at a step to see what it is.
				</p>
				<ActivityChart
					activity={run}
					now={clock}
					{live}
					closeUp={closeSpan}
					center={following ? null : moment}
					expectedSteps={upcoming}
				/>
			</div>
		{/if}
	{:else}
		<p class="text-xs text-muted">
			<span class="tabular-nums text-ink-2">{span(run.end - run.start)}</span>
			· {run.leaves.length.toLocaleString()} steps
			{failed ? `· ${failed} failed or aborted` : '· all succeeded'}.
			<span class="ml-1">Point at the chart to see where the run was; click to keep a moment.</span>
		</p>
	{/if}
{/snippet}

{#if !steps.length}
	<p class="p-4 text-xs text-muted">This run recorded no steps.</p>
{:else}
	<div class="flex flex-col gap-3 px-3 py-2.5">
		{#if live}
			{@render moment_()}
			<div>{@render chart()}</div>
		{:else}
			<div>{@render chart()}</div>
			{@render moment_()}
		{/if}
	</div>
{/if}
