<script lang="ts">
	/** Run a project: set it up, run it, watch it.
	 *
	 * One page, because setting up and watching are one loop — change a param,
	 * run, look, change it again. Left, what the next run will be (the setup it
	 * runs on, params, and outputs), as fields or as the project's YAML. Right,
	 * the run: its plots and its timeline, live while it goes, and the project's
	 * last run when nothing is going.
	 *
	 * A run is the project's own setup file started as its own process
	 * (backend/launcher.py), so it is exactly the run a terminal would start; the
	 * live view reads it from the lab database as it records (backend/live.py).
	 */
	import '$lib/procedures/composer.css';
	import { onDestroy, untrack } from 'svelte';
	import { guardNavigation } from '$lib/confirm.svelte';
	import DevicePicker from '$lib/setups/DevicePicker.svelte';
	import { fly } from 'svelte/transition';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, errorMessage } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import Combobox from '$lib/components/Combobox.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import Select from '$lib/components/Select.svelte';
	import Splitter from '$lib/components/Splitter.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import { dataApi } from '$lib/data/api';
	import LiveRunView from '$lib/live/LiveRunView.svelte';
	import SchemaField from '$lib/run/SchemaField.svelte';
	import { runApi, type LaunchStatus, type Outputs, type ProjectSettings, type RunDetails, type SetupBinding } from '$lib/run/api';
	import { pathKey } from '$lib/run/schema';
	import NeedBindings from '$lib/setups/NeedBindings.svelte';
	import { setupsApi, type Setup } from '$lib/setups/api';
	import { isImage, isImages, leaves, sameNameBindings, withField } from '$lib/setups/model';

	// Width of the settings column; the run takes the rest.
	let setupWidth = $state(528);
	let pageWidth = $state(0);

	const project = $derived(page.url.searchParams.get('project') ?? '');

	// ---- the project's settings, and the draft the form edits ----
	let settings = $state<ProjectSettings | null>(null);
	let loadError = $state('');
	let run = $state<RunDetails>({ operator: null, notes: null });
	let binding = $state<SetupBinding>({ name: null, needs: {} });
	let params = $state<Record<string, unknown>>({});
	let outputs = $state<Outputs>({ files: true, live_plot: 'none', plot: '' });
	let yamlText = $state('');
	let mode = $state<'form' | 'yaml'>('form');
	// Bumped on every load, so the editors re-seed from what was saved.
	let generation = $state(0);

	let clientProblems = $state<Record<string, string>>({});
	let serverProblems = $state<Record<string, string>>({});
	let saveMessage = $state('');
	let saving = $state(false);

	const hasClientProblems = $derived(Object.keys(clientProblems).length > 0);
	const dirty = $derived.by(() => {
		if (!settings) return false;
		if (mode === 'yaml') return yamlText !== settings.yaml;
		return (
			setupDirty ||
			JSON.stringify(run) !== JSON.stringify(settings.run) ||
			JSON.stringify(binding) !== JSON.stringify(settings.setup) ||
			JSON.stringify(params) !== JSON.stringify(settings.params) ||
			JSON.stringify(outputs) !== JSON.stringify(settings.outputs)
		);
	});

	// ---- the setup: the lab's, edited here as a draft and saved with the project ----
	let setups = $state<Setup[]>([]);
	// The chosen setup's fields and mounted device as edited here; saving writes
	// them to the setup, because the bench changed, not just this project.
	let setupDraft = $state<{ fields: Record<string, unknown>; device: string | null } | null>(null);
	const savedSetup = $derived(setups.find((s) => s.name === binding.name) ?? null);
	const setupDirty = $derived(
		savedSetup !== null &&
			setupDraft !== null &&
			(JSON.stringify(setupDraft.fields) !== JSON.stringify(savedSetup.fields) || setupDraft.device !== savedSetup.device)
	);
	const needs = $derived(settings?.needs ?? {});
	const boundPaths = $derived(new Set(Object.values(binding.needs)));
	const otherFields = $derived(setupDraft ? leaves(setupDraft.fields).filter((l) => !boundPaths.has(l.path)) : []);

	async function loadSetups() {
		try {
			setups = await setupsApi.list();
		} catch {
			setups = [];
		}
	}

	function draftFrom(name: string | null) {
		const setup = setups.find((s) => s.name === name);
		setupDraft = setup ? { fields: $state.snapshot(setup.fields), device: setup.device } : null;
	}

	function chooseSetup(name: string | null) {
		const setup = setups.find((s) => s.name === name);
		binding = { name, needs: setup ? sameNameBindings(needs, setup.fields) : {} };
		draftFrom(name);
	}

	function show(next: ProjectSettings) {
		settings = next;
		run = structuredClone(next.run);
		binding = structuredClone(next.setup);
		draftFrom(next.setup.name);
		params = structuredClone(next.params);
		outputs = structuredClone(next.outputs);
		yamlText = next.yaml;
		clientProblems = {};
		serverProblems = {};
		generation++;
	}

	// Unsaved settings, the setup's fields among them, are not lost to a click elsewhere.
	guardNavigation(() => dirty && !saving, {
		title: 'Discard your unsaved settings?',
		description: 'Nothing here, including changes to the setup, is saved until you press Save.',
		confirmLabel: 'Discard',
		tone: 'danger'
	});

	function problem(key: string, message: string | null) {
		if (message) clientProblems[key] = message;
		else delete clientProblems[key];
	}

	async function load() {
		loadError = '';
		try {
			show(await runApi.settings(project));
		} catch (e) {
			loadError = errorMessage(e);
		}
	}

	async function save(): Promise<boolean> {
		saving = true;
		saveMessage = '';
		serverProblems = {};
		try {
			if (mode === 'form' && setupDirty && savedSetup && setupDraft) {
				const saved = await setupsApi.save(savedSetup.name, { ...setupDraft, notes: savedSetup.notes });
				setups = setups.map((s) => (s.name === saved.name ? saved : s));
			}
			show(await runApi.save(project, mode === 'yaml' ? { yaml: yamlText } : { run, setup: binding, params, outputs }));
			return true;
		} catch (e) {
			saveMessage = errorMessage(e);
			if (e instanceof ApiError) {
				serverProblems = Object.fromEntries(e.problems.map((p) => [pathKey(p.path), p.message]));
			}
			return false;
		} finally {
			saving = false;
		}
	}

	// ---- switching between fields and YAML: always through a save, so the two never disagree ----
	async function switchMode(next: string) {
		if (next === mode) return;
		if (dirty && !(await save())) return;
		mode = next as 'form' | 'yaml';
	}

	// ---- suggestions: what this lab has already recorded ----
	let operators = $state<string[]>([]);

	async function loadSuggestions() {
		try {
			const { facets } = await dataApi.facets({});
			const values = (key: string) => facets.find((f) => f.key === key)?.values.map((v) => v.value) ?? [];
			operators = values('operator');
		} catch {
			// No runs yet: nothing to suggest.
		}
	}

	// ---- running ----
	const IDLE: LaunchStatus = {
		state: 'idle',
		launch_id: null,
		pid: null,
		started_at: null,
		run_id: null,
		exit_code: null,
		log: null,
		log_file: null
	};
	let status = $state<LaunchStatus>(IDLE);
	let lastRunId = $state<number | null>(null);
	let runError = $state('');
	let timer: ReturnType<typeof setTimeout> | undefined;

	const active = $derived(status.state === 'starting' || status.state === 'running');
	const shownRun = $derived(status.run_id ?? lastRunId);

	async function poll() {
		clearTimeout(timer);
		try {
			const before = status.state;
			status = await runApi.status(project);
			if ((before === 'starting' || before === 'running') && status.state === 'ended') loadSuggestions();
		} catch {
			// The next poll tries again.
		}
		timer = setTimeout(poll, active ? 700 : 4000);
	}

	async function findLastRun() {
		try {
			const { runs } = await dataApi.runs({ project: [project] }, 1, 1);
			lastRunId = runs[0]?.id ?? null;
		} catch {
			lastRunId = null;
		}
	}

	async function start() {
		runError = '';
		if (dirty && !(await save())) {
			runError = 'Fix the settings before running.';
			return;
		}
		try {
			status = await runApi.launch(project);
			poll();
		} catch (e) {
			runError = errorMessage(e);
		}
	}

	// Stopping unlocks the settings at once rather than when the run has finished
	// making its instruments safe: the dying run read them long ago, and they are
	// only the next run's. A run that is still going after STOP_GRACE_MS locks
	// them again.
	const STOP_GRACE_MS = 10_000;
	let stopping = $state(false);
	let stopTimer: ReturnType<typeof setTimeout> | undefined;
	const locked = $derived(active && !stopping);

	async function stop() {
		if (!active || stopping) return;
		stopping = true;
		hinting = false;
		clearTimeout(stopTimer);
		stopTimer = setTimeout(() => {
			if (!active) return;
			stopping = false;
			runError = 'The run has not stopped yet, so its settings are locked again until it ends.';
		}, STOP_GRACE_MS);
		try {
			status = await runApi.stop(project);
		} catch (e) {
			clearTimeout(stopTimer);
			stopping = false;
			runError = errorMessage(e);
		}
	}

	// The run ending is what stopping waited for.
	$effect(() => {
		if (!active)
			untrack(() => {
				clearTimeout(stopTimer);
				stopping = false;
				hinting = false;
			});
	});

	// ---- the locked settings: one click says how to unlock them, a double click does ----
	let hinting = $state(false);
	let hintTimer: ReturnType<typeof setTimeout> | undefined;

	function hintLocked() {
		if (!locked) return;
		hinting = true;
		clearTimeout(hintTimer);
		hintTimer = setTimeout(() => (hinting = false), 2500);
	}

	// Listened for directly: Svelte's delegated onclick skips a disabled element.
	function lockedClicks(node: HTMLElement) {
		node.addEventListener('click', hintLocked);
		node.addEventListener('dblclick', stop);
		return () => {
			node.removeEventListener('click', hintLocked);
			node.removeEventListener('dblclick', stop);
		};
	}

	// Keyed on the project, not on mount: moving between projects stays on this
	// route, so SvelteKit keeps the component and only the query changes.
	$effect(() => {
		const name = project;
		untrack(() => open(name));
	});
	onDestroy(() => {
		clearTimeout(timer);
		clearTimeout(stopTimer);
		clearTimeout(hintTimer);
	});

	async function open(name: string) {
		clearTimeout(timer);
		settings = null;
		loadError = '';
		runError = '';
		status = IDLE;
		lastRunId = null;
		// Projects are chosen on the Measurements page.
		if (!name) {
			goto('/measurements', { replaceState: true });
			return;
		}
		// The setups first: showing the settings drafts the chosen one.
		await loadSetups();
		await Promise.all([load(), findLastRun(), loadSuggestions()]);
		if (name === project) poll();
	}

	const stateTone = { idle: 'neutral', starting: 'accent', running: 'accent', ended: 'neutral' } as const;
	const stateLabel = $derived(
		status.state === 'starting'
			? 'starting — claiming instruments'
			: status.state === 'ended'
				? status.exit_code === 0
					? 'finished'
					: `ended (exit ${status.exit_code ?? '?'})`
				: status.state
	);
	const schema = $derived(settings?.params_schema ?? null);
	const defs = $derived(schema?.$defs ?? {});
