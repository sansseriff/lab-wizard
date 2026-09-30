<script lang="ts">
	/** How runs are saved as files, for the whole workspace.
	 *
	 * A project only says whether its runs are saved as files too (on by
	 * default, chosen when the measurement is created). Where the folders go
	 * and how they are named is one choice for the lab, kept in the
	 * workspace's config/data.yaml and used by every project's next run.
	 */
	import { onMount } from 'svelte';
	import { errorMessage } from '$lib/api';
	import Combobox from '$lib/components/Combobox.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import {
		settingsApi,
		type FileSettings,
		type FileSettingsView,
		type TemplateCheck
	} from './api';

	let view = $state<FileSettingsView | null>(null);
	// The template as typed, checked by the backend a moment after each change.
	let check = $state<TemplateCheck | null>(null);
	let templateInput = $state<HTMLInputElement | null>(null);
	let checkTimer: ReturnType<typeof setTimeout> | undefined;
	let files = $state<FileSettings>({ root: '', path: '', plot_png: true });
	let error = $state('');
	let saving = $state(false);
	let saved = $state(false);
	// The recorded-key picker only adds; it always goes back to showing its prompt.
	let picked = $state<string | null>(null);

	const errors = $derived(check?.problems.filter((p) => p.level === 'error') ?? []);
	const warnings = $derived(check?.problems.filter((p) => p.level === 'warning') ?? []);
	// The fixed keys first, then what this lab has recorded, grouped by family.
	const fixedKeys = $derived(view?.keys.filter((k) => !k.includes('.')) ?? []);
	const recordedKeys = $derived(view?.keys.filter((k) => k.includes('.')) ?? []);

	const changed = $derived(
		!!view &&
			(files.root !== view.files.root ||
				files.path !== view.files.path ||
				files.plot_png !== view.files.plot_png)
	);

	function show(next: FileSettingsView) {
		view = next;
		files = { ...next.files };
		check = { example: next.example, problems: next.problems };
	}

	function templateChanged() {
		clearTimeout(checkTimer);
		const path = files.path;
		checkTimer = setTimeout(async () => {
			try {
				const next = await settingsApi.checkTemplate(path);
				if (path === files.path) check = next;
			} catch {
				// A failed check only leaves the last answer showing; saving checks again.
			}
		}, 200);
	}

	/** Put ``{key}`` where the cursor is, or at the end. */
	function insertKey(key: string) {
		const text = `{${key}}`;
		const input = templateInput;
		const at = input?.selectionStart ?? files.path.length;
		const to = input?.selectionEnd ?? at;
		files.path = files.path.slice(0, at) + text + files.path.slice(to);
		templateChanged();
		requestAnimationFrame(() => {
			input?.focus();
			input?.setSelectionRange(at + text.length, at + text.length);
		});
	}

	async function save() {
		error = '';
		saved = false;
		saving = true;
		try {
			show(await settingsApi.saveFiles({ ...files, root: files.root.trim(), path: files.path.trim() }));
			saved = true;
		} catch (e) {
			error = errorMessage(e);
		} finally {
			saving = false;
		}
	}

	onMount(async () => {
		try {
			show(await settingsApi.files());
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	});
</script>

<Panel
	title="Saving files"
	description="Projects with file saving on also write each run as a folder of CSV and YAML files. This is where those folders go, for every project in this workspace."
>
	<form
		id="files"
		class="max-w-2xl space-y-4"
		onsubmit={(e) => {
			e.preventDefault();
			save();
		}}
	>
		<div>
			<label class="block text-xs text-ink-2"
				>Folder template
				<input
					class="lw-input mono mt-1 w-full {errors.length ? 'border-crit' : ''}"
					bind:this={templateInput}
					bind:value={files.path}
					oninput={templateChanged}
					aria-invalid={errors.length > 0}
					required
				/>
			</label>
			{#each [...errors, ...warnings] as problem (problem.level + problem.key + problem.message)}
				<p class="mt-1 text-fine {problem.level === 'error' ? 'text-crit' : 'text-warn'}" role="alert">
					{problem.message}
				</p>
			{/each}
			<p class="mt-2 text-fine text-muted">
				Click a key to add it; <code>/</code> starts a subfolder. The same keys the Data page filters
				by. A run missing a value gets <code>none</code>; a name already taken gets <code>_2</code>.
			</p>
			<div class="mt-1.5 flex flex-wrap items-center gap-1">
				{#each fixedKeys as key (key)}
					<button type="button" class="lw-btn lw-btn-sm mono" onclick={() => insertKey(key)}
						>{key}</button
					>
				{/each}
			</div>
			{#if recordedKeys.length}
				<div class="mt-2 max-w-sm">
					<Combobox
						mono
						bind:value={picked}
						options={recordedKeys.map((k) => ({ value: k, label: k }))}
						placeholder="Add a recorded key… ({recordedKeys.length})"
						aria-label="Add a key recorded by this lab"
						onValueChange={(key) => {
							if (key) insertKey(key);
							picked = null;
						}}
					/>
				</div>
			{:else}
				<p class="mt-1 text-fine text-muted">
					Device properties (<code>device.wafer</code>), run metadata (<code>run.cryostat</code>)
					and params (<code>param.…</code>) appear here once a run records them.
				</p>
			{/if}
		</div>

		<div>
			<label class="block text-xs text-ink-2"
				>Save under
				<input
					class="lw-input mono mt-1 w-full"
					bind:value={files.root}
					placeholder="data/files (the default)"
				/>
			</label>
			<p class="mt-1 text-fine text-muted">
				Empty for this workspace's <code>data/files</code>. A relative path is relative to the
				workspace.
			</p>
		</div>

		<label class="flex items-center gap-2 text-body">
			<input type="checkbox" bind:checked={files.plot_png} />
			Also save each run's first plot as <code>plot.png</code>
		</label>

		{#if view && check}
			<div class="rounded border border-line bg-surface-2 px-3 py-2 text-fine text-muted">
				Runs go under <code class="break-all">{view.folder}</code>; the latest run would go in
				<code class="break-all">{check.example}/</code>
			</div>
		{/if}

		{#if error}<p class="text-xs text-crit" role="alert">{error}</p>{/if}
		<div class="flex items-center gap-3">
			<button class="lw-btn lw-btn-primary" disabled={saving || !changed || errors.length > 0 || !files.path.trim()}
				>Save</button
			>
			{#if changed}
				<button
					type="button"
					class="lw-btn"
					onclick={() => view && show(view)}>Discard changes</button
				>
			{:else if saved}
				<span class="text-fine text-muted">Saved. Every project's next run uses it.</span>
			{/if}
		</div>
	</form>
</Panel>
