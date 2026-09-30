<script lang="ts">
	/** The procedure composer (procedure plan Phase 4).
	 *
	 * Edits one definition: its roles, its params, and its step tree. The backend
	 * checks it after every edit and is the only judge of whether it can be
	 * generated; this page places each problem on the step it is about, and shows
	 * the Python the definition becomes, so what is being built is never a
	 * mystery. Save refuses anything that does not check.
	 */
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import '$lib/procedures/composer.css';
	import { page } from '$app/state';
	import { queryChoice, setQuery } from '$lib/url';
	import Tabs from '$lib/components/Tabs.svelte';
	import { ask, guardNavigation } from '$lib/confirm.svelte';
	import { api, errorMessage, unwrap } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ParamGroupEditor from '$lib/procedures/ParamGroupEditor.svelte';
	import PlotsEditor from '$lib/procedures/PlotsEditor.svelte';
	import PresetsPanel from '$lib/procedures/PresetsPanel.svelte';
	import RolesEditor from '$lib/procedures/RolesEditor.svelte';
	import StepOutline from '$lib/procedures/StepOutline.svelte';
	import StepInspector from '$lib/procedures/StepInspector.svelte';
	import { humanize, stepTitle } from '$lib/procedures/presentation';
	import { ProcedureEditor } from '$lib/procedures/editor.svelte';
	import {
		paramLeaves,
		pathKey,
		type Catalog,
		type CheckResult,
		type Definition,
		type Problem
	} from '$lib/procedures/model';

	let { data } = $props();

	// Built once from the loaded data; saving updates it in place rather than
	// reloading, so nothing typed is lost to a navigation.
	const editor = untrack(() =>
		data.catalog && data.definition
			? new ProcedureEditor(
					data.catalog as Catalog,
					data.definition as Definition,
					data.origin,
					data.loadedName
				)
			: null
	);

	const TABS = ['compose', 'params', 'roles', 'plots', 'details', 'yaml', 'python', 'presets'] as const;
	type Tab = (typeof TABS)[number];
	let tab = $state<Tab>(queryChoice('tab', TABS, 'compose'));
	let saving = $state(false);
	let hasBuiltin = $state(untrack(() => !!data.hasBuiltin));
	let saveMessage = $state<{ tone: 'ok' | 'crit'; text: string } | null>(null);
	let yamlText = $state('');
	let yamlError = $state<string | null>(null);
	let yamlBaseline = $state('');
	let yamlSourceJson = $state<string | null>(null);
	let yamlBusy = $state(false);
	const yamlDirty = $derived(yamlText !== yamlBaseline);
	const yamlConflict = $derived(yamlDirty && yamlSourceJson !== editor?.json);
	let presetsOpened = $state(false);
	let presetDirty = $state(false);
	let presetBusy = $state(false);

	$effect(() => {
		if (!editor) return;
		void editor.json;
		saveMessage = null;
		editor.scheduleCheck();
	});

	guardNavigation(() => !!(editor?.dirty || yamlDirty || presetDirty) && !saving, {
		title: 'Leave with unsaved changes?',
		description: 'This procedure has changes that are not saved.',
		confirmLabel: 'Leave',
		tone: 'danger'
	});

	const name = $derived(editor?.definition.name ?? '');
	const renaming = $derived(!!editor?.loadedName && editor.loadedName !== name);
	const problems = $derived(editor?.check?.problems ?? []);
	const warnings = $derived(editor?.check?.warnings ?? []);
	const canSave = $derived(
		!!editor &&
			editor.checkCurrent &&
			!!editor.check?.ok &&
			!editor.checking &&
			!saving &&
			!yamlDirty &&
			!yamlBusy &&
			!presetBusy &&
			(editor.dirty || !editor.origin || editor.origin === 'builtin')
	);

	async function save() {
		if (!editor) return;
		saving = true;
		saveMessage = null;
		const previous = editor.loadedName;
		const previousOrigin = editor.origin;
		const snapshot = $state.snapshot(editor.definition);
		const savedName = snapshot.name;
		try {
			await api.PUT('/api/procedures/{name}', { params: { path: { name: savedName } }, body: { definition: snapshot } });
			let text = `Saved to config/procedures/${savedName}.yml.`;
			if (previous && previous !== savedName && previousOrigin === 'workspace') {
				await api.DELETE('/api/procedures/{name}', { params: { path: { name: previous } } });
				text = `Renamed ${previous} to ${savedName}.`;
			}
			editor.markSaved('workspace', snapshot);
			if (previous !== savedName) {
				hasBuiltin = false;
				try {
					const detail = await unwrap<{ has_builtin: boolean }>(
						api.GET('/api/procedures/{name}', { params: { path: { name: savedName } } })
					);
					hasBuiltin = detail.has_builtin;
				} catch {
					/* Keep the revert action hidden if metadata could not be refreshed. */
				}
			}
			setQuery({ name: savedName, from: null });
			saveMessage = { tone: 'ok', text };
		} catch (e) {
			saveMessage = { tone: 'crit', text: errorMessage(e) };
		} finally {
			saving = false;
		}
	}

	async function openYaml(reload = false) {
		if (!editor || yamlBusy) return;
		tab = 'yaml';
		if (!reload && (yamlDirty || yamlSourceJson === editor.json)) return;
		if (
			reload &&
			yamlDirty &&
			!(await ask({
				title: 'Discard this YAML draft?',
				description: 'It will be reloaded from the workflow.',
				confirmLabel: 'Discard',
				tone: 'danger'
			}))
		)
			return;
		yamlBusy = true;
		yamlError = null;
		const source = editor.json;
		try {
			const out = await unwrap<{ yaml: string }>(
				api.POST('/api/procedures/to-yaml', { body: { definition: JSON.parse(source) } })
			);
			yamlText = yamlBaseline = out.yaml;
			yamlSourceJson = source;
		} catch (e) {
			yamlError = errorMessage(e);
		} finally {
			yamlBusy = false;
		}
	}

	async function applyYaml() {
		if (!editor || yamlBusy || !yamlDirty) return;
		const source = editor.json;
		if (
			yamlConflict &&
			!(await ask({
				title: 'Replace the workflow with this draft?',
				description:
					'The workflow changed after this YAML draft was created. Applying the draft discards those changes.',
				confirmLabel: 'Replace',
				tone: 'danger'
			}))
		)
			return;
		yamlBusy = true;
		yamlError = null;
		try {
			const out = await unwrap<{ definition: Definition }>(
				api.POST('/api/procedures/from-yaml', { body: { yaml: yamlText } })
			);
			const checked = await unwrap<CheckResult>(
				api.POST('/api/procedures/check', { body: { definition: out.definition } })
			);
			if (!checked.ok) throw new Error(checked.problems.map(problemLabel).join('\n'));
			if (editor.json !== source)
				throw new Error(
					'The workflow changed while this draft was being checked. Review the changes before applying again.'
				);
			editor.definition = {
				name: '',
				description: '',
				roles: {},
				params: {},
				...(out.definition as Partial<Definition>)
			} as Definition;
			editor.collapsedSteps = [];
			editor.selectStep(['body']);
			yamlBaseline = yamlText;
			yamlSourceJson = editor.json;
			tab = 'compose';
		} catch (e) {
			yamlError = errorMessage(e);
		} finally {
			yamlBusy = false;
		}
	}

	function switchTab(next: Tab) {
		if (next === 'presets') presetsOpened = true;
		if (next === 'yaml') void openYaml();
		else tab = next;
	}

	onDestroy(() => editor?.dispose());

	const tabs = $derived([
		{ value: 'compose', label: 'Workflow' },
		{ value: 'params', label: `Parameters · ${editor ? paramLeaves(editor.definition.params).length : 0}` },
		{ value: 'roles', label: `Instrument roles · ${Object.keys(editor?.definition.roles ?? {}).length}` },
		{ value: 'plots', label: `Plots · ${editor?.definition.plots?.length ?? 0}` },
		{ value: 'details', label: 'Details' },
		{ value: 'presets', label: 'Presets' },
		{ value: 'yaml', label: 'YAML' },
		{ value: 'python', label: 'Python' }
	]);

	// The tab and the selected step live in the URL, so a reload or a trip to
	// another page and Back returns to the same place.
	onMount(() => {
		const step = page.url.searchParams.get('step');
		if (editor && step)
			editor.selectStep(step.split('.').map((part) => (/^\d+$/.test(part) ? Number(part) : part)));
		if (tab !== 'compose') switchTab(tab);
	});
	$effect(() => {
		setQuery({
			tab: tab === 'compose' ? null : tab,
			step: tab === 'compose' && editor?.selectedPath ? pathKey(editor.selectedPath) : null
		});
	});

	async function revealElement(id: string) {
		await tick();
		const el = document.getElementById(id);
		el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
		const field = el?.matches('button, input, select, textarea')
			? el
			: el?.querySelector<HTMLElement>('input, select, textarea, button');
		field?.focus({ preventScroll: true });
	}

	async function reference(kind: 'params' | 'roles', name: string) {
		if (!editor) return;
		if (kind === 'params') editor.selectedParameter = name;
		else editor.selectedRole = name;
		tab = kind;
		await revealElement(`${kind === 'params' ? 'param' : 'role'}-${name}`);
	}

	async function reveal(problem: Problem) {
		if (!editor) return;
		if (problem.path[0] === 'plots' || problem.path[0] === 'derived') {
			tab = 'plots';
			await revealElement(
				problem.path[0] === 'plots' ? `plot-${problem.path[1]}` : 'derived-columns'
			);
			return;
		}
		if (problem.path[0] === 'params' || problem.path[0] === 'roles') {
			const kind = problem.path[0];
			tab = kind;
			await tick();
			for (let n = problem.path.length; n > 1; n--) {
				const id = `${kind === 'params' ? 'param' : 'role'}-${problem.path.slice(1, n).join('.')}`;
				if (document.getElementById(id)) {
					await reference(kind, problem.path.slice(1, n).join('.'));
					return;
				}
			}
			return;
		}
		const owner = editor.steps
			.filter(({ path }) => path.every((part, i) => problem.path[i] === part))
			.at(-1);
		if (owner) {
			tab = 'compose';
			editor.selectStep(owner.path);
			await revealElement(`step-${pathKey(owner.path)}`);
		} else {
			tab = 'details';
			await revealElement('procedure-details');
		}
	}

	$effect(() => {
		if (!editor?.revealVersion) return;
		untrack(() => {
			if (window.matchMedia('(max-width: 1099px)').matches)
				void tick().then(() =>
					document
						.getElementById('step-inspector')
						?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
				);
		});
	});

	function problemLabel(problem: Problem): string {
		const where = problem.path.join(' › ');
		return where ? `${where}: ${problem.message}` : problem.message;
	}

	async function revertToBuiltin() {
		if (
			!editor ||
			!(await ask({
				title: `Delete this workspace's ${name}?`,
				description: 'The built-in one will be used again.',
				confirmLabel: 'Delete',
				tone: 'danger'
			}))
		)
			return;
		await api.DELETE('/api/procedures/{name}', { params: { path: { name } } });
		editor.savedJson = editor.json; // leaving is intended
		location.href = `/procedures/edit?name=${encodeURIComponent(name)}`;
	}