</script>

{#if loadError}
	<Callout tone="crit">{loadError} <a class="underline" href="/measurements">Choose another project →</a></Callout>
{:else if settings}
	<section class="run-page flex h-[calc(100dvh-46px-2.5rem)] min-h-[560px] flex-col gap-3">
		<PageHeader title={settings.name}>
			<span class="mono" title={settings.path}>{settings.measurement}</span>
			<span class="text-muted">· {settings.kind === 'custom' ? 'custom measurement' : 'procedure'}</span>
			{#if settings.style === 'embedded'}<span class="text-muted">· embedded</span>{/if}
			{#snippet actions()}
				<Pill tone={stateTone[status.state]} dot={active}>{stateLabel}</Pill>
				{#if active}
					<button class="lw-btn" onclick={stop} disabled={stopping}>{stopping ? 'Stopping…' : 'Stop'}</button>
				{:else}
					<button
						class="lw-btn lw-btn-primary"
						onclick={start}
						disabled={hasClientProblems || saving}
						title={dirty ? 'Saves the settings, then runs' : 'Runs the project’s setup file'}
					>
						{dirty ? 'Save and run' : 'Run'}
					</button>
				{/if}
			{/snippet}
		</PageHeader>

		{#if runError}<Callout tone="crit">{runError}</Callout>{/if}

		<div
			class="grid min-h-0 flex-1"
			style:grid-template-columns="{setupWidth}px 1rem minmax(0,1fr)"
			bind:clientWidth={pageWidth}
		>
			{#if settings.style === 'embedded'}
			<!-- An embedded project reads nothing but its setup file; there is nothing here to edit. -->
			<section class="rounded border border-line bg-surface p-3" aria-label="Setup">
				<Callout tone="info" title="Its settings are in its setup file.">
					This project is embedded: the instruments' settings, the params and the run's details are
					all written in <span class="mono">{settings.setup_file}</span>, and nothing else is read when
					it runs. Edit them there; Run starts that file.
				</Callout>
			</section>
			{:else}
			<!-- What the next run will be. Locked while one is going: it read these when it started. -->
			<section class="flex min-h-0 flex-col rounded border border-line bg-surface" aria-label="Setup">
				<Tabs
					value={mode}
					onValueChange={switchMode}
					tabs={[
						{ value: 'form', label: 'Settings' },
						{ value: 'yaml', label: 'YAML' }
					]}
					label="Setup"
					size="sm"
					class="flex min-h-0 flex-1 flex-col"
					panelClass="flex min-h-0 flex-1 flex-col"
				>
					<fieldset
						class="setup-fields relative flex min-h-0 min-w-0 flex-1 flex-col"
						disabled={locked && mode === 'form'}
						{@attach lockedClicks}
					>
						{#if hinting}
							<div
								class="locked-notice"
								role="status"
								transition:fly={{ y: -8, duration: 140 }}
							>
								Double-click to abort the run and edit.
							</div>
						{/if}
						{#if mode === 'yaml'}
							<!-- The composer's YAML view: the file itself, edited in place. -->
							<textarea
								class="editor-code min-h-0 flex-1"
								spellcheck="false"
								bind:value={yamlText}
								readonly={locked}
								aria-label="Project YAML"
							></textarea>
							<p class="editor-code-status">
								The whole project file. Save checks it against the project before writing anything.
							</p>
						{:else}
						<ScrollArea class="min-h-0 flex-1" viewportClasses="p-3">
							{#key generation}
								<div class="space-y-5">
									<div class="space-y-2">
										<h3 class="text-2xs font-semibold uppercase tracking-[0.09em] text-muted">Setup</h3>
										<div>
											<label class="lw-label" for="run-setup">The setup it runs on</label>
											<Combobox
												id="run-setup"
												mono
												value={binding.name}
												options={setups.map((s) => ({ value: s.name, label: s.name, hint: s.device ?? undefined }))}
												onValueChange={chooseSetup}
												noneLabel="No setup"
											>
												{#snippet empty(search)}
													No setup named “{search}”. Create it on the
													<a class="underline" href="/setups">Setups page</a>.
												{/snippet}
											</Combobox>
										</div>
										{#if serverProblems['setup.name']}<p class="text-fine text-crit">{serverProblems['setup.name']}</p>{/if}
										{#if setupDraft}
											<div>
												<label class="lw-label" for="run-device">Mounted device</label>
												<DevicePicker
													id="run-device"
													value={setupDraft.device}
													onchange={(d) => setupDraft && (setupDraft.device = d)}
												/>
											</div>
										{/if}
										{#if Object.keys(needs).length}
											<p class="text-fine text-muted">
												What {settings.measurement} reads from the setup to draw its plots. Changing a value here
												changes the setup, for every measurement on it.
											</p>
											<NeedBindings
												{needs}
												fields={setupDraft?.fields ?? null}
												bindings={binding.needs}
												editable
												onchange={(b) => (binding = { ...binding, needs: b })}
												onsetfield={(path, value) => {
													if (setupDraft) setupDraft.fields = withField(setupDraft.fields, path, value);
												}}
											/>
										{/if}
										{#if setupDraft && otherFields.length}
											<details class="rounded border border-line px-2.5 py-1.5">
												<summary class="cursor-pointer text-xs text-ink-2">
													Its other fields ({otherFields.length}), recorded with every run
												</summary>
												<dl class="mt-1.5 grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 gap-y-0.5 text-xs">
													{#each otherFields as leaf (leaf.path)}
														<dt class="mono text-muted">{leaf.path}</dt>
														<dd class="mono truncate">{isImage(leaf.value) || isImages(leaf.value) ? 'picture' : leaf.shown}</dd>
													{/each}
												</dl>
												<a class="mt-1 inline-block text-fine text-accent hover:underline" href="/setups?name={encodeURIComponent(binding.name ?? '')}"
													>Edit on the Setups page →</a
												>
											</details>
										{/if}
										{#if setupDirty}
											<p class="text-fine text-warn">Saving also saves {binding.name}: the setup changed, not only this project.</p>
										{/if}
									</div>

									<div class="space-y-2">
										<h3 class="text-2xs font-semibold uppercase tracking-[0.09em] text-muted">This run</h3>
										<div>
											<label class="lw-label" for="run-operator">Operator</label>
											<input id="run-operator" class="lw-input" list="known-operators" bind:value={() => run.operator ?? '', (v) => (run.operator = v.trim() ? v : null)} />
										</div>
										<datalist id="known-operators">{#each operators as o (o)}<option value={o}></option>{/each}</datalist>
										<div>
											<label class="lw-label" for="run-notes">Notes</label>
											<textarea id="run-notes" class="lw-input" rows="2" bind:value={() => run.notes ?? '', (v) => (run.notes = v.trim() ? v : null)}></textarea>
										</div>
									</div>

									<div class="space-y-2">
										<h3 class="text-2xs font-semibold uppercase tracking-[0.09em] text-muted">Parameters</h3>
										{#if schema}
											{#each Object.entries(schema.properties ?? {}) as [name, fieldSchema] (name)}
												<SchemaField
													{name}
													schema={fieldSchema}
													{defs}
													value={params[name]}
													path={['measurement', 'params', name]}
													onchange={(v) => (params = { ...params, [name]: v })}
													onproblem={problem}
													problems={serverProblems}
												/>
											{/each}
										{:else}
											<p class="text-fine text-muted">
												This project's params model could not be read, so they can only be edited in the YAML tab.
											</p>
										{/if}
									</div>

									<div class="space-y-2">
										<h3 class="text-2xs font-semibold uppercase tracking-[0.09em] text-muted">Outputs</h3>
										<label class="flex items-center gap-2 text-body">
											<input type="checkbox" bind:checked={outputs.files} />
											Also save each run as files
										</label>
										<label class="lw-label mt-2" for="live-plot">Live plot when run from a terminal</label>
										<Select
											id="live-plot"
											bind:value={outputs.live_plot}
											options={[
												{ value: 'web', label: 'Web page (a window here, or a link over SSH)' },
												{ value: 'window', label: 'Window (matplotlib)' },
												{ value: 'none', label: 'None' }
											]}
										/>
										<p class="text-fine text-muted">Runs started here are always shown on this page.</p>
									</div>
								</div>
							{/key}
						</ScrollArea>
						{/if}
					</fieldset>
					<div class="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2">
						<button class="lw-btn lw-btn-sm" onclick={save} disabled={!dirty || hasClientProblems || saving || locked}>Save</button>
						{#if dirty}
							<button class="lw-btn lw-btn-sm" onclick={() => settings && show(settings)} disabled={saving}>Discard</button>
						{/if}
						{#if locked}
							<span class="min-w-0 flex-1 basis-0 truncate text-fine text-muted" title="The run read these when it started. Double-click them to abort it and edit.">Locked while it runs · double-click to edit</span>
						{:else if hasClientProblems}
							<span class="text-fine text-crit">Fix the fields marked in red.</span>
						{:else if saveMessage}
							<span class="text-fine text-crit" role="alert">{saveMessage}</span>
						{:else if !dirty}
							<span class="text-fine text-muted">Saved. The next run reads these.</span>
						{/if}
					</div>
				</Tabs>
			</section>
			{/if}

			<Splitter
				bind:size={setupWidth}
				initial={528}
				min={320}
				max={pageWidth - 400}
				storageKey="lw.run.setupWidth"
				label="Resize the settings panel"
			/>

			<!-- The run: live while it goes, the last one otherwise. -->
			<section class="flex min-h-0 flex-col gap-2" aria-label="Run">
				{#if shownRun}
					{#key shownRun}
						<LiveRunView runId={shownRun} plot={outputs.plot} />
					{/key}
				{:else if status.state === 'starting'}
					<p class="rounded border border-line bg-surface p-4 text-body text-muted">
						Starting: claiming and connecting the instruments. The plots and timeline appear with the run.
					</p>
				{:else}
					<p class="rounded border border-line bg-surface p-4 text-body text-muted">
						This project has not been run yet. Press Run.
					</p>
				{/if}
				{#if status.log && (active || (status.state === 'ended' && status.exit_code !== 0))}
					<details class="rounded border border-line bg-surface" open={status.state === 'ended'}>
						<summary class="cursor-pointer px-3 py-1.5 text-xs text-ink-2">Output of the run process</summary>
						<pre class="mono max-h-48 overflow-auto whitespace-pre-wrap px-3 pb-2 text-fine">{status.log}</pre>
					</details>
				{/if}
			</section>
		</div>
	</section>
{/if}

<style>
	/* Disabled controls swallow pointer events in WebKit, so a click on one would
	   never reach the fieldset; let it fall through to the field around it. The
	   YAML is read-only instead of disabled, so it still scrolls and still hears
	   the click. */
	.setup-fields:disabled :global(:is(input, textarea, select, button)) {
		pointer-events: none;
	}
	.setup-fields:disabled {
		cursor: default;
	}
	.setup-fields textarea[readonly] {
		opacity: 0.6;
	}
	.locked-notice {
		position: absolute;
		top: 0.5rem;
		left: 50%;
		translate: -50% 0;
		z-index: 10;
		white-space: nowrap;
		border: 1px solid var(--color-line);
		border-radius: 0.375rem;
		background: var(--color-surface);
		box-shadow: 0 4px 14px rgb(0 0 0 / 0.12);
		padding: 0.375rem 0.75rem;
		font-size: 0.75rem;
		color: var(--color-ink);
		pointer-events: none;
	}
</style>
