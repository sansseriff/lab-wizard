<script lang="ts">
	/** The procedure composer (procedure plan Phase 4).
	 *
	 * Edits one definition: its roles, its params, and its step tree. The backend
	 * checks it after every edit and is the only judge of whether it can be
	 * generated; this page places each problem on the step it is about, and shows
	 * the Python the definition becomes, so what is being built is never a
	 * mystery. Save refuses anything that does not check.
	 */
	import { untrack } from 'svelte';
	import { beforeNavigate, replaceState } from '$app/navigation';
	import { fetchWithConfig } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ParamGroupEditor from '$lib/procedures/ParamGroupEditor.svelte';
	import PresetsPanel from '$lib/procedures/PresetsPanel.svelte';
	import RolesEditor from '$lib/procedures/RolesEditor.svelte';
	import StepNode from '$lib/procedures/StepNode.svelte';
	import { ProcedureEditor } from '$lib/procedures/editor.svelte';
	import type { Catalog, Definition, Problem } from '$lib/procedures/model';

	let { data } = $props();

	// Built once from the loaded data; saving updates it in place rather than
	// reloading, so nothing typed is lost to a navigation.
	const editor = untrack(() =>
		data.catalog && data.definition
			? new ProcedureEditor(data.catalog as Catalog, data.definition as Definition, data.origin, data.loadedName)
			: null
	);

	type Tab = 'compose' | 'yaml' | 'python' | 'presets';
	let tab = $state<Tab>('compose');
	let saving = $state(false);
	let saveMessage = $state<{ tone: 'ok' | 'crit'; text: string } | null>(null);
	let yamlText = $state('');
	let yamlError = $state<string | null>(null);
	let yamlDirty = $state(false);

	$effect(() => {
		if (!editor) return;
		void editor.json;
		editor.scheduleCheck();
	});

	beforeNavigate((navigation) => {
		if (editor?.dirty && !saving && !confirm('This procedure has unsaved changes. Leave anyway?')) {
			navigation.cancel();
		}
	});

	const name = $derived(editor?.definition.name ?? '');
	const renaming = $derived(!!editor?.loadedName && editor.loadedName !== name);
	const problems = $derived(editor?.check?.problems ?? []);
	const canSave = $derived(!!editor && !!editor.check?.ok && !editor.checking && !saving && (editor.dirty || !editor.origin || editor.origin === 'builtin'));

	async function save() {
		if (!editor) return;
		saving = true;
		saveMessage = null;
		const previous = editor.loadedName;
		const previousOrigin = editor.origin;
		try {
			await fetchWithConfig(`/api/procedures/${encodeURIComponent(name)}`, 'PUT', {
				definition: $state.snapshot(editor.definition)
			});
			let text = `Saved to config/procedures/${name}.yml.`;
			if (previous && previous !== name && previousOrigin === 'workspace') {
				await fetchWithConfig(`/api/procedures/${encodeURIComponent(previous)}`, 'DELETE');
				text = `Renamed ${previous} to ${name}.`;
			}
			editor.markSaved('workspace');
			replaceState(`/procedures/edit?name=${encodeURIComponent(name)}`, {});
			saveMessage = { tone: 'ok', text };
		} catch (e) {
			saveMessage = { tone: 'crit', text: e instanceof Error ? e.message : String(e) };
		} finally {
			saving = false;
		}
	}

	async function openYaml() {
		if (!editor) return;
		tab = 'yaml';
		yamlError = null;
		yamlDirty = false;
		try {
			const out = await fetchWithConfig<{ yaml: string }>('/api/procedures/to-yaml', 'POST', {
				definition: $state.snapshot(editor.definition)
			});
			yamlText = out.yaml;
		} catch (e) {
			yamlError = e instanceof Error ? e.message : String(e);
		}
	}

	async function applyYaml() {
		if (!editor) return;
		try {
			const out = await fetchWithConfig<{ definition: Definition }>('/api/procedures/from-yaml', 'POST', {
				yaml: yamlText
			});
			editor.definition = { name: '', description: '', roles: {}, params: {}, ...(out.definition as Partial<Definition>) } as Definition;
			yamlError = null;
			yamlDirty = false;
			tab = 'compose';
		} catch (e) {
			yamlError = e instanceof Error ? e.message : String(e);
		}
	}

	function switchTab(next: Tab) {
		if (tab === 'yaml' && yamlDirty && !confirm('Discard your YAML edits? They have not been applied.')) return;
		if (next === 'yaml') openYaml();
		else tab = next;
	}

	/** Scroll to the step (or the nearest enclosing one) a problem is about. */
	function reveal(problem: Problem) {
		tab = 'compose';
		requestAnimationFrame(() => {
			for (let n = problem.path.length; n > 0; n--) {
				const el = document.getElementById(`step-${problem.path.slice(0, n).join('.')}`);
				if (el) {
					el.scrollIntoView({ behavior: 'smooth', block: 'center' });
					el.animate([{ outlineColor: 'var(--crit)' }, { outlineColor: 'transparent' }], { duration: 1200 });
					return;
				}
			}
		});
	}

	function problemLabel(problem: Problem): string {
		const where = problem.path.join(' › ');
		return where ? `${where}: ${problem.message}` : problem.message;
	}

	async function revertToBuiltin() {
		if (!editor || !confirm(`Delete this workspace's ${name} and use the built-in one again?`)) return;
		await fetchWithConfig(`/api/procedures/${encodeURIComponent(name)}`, 'DELETE');
		editor.savedJson = editor.json; // leaving is intended
		location.href = `/procedures/edit?name=${encodeURIComponent(name)}`;
	}
