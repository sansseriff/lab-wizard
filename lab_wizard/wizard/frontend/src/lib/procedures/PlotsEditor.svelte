<script lang="ts">
	/** A procedure's plots and derived columns (data plan §9–§10).
	 *
	 * The first plot is what the Data page and a live plotter draw for a run of
	 * this procedure; the rest are there to switch to. Derived columns are
	 * computed from the recorded ones when a run is read, never stored, so an
	 * expression fixed here fixes every past run. The backend checks every
	 * name and expression; this panel only edits.
	 */
	import Panel from '$lib/components/Panel.svelte';
	import PlotSettings from '$lib/components/PlotSettings.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { newPlot, uniqueName, validIdentifier } from './model';

	let { editor }: { editor: ProcedureEditor } = $props();

	const plots = $derived(editor.definition.plots ?? []);
	const derivedColumns = $derived(Object.entries(editor.definition.derived ?? {}));
	const records = $derived(editor.check?.records ?? []);
	/** Parameters the steps sweep, sweeps first: the axes a plot is likely to be against. */
	const swept = $derived(
		editor.steps
			.map(({ step }) => step)
			.filter((step) => step.type === 'sweep' && typeof step.parameter === 'string')
			.map((step) => step.parameter as string)
	);
	/** Everything varied between a run's rows: sweeps and repeats. */
	const varied = $derived([
		...swept,
		...editor.steps
			.map(({ step }) => step)
			.filter((step) => step.type === 'repeat')
			.map((step) => (typeof step.parameter === 'string' ? step.parameter : 'repeat'))
	]);
	/** Every name an axis can use: recorded columns, then derived ones. */
	const columns = $derived([...records, ...derivedColumns.map(([name]) => name)]);
	const problems = $derived(
		(editor.check?.problems ?? []).filter((p) => p.path[0] === 'plots' || p.path[0] === 'derived')
	);

	let selected = $state(0);
	const plot = $derived(plots[selected] ?? null);
	let derivedError = $state('');

	function plotProblems(index: number) {
		return problems.filter((p) => p.path[0] === 'plots' && p.path[1] === index);
	}

	function addPlot() {
		const next = newPlot(records, swept, plots.map((p) => p.name));
		editor.definition.plots = [...plots, next];
		selected = plots.length;
	}

	function removePlot(index: number) {
		const remaining = plots.filter((_, i) => i !== index);
		if (remaining.length) editor.definition.plots = remaining;
		else delete editor.definition.plots;
		selected = Math.max(0, Math.min(selected, remaining.length - 1));
	}

	function makeDefault(index: number) {
		const reordered = [...plots];
		const [moved] = reordered.splice(index, 1);
		editor.definition.plots = [moved, ...reordered];
		selected = 0;
	}

	// ---- derived ----
	function addDerived() {
		const name = uniqueName('derived', columns);
		editor.definition.derived = { ...(editor.definition.derived ?? {}), [name]: '' };
	}

	function renameDerived(from: string, event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const to = input.value.trim();
		derivedError = '';
		if (to === from) return;
		if (!validIdentifier(to)) {
			derivedError = 'A derived column needs a name like above_dark: letters, numbers and underscores.';
			input.value = from;
			return;
		}
		if (columns.includes(to)) {
			derivedError = `${to} is already a column.`;
			input.value = from;
			return;
		}
		const next: Record<string, string> = {};
		for (const [name, text] of derivedColumns) next[name === from ? to : name] = text;
		editor.definition.derived = next;
	}

	function removeDerived(name: string) {
		const { [name]: _removed, ...rest } = editor.definition.derived ?? {};
		if (Object.keys(rest).length) editor.definition.derived = rest;
		else delete editor.definition.derived;
	}
</script>

<div class="editor-grid">
	<div class="min-w-0 space-y-3">
		<Panel
			title="Plots"
			description="The first is what a run of this procedure is shown as, live and in the Data page."
			flush
		>
			{#snippet actions()}<button class="lw-btn lw-btn-sm" onclick={addPlot}>Add plot</button>{/snippet}
			<div class="p-2">
				{#each plots as entry, index (index)}
					<button
						class="editor-row"
						id="plot-{index}"
						aria-pressed={selected === index}
						onclick={() => (selected = index)}
					>
						<span>{entry.name || `plot ${index + 1}`}</span><span class="editor-row-summary mono"
							>{entry.y.join(', ') || '—'} against {entry.x || '—'}{index === 0 ? ' · default' : ''}</span
						>
						{#if plotProblems(index).length}<span class="text-xs text-crit">Needs attention</span>{/if}
					</button>
				{:else}<p class="editor-empty">
						No plots yet. Without one, a run is shown as its first recorded column against its
						innermost sweep.
					</p>{/each}
			</div>
		</Panel>

		<Panel
			title="Derived columns"
			description="Computed from recorded columns when a run is read, never stored."
			flush
		>
			{#snippet actions()}<button class="lw-btn lw-btn-sm" onclick={addDerived}>Add column</button
				>{/snippet}
			<div class="space-y-2 p-3" id="derived-columns">
				{#each derivedColumns as [name, text] (name)}
					<div class="flex flex-wrap items-center gap-2">
						<input
							class="lw-input mono w-40"
							value={name}
							onchange={(e) => renameDerived(name, e)}
							aria-label="Derived column name"
						/>
						<span class="text-muted">=</span>
						<input
							class="lw-input mono min-w-60 flex-1"
							value={text}
							onchange={(e) => {
								if (editor.definition.derived) editor.definition.derived[name] = e.currentTarget.value;
							}}
							aria-label="Expression for {name}"
							placeholder={'count_rate - mean(count_rate, phase == "background")'}
						/>
						<button class="lw-btn lw-btn-sm" onclick={() => removeDerived(name)}>Remove</button>
					</div>
					{#each problems.filter((p) => p.path[0] === 'derived' && p.path[1] === name) as problem}
						<p class="text-xs text-crit">{problem.message}</p>
					{/each}
				{:else}<p class="text-xs text-muted">None.</p>{/each}
				{#if derivedError}<p class="text-xs text-crit" role="alert">{derivedError}</p>{/if}
				<p class="text-xs text-muted">
					Use column names, numbers, <span class="mono">+ - * / **</span>,
					<span class="mono">abs sqrt exp log log10</span>, a run's own params as
					<span class="mono">param("readout.gate_time_s")</span>, and per-run
					<span class="mono">mean min max sum count first last</span>, each with an optional
					condition: <span class="mono">mean(counts, phase == "background")</span>.
				</p>
			</div>
		</Panel>
	</div>

	<aside class="editor-detail" aria-label="Selected plot">
		{#if plot}
			<p class="mono mb-1 text-xs text-muted">{selected === 0 ? 'default plot' : `plot ${selected + 1}`}</p>
			<h3>Plot settings</h3>
			<PlotSettings {plot} {columns} swept={varied} />
			{#each plotProblems(selected) as problem}<p class="mt-2 text-xs text-crit">{problem.message}</p>{/each}
			<div class="editor-actions">
				{#if selected > 0}<button class="lw-btn lw-btn-sm" onclick={() => makeDefault(selected)}
						>Make default</button
					>{/if}
				<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={() => removePlot(selected)}
					>Remove plot</button
				>
			</div>
		{:else}<p class="text-body text-muted">Select a plot or add one.</p>{/if}
	</aside>
</div>
