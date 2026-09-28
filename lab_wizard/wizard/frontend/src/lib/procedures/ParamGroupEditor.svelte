<script lang="ts">
	import Panel from '$lib/components/Panel.svelte';
	import RowContextMenu from '$lib/components/menu/RowContextMenu.svelte';
	import type { MenuAction } from '$lib/components/menu/items';
	import ParamValueInput from './ParamValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import Select from '$lib/components/Select.svelte';
	import {
		PARAM_TYPES,
		type ParamDecl,
		type ParamGroup,
		type ParamType,
		getAt,
		isParamDecl,
		paramNodes,
		paramLeaves,
		uniqueName,
		validIdentifier
	} from './model';
	import { formatValue, humanize } from './presentation';
	let { editor }: { editor: ProcedureEditor } = $props();
	const nodes = $derived(paramNodes(editor.definition.params));
	const selected = $derived(
		nodes.find((n) => n.path.join('.') === editor.selectedParameter) ??
			nodes.find((n) => isParamDecl(n.entry)) ??
			nodes[0]
	);
	const key = $derived(selected?.path.join('.') ?? '');
	const leaf = $derived(selected && isParamDecl(selected.entry) ? selected.entry : null);
	const selectedName = $derived(selected?.path.at(-1) ?? '');
	const problems = $derived(
		(editor.check?.problems ?? []).filter(
			(p) => p.path[0] === 'params' && selected?.path.every((part, i) => p.path[i + 1] === part)
		)
	);
	let renameError = $state('');
	function select(name: string) {
		editor.selectedParameter = name;
		renameError = '';
	}
	function rename(event: Event) {
		if (!selected) return;
		const input = event.currentTarget as HTMLInputElement;
		const to = input.value.trim();
		renameError = '';
		if (!validIdentifier(to))
			renameError =
				'Use a name beginning with a letter or underscore, followed by letters, numbers, or underscores.';
		else if (to !== selectedName) {
			const parent = selected.path.slice(0, -1);
			if (editor.renameParam(parent, selectedName, to))
				editor.selectedParameter = [...parent, to].join('.');
			else renameError = `${to} already exists in this group.`;
		}
		input.value = selectedName;
	}
	// Where the Add buttons put a new entry: inside the selected group, or
	// beside the selected parameter in its group; the top level otherwise.
	const target = $derived(selected ? (leaf ? selected.path.slice(0, -1) : selected.path) : []);
	const where = $derived(target.length ? ` to ${humanize(target.at(-1)!)}` : '');

	/** Add a parameter (or group) to ``groupPath``: at its end, or right after ``after``. */
	function add(groupPath: string[], group: boolean, after?: string) {
		const parent = getAt(editor.definition.params, groupPath) as ParamGroup;
		const name = uniqueName(group ? 'group' : 'value', Object.keys(parent));
		const entry: ParamDecl | ParamGroup = group ? {} : { type: 'float', default: 0 };
		if (after !== undefined && after in parent) {
			// A group's order is its keys' order, so it is replaced by a copy with
			// the new entry in place (reordering keys on the live object does not
			// reorder them).
			const rebuilt: ParamGroup = {};
			for (const [k, v] of Object.entries(parent)) {
				rebuilt[k] = v;
				if (k === after) rebuilt[name] = entry;
			}
			if (groupPath.length) {
				(getAt(editor.definition.params, groupPath.slice(0, -1)) as ParamGroup)[groupPath.at(-1)!] = rebuilt;
			} else {
				editor.definition.params = rebuilt;
			}
		} else {
			parent[name] = entry;
		}
		select([...groupPath, name].join('.'));
	}
	function remove(path: string[] = selected?.path ?? []) {
		if (!path.length) return;
		delete getAt(editor.definition.params, path.slice(0, -1))[path.at(-1)!];
		editor.selectedParameter = null;
		renameError = '';
	}
	/** A row's right-click menu: add inside it (a group) or after it, or remove it. */
	function rowActions(path: string[], isGroup: boolean): MenuAction[] {
		const parent = path.slice(0, -1);
		const name = path.at(-1)!;
		return [
			...(isGroup
				? [
						{ label: 'Add parameter inside', onselect: () => add(path, false) },
						{ label: 'Add group inside', onselect: () => add(path, true) }
					]
				: []),
			{ label: 'Add parameter after', onselect: () => add(parent, false, name) },
			{ label: 'Add group after', onselect: () => add(parent, true, name) },
			{ label: `Remove ${isGroup ? 'group' : 'parameter'}`, danger: true, onselect: () => remove(path) }
		];
	}
	function setType(type: ParamType) {
		if (!leaf || leaf.type === type) return;
		leaf.type = type;
		leaf.default =
			type === 'sweep'
				? { mode: 'linear', start: 0, stop: 1, step: 0.01 }
				: type === 'bool'
					? false
					: type === 'str'
						? ''
						: 0;
	}