</script>

{#if !editor}
	<Callout tone="crit" title="Could not open the composer. ">{data.error}</Callout>
{:else}
	<div class="space-y-4">
		<PageHeader
			title={editor.loadedName ? `Procedure ${editor.loadedName}` : 'New procedure'}
			lede="Roles say which behaviors the procedure needs, params what a project can tune, and the step tree what it does. It is saved as YAML and generated as Python."
		>
			{#snippet actions()}
				<a class="lw-btn" href="/procedures">Library</a>
				<button class="lw-btn lw-btn-primary" onclick={save} disabled={!canSave}>
					{saving ? 'Saving…' : renaming ? `Save as ${name}` : 'Save'}
				</button>
			{/snippet}
		</PageHeader>

		<div class="grid gap-3 sm:grid-cols-[16rem_minmax(0,1fr)]">
			<label>
				<span class="lw-label">Name</span>
				<input class="lw-input mono" bind:value={editor.definition.name} aria-label="Procedure name" />
			</label>
			<label>
				<span class="lw-label">Description</span>
				<input class="lw-input" bind:value={editor.definition.description} placeholder="One line: what it measures" />
			</label>
		</div>

		{#if editor.origin === 'builtin' && !renaming}
			<Callout tone="info">
				Built into lab_wizard. Saving keeps the built-in and writes this workspace's own copy, which takes
				precedence here.
			</Callout>
		{:else if editor.origin === 'workspace' && data.hasBuiltin && !renaming}
			<Callout tone="info">
				This workspace's copy overrides the built-in <span class="mono">{name}</span>.
				<button class="ml-1 font-semibold underline" onclick={revertToBuiltin}>Revert to the built-in</button>
			</Callout>
		{:else if renaming && editor.origin === 'workspace'}
			<Callout tone="info">Saving renames <span class="mono">{editor.loadedName}</span>. Projects already generated keep their copy.</Callout>
		{:else if renaming}
			<Callout tone="info">Saving creates <span class="mono">{name}</span>; the built-in <span class="mono">{editor.loadedName}</span> stays.</Callout>
		{/if}
		{#if saveMessage}
			<Callout tone={saveMessage.tone}>
				{saveMessage.text}
				{#if saveMessage.tone === 'ok'}
					<a class="ml-1 font-semibold underline" href="/measurements/resources?name={encodeURIComponent(name)}&kind=procedure">
						Create a measurement from it
					</a>
				{/if}
			</Callout>
		{/if}

		<div class="flex gap-1 border-b border-line" role="tablist">
			{#each [['compose', 'Compose'], ['yaml', 'YAML'], ['python', 'Python'], ['presets', 'Presets']] as [id, label] (id)}
				<button
					role="tab"
					aria-selected={tab === id}
					class="-mb-px border-b-2 px-3 py-1.5 text-[13px] {tab === id
						? 'border-accent font-semibold text-accent-strong'
						: 'border-transparent text-muted hover:text-ink'}"
					onclick={() => switchTab(id as Tab)}
				>
					{label}
				</button>
			{/each}
		</div>

		<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
			<div class="min-w-0 space-y-4">
				{#if tab === 'compose'}
					<div class="grid gap-4 xl:grid-cols-2">
						<Panel title="Roles" description="The behaviors an instrument must have — this is the signature.">
							<RolesEditor {editor} />
						</Panel>
						<Panel title="Params" description="What a project's YAML and presets can set.">
							<ParamGroupEditor {editor} />
						</Panel>
					</div>
					<Panel title="Steps" description="Run top to bottom; nested steps run inside their parent.">
						<StepNode {editor} path={['body']} place="root" />
					</Panel>
				{:else if tab === 'yaml'}
					<Panel title="YAML" description="The definition as it is saved. Edit and apply to load it into the composer.">
						{#snippet actions()}
							<button class="lw-btn lw-btn-sm" onclick={openYaml}>Reload</button>
							<button class="lw-btn lw-btn-primary lw-btn-sm" onclick={applyYaml} disabled={!yamlDirty}>Apply</button>
						{/snippet}
						<textarea
							class="lw-input mono h-[60vh] w-full resize-y text-[12px] leading-snug"
							bind:value={yamlText}
							oninput={() => (yamlDirty = true)}
							spellcheck="false"
							aria-label="Procedure YAML"
						></textarea>
						{#if yamlError}
							<div class="mt-2"><Callout tone="crit">{yamlError}</Callout></div>
						{/if}
					</Panel>
				{:else if tab === 'python'}
					<Panel title="Generated Python" description="The module a project gets. The tree between the wizard:procedure markers is regenerated on refresh.">
						{#if editor.check?.python}
							<pre class="mono max-h-[70vh] overflow-auto rounded bg-surface-2 p-3 text-[12px] leading-snug">{editor.check.python}</pre>
						{:else}
							<p class="text-sm text-muted">Nothing to show until the procedure checks — fix the problems listed beside.</p>
						{/if}
					</Panel>
				{:else if tab === 'presets'}
					<Panel title="Presets" description="Named params sets, offered when creating a measurement.">
						{#if editor.origin === 'workspace' || editor.origin === 'builtin'}
							<PresetsPanel {editor} name={editor.loadedName ?? name} />
						{:else}
							<p class="text-sm text-muted">Save the procedure to add presets.</p>
						{/if}
					</Panel>
				{/if}
			</div>

			<aside class="space-y-3 lg:sticky lg:top-[62px]">
				<Panel title="Check">
					{#if editor.checkError}
						<Callout tone="crit">Could not check: {editor.checkError}</Callout>
					{:else if !editor.check}
						<p class="text-xs text-muted">Checking…</p>
					{:else if editor.check.ok}
						<div class="flex items-center gap-2">
							<Pill tone="ok" dot>Ready to generate</Pill>
							{#if editor.dirty}<Pill tone="warn">unsaved</Pill>{/if}
						</div>
					{:else}
						<div class="mb-2 flex items-center gap-2">
							<Pill tone="crit" dot>{problems.length} problem{problems.length === 1 ? '' : 's'}</Pill>
							{#if editor.checking}<span class="text-[11px] text-muted">checking…</span>{/if}
						</div>
						<ul class="space-y-1">
							{#each problems as problem, i (i)}
								<li>
									<button
										class="w-full rounded px-1.5 py-1 text-left text-[11.5px] leading-snug text-crit hover:bg-crit-wash"
										onclick={() => reveal(problem)}
									>
										{problemLabel(problem)}
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</Panel>

				<Panel title="Records" description="Data columns each run's rows can carry — what a plot's axes choose from.">
					{#if editor.check?.records.length}
						<div class="flex flex-wrap gap-1">
							{#each editor.check.records as column (column)}
								<span class="mono rounded bg-surface-2 px-1.5 py-0.5 text-[11.5px]">{column}</span>
							{/each}
						</div>
					{:else}
						<p class="text-xs text-muted">Nothing recorded yet. A count, a voltage reading, or a sweep adds columns.</p>
					{/if}
				</Panel>
			</aside>
		</div>
	</div>
{/if}
