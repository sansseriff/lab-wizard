<script lang="ts">
	/** A group of params: typed leaves and nested groups, in the order the YAML keeps.
	 *
	 * Renaming a param or a group rewrites every `{param: …}` that pointed at
	 * it, so a rename never leaves the tree reading a param that is gone.
	 */
	import XIcon from 'phosphor-svelte/lib/X';
	import Self from './ParamGroupEditor.svelte';
	import ParamValueInput from './ParamValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { PARAM_TYPES, type ParamDecl, type ParamGroup, type ParamType, getAt, isParamDecl, uniqueName } from './model';

	let { editor, groupPath = [] }: { editor: ProcedureEditor; groupPath?: string[] } = $props();

	const group = $derived(getAt(editor.definition.params, groupPath) as ParamGroup);
	const entries = $derived(Object.entries(group ?? {}));
	const prefix = $derived(groupPath.length ? `${groupPath.join('.')}.` : '');

	let renameError = $state<string | null>(null);

	function rename(from: string, to: string) {
		const clean = to.trim();
		renameError = null;
		if (!clean || clean === from) return;
		if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(clean)) {
			renameError = `${clean} is not a valid name`;
			return;
		}
		if (!editor.renameParam(groupPath, from, clean)) renameError = `${clean} is already taken`;
	}

	function setType(name: string, type: ParamType) {
		const decl = group[name] as ParamDecl;
		decl.type = type;
		decl.default =
			type === 'sweep' ? { mode: 'linear', start: 0, stop: 1, step: 0.01 } : type === 'bool' ? false : type === 'str' ? '' : 0;
	}

	function addLeaf() {
		group[uniqueName('value', Object.keys(group))] = { type: 'float', default: 0 };
	}

	function addGroup() {
		group[uniqueName('group', Object.keys(group))] = {};
	}

	function remove(name: string) {
		delete group[name];
	}
</script>

<div class="space-y-1.5">
	{#each entries as [name, entry] (name)}
		{@const uses = editor.paramUses(`${prefix}${name}`)}
		{#if isParamDecl(entry)}
			<div class="rounded border border-line px-2 py-1.5">
				<div class="flex items-center gap-1.5">
					<input
						class="lw-input mono min-w-0 flex-1"
						value={name}
						onchange={(e) => rename(name, e.currentTarget.value)}
						aria-label="Param name"
					/>
					<select
						class="lw-select w-auto shrink-0"
						value={entry.type}
						onchange={(e) => setType(name, e.currentTarget.value as ParamType)}
						aria-label="{name}: type"
					>
						{#each PARAM_TYPES as t (t)}<option value={t}>{t}</option>{/each}
					</select>
					<input
						class="lw-input w-14 shrink-0"
						value={entry.unit ?? ''}
						onchange={(e) => ((group[name] as ParamDecl).unit = e.currentTarget.value || undefined)}
						placeholder="unit"
						aria-label="{name}: unit"
					/>
					<span class="shrink-0 whitespace-nowrap text-[11px] text-muted" title="Steps reading this param">
						{uses ? `used ${uses}×` : 'unused'}
					</span>
					<button class="lw-btn lw-btn-sm px-1" onclick={() => remove(name)} aria-label="Remove param {name}">
						<XIcon size={12} />
					</button>
				</div>
				<div class="mt-1.5 grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-x-2 gap-y-1">
					<span class="text-[11px] text-muted">default</span>
					<ParamValueInput
						type={entry.type}
						value={entry.default}
						label="{prefix}{name} default"
						onchange={(v) => ((group[name] as ParamDecl).default = v)}
					/>
					<span class="text-[11px] text-muted">meaning</span>
					<input
						class="lw-input"
						value={entry.description ?? ''}
						onchange={(e) => ((group[name] as ParamDecl).description = e.currentTarget.value || undefined)}
						placeholder="What it controls — shown as a comment in project YAML"
						aria-label="{name}: description"
					/>
				</div>
			</div>
		{:else}
			<div class="rounded border border-line">
				<div class="flex items-center gap-1.5 border-b border-line bg-surface-2 px-2 py-1">
					<input
						class="lw-input mono w-36 font-semibold"
						value={name}
						onchange={(e) => rename(name, e.currentTarget.value)}
						aria-label="Group name"
					/>
					<span class="text-[11px] text-muted">group</span>
					<button class="lw-btn lw-btn-sm ml-auto px-1" onclick={() => remove(name)} aria-label="Remove group {name}">
						<XIcon size={12} />
					</button>
				</div>
				<div class="p-2">
					<Self {editor} groupPath={[...groupPath, name]} />
				</div>
			</div>
		{/if}
	{/each}

	{#if renameError}
		<p class="text-[11.5px] text-crit">{renameError}</p>
	{/if}

	<div class="flex gap-1.5">
		<button class="lw-btn lw-btn-sm" onclick={addLeaf}>Add param</button>
		<button class="lw-btn lw-btn-sm" onclick={addGroup}>Add group</button>
	</div>
</div>
