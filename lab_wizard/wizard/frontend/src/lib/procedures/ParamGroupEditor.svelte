<script lang="ts">
	import Panel from '$lib/components/Panel.svelte';
	import ParamValueInput from './ParamValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import {
		PARAM_TYPES,
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
	function add(groupPath: string[], group: boolean) {
		const parent = getAt(editor.definition.params, groupPath) as ParamGroup;
		const name = uniqueName(group ? 'group' : 'value', Object.keys(parent));
		parent[name] = group ? {} : { type: 'float', default: 0 };
		select([...groupPath, name].join('.'));
	}
	function remove() {
		if (!selected) return;
		delete getAt(editor.definition.params, selected.path.slice(0, -1))[selectedName];
		editor.selectedParameter = null;
		renameError = '';
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
		description="Select a parameter to edit its default, type, and description."
		flush
	>
		{#snippet actions()}
			<button class="lw-btn lw-btn-sm" onclick={() => add([], false)}>Add parameter</button>
			<button class="lw-btn lw-btn-sm" onclick={() => add([], true)}>Add group</button>
		{/snippet}
		<div class="p-2">
			{#each nodes as node (node.path.join('.'))}
				{@const dotted = node.path.join('.')}
				{@const decl = isParamDecl(node.entry) ? node.entry : null}
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
					><span>Type</span><select
						class="lw-select"
						value={leaf.type}
						onchange={(e) => setType(e.currentTarget.value as ParamType)}
						aria-label="Parameter type"
						>{#each PARAM_TYPES as type}<option value={type}>{type}</option>{/each}</select
					></label
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
				<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={remove}
					>Remove {leaf ? 'parameter' : 'group'}</button
				>
			</div>
		{:else}<p class="text-sm text-muted">Select a parameter or add one to get started.</p>{/if}
	</aside>
</div>
