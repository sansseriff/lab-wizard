<script lang="ts">
	/** The lab's setups: what each experiment is, now.
	 *
	 * A setup is the bench a measurement runs on: its fields (the bias
	 * resistor, the balun, the QCL power), pictures of it, and the device
	 * mounted in it. Measurements name a setup; every run copies its fields when
	 * it starts, so editing a setup here changes what the next run records and
	 * never a run already taken (plans/setup_plan.md).
	 */
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { errorMessage } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { ask, guardNavigation } from '$lib/confirm.svelte';
	import { dataApi } from '$lib/data/api';
	import { localTime } from '$lib/data/model';
	import DevicePicker from '$lib/setups/DevicePicker.svelte';
	import FieldsEditor from '$lib/setups/FieldsEditor.svelte';
	import { setupsApi, type Setup } from '$lib/setups/api';
	import { setQuery } from '$lib/url';

	let setups = $state<Setup[]>([]);
	let recorded = $state<Record<string, string[]>>({});
	let loadError = $state('');

	// The setup being edited: an existing one by name, or a new one (null).
	let current = $state<string | null>(null);
	let creating = $state(false);
	let name = $state('');
	let fields = $state<Record<string, unknown>>({});
	let device = $state<string | null>(null);
	let notes = $state('');
	// Bumped to re-seed the field editor, which owns its tree once seeded.
	let generation = $state(0);
	let problems = $state<Record<string, string>>({});
	let saving = $state(false);
	let saveError = $state('');

	const selected = $derived(setups.find((s) => s.name === current) ?? null);
	const dirty = $derived(
		creating ||
			(selected !== null &&
				(JSON.stringify(fields) !== JSON.stringify(selected.fields) ||
					device !== selected.device ||
					(notes.trim() || null) !== (selected.notes ?? null)))
	);

	function edit(setup: Setup | null) {
		creating = setup === null;
		current = setup?.name ?? null;
		name = setup?.name ?? '';
		fields = $state.snapshot(setup?.fields ?? {});
		device = setup?.device ?? null;
		notes = setup?.notes ?? '';
		problems = {};
		saveError = '';
		generation++;
		setQuery({ name: setup?.name });
	}

	// A setup can take a while to write down; leaving the page must not lose it.
	const question = (what: string) => ({
		title: `Discard your unsaved changes to ${what}?`,
		description: 'They are not saved to the setup until you press Save.',
		confirmLabel: 'Discard',
		tone: 'danger' as const
	});
	guardNavigation(() => dirty && !saving, question('this setup'));

	function cancelNew() {
		if (setups.length) edit(setups[0]);
		else creating = false;
	}

	async function load(select: string | null) {
		loadError = '';
		try {
			setups = await setupsApi.list();
		} catch (e) {
			loadError = errorMessage(e);
			return;
		}
		const found = setups.find((s) => s.name === select) ?? setups[0] ?? null;
		edit(found);
	}

	async function loadSuggestions() {
		try {
			const { facets } = await dataApi.facets({});
			recorded = Object.fromEntries(
				facets.filter((f) => f.key.startsWith('setup.')).map((f) => [f.key, f.values.map((v) => v.value)])
			);
		} catch {
			// No runs yet: nothing to suggest.
		}
	}

	onMount(() => {
		load(untrack(() => page.url.searchParams.get('name')));
		loadSuggestions();
	});

	/** Open another setup, or a new one (null), asking first if this one has unsaved changes. */
	async function choose(setup: Setup | null) {
		if (dirty && !(await ask(question(creating ? name.trim() || 'the new setup' : (current ?? 'this setup')))))
			return;
		edit(setup);
	}

	async function save() {
		const target = (creating ? name : current ?? '').trim();
		if (!target) {
			saveError = 'A setup needs a name, like mid-ir-bench.';
			return;
		}
		if (creating && setups.some((s) => s.name === target)) {
			saveError = `There is already a setup named ${target}.`;
			return;
		}
		saving = true;
		saveError = '';
		try {
			await setupsApi.save(target, { fields, device, notes: notes.trim() || null });
			await load(target);
		} catch (e) {
			saveError = errorMessage(e);
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!selected) return;
		const using = selected.projects;
		const yes = await ask({
			title: `Delete ${selected.name}?`,
			description:
				`Its ${selected.runs} run${selected.runs === 1 ? '' : 's'} keep their copies of it.` +
				(using.length ? ` ${using.join(', ')} name${using.length === 1 ? 's' : ''} it, and cannot run until given another setup.` : ''),
			confirmLabel: 'Delete',
			tone: 'danger'
		});
		if (!yes) return;
		try {
			await setupsApi.remove(selected.name);
			await load(null);
		} catch (e) {
			saveError = errorMessage(e);
		}
	}

	const hasProblems = $derived(Object.keys(problems).length > 0);
	const runsHref = (setupName: string) =>
		`/data?filters=${encodeURIComponent(JSON.stringify({ setup: [setupName] }))}`;
</script>

