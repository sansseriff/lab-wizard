<script lang="ts">
	/** The settings of one plot: what goes on each axis, which rows, one line per what.
	 *
	 * Shared by the composer (a procedure's plots) and the Data page (the plot
	 * being looked at), so a plot is edited the same way wherever it is. It
	 * edits ``plot`` in place; the backend checks every name and expression.
	 */
	import { describePlot, sweptWithin, sweptWithinWarning, type LineShape } from '$lib/data/model';
	import { PER_RUN, whereValue, type AxisRange, type PlotDecl } from '$lib/procedures/model';
	import Select from '$lib/components/Select.svelte';

	let {
		plot,
		columns,
		labelKeys = ['device', 'operator', 'date', 'procedure'],
		shape = null,
		swept = []
	}: {
		plot: PlotDecl;
		/** Every name an axis can use: recorded columns, then derived ones. */
		columns: string[];
		/** Filters a run's legend text can come from. */
		labelKeys?: string[];
		/** How the drawn rows fell into lines, where something has been drawn. */
		shape?: LineShape | null;
		/** The parameters the procedure's sweeps and repeats bind (the composer knows them). */
		swept?: string[];
	} = $props();

	// Said where the plot is chosen, before anything is measured.
	const warning = $derived(sweptWithinWarning(plot, swept));
	const fixes = $derived(sweptWithin(plot, swept));

	const id = $props.id();

	function update(change: Partial<PlotDecl>) {
		Object.assign(plot, change);
	}

	function setList(field: 'y' | 'y2', text: string) {
		const names = text
			.split(',')
			.map((n) => n.trim())
			.filter(Boolean);
		if (field === 'y2' && !names.length) delete plot.y2;
		else update({ [field]: names });
	}

	/** One end of an axis's range; a blank end fits the data. */
	function setEnd(field: 'x_range' | 'y_range', end: 0 | 1, text: string) {
		const bounds: AxisRange = [...(plot[field] ?? [null, null])];
		const value = text.trim() === '' ? null : Number(text);
		bounds[end] = value !== null && Number.isFinite(value) ? value : null;
		if (bounds[0] === null && bounds[1] === null) delete plot[field];
		else plot[field] = bounds;
	}

	const whereRows = $derived(Object.entries(plot.where ?? {}));

	function whereMode(value: unknown): string {
		return value && typeof value === 'object' && 'per_run' in value
			? `per_run:${(value as { per_run: string }).per_run}`
			: value && typeof value === 'object'
				? 'yaml'
				: 'equals';
	}

	function setWhere(column: string, mode: string, text: string) {
		const where = { ...(plot.where ?? {}) };
		where[column] = mode.startsWith('per_run:')
			? { per_run: mode.slice('per_run:'.length) as (typeof PER_RUN)[number] }
			: whereValue(text);
		plot.where = where;
	}

	function renameWhere(from: string, to: string) {
		if (!plot.where || from === to || !to) return;
		const where: typeof plot.where = {};
		for (const [key, value] of Object.entries(plot.where)) where[key === from ? to : key] = value;
		plot.where = where;
	}

	function addWhere() {
		const column = columns.find((c) => !(c in (plot.where ?? {}))) ?? 'column';
		plot.where = { ...(plot.where ?? {}), [column]: '' };
	}

	function removeWhere(column: string) {
		if (!plot.where) return;
		const { [column]: _removed, ...rest } = plot.where;
		if (Object.keys(rest).length) plot.where = rest;
		else delete plot.where;
	}
</script>

