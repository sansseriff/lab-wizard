<script lang="ts">
	/** Every run in the lab, filtered by what it was, plotted by what it recorded.
	 *
	 * Left, the filters (built from the runs themselves); middle, the runs that
	 * match; right, the chosen run drawn with its procedure's own plots, its
	 * timeline and its details. Choose several runs and they overlay. Anything
	 * this page draws can be taken to a notebook, where the analysis this page
	 * deliberately does not do (fits, arithmetic between runs) belongs.
	 * See plans/semantic_data_plan.md Phase 7.
	 */
	import '$lib/procedures/composer.css';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import BokehPlot from '$lib/components/BokehPlot.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import PlotSettings from '$lib/components/PlotSettings.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import { queryChoice } from '$lib/url';
	import Select from '$lib/components/Select.svelte';
	import DevicesPanel from '$lib/data/DevicesPanel.svelte';
	import FacetSidebar from '$lib/data/FacetSidebar.svelte';
	import RunDetails from '$lib/data/RunDetails.svelte';
	import RunList from '$lib/data/RunList.svelte';
	import Timeline from '$lib/data/Timeline.svelte';
	import { dataApi } from '$lib/data/api';
	import {
		axisLabel,
		describePlot,
		flatten,
		sameSpec,
		show,
		specForRuns,
		stepsOfPoint,
		type Facet,
		type Filters,
		type LineShape,
		type PlotSpec,
		type Point,
		type RunDetail,
		type RunRow,
		type Series,
		type Step
	} from '$lib/data/model';

	const PAGE = 100;
	const POLL_MS = 2500;

	function fromUrl<T>(name: string, parse: (text: string) => T, fallback: T): T {
		const text = page.url.searchParams.get(name);
		try {
			return text ? parse(text) : fallback;
		} catch {
			return fallback;
		}
	}

	function message(e: unknown): string {
		return (e instanceof Error ? e.message : String(e)).replace(/^Failed to fetch: HTTP \d+: /, '');
	}

	// ---- what is chosen: in the URL, so a view can be reloaded or shared ----
	let filters = $state<Filters>(fromUrl('filters', JSON.parse, {}));
	let selected = $state<number[]>(
		fromUrl('runs', (t) => t.split(',').map(Number).filter(Number.isInteger), [])
	);
	const focus = $derived(selected[0] ?? null);

	// ---- the sidebar and the run list ----
	let facets = $state<Facet[]>([]);
	let matching = $state(0);
	let runs = $state<RunRow[]>([]);
	let total = $state(0);
	let pages = 1;
	let loadingRuns = $state(true);
	let listError = $state('');

	// ---- the chosen run ----
	let detail = $state<RunDetail | null>(null);
	let plotIndex = $state(0);
	let spec = $state<PlotSpec | null>(null);
	let drawn = $state<{ series: Series[]; units: Record<string, string | null>; shape: LineShape } | null>(null);
	let plotError = $state('');
	let tab = $state(queryChoice('tab', ['plot', 'timeline', 'details'] as const, 'plot'));
	let editing = $state(false);
	let steps = $state<Step[]>([]);
	let stepsRun = $state<number | null>(null);
	let point = $state<(Point & { run: number }) | null>(null);

	// ---- dialogs ----
	let notebookSource = $state<string | null>(null);
	let copied = $state(false);
	let saving = $state<{ name: string; error: string } | null>(null);
	let devicesOpen = $state(false);

	const plotSource = $derived(detail?.plots[plotIndex] ?? null);
	const edited = $derived(!!spec && !!plotSource && !sameSpec(spec, specForRuns(plotSource, selected)));
	const columns = $derived(detail ? [...Object.keys(detail.columns), ...Object.keys(detail.derived)] : []);
	const labelKeys = $derived([
		'device',
		'operator',
		'date',
		'procedure',
		...facets.map((f) => f.key).filter((k) => k.startsWith('run.') || k.startsWith('device.'))
	]);
	const highlight = $derived(point && point.run === stepsRun ? stepsOfPoint(point) : new Set<string>());
	const running = $derived(runs.find((r) => r.id === focus)?.status === 'running');

	function syncUrl() {
		const url = new URL(page.url);
		if (Object.keys(filters).length) url.searchParams.set('filters', JSON.stringify(filters));
		else url.searchParams.delete('filters');
		if (selected.length) url.searchParams.set('runs', selected.join(','));
		else url.searchParams.delete('runs');
		if (tab !== 'plot') url.searchParams.set('tab', tab);
		else url.searchParams.delete('tab');
		try {
			replaceState(url, page.state);
		} catch {
			// before the router is ready; the next change writes it
		}
	}

	async function refreshList() {
		const asked = JSON.stringify(filters);
		const current = $state.snapshot(filters);
		loadingRuns = true;
		try {
			const [f, r] = await Promise.all([dataApi.facets(current), dataApi.runs(current, 1, PAGE * pages)]);
			if (asked !== JSON.stringify(filters)) return; // the filters moved on meanwhile
			facets = f.facets;
			matching = f.runs;
			runs = r.runs;
			total = r.total;
			listError = '';
			if (!selected.length && runs.length) selected = [runs[0].id];
		} catch (e) {
			listError = message(e);
		} finally {
			loadingRuns = false;
		}
	}

	async function more() {
		pages += 1;
		await refreshList();
	}

	$effect(() => {
		JSON.stringify(filters);
		untrack(() => {
			pages = 1;
			refreshList();
			syncUrl();
		});
	});

	// ---- selection: load the focused run, and keep the plot's runs in step ----
	$effect(() => {
		const ids = [...selected];
		untrack(() => choose(ids));
	});

	async function choose(ids: number[]) {
		syncUrl();
		if (!ids.length) {
			detail = spec = drawn = point = null;
			return;
		}
		if (detail?.run.id === ids[0]) {
			if (spec) spec = specForRuns(spec, ids);
			return;
		}
		try {
			const next = await dataApi.run(ids[0]);
			if (selected[0] !== ids[0]) return;
			detail = next;
			plotIndex = 0;
			point = null;
			spec = next.plots.length ? specForRuns(next.plots[0], ids) : null;
			if (!spec) drawn = null;
			if (tab === 'timeline') loadSteps(ids[0]);
		} catch (e) {
			plotError = message(e);
		}
	}

	function showPlot(index: number) {
		if (!detail) return;
		plotIndex = index;
		spec = specForRuns(detail.plots[index], selected);
	}

	// ---- drawing: every change to the spec, a moment after the last one ----
	$effect(() => {
		if (!spec) return;
		const asked = $state.snapshot(spec);
		const timer = setTimeout(() => draw(asked), 150);
		return () => clearTimeout(timer);
	});

	async function draw(asked: PlotSpec) {
		try {
			const result = await dataApi.plot(asked);
			if (!sameSpec(asked, spec)) return;
			drawn = result;
			plotError = '';
		} catch (e) {
			plotError = message(e);
		}
	}

	async function loadSteps(runId: number) {
		stepsRun = runId;
		const result = await dataApi.steps(runId);
		if (stepsRun === runId) steps = result.steps;
	}

	function showTab(next: typeof tab) {
		tab = next;
		syncUrl();
		const wanted = point?.run ?? focus;
		if (next === 'timeline' && wanted !== null && stepsRun !== wanted) loadSteps(wanted);
	}

	async function onpoint(runId: number, seq: number) {
		try {
			point = { ...(await dataApi.point(runId, seq)), run: runId };
			if (tab === 'timeline' && stepsRun !== runId) loadSteps(runId);
		} catch (e) {
			plotError = message(e);
		}
	}

	// ---- a run still being recorded is followed as it grows ----
	onMount(() => {
		const timer = setInterval(async () => {
			if (document.hidden) return;
			if (runs.some((r) => r.status === 'running')) await refreshList();
			if (!running || focus === null || !detail) return;
			try {
				const next = await dataApi.run(focus);
				if (detail?.run.id !== next.run.id) return;
				detail.run = next.run;
				detail.columns = next.columns;
				if (spec) draw($state.snapshot(spec));
				if (tab === 'timeline' && stepsRun === focus) loadSteps(focus);
			} catch {
				// the next tick tries again
			}
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	// ---- taking the plot elsewhere ----
	async function openNotebook() {
		if (!spec) return;
		try {
			notebookSource = (await dataApi.notebook($state.snapshot(spec))).source;
			copied = false;
		} catch (e) {
			plotError = message(e);
		}
	}

	async function copyNotebook() {
		if (notebookSource === null) return;
		await navigator.clipboard.writeText(notebookSource);
		copied = true;
	}

	async function savePlot() {
		if (!saving || !spec || !detail) return;
		const name = saving.name.trim();
		if (!name) {
			saving.error = 'Give the plot a name.';
			return;
		}
		try {
			await dataApi.savePlot(detail.run.procedure, { ...$state.snapshot(spec), name });
			const next = await dataApi.run(detail.run.id);
			detail = next;
			const index = next.plots.findIndex((p) => p.name === name);
			showPlot(Math.max(index, 0));
			saving = null;
		} catch (e) {
			if (saving) saving.error = message(e);
		}
	}

	function pointValues(values: Record<string, unknown>): [string, string][] {
		return flatten(values).map(([key, value]) => [
			key,
			Array.isArray(value) ? `[${value.length} values]` : show(value)
		]);
	}
</script>

<div class="data-viewer flex h-[calc(100dvh-46px-1rem)] min-h-[520px] flex-col gap-3">
	<div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
		<h1 class="text-headline font-semibold tracking-tight">Data</h1>
		<p class="text-body text-muted">
			Every run recorded in this workspace. Choose one to plot it; Cmd/Ctrl-click to overlay more.
		</p>
		<div class="ml-auto flex gap-2">
			<button class="lw-btn lw-btn-sm" onclick={() => (devicesOpen = true)}>Devices</button>
		</div>
	</div>

	{#if listError}
		<p class="text-xs text-crit" role="alert">{listError}</p>
	{/if}

	<div
		class="grid min-h-0 flex-1 grid-cols-[15rem_19rem_minmax(0,1fr)] overflow-hidden rounded border border-line bg-surface"
	>
		<aside class="min-h-0 border-r border-line" aria-label="Filters">
			<FacetSidebar {facets} {filters} runs={matching} onchange={(next) => (filters = next)} />
		</aside>

		<section class="min-h-0 border-r border-line" aria-label="Runs">
			<RunList
				{runs}
				{total}
				{selected}
				loading={loadingRuns}
				onselect={(ids) => (selected = ids)}
				onmore={more}
			/>
		</section>

		<section class="flex min-h-0 min-w-0 flex-col" aria-label="The chosen run">
			{#if !detail}
				<div class="grid flex-1 place-items-center p-8 text-center">
					<div class="max-w-[46ch] text-body text-muted">
						{#if !loadingRuns && !total && !Object.keys(filters).length}
							<p class="font-medium text-ink">No runs recorded yet.</p>
							<p class="mt-1">
								Every run started from a project is recorded in this workspace's
								<code>data/lab.db</code> and shows up here, including while it runs.
							</p>
						{:else}
							<p>Choose a run to see it.</p>
						{/if}
					</div>
				</div>
			{:else}
				<Tabs
					value={tab}
					onValueChange={(v) => showTab(v as typeof tab)}
					tabs={[
						{ value: 'plot', label: 'Plot' },
						{ value: 'timeline', label: 'Timeline' },
						{ value: 'details', label: 'Details' }
					]}
					label="View"
					class="flex min-h-0 flex-1 flex-col"
					panelClass="flex min-h-0 flex-1 flex-col"
				>
					{#snippet lead()}
						<div class="px-3 py-1.5">
							<p class="truncate text-body font-semibold">
								{detail!.run.procedure} on {detail!.run.device ?? 'no device'}
								<span class="font-normal text-muted">#{detail!.run.id}</span>
								{#if selected.length > 1}<span class="font-normal text-muted"> and {selected.length - 1} more</span>{/if}
							</p>
							{#if running}<p class="text-fine text-accent">Recording: following it as it grows</p>{/if}
						</div>
					{/snippet}
					{#snippet actions()}
						<Tooltip text="This run as a folder of CSV and YAML files, zipped">{#snippet child({ props })}<a {...props} class="lw-btn lw-btn-sm mr-3" href={dataApi.exportUrl(detail!.run.id)} download>Export run</a>{/snippet}</Tooltip>
					{/snippet}
					{#if tab === 'plot'}
						<div class="flex flex-wrap items-center gap-1 border-b border-line px-3 py-1.5">
							{#each detail.plots as plot, i (i)}
								<button
									class="rounded px-2 py-0.5 text-xs {plotIndex === i
										? 'bg-surface-3 font-medium text-ink'
										: 'text-muted hover:bg-surface-2'}"
									onclick={() => showPlot(i)}>{plot.name ?? `${plot.y.join(', ')} against ${plot.x}`}</button
								>
							{/each}
							{#if edited}
								<span class="text-fine text-warn">edited</span>
								<button class="text-fine text-accent hover:underline" onclick={() => showPlot(plotIndex)}>Reset</button>
							{/if}
							<div class="ml-auto flex gap-1">
								<button class="lw-btn lw-btn-sm" aria-pressed={editing} onclick={() => (editing = !editing)} disabled={!spec}
									>{editing ? 'Done editing' : 'Edit plot'}</button
								>
								<button class="lw-btn lw-btn-sm" onclick={openNotebook} disabled={!spec}>Open in notebook</button>
								<Tooltip text="Add this plot to {detail.run.procedure}, for every run of it">{#snippet child({ props })}<button {...props}
									class="lw-btn lw-btn-sm"
									disabled={!spec}
									onclick={() => (saving = { name: spec?.name ?? '', error: '' })}>Save to procedure</button>{/snippet}</Tooltip>
							</div>
						</div>

						{#if spec && drawn && !editing}
							<p class="border-b border-line px-3 py-1 text-xs text-ink-2">{describePlot(spec, drawn.shape)}</p>
						{/if}

						<div class="flex min-h-0 flex-1">
							<div class="flex min-h-0 min-w-0 flex-1 flex-col">
								{#if !spec}
									<p class="p-6 text-body text-muted">
										Nothing to plot: this run recorded no columns to draw against each other.
									</p>
								{:else}
									<div class="relative min-h-0 flex-1 p-2">
										{#if drawn}
											<BokehPlot
												series={drawn.series}
												xLabel={axisLabel([spec.x], drawn.units)}
												yLabel={axisLabel(spec.y, drawn.units)}
												y2Label={axisLabel(spec.y2 ?? [], drawn.units)}
												kind={spec.kind}
												connect={spec.connect}
												logX={!!spec.log_x}
												logY={!!spec.log_y}
												{onpoint}
											/>
										{/if}
										{#if plotError}
											<p class="absolute inset-x-4 top-4 rounded border border-crit/30 bg-crit-wash px-3 py-2 text-xs text-crit" role="alert">
												{plotError}
											</p>
										{:else if drawn && !drawn.series.length}
											<p class="absolute inset-x-4 top-4 text-center text-xs text-muted">
												No point has both {spec.x} and {spec.y.join(', ')}{Object.keys(spec.where ?? {}).length
													? ' where the conditions hold'
													: ''}.
											</p>
										{/if}
									</div>
									{#if point}
										<div class="max-h-48 shrink-0 overflow-y-auto border-t border-line px-3 py-2 text-xs">
											<div class="flex items-center gap-2">
												<p class="font-semibold">Point {point.seq} of run #{point.run}</p>
												<button class="text-accent hover:underline" onclick={() => showTab('timeline')}>Show in timeline</button>
												<button class="ml-auto text-muted hover:text-ink" onclick={() => (point = null)}>Close</button>
											</div>
											<dl class="mt-1 grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-x-4 gap-y-0.5">
												{#each pointValues(point.values) as [key, value] (key)}
													<div class="flex gap-2"><dt class="mono text-muted">{key}</dt><dd class="mono">{value}</dd></div>
												{/each}
											</dl>
											<p class="mono mt-1 break-all text-fine text-muted">{point.steps.join('  ·  ')}</p>
										</div>
									{/if}
								{/if}
							</div>
							{#if editing && spec}
								<aside class="w-80 shrink-0 overflow-y-auto border-l border-line px-3 pb-4" aria-label="Plot settings">
									<PlotSettings plot={spec} {columns} {labelKeys} shape={drawn?.shape} />
									<p class="mt-3 text-fine text-muted">
										An axis can be any expression of the columns, such as
										<span class="mono">count_rate / 1000</span> or
										<span class="mono">counts - mean(counts, phase == "background")</span>.
									</p>
								</aside>
							{/if}
						</div>
					{:else if tab === 'timeline'}
						<div class="min-h-0 flex-1 overflow-y-auto">
							{#if selected.length > 1}
								<div class="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs text-muted">
									Timeline of run
									<Select
										class="w-auto"
										value={String(stepsRun)}
										onValueChange={(v) => loadSteps(Number(v))}
										options={selected.map((id) => ({ value: String(id), label: `#${id}` }))}
									/>
								</div>
							{/if}
							<Timeline {steps} {highlight} />
						</div>
					{:else}
						<div class="min-h-0 flex-1 overflow-y-auto">
							<RunDetails {detail} />
						</div>
					{/if}
				</Tabs>
			{/if}
		</section>
	</div>
</div>

{#if notebookSource !== null}
	<Modal
		title="Open in notebook"
		subtitle="Paste into a notebook cell. It reads the same runs from the lab database and draws the same plot."
		onclose={() => (notebookSource = null)}
		width="max-w-3xl"
	>
		<pre class="mono overflow-x-auto rounded bg-surface-2 p-3 text-xs leading-relaxed">{notebookSource}</pre>
		{#snippet footer()}
			<button class="lw-btn lw-btn-primary" onclick={copyNotebook}>{copied ? 'Copied' : 'Copy'}</button>
		{/snippet}
	</Modal>
{/if}

{#if saving && detail}
	<Modal
		title="Save plot to {detail.run.procedure}"
		subtitle="Every run of {detail.run.procedure}, past ones included, will offer it."
		onclose={() => (saving = null)}
	>
		<label class="block text-xs text-ink-2"
			>Name
			<input class="lw-input mt-1 w-full" bind:value={saving.name} placeholder="MCR, log scale" />
		</label>
		{#if detail.plots.some((p) => p.name === saving?.name.trim())}
			<p class="mt-2 text-xs text-warn">The procedure already has a plot named this; saving replaces it.</p>
		{/if}
		{#if detail.definition_source === 'recorded'}
			<p class="mt-2 text-xs text-warn">This run's procedure no longer exists, so there is nowhere to save it.</p>
		{/if}
		<p class="mt-2 text-xs text-muted">
			Saving to a built-in procedure makes this workspace's own copy of it, as any edit does.
		</p>
		{#if saving.error}<p class="mt-2 text-xs text-crit" role="alert">{saving.error}</p>{/if}
		{#snippet footer()}
			<button class="lw-btn" onclick={() => (saving = null)}>Cancel</button>
			<button class="lw-btn lw-btn-primary" onclick={savePlot} disabled={detail?.definition_source === 'recorded'}
				>Save</button
			>
		{/snippet}
	</Modal>
{/if}

{#if devicesOpen}
	<DevicesPanel onclose={() => (devicesOpen = false)} onchanged={refreshList} />
{/if}
