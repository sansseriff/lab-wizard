<script lang="ts">
	/** The settings of one plot: what goes on each axis, which rows, one line per what.
	 *
	 * Shared by the composer (a procedure's plots) and the Data page (the plot
	 * being looked at), so a plot is edited the same way wherever it is. It
	 * edits ``plot`` in place; the backend checks every name and expression.
	 */
	import { describePlot, splitWithin, type LineShape } from '$lib/data/model';
	import { PER_RUN, whereValue, type AxisRange, type PlotDecl } from '$lib/procedures/model';
	import Combobox from '$lib/components/Combobox.svelte';
	import Select from '$lib/components/Select.svelte';
	import ExpressionInput from '$lib/expressions/ExpressionInput.svelte';
	import { expressionCandidates, splitTopLevel, type Candidate } from '$lib/expressions/refs';

	let {
		plot,
		columns,
		candidates = undefined,
		ondeclare = undefined,
		labelKeys = ['device', 'operator', 'date', 'procedure'],
		shape = null,
		swept = []
	}: {
		plot: PlotDecl;
		/** Every name an axis can use: recorded columns, then derived ones. */
		columns: string[];
		/** What an axis can refer to, offered while typing; the columns if not given. */
		candidates?: Candidate[];
		/** Declare a setup need, from an axis being typed (the composer offers it). */
		ondeclare?: (name: string) => void;
		/** Filters a run's legend text can come from. */
		labelKeys?: string[];
		/** How the drawn rows fell into lines, where something has been drawn. */
		shape?: LineShape | null;
		/** The parameters the procedure varies between rows (the composer knows them). */
		swept?: string[];
	} = $props();

	// Said where the plot is chosen, before anything is measured.
	const within = $derived(splitWithin(plot, swept, columns));

	const offered = $derived(candidates ?? expressionCandidates({ columns }));

	function update(change: Partial<PlotDecl>) {
		Object.assign(plot, change);
	}

	function setList(field: 'y' | 'y2', text: string) {
		// Split where a comma separates two expressions, not inside mean(x, phase == "a").
		const names = splitTopLevel(text);
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

<label class="editor-field"
	><span>Name</span><input
		class="lw-input"
		value={plot.name ?? ''}
		onchange={(e) => update({ name: e.currentTarget.value || null })}
	/></label
>
<div class="editor-field">
	<span>x</span><ExpressionInput
		value={plot.x}
		candidates={offered}
		{ondeclare}
		onchange={(text) => update({ x: text })}
		aria-label="x"
	/>
</div>
<div class="editor-field">
	<span>y <span class="text-muted">(several: separate with commas)</span></span><ExpressionInput
		value={plot.y.join(', ')}
		candidates={offered}
		{ondeclare}
		onchange={(text) => setList('y', text)}
		aria-label="y"
	/>
</div>
<div class="editor-field">
	<span>Second y axis</span><ExpressionInput
		value={(plot.y2 ?? []).join(', ')}
		candidates={offered}
		{ondeclare}
		onchange={(text) => setList('y2', text)}
		placeholder="none"
		aria-label="Second y axis"
	/>
</div>
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
	<p class="mt-1.5 text-xs text-ink-2">{describePlot(plot, shape, within)}</p>
</div>
{#if plot.series === undefined || plot.series === 'run'}
	<div class="editor-field">
		<span>Name each run's line by</span><Combobox
			mono
			value={plot.label ?? null}
			options={[...new Set([...labelKeys, ...(plot.label ? [plot.label] : [])])].map((key) => ({ value: key, label: key }))}
			onValueChange={(key) => update({ label: key })}
			noneLabel="its run number"
			aria-label="Name each run's line by"
		/>
	</div>
{/if}
<div class="editor-field">
	<span>Only rows where</span>
	{#each whereRows as [column, value] (column)}
		<div class="mt-1 flex flex-wrap items-center gap-1">
			<ExpressionInput
				class="w-40"
				value={column}
				candidates={offered}
				onchange={(text) => renameWhere(column, text)}
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
