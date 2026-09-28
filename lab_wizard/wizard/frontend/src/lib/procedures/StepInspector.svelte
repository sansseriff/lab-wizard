<script lang="ts">
	/** Edit only the selected step. Field editors remain catalog-driven. */
	import { stepContext, stepTitle, cleanupSummary, humanize } from './presentation';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ArrowUpIcon from 'phosphor-svelte/lib/ArrowUp';
	import ArrowDownIcon from 'phosphor-svelte/lib/ArrowDown';
	import XIcon from 'phosphor-svelte/lib/X';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import StepPicker from './StepPicker.svelte';
	import ValueInput from './ValueInput.svelte';
	import Select from '$lib/components/Select.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { type FieldSpec, type Path, pathKey, roleFits } from './model';

	let {
		editor,
		path,
		onreference
	}: {
		editor: ProcedureEditor;
		path: Path;
		onreference: (kind: 'params' | 'roles', name: string) => void;
	} = $props();
	const context = $derived(stepContext(editor.definition, editor.catalog, path));
	const scope = $derived(context.scope);
	const place = $derived(context.place);
	const index = $derived(context.index);
	const count = $derived(context.count);
	const optional = $derived(context.optional);

	type Picker = {
		title: string;
		containersOnly?: boolean;
		pick: (type: string, role: string | null) => void;
	};

	const catalog = $derived(editor.catalog);
	const step = $derived(editor.step(path));
	const spec = $derived(step ? catalog.steps[step.type] : undefined);
	const key = $derived(pathKey(path));
	const problems = $derived(editor.placedProblems.byStep.get(key) ?? []);
	const fieldEntries = $derived(Object.entries(spec?.fields ?? {}));
	const scalarFields = $derived(
		fieldEntries.filter(([, f]) => f.kind !== 'step' && f.kind !== 'steps')
	);
	const childFields = $derived(
		fieldEntries.filter(([, f]) => f.kind === 'step' || f.kind === 'steps')
	);
	const innerScope = $derived(scope);
	const records = $derived(editor.check?.records ?? []);
	const canUnwrap = $derived.by(() => {
		if (!step || !childFields.length) return false;
		let n = 0;
		for (const [name, f] of childFields)
			n += f.kind === 'steps' ? (step[name]?.length ?? 0) : step[name] ? 1 : 0;
		return n === 1;
	});

	let picker = $state<Picker | null>(null);

	function fieldHasProblem(name: string): boolean {
		return problems.some((p) => p.path.length > path.length && p.path[path.length] === name);
	}

	function problemText(message: string, problemPath: Path): string {
		const field = problemPath.length > path.length ? String(problemPath[path.length]) : null;
		return field && message === 'Field required' ? `${field} is required` : message;
	}

	function roleOptions(field: FieldSpec) {
		return Object.entries(editor.definition.roles).map(([role, decl]) => ({
			role,
			behavior: decl.behavior,
			fits: roleFits(catalog, decl.behavior, field.requires)
		}));
	}

	function onRoleChange(name: string, field: FieldSpec, value: string) {
		if (value === '__new__') {
			const behavior =
				field.requires[0] ??
				Object.keys(catalog.behaviors).find((b) => catalog.behaviors[b].bindable);
			if (!behavior) return;
			editor.setAt([...path, name], { role: editor.addRole(name, behavior) });
			return;
		}
		editor.setAt([...path, name], value ? { role: value } : undefined);
	}

	function mapEntries(name: string): [string, unknown][] {
		return Object.entries(step[name] ?? {});
	}

	function renameMapKey(name: string, from: string, to: string) {
		const map = step[name];
		if (!to || from === to || to in map) return;
		const entries = Object.entries(map);
		for (const k of Object.keys(map)) delete map[k];
		for (const [k, v] of entries) map[k === from ? to : k] = v;
	}

	function addMapEntry(name: string) {
		const map = step[name] ?? (step[name] = {});
		let k = 'setting';
		for (let n = 2; k in map; n++) k = `setting_${n}`;
		map[k] = 0;
	}
</script>