<datalist id="{id}-columns">
	{#each columns as column (column)}<option value={column}></option>{/each}
</datalist>
<datalist id="{id}-labels">
	{#each labelKeys as key (key)}<option value={key}></option>{/each}
</datalist>

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
		list="{id}-columns"
		value={plot.x}
		onchange={(e) => update({ x: e.currentTarget.value.trim() })}
	/></label
>
<label class="editor-field"
	><span>y (comma separated)</span><input
		class="lw-input mono"
		list="{id}-columns"
		value={plot.y.join(', ')}
		onchange={(e) => setList('y', e.currentTarget.value)}
	/></label
>
<label class="editor-field"
	><span>Second y axis</span><input
		class="lw-input mono"
		list="{id}-columns"
		value={(plot.y2 ?? []).join(', ')}
		onchange={(e) => setList('y2', e.currentTarget.value)}
		placeholder="none"
	/></label
>
<div>
	<label class="editor-field"
		><span>One line per <span class="mono text-muted">(series)</span></span><Select
			value={plot.series === undefined ? 'run' : (plot.series ?? '')}
			onValueChange={(v) => update({ series: v || null })}
			options={[
				{ value: 'run', label: 'run' },
				{ value: '', label: 'nothing (one line)' },
				...columns.map((column) => ({ value: column, label: `value of ${column}` }))
			]}
		/></label
	>
	<p class="mt-1.5 text-xs text-ink-2">{describePlot(plot, shape)}</p>
	{#if warning}
		<div class="mt-2 rounded border border-warn/30 bg-warn-wash px-2.5 py-2 text-xs text-warn" role="status">
			<p>{warning}</p>
			<div class="mt-1.5 flex flex-wrap gap-1">
				{#each fixes as column (column)}
					<button class="lw-btn lw-btn-sm" onclick={() => update({ series: column })}>One line per {column}</button>
				{/each}
			</div>
		</div>
	{/if}
</div>
{#if plot.series === undefined || plot.series === 'run'}
	<label class="editor-field"
		><span>Name each run's line by</span><input
			class="lw-input mono"
			list="{id}-labels"
			value={plot.label ?? ''}
			onchange={(e) => update({ label: e.currentTarget.value.trim() || null })}
			placeholder="its run number"
		/></label
	>
{/if}
<div class="editor-field">
	<span>Only rows where</span>
	{#each whereRows as [column, value] (column)}
		<div class="mt-1 flex flex-wrap items-center gap-1">
			<input
				class="lw-input mono w-32"
				list="{id}-columns"
				value={column}
				onchange={(e) => renameWhere(column, e.currentTarget.value.trim())}
				aria-label="Filter column"
			/>
			{#if whereMode(value) === 'yaml'}
				<span class="text-xs text-muted">set in YAML</span>
			{:else}
				<Select
					class="w-40"
					value={whereMode(value)}
					onValueChange={(v) => setWhere(column, v, '')}
					aria-label="Condition for {column}"
					options={[
						{ value: 'equals', label: 'equals' },
						...PER_RUN.map((how) => ({ value: `per_run:${how}`, label: `is each run's ${how}` }))
					]}
				/>
				{#if whereMode(value) === 'equals'}
					<input
						class="lw-input mono w-28"
						value={String(value)}
						onchange={(e) => setWhere(column, 'equals', e.currentTarget.value)}
						aria-label="Value of {column}"
					/>
				{/if}
			{/if}
			<button class="lw-btn" onclick={() => removeWhere(column)}>Remove</button>
		</div>
	{/each}
	<button class="lw-btn lw-btn-sm mt-1" onclick={addWhere}>Add condition</button>
</div>
<label class="editor-field"
	><span>Draw as</span><Select
		value={plot.kind ?? 'line'}
		onValueChange={(v) => update({ kind: v === 'line' ? undefined : (v as PlotDecl['kind']) })}
		options={[
			{ value: 'line', label: 'lines' },
			{ value: 'scatter', label: 'points' },
			{ value: 'histogram', label: 'a histogram per point (array column)' },
			{ value: 'waterfall', label: 'a waterfall (array column against x)' }
		]}
	/></label
>
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
		>Join points <Select
			class="w-auto"
			value={plot.connect ?? 'seq'}
			onValueChange={(v) => update({ connect: v === 'seq' ? undefined : (v as PlotDecl['connect']) })}
			options={[
				{ value: 'seq', label: 'in recording order' },
				{ value: 'x', label: 'sorted by x' },
				{ value: 'none', label: 'not at all' }
			]}
		/></label
	>
</div>
<div class="editor-field">
	<span>Show <span class="text-muted">(blank fits the data)</span></span>
	{#each [['x_range', 'x'], ['y_range', 'y']] as const as [field, axis] (field)}
		<div class="mt-1 flex items-center gap-1 text-xs">
			<span class="mono w-4">{axis}</span>
			{#each [0, 1] as const as end (end)}
				<input
					class="lw-input mono w-28"
					type="number"
					step="any"
					value={plot[field]?.[end] ?? ''}
					onchange={(e) => setEnd(field, end, e.currentTarget.value)}
					placeholder={end ? 'max' : 'min'}
					aria-label="{axis} {end ? 'max' : 'min'}"
				/>
				{#if !end}<span class="text-muted">to</span>{/if}
			{/each}
		</div>
	{/each}
</div>
