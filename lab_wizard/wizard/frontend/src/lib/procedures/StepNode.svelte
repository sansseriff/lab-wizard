<script lang="ts">
	/** One step of the tree, with its fields and its children.
	 *
	 * Rendered entirely from the step's catalog entry: a field's `kind` picks its
	 * editor, so a new step type in `lib/procedures/steps` appears here with no
	 * frontend change. Children render as nested nodes, indented, so the tree's
	 * shape on screen is the shape of the generated code.
	 */
	import CaretDownIcon from 'phosphor-svelte/lib/CaretDown';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import ArrowUpIcon from 'phosphor-svelte/lib/ArrowUp';
	import ArrowDownIcon from 'phosphor-svelte/lib/ArrowDown';
	import XIcon from 'phosphor-svelte/lib/X';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import Self from './StepNode.svelte';
	import StepPicker from './StepPicker.svelte';
	import ValueInput from './ValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { type FieldSpec, type Path, boundNames, pathKey, roleFits } from './model';

	let {
		editor,
		path,
		scope = [],
		/** Where this node sits: a list item can move; a slot or the root cannot. */
		place = 'slot',
		index = 0,
		count = 1,
		optional = false
	}: {
		editor: ProcedureEditor;
		path: Path;
		scope?: string[];
		place?: 'root' | 'slot' | 'list';
		index?: number;
		count?: number;
		/** An optional slot, which can be emptied rather than reset. */
		optional?: boolean;
	} = $props();

	type Picker = { title: string; containersOnly?: boolean; pick: (type: string, role: string | null) => void };

	const catalog = $derived(editor.catalog);
	const step = $derived(editor.step(path));
	const spec = $derived(step ? catalog.steps[step.type] : undefined);
	const key = $derived(pathKey(path));
	const problems = $derived(editor.placedProblems.byStep.get(key) ?? []);
	const fieldEntries = $derived(Object.entries(spec?.fields ?? {}));
	const scalarFields = $derived(fieldEntries.filter(([, f]) => f.kind !== 'step' && f.kind !== 'steps'));
	const childFields = $derived(fieldEntries.filter(([, f]) => f.kind === 'step' || f.kind === 'steps'));
	const innerScope = $derived(step ? [...scope, ...boundNames(step, spec)] : scope);
	const records = $derived(editor.check?.records ?? []);
	const canUnwrap = $derived.by(() => {
		if (!step || !childFields.length) return false;
		let n = 0;
		for (const [name, f] of childFields) n += f.kind === 'steps' ? (step[name]?.length ?? 0) : step[name] ? 1 : 0;
		return n === 1;
	});

	let collapsed = $state(false);
	let picker = $state<Picker | null>(null);

	function fieldHasProblem(name: string): boolean {
		return problems.some((p) => p.path.length > path.length && p.path[path.length] === name);
	}

	function problemText(message: string, problemPath: Path): string {
		const field = problemPath.length > path.length ? String(problemPath[path.length]) : null;
		return field && message === 'Field required' ? `${field} is required` : message;
	}

	function childCount(name: string, field: FieldSpec): number {
		if (field.kind === 'steps') return step[name]?.length ?? 0;
		return step[name] ? 1 : 0;
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
			const behavior = field.requires[0] ?? Object.keys(catalog.behaviors).find((b) => catalog.behaviors[b].bindable);
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

{#if step && spec}
	<div
		id="step-{key}"
		class="rounded border bg-surface {problems.length ? 'border-crit/50' : 'border-line'}"
	>
		<div class="flex items-center gap-1.5 px-2 py-1.5">
			<button
				class="rounded p-0.5 text-muted hover:bg-surface-2"
				onclick={() => (collapsed = !collapsed)}
				aria-label={collapsed ? 'Expand step' : 'Collapse step'}
			>
				{#if collapsed}<CaretRightIcon size={12} />{:else}<CaretDownIcon size={12} />{/if}
			</button>
			<span class="mono text-[12.5px] font-semibold" title={spec.doc}>{step.type}</span>
			{#if problems.length}
				<span class="text-[11px] font-semibold text-crit">{problems.length} problem{problems.length === 1 ? '' : 's'}</span>
			{/if}
			<span class="hidden min-w-0 truncate text-[11.5px] text-muted md:inline">{spec.summary}</span>

			<div class="ml-auto flex shrink-0 items-center gap-1">
				<input
					class="lw-input w-28 py-0.5 text-[11px]"
					value={step.name ?? ''}
					onchange={(e) => editor.setAt([...path, 'name'], e.currentTarget.value || undefined)}
					placeholder="label"
					title="Optional label, shown in run logs"
					aria-label="Step label"
				/>
				{#if place === 'list'}
					<button
						class="lw-btn lw-btn-sm px-1"
						disabled={index === 0}
						onclick={() => editor.moveStep(path, -1)}
						aria-label="Move up"><ArrowUpIcon size={12} /></button
					>
					<button
						class="lw-btn lw-btn-sm px-1"
						disabled={index === count - 1}
						onclick={() => editor.moveStep(path, 1)}
						aria-label="Move down"><ArrowDownIcon size={12} /></button
					>
				{/if}
				<button
					class="lw-btn lw-btn-sm"
					title="Put this step inside a new one — a sweep, a guard, a retry"
					onclick={() =>
						(picker = {
							title: `Wrap ${step.type} in…`,
							containersOnly: true,
							pick: (type) => editor.wrapStep(path, type)
						})}>Wrap</button
				>
				{#if canUnwrap}
					<button
						class="lw-btn lw-btn-sm"
						title="Replace this step with the one step inside it"
						onclick={() => editor.unwrapStep(path)}>Unwrap</button
					>
				{/if}
				<button
					class="lw-btn lw-btn-sm"
					title="Swap this step for another type"
					onclick={() =>
						(picker = {
							title: `Replace ${step.type} with…`,
							pick: (type, role) => editor.replaceStep(path, type, role)
						})}>Replace</button
				>
				{#if place !== 'root'}
					<button
						class="lw-btn lw-btn-sm px-1"
						onclick={() => editor.removeStep(path)}
						aria-label={optional || place === 'list' ? 'Remove step' : 'Reset step to an empty sequence'}
						title={optional || place === 'list' ? 'Remove' : 'Empty this slot'}><XIcon size={12} /></button
					>
				{/if}
			</div>
		</div>

		{#if !collapsed}
			{#if scalarFields.length || problems.length || spec.emits.length}
				<div class="space-y-1.5 border-t border-line px-3 py-2">
					{#each scalarFields as [name, field] (name)}
						<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-2">
							<span
								class="mono truncate text-[11.5px] {fieldHasProblem(name) ? 'font-semibold text-crit' : 'text-ink-2'}"
								title={name}
							>
								{name}{field.required ? '' : '?'}
							</span>

							{#if field.kind === 'role'}
								<select
									class="lw-select mono w-auto min-w-[12rem]"
									value={step[name]?.role ?? ''}
									onchange={(e) => onRoleChange(name, field, e.currentTarget.value)}
									aria-label="{name}: role"
								>
									<option value="">— choose a role —</option>
									{#each roleOptions(field) as opt (opt.role)}
										<option value={opt.role} disabled={!opt.fits}>
											{opt.role} ({opt.behavior}){opt.fits ? '' : ` — needs ${field.requires.join(' or ')}`}
										</option>
									{/each}
									<option value="__new__">New {field.requires[0] ?? ''} role…</option>
								</select>
							{:else if field.kind === 'value' || field.kind === 'values'}
								<ValueInput {editor} path={[...path, name]} {field} fieldName={name} scope={innerScope} />
							{:else if field.kind === 'value_map'}
								<div class="space-y-1">
									{#each mapEntries(name) as [entryKey] (entryKey)}
										<div class="flex items-center gap-1.5">
											<input
												class="lw-input mono w-32"
												value={entryKey}
												onchange={(e) => renameMapKey(name, entryKey, e.currentTarget.value.trim())}
												aria-label="Setting name"
											/>
											<ValueInput {editor} path={[...path, name, entryKey]} {field} fieldName={entryKey} scope={innerScope} />
											<button
												class="lw-btn lw-btn-sm px-1"
												onclick={() => delete step[name][entryKey]}
												aria-label="Remove setting"><XIcon size={12} /></button
											>
										</div>
									{/each}
									<button class="lw-btn lw-btn-sm" onclick={() => addMapEntry(name)}>Add setting</button>
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
								<div class="flex items-center gap-2">
									<input
										class="lw-input mono w-48"
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
										<span class="text-[11px] text-muted">recorded as a data column</span>
									{:else if field.column === 'reads'}
										<datalist id="records-{key}">
											{#each records as column (column)}<option value={column}></option>{/each}
										</datalist>
										<span class="text-[11px] text-muted">a recorded column</span>
									{/if}
								</div>
							{/if}
						</div>
					{/each}

					{#if spec.emits.length}
						<div class="text-[11px] text-muted">
							Records <span class="mono">{spec.emits.join(', ')}</span>
						</div>
					{/if}

					{#each problems as problem, i (i)}
						<div class="text-[11.5px] text-crit">{problemText(problem.message, problem.path)}</div>
					{/each}
				</div>
			{/if}

			{#each childFields as [name, field] (name)}
				<div class="border-t border-line px-3 py-2">
					{#if childFields.length > 1 || field.kind === 'step'}
						<div class="mb-1 flex items-center gap-2">
							<span class="mono text-[11px] text-muted">{name}{field.optional ? '?' : ''}</span>
							{#if innerScope.length > scope.length}
								<span class="text-[11px] text-muted">
									with <span class="mono">{innerScope.slice(scope.length).join(', ')}</span>
								</span>
							{/if}
						</div>
					{/if}
					<div class="space-y-1.5 border-l-2 border-line pl-3">
						{#if field.kind === 'steps'}
							{#each step[name] ?? [] as child, i (child)}
								<Self
									{editor}
									path={[...path, name, i]}
									scope={innerScope}
									place="list"
									index={i}
									count={step[name].length}
								/>
							{/each}
						{:else if step[name]}
							<Self {editor} path={[...path, name]} scope={innerScope} optional={field.optional} />
						{/if}

						{#if field.kind === 'steps' || !step[name]}
							<button
								class="flex items-center gap-1 rounded px-1.5 py-1 text-[11.5px] text-accent hover:bg-accent-wash"
								onclick={() =>
									(picker = {
										title: `Add a step to ${step.type}${childFields.length > 1 ? `.${name}` : ''}`,
										pick: (type, role) => editor.insertStep([...path, name], type, role)
									})}
							>
								<PlusIcon size={11} /> Add step
								{#if childCount(name, field) === 0 && field.kind === 'steps'}
									<span class="text-muted">— empty</span>
								{/if}
							</button>
						{/if}
					</div>
				</div>
			{/each}
		{/if}
	</div>

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
{:else}
	<div class="rounded border border-crit/50 px-3 py-2 text-xs text-crit">
		Unknown step type <span class="mono">{step?.type}</span>
		<button class="lw-btn lw-btn-sm ml-2" onclick={() => editor.removeStep(path)}>Remove</button>
	</div>
{/if}
