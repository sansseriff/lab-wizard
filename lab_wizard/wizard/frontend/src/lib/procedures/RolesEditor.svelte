<script lang="ts">
	/** The procedure's signature: each role and the behavior an instrument needs to fill it.
	 *
	 * A role names a behavior, never an instrument, which is what lets one
	 * procedure run on any rack. The count of this workspace's instruments that
	 * can fill a role is shown beside it: a role nothing here can fill is legal
	 * (another workspace's server may have one) but worth noticing.
	 */
	import XIcon from 'phosphor-svelte/lib/X';
	import type { ProcedureEditor } from './editor.svelte';
	import { uniqueName } from './model';

	let { editor }: { editor: ProcedureEditor } = $props();

	const catalog = $derived(editor.catalog);
	const bindable = $derived(Object.values(catalog.behaviors).filter((b) => b.bindable));
	const roles = $derived(Object.entries(editor.definition.roles));

	let error = $state<string | null>(null);

	function rename(from: string, to: string) {
		const clean = to.trim();
		error = null;
		if (!clean || clean === from) return;
		if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(clean)) {
			error = `${clean} is not a valid role name`;
			return;
		}
		if (!editor.renameRole(from, clean)) error = `A role named ${clean} already exists`;
	}

	function add() {
		const behavior = bindable[0]?.name;
		if (!behavior) return;
		editor.addRole(uniqueName(behavior.toLowerCase(), Object.keys(editor.definition.roles)), behavior);
	}
</script>

<div class="space-y-1.5">
	{#each roles as [name, decl] (name)}
		{@const uses = editor.roleUses(name)}
		{@const fillers = catalog.fillers[decl.behavior] ?? []}
		{@const problems = editor.placedProblems.byRole.get(name) ?? []}
		<div class="rounded border px-2 py-1.5 {problems.length ? 'border-crit/50' : 'border-line'}">
			<div class="flex flex-wrap items-center gap-1.5">
				<input
					class="lw-input mono w-36"
					value={name}
					onchange={(e) => rename(name, e.currentTarget.value)}
					aria-label="Role name"
				/>
				<select
					class="lw-select w-auto"
					value={decl.behavior}
					onchange={(e) => (editor.definition.roles[name].behavior = e.currentTarget.value)}
					aria-label="{name}: behavior"
				>
					{#if !catalog.behaviors[decl.behavior]}
						<option value={decl.behavior}>{decl.behavior} (unknown)</option>
					{/if}
					{#each bindable as b (b.name)}
						<option value={b.name} title={b.summary}>{b.name}</option>
					{/each}
				</select>
				<span class="ml-auto text-[11px] text-muted">{uses ? `used ${uses}×` : 'unused'}</span>
				<button class="lw-btn lw-btn-sm px-1" onclick={() => editor.removeRole(name)} aria-label="Remove role {name}">
					<XIcon size={12} />
				</button>
			</div>
			<input
				class="lw-input mt-1.5"
				value={decl.description ?? ''}
				onchange={(e) => (editor.definition.roles[name].description = e.currentTarget.value || undefined)}
				placeholder="What this instrument does in the measurement"
				aria-label="{name}: description"
			/>
			<p class="mt-1 text-[11px] {fillers.length ? 'text-muted' : 'text-warn'}" title={fillers.join(', ')}>
				{#if fillers.length}
					{fillers.length} instrument{fillers.length === 1 ? '' : 's'} in this workspace can fill it
				{:else}
					No instrument in this workspace is a {decl.behavior}; a server's may be
				{/if}
			</p>
			{#each problems as problem, i (i)}
				<p class="text-[11.5px] text-crit">{problem.message}</p>
			{/each}
		</div>
	{/each}

	{#if !roles.length}
		<p class="text-xs text-muted">
			No roles yet. Adding an instrument step declares the role it needs, or add one here.
		</p>
	{/if}
	{#if error}
		<p class="text-[11.5px] text-crit">{error}</p>
	{/if}
	<button class="lw-btn lw-btn-sm" onclick={add}>Add role</button>
</div>