<section class="flex h-[calc(100dvh-46px-2.5rem)] min-h-[480px] flex-col gap-4">
	<PageHeader title="Setups">
		What each experiment is: its bias resistor, its optics, pictures of how it is wired, and the device
		mounted in it. A measurement runs on a setup, and every run copies the setup's fields as they are
		when it starts, so they are filters on the Data page and a change here never alters a past run.
		{#snippet actions()}
			<button class="lw-btn lw-btn-primary" onclick={() => choose(null)} disabled={creating}>New setup</button>
		{/snippet}
	</PageHeader>

	{#if loadError}<Callout tone="crit">{loadError}</Callout>{/if}

	<div class="grid min-h-0 flex-1 grid-cols-[15rem_minmax(0,1fr)] gap-4">
		<nav class="min-h-0 rounded border border-line bg-surface" aria-label="Setups">
			<ScrollArea class="h-full" viewportClasses="p-1.5">
				<ul class="space-y-0.5">
					{#each setups as setup (setup.name)}
						<li>
							<button
								class="w-full rounded px-2.5 py-1.5 text-left {current === setup.name && !creating
									? 'bg-accent-wash text-accent-strong'
									: 'hover:bg-surface-2'}"
								onclick={() => choose(setup)}
							>
								<span class="block truncate text-body font-medium">{setup.name}</span>
								<span class="block truncate text-fine text-muted">
									{setup.device ?? 'no device'} · {setup.runs} run{setup.runs === 1 ? '' : 's'}
								</span>
							</button>
						</li>
					{/each}
					{#if creating}
						<li class="rounded bg-accent-wash px-2.5 py-1.5 text-body text-accent-strong">
							{name.trim() || 'New setup'}
						</li>
					{/if}
				</ul>
				{#if !setups.length && !creating}
					<p class="p-2 text-fine text-muted">No setups yet.</p>
				{/if}
			</ScrollArea>
		</nav>

		<div class="flex min-h-0 flex-col rounded border border-line bg-surface">
			{#if creating || selected}
				<ScrollArea class="min-h-0 flex-1" viewportClasses="p-4">
					{#key generation}
						<div class="max-w-3xl space-y-5">
							{#if creating}
								<div>
									<label class="lw-label" for="setup-name">Name</label>
									<input id="setup-name" class="lw-input mono" placeholder="mid-ir-bench" bind:value={name} />
								</div>
							{:else if selected}
								<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
									<h2 class="text-title font-semibold">{selected.name}</h2>
									<span class="text-fine text-muted">
										{#if selected.runs}
											<a class="text-accent hover:underline" href={runsHref(selected.name)}
												>{selected.runs} run{selected.runs === 1 ? '' : 's'}</a
											>, the last {localTime(selected.last_run ?? '')}
										{:else}No runs yet{/if}
										{#if selected.projects.length}
											· named by {selected.projects.join(', ')}
										{/if}
									</span>
								</div>
							{/if}

							<div>
								<label class="lw-label" for="setup-device">Mounted device</label>
								<DevicePicker id="setup-device" value={device} onchange={(d) => (device = d)} />
								<p class="mt-1 text-fine text-muted">Each run on this setup records it as the device under test.</p>
							</div>

							<div class="space-y-2">
								<h3 class="text-2xs font-semibold uppercase tracking-[0.09em] text-muted">Fields</h3>
								<FieldsEditor
									value={fields}
									{recorded}
									onchange={(v) => (fields = v)}
									onproblem={(key, message) => {
										if (message) problems[key] = message;
										else delete problems[key];
									}}
								/>
							</div>

							<div>
								<label class="lw-label" for="setup-notes">Notes</label>
								<textarea id="setup-notes" class="lw-input" rows="3" bind:value={notes}></textarea>
							</div>
						</div>
					{/key}
				</ScrollArea>
				<div class="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2">
					<button class="lw-btn lw-btn-primary lw-btn-sm" onclick={save} disabled={!dirty || hasProblems || saving}>
						{creating ? 'Create setup' : 'Save'}
					</button>
					{#if dirty}
						<button class="lw-btn lw-btn-sm" onclick={() => (creating ? cancelNew() : edit(selected))} disabled={saving}
							>{creating ? 'Cancel' : 'Discard'}</button
						>
					{/if}
					{#if saveError}
						<span class="text-fine text-crit" role="alert">{saveError}</span>
					{:else if hasProblems}
						<span class="text-fine text-crit">Fix the fields marked in red.</span>
					{:else if !dirty}
						<span class="text-fine text-muted">Saved. The next run on it copies these.</span>
					{/if}
					{#if selected && !creating}
						<button class="lw-btn lw-btn-sm ml-auto" onclick={remove}>Delete</button>
					{/if}
				</div>
			{:else}
				<div class="p-6 text-body text-muted">
					A setup holds what a run's data means next to: which resistor, which optics, what is mounted.
					<button class="text-accent hover:underline" onclick={() => edit(null)}>Create the first one</button>.
				</div>
			{/if}
		</div>
	</div>
</section>
