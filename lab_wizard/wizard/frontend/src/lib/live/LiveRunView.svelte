<script lang="ts">
	/** A run as it happens: its status, every plot its procedure declares, and its timeline.
	 *
	 * The same view follows a run still going and shows one that has ended, so
	 * the Run page shows the project's last run until a new one starts, and the
	 * standalone live page (a web plotter's) is this view on its own.
	 */
	import { onDestroy } from 'svelte';
	import BokehPlot from '$lib/components/BokehPlot.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import Timeline from '$lib/data/Timeline.svelte';
	import { axisLabel, describePlot, duration } from '$lib/data/model';
	import { LiveRun } from './liveRun.svelte';

	let { runId, plot = '' }: { runId: number; /** The plot to open on, by name. */ plot?: string } = $props();

	let live = $state<LiveRun | null>(null);
	let tab = $state('');

	$effect(() => {
		const next = new LiveRun(runId);
		next.connect();
		live = next;
		return () => next.close();
	});
	onDestroy(() => live?.close());

	const plots = $derived(live?.plots ?? []);
	const tabs = $derived(plots.map((p, i) => ({ value: String(i), label: p.name ?? `Plot ${i + 1}` })));
	// Open on the named plot, else the first; keep whichever was chosen after that.
	$effect(() => {
		if (tab || !plots.length) return;
		const named = plots.findIndex((p) => p.name === plot);
		tab = String(named >= 0 ? named : 0);
	});
	const shown = $derived(plots[Number(tab)] ?? plots[0] ?? null);

	// Where the run is now: every step still open, outermost first — the branch
	// of the procedure it is in, which scrolling the timeline would lose.
	const branch = $derived(
		live?.running
			? live.steps
					.filter((s) => !s.ended_at)
					.sort((a, b) => a.path.split('/').length - b.path.split('/').length)
					.map((s) => s.path.split('/').at(-1) ?? s.path)
			: []
	);

	const tone: Record<string, 'ok' | 'crit' | 'warn' | 'accent'> = {
		success: 'ok',
		failed: 'crit',
		aborted: 'warn',
		interrupted: 'crit',
		running: 'accent'
	};
</script>

{#if !live || (!live.run && !live.error)}
	<p class="p-4 text-body text-muted">Connecting to run {runId}…</p>
{:else if live.error}
	<p class="p-4 text-body text-crit" role="alert">{live.error}</p>
{:else if live.run}
	{@const run = live.run}
	<div class="flex h-full min-h-0 flex-col gap-3">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-body">
			<Pill tone={tone[run.status] ?? 'neutral'} dot={run.status === 'running'}>{run.status}</Pill>
			<span class="font-medium">{run.procedure}</span>
			{#if run.device}<span class="text-muted">on {run.device}</span>{/if}
			<span class="text-muted">run {run.id}</span>
			<span class="ml-auto tabular-nums text-muted">
				{run.points} point{run.points === 1 ? '' : 's'} ·
				{duration(run.started_at, run.ended_at, live.now)}
			</span>
		</div>

		<section class="rounded border border-line bg-surface" aria-label="Plots">
			{#if !plots.length}
				<p class="p-4 text-xs text-muted">
					{live.running ? 'The plots appear with the first points.' : 'This run has nothing to plot.'}
				</p>
			{:else}
				<Tabs value={tab} onValueChange={(v) => (tab = v)} tabs={tabs} label="Plots" size="sm">
					{#if shown}
						<div class="p-2">
							{#if shown.error}
								<p class="p-2 text-xs text-crit">{shown.error}</p>
							{:else}
								<div class="h-[340px]">
									<BokehPlot
										series={shown.series}
										xLabel={axisLabel([shown.spec.x], shown.units)}
										yLabel={axisLabel(shown.spec.y, shown.units)}
										y2Label={axisLabel(shown.spec.y2 ?? [], shown.units)}
										kind={shown.spec.kind}
										connect={shown.spec.connect}
										logX={!!shown.spec.log_x}
										logY={!!shown.spec.log_y}
									/>
								</div>
								<p class="px-1 pt-1 text-fine text-muted">{describePlot(shown.spec, shown.shape)}</p>
							{/if}
						</div>
					{/if}
				</Tabs>
			{/if}
		</section>

		<section class="flex min-h-[180px] flex-1 flex-col rounded border border-line bg-surface" aria-label="Timeline">
			<div class="flex items-baseline gap-3 border-b border-line px-3 py-1.5">
				<h3 class="text-xs font-semibold text-ink-2">Timeline</h3>
				{#if branch.length}
					<p class="mono min-w-0 truncate text-fine text-accent-strong" title={branch.join(' › ')}>
						Now: {branch.join(' › ')}
					</p>
				{/if}
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto">
				<Timeline steps={live.steps} follow={live.running} now={live.now} />
			</div>
		</section>
	</div>
{/if}