<section class="inspector space-y-4" aria-label="Selected step" id="step-inspector">
	{#if step}
		<div>
			<p class="mono text-xs text-muted">{step.type}</p>
			<h2 class="mt-1 text-title font-semibold">{stepTitle(step)}</h2>
			{#if spec}<p class="mt-1 text-xs leading-relaxed text-muted">
					{spec.summary.replaceAll('``', '')}
				</p>{/if}
		</div>
		{#if context.breadcrumbs.length}
			<nav aria-label="Step ancestors" class="flex flex-wrap items-center gap-1 text-xs text-muted">
				{#each context.breadcrumbs as entry, i}
					{#if i > 0}<span aria-hidden="true">/</span>{/if}
					<button
						class="text-left hover:text-accent hover:underline"
						onclick={() => editor.selectStep(entry.path)}>{stepTitle(entry.step)}</button
					>
				{/each}
			</nav>
		{/if}
		<label class="block"
			><span class="lw-label">Step label (optional)</span>
			<input
				class="lw-input"
				value={step.name ?? ''}
				onchange={(e) => editor.setAt([...path, 'name'], e.currentTarget.value || undefined)}
				placeholder="A name for this step"
				aria-label="Step label"
			/>
		</label>
		<details class="border-y border-line py-2">
			<summary class="cursor-pointer text-xs text-ink-2">Step actions</summary>
			<div class="mt-2 flex flex-wrap gap-1.5">
				{#if place === 'list'}
					<button
						class="lw-btn lw-btn-sm px-1"
						disabled={index === 0}
						onclick={() => editor.moveStep(path, -1)}
						aria-label="Move up"><ArrowUpIcon size={12} /> Move up</button
					>
					<button
						class="lw-btn lw-btn-sm px-1"
						disabled={index === count - 1}
						onclick={() => editor.moveStep(path, 1)}
						aria-label="Move down"><ArrowDownIcon size={12} /> Move down</button
					>
				{/if}
				<Tooltip text="Put this step inside a new one — a sweep, a guard, a retry">{#snippet child({ props })}<button {...props}
					class="lw-btn lw-btn-sm"
					onclick={() =>
						(picker = {
							title: `Wrap ${step.type} in…`,
							containersOnly: true,
							pick: (type) => editor.wrapStep(path, type)
						})}>Wrap</button>{/snippet}</Tooltip>
				{#if canUnwrap}
					<Tooltip text="Replace this step with the one step inside it">{#snippet child({ props })}<button {...props}
						class="lw-btn lw-btn-sm"
						onclick={() => editor.unwrapStep(path)}>Unwrap</button>{/snippet}</Tooltip>
				{/if}
				<Tooltip text="Swap this step for another type">{#snippet child({ props })}<button {...props}
					class="lw-btn lw-btn-sm"
					onclick={() =>
						(picker = {
							title: `Replace ${step.type} with…`,
							pick: (type, role) => editor.replaceStep(path, type, role)
						})}>Replace</button>{/snippet}</Tooltip>
				{#if place !== 'root'}
					<button
						class="lw-btn lw-btn-sm px-1"
						onclick={() => editor.removeStep(path)}
						aria-label={optional || place === 'list'
							? 'Remove step'
							: 'Reset step to an empty sequence'}
						><XIcon size={12} /> {optional || place === 'list' ? 'Remove' : 'Empty'}</button>
				{/if}
				{#if place === 'list'}
					<button
						class="lw-btn lw-btn-sm"
						onclick={() =>
							(picker = {
								title: `Insert before ${stepTitle(step)}`,
								pick: (type, role) => editor.insertStep(path.slice(0, -1), type, role, index)
							})}>Insert before</button
					>
					<button
						class="lw-btn lw-btn-sm"
						onclick={() =>
							(picker = {
								title: `Insert after ${stepTitle(step)}`,
								pick: (type, role) => editor.insertStep(path.slice(0, -1), type, role, index + 1)
							})}>Insert after</button
					>
				{/if}
			</div>
		</details>
		{#if spec}
			{#if scalarFields.length || problems.length || spec.emits.length}
				<div class="space-y-4">
					{#each scalarFields as [name, field] (name)}
						<div class="space-y-1.5">
							<span
								class="block text-xs {fieldHasProblem(name)
									? 'font-semibold text-crit'
									: 'text-ink-2'}"
								title={name}
							>
								{humanize(name)}{field.required ? '' : ' (optional)'}
							</span>

							{#if field.kind === 'role'}
								<Select
									mono
									class="min-w-0"
									value={step[name]?.role ?? ''}
									onValueChange={(v) => onRoleChange(name, field, v)}
									aria-label="{name}: role"
									options={[
										{ value: '', label: '— choose a role —' },
										...roleOptions(field).map((opt) => ({
											value: opt.role,
											label: `${opt.role} (${opt.behavior})${opt.fits ? '' : ` — needs ${field.requires.join(' or ')}`}`,
											disabled: !opt.fits
										})),
										{ value: '__new__', label: `New ${field.requires[0] ?? ''} role…` }
									]}
								/>
								{#if step[name]?.role}<button
										class="text-xs text-accent hover:underline"
										onclick={() => onreference('roles', step[name].role)}>Edit role ↗</button
									>{/if}
							{:else if field.kind === 'value' || field.kind === 'values'}
								<ValueInput
									{editor}
									path={[...path, name]}
									{field}
									fieldName={name}
									scope={innerScope}
									{onreference}
								/>
							{:else if field.kind === 'value_map'}
								<div class="space-y-1">
									{#each mapEntries(name) as [entryKey] (entryKey)}
										<div class="flex flex-wrap items-center gap-1.5">
											<input
												class="lw-input mono w-32"
												value={entryKey}
												onchange={(e) => renameMapKey(name, entryKey, e.currentTarget.value.trim())}
												aria-label="Setting name"
											/>
											<ValueInput
												{editor}
												path={[...path, name, entryKey]}
												{field}
												fieldName={entryKey}
												scope={innerScope}
												{onreference}
											/>
											<button
												class="lw-btn lw-btn-sm px-1"
												onclick={() => delete step[name][entryKey]}
												aria-label="Remove setting"><XIcon size={12} /></button
											>
										</div>
									{/each}
									<button class="lw-btn lw-btn-sm" onclick={() => addMapEntry(name)}
										>Add setting</button
									>
								</div>
							{:else if field.literal_type === 'bool'}
								<label class="flex items-center gap-1.5 text-xs">
									<input
										type="checkbox"
										checked={Boolean(step[name])}
										onchange={(e) => editor.setAt([...path, name], e.currentTarget.checked)}
									/>
									{step[name] ? 'yes' : 'no'}
								</label>
							{:else}
								<div class="flex flex-wrap items-center gap-2">
									<input
										class="lw-input mono min-w-0 w-full"
										value={step[name] ?? ''}
										list={field.column === 'reads' ? `records-${key}` : undefined}
										onchange={(e) =>
											editor.setAt(
												[...path, name],
												field.literal_type === 'int' || field.literal_type === 'float'
													? Number(e.currentTarget.value)
													: e.currentTarget.value
											)}
										aria-label={name}
									/>
									{#if field.column === 'records'}
										<span class="text-fine text-muted">recorded as a data column</span>
									{:else if field.column === 'reads'}
										<datalist id="records-{key}">
											{#each records as column (column)}<option value={column}></option>{/each}
										</datalist>
										<span class="text-fine text-muted">a recorded column</span>
									{/if}
								</div>
							{/if}
						</div>
					{/each}

					{#if spec.emits.length}
						<div class="text-fine text-muted">
							Records <span class="mono">{spec.emits.join(', ')}</span>
						</div>
					{/if}

					{#each problems as problem, i (i)}
						<div class="text-fine text-crit">{problemText(problem.message, problem.path)}</div>
					{/each}
				</div>
			{/if}

			{#if context.bindings.length}
				<div class="border-t border-line pt-3 text-xs">
					<h3 class="mb-1 text-muted">Inherited row labels</h3>
					{#each context.bindings as [name, value]}<div class="break-words">
							<span class="mono">{name}</span> = {value}
						</div>{/each}
				</div>
			{/if}
			{#if cleanupSummary(step, editor.definition)}
				<div class="border-t border-line pt-3 text-xs text-ink-2">
					{cleanupSummary(step, editor.definition)}
					<p class="mt-1 text-muted">Cleanup also runs after failure or abort.</p>
				</div>
			{/if}
			{#if childFields.length}
				<div class="space-y-2 border-t border-line pt-3">
					<h3 class="text-xs text-muted">Enclosed steps</h3>
					{#each childFields as [name, field]}
						{#if field.kind === 'step' && step[name]}
							<button
								class="block text-left text-xs text-accent hover:underline"
								onclick={() => editor.selectStep([...path, name])}
								>{humanize(name)} · {stepTitle(step[name])} ↗</button
							>
						{:else}
							<button
								class="lw-btn lw-btn-sm"
								onclick={() =>
									(picker = {
										title: `Add to ${stepTitle(step)} · ${name}`,
										pick: (type, role) => editor.insertStep([...path, name], type, role)
									})}
								><PlusIcon size={12} />
								{field.kind === 'steps'
									? 'Add step'
									: `Add ${humanize(name).toLowerCase()} step`}</button
							>
						{/if}
					{/each}
				</div>
			{/if}
		{:else}<p class="text-body text-crit">
				Unknown step type. Replace or remove it using Step actions.
			</p>{/if}
	{/if}
</section>
{#if picker}
	<StepPicker
		{editor}
		title={picker.title}
		containersOnly={picker.containersOnly}
		onclose={() => (picker = null)}
		onpick={(type, role) => {
			picker?.pick(type, role);
			picker = null;
		}}
	/>
{/if}

<style>
	.inspector {
		min-width: 0;
		overflow-wrap: anywhere;
		scroll-margin-top: 64px;
	}
	.inspector :global(input),
	.inspector :global(select) {
		max-width: 100%;
	}
</style>
