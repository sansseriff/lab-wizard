<script lang="ts">
	import Panel from '$lib/components/Panel.svelte';
	import Select from '$lib/components/Select.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { uniqueName, validIdentifier } from './model';
	let { editor }: { editor: ProcedureEditor } = $props();
	const catalog = $derived(editor.catalog);
	const bindable = $derived(Object.values(catalog.behaviors).filter((b) => b.bindable));
	const roles = $derived(Object.entries(editor.definition.roles));
	const name = $derived(
		editor.selectedRole && editor.definition.roles[editor.selectedRole]
			? editor.selectedRole
			: roles[0]?.[0]
	);
	const decl = $derived(name ? editor.definition.roles[name] : null);
	const fillers = $derived(decl ? (catalog.fillers[decl.behavior] ?? []) : []);
	const problems = $derived(name ? (editor.placedProblems.byRole.get(name) ?? []) : []);
	let error = $state('');
	function select(role: string) {
		editor.selectedRole = role;
		error = '';
	}
	function rename(event: Event) {
		if (!name) return;
		const input = event.currentTarget as HTMLInputElement;
		const to = input.value.trim();
		error = '';
		if (!validIdentifier(to))
			error =
				'Use a name beginning with a letter or underscore, followed by letters, numbers, or underscores.';
		else if (to !== name) {
			if (editor.renameRole(name, to)) editor.selectedRole = to;
			else error = `${to} already exists.`;
		}
		input.value = name;
	}
	function add() {
		const behavior = bindable[0]?.name;
		if (behavior)
			select(
				editor.addRole(
					uniqueName(behavior.toLowerCase(), Object.keys(editor.definition.roles)),
					behavior
				)
			);
	}
</script>

<div class="editor-grid">
	<Panel
		title="Instrument roles"
		description="Select a role to edit the behavior this procedure requires."
		flush
	>
		{#snippet actions()}<button class="lw-btn lw-btn-sm" onclick={add} disabled={!bindable.length}
				>Add role</button
			>{/snippet}
		<div class="p-2">
			{#each roles as [role, entry] (role)}
				<button
					class="editor-row scroll-mt-16"
					id="role-{role}"
					aria-pressed={name === role}
					onclick={() => select(role)}
				>
					<span>{role}</span><span class="editor-row-summary"
						>{entry.behavior} · {editor.roleUses(role)} references</span
					>
					{#if editor.placedProblems.byRole.get(role)?.length}<span class="text-xs text-crit"
							>Needs attention</span
						>{/if}
				</button>
			{:else}<p class="editor-empty">
					No roles yet. Adding an instrument step creates the role it needs, or add one here.
				</p>{/each}
		</div>
	</Panel>
	<aside class="editor-detail" aria-label="Selected instrument role">
		{#if name && decl}
			<p class="mono mb-1 text-xs text-muted">{name}</p>
			<h3>Role settings</h3>
			<label class="editor-field"
				><span>Role name</span><input
					class="lw-input mono"
					value={name}
					onchange={rename}
					aria-label="Role name"
				/></label
			>
			{#if error}<p class="mt-2 text-xs text-crit" role="alert">{error}</p>{/if}
			<label class="editor-field"
				><span>Required behavior</span><Select
					value={decl.behavior}
					onValueChange={(v) => {
						if (decl) decl.behavior = v;
					}}
					aria-label="Required behavior"
					options={[
						...(catalog.behaviors[decl.behavior]
							? []
							: [{ value: decl.behavior, label: `${decl.behavior} (unknown)` }]),
						...bindable.map((b) => ({ value: b.name, label: b.name }))
					]}
				/></label
			>
			<p class="mt-2 text-xs text-muted">{catalog.behaviors[decl.behavior]?.summary}</p>
			<label class="editor-field"
				><span>Description</span><textarea
					class="lw-input min-h-24 resize-y"
					value={decl.description ?? ''}
					onchange={(e) => {
						if (decl) decl.description = e.currentTarget.value || undefined;
					}}
					aria-label="Role description"
					placeholder="What this instrument does in the measurement"
				></textarea></label
			>
			{#each problems as problem}<p class="mt-2 text-xs text-crit">{problem.message}</p>{/each}
			<div class="mt-4 border-t border-line pt-3 text-xs text-muted">
				<p>
					{fillers.length} compatible instrument{fillers.length === 1 ? '' : 's'} in this workspace.
				</p>
				<p class="mt-2">
					Assign an actual instrument when creating a measurement. Renaming this role updates all
					step references.
				</p>
			</div>
			<div class="editor-actions">
				<button
					class="lw-btn lw-btn-danger lw-btn-sm"
					onclick={() => {
						if (name) editor.removeRole(name);
						editor.selectedRole = null;
						error = '';
					}}>Remove role</button
				>
			</div>
		{:else}<p class="text-body text-muted">Select a role or add one to get started.</p>{/if}
	</aside>
</div>
