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
	import type { ProcedureEditor } from './editor.svelte';
	import { PER_RUN, newPlot, uniqueName, validIdentifier, whereValue, type PlotDecl } from './model';

	let { editor }: { editor: ProcedureEditor } = $props();

	const plots = $derived(editor.definition.plots ?? []);
	const derivedColumns = $derived(Object.entries(editor.definition.derived ?? {}));
	const records = $derived(editor.check?.records ?? []);
	const swept = $derived(
		editor.steps
			.map(({ step }) => step)
			.filter((step) => step.type === 'sweep' && typeof step.parameter === 'string')
			.map((step) => step.parameter as string)
	);
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

	function update(change: Partial<PlotDecl>) {
		if (!plot) return;
		Object.assign(plot, change);
	}

	function setList(field: 'y' | 'y2', text: string) {
		const names = text
			.split(',')
			.map((n) => n.trim())
			.filter(Boolean);
		if (field === 'y2' && !names.length) delete plot?.y2;
		else update({ [field]: names });
	}

	// ---- where ----
	const whereRows = $derived(Object.entries(plot?.where ?? {}));

	function whereMode(value: unknown): string {
		return value && typeof value === 'object' && 'per_run' in value
			? `per_run:${(value as { per_run: string }).per_run}`
			: value && typeof value === 'object'
				? 'yaml'
				: 'equals';
	}

	function setWhere(column: string, mode: string, text: string) {
		if (!plot) return;
		const where = { ...(plot.where ?? {}) };
		where[column] = mode.startsWith('per_run:')
			? { per_run: mode.slice('per_run:'.length) as (typeof PER_RUN)[number] }
			: whereValue(text);
		plot.where = where;
	}

	function renameWhere(from: string, to: string) {
		if (!plot?.where || from === to || !to) return;
		const where: typeof plot.where = {};
		for (const [key, value] of Object.entries(plot.where)) where[key === from ? to : key] = value;
		plot.where = where;
	}

	function addWhere() {
		if (!plot) return;
		const column = columns.find((c) => !(c in (plot.where ?? {}))) ?? 'column';
		plot.where = { ...(plot.where ?? {}), [column]: '' };
	}

	function removeWhere(column: string) {
		if (!plot?.where) return;
		const { [column]: _removed, ...rest } = plot.where;
		if (Object.keys(rest).length) plot.where = rest;
		else delete plot.where;
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

<datalist id="plot-columns">
	{#each columns as column (column)}<option value={column}></option>{/each}
</datalist>

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
			<label class="editor-field"
				><span>Name</span><input
					class="lw-input"
					value={plot.name ?? ''}
					onchange={(e) => update({ name: e.currentTarget.value || null })}
				/></label
			>
			<label class="editor-field"
				><span>x</span><input
					class="lw-input mono"
					list="plot-columns"
					value={plot.x}
					onchange={(e) => update({ x: e.currentTarget.value.trim() })}
				/></label
			>
			<label class="editor-field"
				><span>y (comma separated)</span><input
					class="lw-input mono"
					list="plot-columns"
					value={plot.y.join(', ')}
					onchange={(e) => setList('y', e.currentTarget.value)}
				/></label
			>
			<label class="editor-field"
				><span>Second y axis</span><input
					class="lw-input mono"
					list="plot-columns"
					value={(plot.y2 ?? []).join(', ')}
					onchange={(e) => setList('y2', e.currentTarget.value)}
					placeholder="none"
				/></label
			>
			<label class="editor-field"
				><span>One line per</span><select
					class="lw-select"
					value={plot.series === undefined ? 'run' : (plot.series ?? '')}
					onchange={(e) => update({ series: e.currentTarget.value || null })}
				>
					<option value="run">run</option>
					<option value="">nothing (one line)</option>
					{#each columns as column (column)}<option value={column}>value of {column}</option>{/each}
				</select></label
			>
			<div class="editor-field">
				<span>Only rows where</span>
				{#each whereRows as [column, value] (column)}
					<div class="mt-1 flex flex-wrap items-center gap-1">
						<input
							class="lw-input mono w-32"
							list="plot-columns"
							value={column}
							onchange={(e) => renameWhere(column, e.currentTarget.value.trim())}
							aria-label="Filter column"
						/>
						{#if whereMode(value) === 'yaml'}
							<span class="text-xs text-muted">set in YAML</span>
						{:else}
							<select
								class="lw-select w-40"
								value={whereMode(value)}
								onchange={(e) => setWhere(column, e.currentTarget.value, '')}
								aria-label="Condition for {column}"
							>
								<option value="equals">equals</option>
								{#each PER_RUN as how}<option value="per_run:{how}">is each run's {how}</option>{/each}
							</select>
							{#if whereMode(value) === 'equals'}
								<input
									class="lw-input mono w-28"
									value={String(value)}
									onchange={(e) => setWhere(column, 'equals', e.currentTarget.value)}
									aria-label="Value of {column}"
								/>
							{/if}
						{/if}
						<button class="lw-btn lw-btn-sm" onclick={() => removeWhere(column)}>Remove</button>
					</div>
				{/each}
				<button class="lw-btn lw-btn-sm mt-1" onclick={addWhere}>Add condition</button>
			</div>
			<div class="editor-field flex flex-wrap gap-4">
				<label class="flex items-center gap-1 text-xs"
					><input
						type="checkbox"
						checked={!!plot.log_x}
						onchange={(e) => update({ log_x: e.currentTarget.checked || undefined })}
					/> log x</label
				>
				<label class="flex items-center gap-1 text-xs"
					><input
						type="checkbox"
						checked={!!plot.log_y}
						onchange={(e) => update({ log_y: e.currentTarget.checked || undefined })}
					/> log y</label
				>
				<label class="flex items-center gap-1 text-xs"
					>Join points <select
						class="lw-select w-auto"
						value={plot.connect ?? 'seq'}
						onchange={(e) =>
							update({ connect: e.currentTarget.value === 'seq' ? undefined : (e.currentTarget.value as PlotDecl['connect']) })}
					>
						<option value="seq">in recording order</option>
						<option value="x">sorted by x</option>
						<option value="none">not at all</option>
					</select></label
				>
			</div>
			{#each plotProblems(selected) as problem}<p class="mt-2 text-xs text-crit">{problem.message}</p>{/each}
			<div class="editor-actions">
				{#if selected > 0}<button class="lw-btn lw-btn-sm" onclick={() => makeDefault(selected)}
						>Make default</button
					>{/if}
				<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={() => removePlot(selected)}
					>Remove plot</button
				>
			</div>
		{:else}<p class="text-sm text-muted">Select a plot or add one.</p>{/if}
	</aside>
</div>