</script>

<div class="editor-grid">
	<Panel
		title="Parameter defaults"
		description="Select a parameter to edit it. Add puts a new one in the selected group; right-click a row to add inside or after it."
		flush
	>
		{#snippet actions()}
			<button class="lw-btn lw-btn-sm" onclick={() => add(target, false)}>Add parameter{where}</button>
			<button class="lw-btn lw-btn-sm" onclick={() => add(target, true)}>Add group{where}</button>
		{/snippet}
		<div class="p-2">
			{#each nodes as node (node.path.join('.'))}
				{@const dotted = node.path.join('.')}
				{@const decl = isParamDecl(node.entry) ? node.entry : null}
				<RowContextMenu items={rowActions(node.path, !decl)}>
				<button
					id="param-{dotted}"
					class="editor-row scroll-mt-16"
					style:padding-left="{10 + (node.path.length - 1) * 16}px"
					aria-pressed={key === dotted}
					onclick={() => select(dotted)}
				>
					<span class:font-semibold={!decl}>{humanize(node.path.at(-1)!)}</span>
					<span class="editor-row-summary"
						>{decl
							? `${formatValue(decl.default)}${decl.unit ? ` ${decl.unit}` : ''}`
							: `${paramLeaves(node.entry as ParamGroup).length} parameters`}</span
					>
					{#if (editor.check?.problems ?? []).some((p) => p.path[0] === 'params' && node.path.every((part, i) => p.path[i + 1] === part))}<span
							class="text-xs text-crit">Needs attention</span
						>{/if}
				</button>
				</RowContextMenu>
			{:else}<p class="editor-empty">
					No parameters yet. Add a shared value here, or use “Make parameter” in a step.
				</p>{/each}
		</div>
	</Panel>
	<aside class="editor-detail" aria-label="Selected parameter">
		{#if selected}
			<p class="mono mb-1 text-xs text-muted">{key}</p>
			<h3>{leaf ? 'Parameter settings' : 'Parameter group'}</h3>
			<p class="mt-1 text-xs text-muted">
				{editor.paramUses(key)} step reference{editor.paramUses(key) === 1 ? '' : 's'}
			</p>
			<label class="editor-field"
				><span>{leaf ? 'Parameter name' : 'Group name'}</span><input
					class="lw-input mono"
					value={selectedName}
					onchange={rename}
					aria-label={leaf ? 'Parameter name' : 'Group name'}
				/></label
			>
			{#if renameError}<p class="mt-2 text-xs text-crit" role="alert">{renameError}</p>{/if}
			{#if leaf}
				<label class="editor-field"
					><span>Type</span><Select
						value={leaf.type}
						onValueChange={(v) => setType(v as ParamType)}
						aria-label="Parameter type"
						options={PARAM_TYPES.map((type) => ({ value: type, label: type }))}
					/></label
				>
				<div class="editor-field">
					<span>Default value</span><ParamValueInput
						type={leaf.type}
						value={leaf.default}
						label="{key} default"
						onchange={(v) => {
							if (leaf) leaf.default = v;
						}}
					/>
				</div>
				<label class="editor-field"
					><span>Unit</span><input
						class="lw-input"
						value={leaf.unit ?? ''}
						onchange={(e) => {
							if (leaf) leaf.unit = e.currentTarget.value || undefined;
						}}
						placeholder="e.g. V, s, dB"
						aria-label="Parameter unit"
					/></label
				>
				<label class="editor-field"
					><span>Description</span><textarea
						class="lw-input min-h-20 resize-y"
						value={leaf.description ?? ''}
						onchange={(e) => {
							if (leaf) leaf.description = e.currentTarget.value || undefined;
						}}
						placeholder="What this value controls"
						aria-label="Parameter description"
					></textarea></label
				>
			{:else}
				<div class="editor-actions">
					<button class="lw-btn lw-btn-sm" onclick={() => add(selected.path, false)}
						>Add parameter here</button
					><button class="lw-btn lw-btn-sm" onclick={() => add(selected.path, true)}
						>Add subgroup</button
					>
				</div>
			{/if}
			{#each problems as problem}<p class="mt-2 text-xs text-crit">{problem.message}</p>{/each}
			<p class="mt-4 border-t border-line pt-3 text-xs text-muted">
				Renaming updates all step references. Projects and presets can override the defaults.
			</p>
			<div class="editor-actions">
				<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={() => remove()}
					>Remove {leaf ? 'parameter' : 'group'}</button
				>
			</div>
		{:else}<p class="text-body text-muted">Select a parameter or add one to get started.</p>{/if}
	</aside>
</div>
