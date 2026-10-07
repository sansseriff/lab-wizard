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
	import UnitInput from '$lib/setups/UnitInput.svelte';
	import PlotSettings from '$lib/components/PlotSettings.svelte';
	import ExpressionInput from '$lib/expressions/ExpressionInput.svelte';
	import { expressionCandidates } from '$lib/expressions/refs';
	import type { ProcedureEditor } from './editor.svelte';
	import { newPlot, paramLeaves, uniqueName, validIdentifier } from './model';

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
	/** Everything varied between a run's rows: sweeps, repeats and labels (``varied_parameters``). */
	const varied = $derived([
		...swept,
		...editor.steps
			.map(({ step }) => step)
			.filter((step) => step.type === 'repeat' || step.type === 'with_parameter')
			.map((step) => (typeof step.parameter === 'string' ? step.parameter : 'repeat'))
	]);
	/** Every name an axis can use: recorded columns, then derived ones. */
	const columns = $derived([...records, ...derivedColumns.map(([name]) => name)]);
	const problems = $derived(
		(editor.check?.problems ?? []).filter((p) => p.path[0] === 'plots' || p.path[0] === 'derived')
	);

	let selected = $state(0);
	const plot = $derived(plots[selected] ?? null);
	// What an expression can refer to: the setup needs, the columns, the numeric params.
	const candidates = $derived(
		expressionCandidates({
			columns,
			params: paramLeaves(editor.definition.params)
				.filter(({ decl }) => decl.type === 'float' || decl.type === 'int')
				.map(({ name, decl }) => ({ name, unit: decl.unit })),
			needs: editor.definition.needs ?? {}
		})
	);
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

	// ---- needs: what the derived columns read from the setup ----
	const needs = $derived(Object.entries(editor.definition.needs ?? {}));
	let needError = $state('');

	function addNeed(name = uniqueName('need', Object.keys(editor.definition.needs ?? {})), unit: string | null = null) {
		editor.definition.needs = { ...(editor.definition.needs ?? {}), [name]: { unit, description: '' } };
	}

	function renameNeed(from: string, event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const to = input.value.trim();
		needError = '';
		if (to === from) return;
		if (!validIdentifier(to) || to in (editor.definition.needs ?? {})) {
			needError = validIdentifier(to) ? `${to} is already a need.` : 'A need is a name like bias_resistance.';
			input.value = from;
			return;
		}
		const next: NonNullable<typeof editor.definition.needs> = {};
		for (const [name, decl] of needs) next[name === from ? to : name] = decl;
		editor.definition.needs = next;
		// The derived columns that read it read it by its new name.
		for (const [column, text] of derivedColumns)
			editor.definition.derived![column] = text.replaceAll(`setup("${from}")`, `setup("${to}")`).replaceAll(`setup('${from}')`, `setup('${to}')`);
	}

	function removeNeed(name: string) {
		const { [name]: _removed, ...rest } = editor.definition.needs ?? {};
		if (Object.keys(rest).length) editor.definition.needs = rest;
		else delete editor.definition.needs;
	}

	/** A derived column reading setup("x") with no need x: offer to declare it. */
	function undeclaredNeed(message: string): string | null {
		return /setup\('([^']+)'\), which is not declared under needs/.exec(message)?.[1] ?? null;
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
						<ExpressionInput
							class="min-w-60 flex-1"
							value={text}
							{candidates}
							ondeclare={(need) => addNeed(need)}
							onchange={(next) => {
								if (editor.definition.derived) editor.definition.derived[name] = next;
							}}
							aria-label="Expression for {name}"
							placeholder="start typing a column, a param or a setup need"
						/>
						<button class="lw-btn lw-btn-sm" onclick={() => removeDerived(name)}>Remove</button>
					</div>
					{#each problems.filter((p) => p.path[0] === 'derived' && p.path[1] === name) as problem}
						{@const missing = undeclaredNeed(problem.message)}
						<p class="text-xs text-crit">
							{problem.message}
							{#if missing}<button class="ml-1 text-accent underline" onclick={() => addNeed(missing)}>Declare {missing}</button>{/if}
						</p>
					{/each}
				{:else}<p class="text-xs text-muted">None.</p>{/each}
				{#if derivedError}<p class="text-xs text-crit" role="alert">{derivedError}</p>{/if}
				<p class="text-xs text-muted">
					Start typing and pick from the list: a column, one of the run's params, a setup need (a
					new one can be made from the list), or a function. Combine them with numbers and
					<span class="mono">+ - * / **</span>. The per-run functions
					<span class="mono">mean min max sum count first last</span> take an optional condition:
					<span class="mono">mean(counts, phase == "background")</span>.
				</p>
			</div>
		</Panel>

		<Panel
			title="Setup needs"
			description="Facts about the bench the derived columns read, like the bias resistor a current is inferred through. Each measurement binds them to its setup's fields."
			flush
		>
			{#snippet actions()}<button class="lw-btn lw-btn-sm" onclick={() => addNeed()}>Add need</button>{/snippet}
			<div class="space-y-2 p-3" id="setup-needs">
				{#each needs as [name, decl] (name)}
					<div class="flex flex-wrap items-center gap-2">
						<input
							class="lw-input mono w-40"
							value={name}
							onchange={(e) => renameNeed(name, e)}
							aria-label="Need name"
						/>
						<UnitInput
							class="w-24"
							value={decl.unit ?? ''}
							onchange={(unit) => (decl.unit = unit.trim() || null)}
							aria-label="Unit of {name}"
							placeholder="unit"
						/>
						<input
							class="lw-input min-w-48 flex-1"
							value={decl.description ?? ''}
							onchange={(e) => (decl.description = e.currentTarget.value)}
							aria-label="Description of {name}"
							placeholder="what it is"
						/>
						<button class="lw-btn lw-btn-sm" onclick={() => removeNeed(name)}>Remove</button>
					</div>
				{:else}<p class="text-xs text-muted">None: this procedure reads nothing from its setup.</p>{/each}
				{#if needError}<p class="text-xs text-crit" role="alert">{needError}</p>{/if}
				<p class="text-xs text-muted">
					Pick one while typing a derived column or a plot axis; typing a name that is not one offers to
					make it. It is read in the unit given here: a field in kΩ reads as ohms. A need has no default,
					so a run does not start until its setup has it.
				</p>
			</div>
		</Panel>
	</div>

	<aside class="editor-detail" aria-label="Selected plot">
		{#if plot}
			<p class="mono mb-1 text-xs text-muted">{selected === 0 ? 'default plot' : `plot ${selected + 1}`}</p>
			<h3>Plot settings</h3>
			<PlotSettings {plot} {columns} {candidates} ondeclare={(need) => addNeed(need)} swept={varied} />
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