</script>

{#if !editor}
	<Callout tone="crit" title="Could not open the composer. ">{data.error}</Callout>
{:else}
	<div class="procedure-editor space-y-4">
		<PageHeader
			title={editor.loadedName ? humanize(name) : 'New procedure'}
			lede={editor.definition.description ||
				'Build a workflow, then select a step to edit its settings.'}
		>
			{#snippet actions()}
				<a class="lw-btn" href="/procedures">Library</a>
				<button class="lw-btn lw-btn-primary" onclick={save} disabled={!canSave}
					>{saving
						? 'Saving…'
						: renaming
							? `Save as ${name}`
							: editor.origin === 'builtin'
								? 'Save workspace copy'
								: 'Save'}</button
				>
			{/snippet}
		</PageHeader>
		<div class="flex flex-wrap items-center gap-2 text-xs" aria-live="polite">
			{#if editor.checkError}<span class="text-crit">Could not check: {editor.checkError}</span>
			{:else if editor.checking || !editor.checkCurrent}<span class="text-muted">Checking…</span>
			{:else if editor.check?.ok}<Pill tone="ok" dot>Ready to generate</Pill>
			{:else}<Pill tone="crit" dot>{problems.length} problem{problems.length === 1 ? '' : 's'}</Pill
				>{/if}
			{#if editor.dirty}<Pill tone="warn">Unsaved changes</Pill>{/if}
			{#if yamlDirty}<button
					class="text-xs text-accent-strong underline"
					onclick={() => switchTab('yaml')}>Unapplied YAML changes</button
				>{/if}
			{#if presetDirty}<button
					class="text-xs text-accent-strong underline"
					onclick={() => switchTab('presets')}>Unsaved preset</button
				>{/if}
			{#if editor.origin === 'builtin'}<span class="text-muted"
					>Built-in procedure · saving creates a workspace copy.</span
				>{/if}
		</div>
		{#if problems.length}
			<details class="rounded border border-crit/30 bg-surface px-3 py-2" open>
				<summary class="cursor-pointer text-xs text-crit"
					>Review {problems.length} problem{problems.length === 1 ? '' : 's'}</summary
				>
				<ul class="mt-2 space-y-1">
					{#each problems as problem, i (i)}<li>
							<button
								class="text-left text-xs text-crit hover:underline"
								onclick={() => reveal(problem)}>{problemLabel(problem)}</button
							>
						</li>{/each}
				</ul>
			</details>
		{/if}
		{#if warnings.length}
			<details class="rounded border border-warn/40 bg-surface px-3 py-2">
				<summary class="cursor-pointer text-xs text-warn"
					>{warnings.length} warning{warnings.length === 1 ? '' : 's'} · generates, but check this is
					intended</summary
				>
				<ul class="mt-2 space-y-1">
					{#each warnings as warning, i (i)}<li>
							<button class="text-left text-xs text-ink-2 hover:underline" onclick={() => reveal(warning)}
								>{problemLabel(warning)}</button
							>
						</li>{/each}
				</ul>
			</details>
		{/if}
		{#if saveMessage}<Callout tone={saveMessage.tone}
				>{saveMessage.text}{#if saveMessage.tone === 'ok'}
					<a
						class="ml-1 underline"
						href="/measurements/resources?name={encodeURIComponent(name)}&kind=procedure"
						>Create a measurement</a
					>{/if}</Callout
			>{/if}
		<Tabs value={tab} onValueChange={(v) => switchTab(v as Tab)} {tabs} label="Procedure sections">
			{#if tab === 'compose'}
				<div class="editor-grid">
					<div class="min-w-0" id="workflow-outline">
						<Panel
							title="Execution order"
							description="Run from top to bottom. Values shown are parameter defaults."
							flush
						>
							<div class="p-2"><StepOutline {editor} path={['body']} /></div>
						</Panel>
						<details class="mt-3 rounded border border-line bg-surface px-3.5 py-2.5">
							<summary class="cursor-pointer text-xs text-ink-2"
								>Recorded columns · {editor.check?.records.length ?? 0}</summary
							>
							<div class="mt-2 flex flex-wrap gap-1">
								{#each editor.check?.records ?? [] as column}<span
										class="mono rounded bg-surface-2 px-1.5 py-0.5 text-xs">{column}</span
									>{:else}<p class="text-xs text-muted">
										No data columns yet. Add a measurement step to record data.
									</p>{/each}
							</div>
						</details>
					</div>
					<aside class="editor-detail">
						{#if editor.selectedPath}
							{#key editor.selectedStep}<StepInspector
									{editor}
									path={editor.selectedPath}
									onreference={reference}
								/>{/key}
						{:else}<p class="text-body text-muted">Select a step to edit its settings.</p>{/if}
					</aside>
				</div>
			{:else if tab === 'params'}
				<div class="space-y-3">
					<button class="lw-btn lw-btn-sm" onclick={() => switchTab('compose')}
						>← Back to workflow{editor.selectedStep
							? ` · ${stepTitle(editor.selectedStep)}`
							: ''}</button
					>
					<ParamGroupEditor {editor} />
				</div>
			{:else if tab === 'roles'}
				<div class="space-y-3">
					<button class="lw-btn lw-btn-sm" onclick={() => switchTab('compose')}
						>← Back to workflow{editor.selectedStep
							? ` · ${stepTitle(editor.selectedStep)}`
							: ''}</button
					>
					<RolesEditor {editor} />
				</div>
			{:else if tab === 'plots'}
				<PlotsEditor {editor} />
			{:else if tab === 'details'}
				<Panel
					title="Procedure details"
					description="Give this procedure a name and explain what it measures."
				>
					<div class="max-w-3xl space-y-4" id="procedure-details">
						<div class="space-y-4">
							<label class="block">
								<span class="lw-label">Procedure name</span>
								<input
									class="lw-input mono"
									bind:value={editor.definition.name}
									aria-label="Procedure name"
								/>
							</label>
							<label class="block">
								<span class="lw-label">Description</span>
								<textarea
									class="lw-input min-h-28 resize-y"
									bind:value={editor.definition.description}
									placeholder="What this procedure measures and when to use it"
								></textarea>
							</label>
						</div>
						{#if editor.origin === 'builtin' && !renaming}
							<Callout tone="info">
								Built into lab_wizard. Saving keeps the built-in and writes this workspace's own
								copy, which takes precedence here.
							</Callout>
						{:else if editor.origin === 'workspace' && hasBuiltin && !renaming}
							<Callout tone="info">
								This workspace's copy overrides the built-in <span class="mono">{name}</span>.
								<button class="ml-1 font-semibold underline" onclick={revertToBuiltin}
									>Revert to the built-in</button
								>
							</Callout>
						{:else if renaming && editor.origin === 'workspace'}
							<Callout tone="info"
								>Saving renames <span class="mono">{editor.loadedName}</span>. Projects already
								generated keep their copy.</Callout
							>
						{:else if renaming}
							<Callout tone="info"
								>Saving creates <span class="mono">{name}</span>; the built-in
								<span class="mono">{editor.loadedName}</span> stays.</Callout
							>
						{/if}
					</div>
				</Panel>
			{:else if tab === 'yaml'}
				<Panel
					title="YAML"
					description="Edit the complete definition, then apply your draft to the workflow before saving."
					flush
				>
					{#snippet actions()}
						<button class="lw-btn lw-btn-sm" onclick={() => openYaml(true)} disabled={yamlBusy}
							>Reload from workflow</button
						>
						<button
							class="lw-btn lw-btn-primary lw-btn-sm"
							onclick={applyYaml}
							disabled={!yamlDirty || yamlBusy}>Apply to workflow</button
						>
					{/snippet}
					<textarea
						class="editor-code"
						bind:value={yamlText}
						disabled={yamlBusy || yamlSourceJson === null}
						spellcheck="false"
						aria-label="Procedure YAML"
					></textarea>
					<p class="editor-code-status" aria-live="polite">
						{yamlBusy
							? 'Working…'
							: yamlConflict
								? 'The workflow has changed since this draft. Applying will replace those changes.'
								: yamlDirty
									? 'Draft retained when switching tabs. Apply to the workflow before saving.'
									: 'In sync with the workflow. Changes here remain a draft until applied.'}
					</p>
					{#if yamlError}
						<div class="p-3"><Callout tone="crit">{yamlError}</Callout></div>
					{/if}
				</Panel>
			{:else if tab === 'python'}
				<Panel
					title="Generated Python"
					description="Preview the module generated from the current procedure. Edit the workflow or YAML to change it."
					flush
				>
					{#if editor.checkCurrent && editor.check?.ok && editor.check.python}
						<!-- The scrollable code region must be reachable by keyboard. -->
						<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
						<pre
							class="editor-code"
							tabindex="0"
							role="region"
							aria-label="Generated Python">{editor.check.python}</pre>
					{:else}
						<p class="editor-empty">
							{editor.checking || !editor.checkCurrent
								? 'Checking the current procedure…'
								: 'Resolve the problems above to generate Python.'}
						</p>
					{/if}
				</Panel>
			{/if}
			{#if presetsOpened}
				<div hidden={tab !== 'presets'}>
					{#if editor.origin === 'workspace' || editor.origin === 'builtin'}
						<PresetsPanel
							{editor}
							name={editor.loadedName ?? name}
							ondirty={(dirty) => (presetDirty = dirty)}
							onbusy={(busy) => (presetBusy = busy)}
						/>
					{:else}
						<Panel
							title="Presets"
							description="Named params sets, offered when creating a measurement."
						>
							<p class="text-body text-muted">Save the procedure to add presets.</p>
						</Panel>
					{/if}
				</div>
			{/if}
		</Tabs>
	</div>
{/if}
