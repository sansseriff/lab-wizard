<script lang="ts">
	/** Named params sets for a saved procedure — "the lab's standard sweep".
	 *
	 * A preset is copied into a project when the project is generated and never
	 * read again, so editing one changes no existing project. Presets are checked
	 * against the *saved* definition's params, which is why this panel waits for
	 * unsaved param changes to be saved.
	 */
	import { onMount } from 'svelte';
	import { fetchWithConfig } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import ParamValueInput from './ParamValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { paramLeaves } from './model';

	let { editor, name }: { editor: ProcedureEditor; name: string } = $props();

	type PresetData = { defaults: Record<string, any>; presets: Record<string, Record<string, any>>; errors: Record<string, string> };

	let data = $state<PresetData | null>(null);
	let loadError = $state<string | null>(null);
	let selected = $state<string | null>(null);
	let editing = $state(false);
	let draftName = $state('');
	let draft = $state<Record<string, any>>({});
	let message = $state<{ tone: 'ok' | 'crit'; text: string } | null>(null);
	let busy = $state(false);

	const leaves = $derived(paramLeaves(editor.definition.params));
	const url = $derived(`/api/procedures/${encodeURIComponent(name)}/presets`);

	async function load() {
		try {
			data = await fetchWithConfig<PresetData>(url, 'GET');
			loadError = null;
		} catch (e) {
			loadError = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(load);

	function getDotted(obj: any, dotted: string): unknown {
		return dotted.split('.').reduce((node, part) => (node == null ? undefined : node[part]), obj);
	}

	function setDotted(obj: any, dotted: string, value: unknown) {
		const parts = dotted.split('.');
		let node = obj;
		for (const part of parts.slice(0, -1)) node = node[part] ??= {};
		node[parts[parts.length - 1]] = value;
	}

	function open(preset: string | null) {
		message = null;
		selected = preset;
		editing = true;
		draftName = preset ?? '';
		draft = $state.snapshot(preset && data ? data.presets[preset] : (data?.defaults ?? {}));
	}

	async function save() {
		const target = draftName.trim();
		if (!target) return;
		busy = true;
		try {
			await fetchWithConfig(`${url}/${encodeURIComponent(target)}`, 'PUT', { values: $state.snapshot(draft) });
			message = { tone: 'ok', text: `Saved preset ${target}.` };
			await load();
			selected = target;
		} catch (e) {
			message = { tone: 'crit', text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}

	async function remove(preset: string) {
		busy = true;
		try {
			await fetchWithConfig(`${url}/${encodeURIComponent(preset)}`, 'DELETE');
			if (selected === preset) {
				selected = null;
				editing = false;
			}
			await load();
		} catch (e) {
			message = { tone: 'crit', text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}
</script>

<div class="space-y-3">
	{#if editor.dirty}
		<Callout tone="warn">Save the procedure first — presets are checked against its saved params.</Callout>
	{/if}
	{#if loadError}
		<Callout tone="crit">{loadError}</Callout>
	{/if}

	{#if data}
		<div class="flex flex-wrap items-center gap-1.5">
			{#each Object.keys(data.presets) as preset (preset)}
				<button
					class="lw-btn lw-btn-sm {selected === preset ? 'border-accent text-accent-strong' : ''}"
					onclick={() => open(preset)}>{preset}</button
				>
			{/each}
			{#each Object.entries(data.errors) as [preset, error] (preset)}
				<span class="text-[11.5px] text-crit" title={error}>{preset} (no longer fits the params)</span>
				<button class="lw-btn lw-btn-sm" onclick={() => remove(preset)} disabled={busy}>Delete {preset}</button>
			{/each}
			<button class="lw-btn lw-btn-sm" onclick={() => open(null)} disabled={editor.dirty}>New preset</button>
		</div>

		{#if editing}
			<div class="space-y-1.5 rounded border border-line p-2.5">
				<div class="flex items-center gap-2">
					<span class="lw-label mb-0">Preset name</span>
					<input class="lw-input mono w-48" bind:value={draftName} placeholder="standard" aria-label="Preset name" />
				</div>
				{#each leaves as leaf (leaf.name)}
					<div class="grid grid-cols-[10rem_minmax(0,1fr)] items-center gap-2">
						<span class="mono truncate text-[11.5px] text-ink-2" title={leaf.decl.description}>
							{leaf.name}{leaf.decl.unit ? ` (${leaf.decl.unit})` : ''}
						</span>
						<ParamValueInput
							type={leaf.decl.type}
							value={getDotted(draft, leaf.name)}
							label={leaf.name}
							onchange={(v) => setDotted(draft, leaf.name, v)}
						/>
					</div>
				{/each}
				{#if !leaves.length}
					<p class="text-xs text-muted">This procedure has no params, so a preset would be empty.</p>
				{/if}
				<div class="flex gap-1.5 pt-1">
					<button class="lw-btn lw-btn-primary lw-btn-sm" onclick={save} disabled={busy || editor.dirty || !draftName.trim()}>
						Save preset
					</button>
					{#if selected}
						<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={() => remove(selected!)} disabled={busy}>
							Delete
						</button>
					{/if}
				</div>
			</div>
		{/if}
	{/if}

	{#if message}
		<Callout tone={message.tone}>{message.text}</Callout>
	{/if}
</div>
