<script lang="ts">
	import { untrack } from 'svelte';
	import { fetchWithConfig } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import ParamValueInput from './ParamValueInput.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { paramLeaves } from './model';

	let {
		editor,
		name,
		ondirty,
		onbusy
	}: {
		editor: ProcedureEditor;
		name: string;
		ondirty: (dirty: boolean) => void;
		onbusy: (busy: boolean) => void;
	} = $props();
	type PresetData = {
		defaults: Record<string, any>;
		presets: Record<string, Record<string, any>>;
		errors: Record<string, string>;
	};
	let data = $state<PresetData | null>(null);
	let loadError = $state<string | null>(null);
	let selected = $state<string | null>(null);
	let editing = $state(false);
	let draftName = $state('');
	let draft = $state<Record<string, any>>({});
	let baseline = $state('');
	let draftSchema = $state('');
	let message = $state<{ tone: 'ok' | 'crit'; text: string } | null>(null);
	let busy = $state(false);
	let loading = $state(false);
	let loadVersion = 0;
	const dirty = $derived(editing && JSON.stringify([draftName, draft]) !== baseline);
	const schemaChanged = $derived(editing && draftSchema !== editor.savedJson);
	const leaves = $derived(
		paramLeaves(JSON.parse(editing ? draftSchema : editor.savedJson).params ?? {})
	);
	const url = $derived(`/api/procedures/${encodeURIComponent(name)}/presets`);
	$effect(() => ondirty(dirty));
	$effect(() => onbusy(busy));
	$effect(() => {
		void url;
		void editor.savedJson;
		untrack(() => {
			if (!dirty) editing = false;
			void load();
		});
	});

	async function load() {
		const version = ++loadVersion;
		loading = true;
		try {
			const result = await fetchWithConfig<PresetData>(url, 'GET');
			if (version === loadVersion) {
				data = result;
				loadError = null;
			}
		} catch (e) {
			if (version === loadVersion) loadError = e instanceof Error ? e.message : String(e);
		} finally {
			if (version === loadVersion) loading = false;
		}
	}
	function getDotted(obj: any, dotted: string): unknown {
		return dotted.split('.').reduce((node, part) => node?.[part], obj);
	}
	function setDotted(obj: any, dotted: string, value: unknown) {
		const parts = dotted.split('.');
		let node = obj;
		for (const part of parts.slice(0, -1)) node = node[part] ??= {};
		node[parts[parts.length - 1]] = value;
	}
	function open(preset: string | null) {
		if (dirty && !confirm('Discard the unsaved preset draft?')) return;
		message = null;
		selected = preset;
		editing = true;
		draftName = preset ?? '';
		draft = structuredClone(
			$state.snapshot(preset && data ? data.presets[preset] : (data?.defaults ?? {}))
		);
		draftSchema = editor.savedJson;
		baseline = JSON.stringify([draftName, draft]);
	}
	function discard() {
		editing = false;
		selected = null;
		message = null;
	}
	async function save() {
		const target = draftName.trim();
		if (!target || busy || editor.dirty || schemaChanged) return;
		if (
			target !== selected &&
			(Object.hasOwn(data?.presets ?? {}, target) || Object.hasOwn(data?.errors ?? {}, target))
		) {
			message = {
				tone: 'crit',
				text: `A preset named ${target} already exists. Choose another name or open that preset to edit it.`
			};
			return;
		}
		busy = true;
		try {
			await fetchWithConfig(`${url}/${encodeURIComponent(target)}`, 'PUT', {
				values: $state.snapshot(draft)
			});
			await load();
			selected = draftName = target;
			draft = structuredClone($state.snapshot(data?.presets[target] ?? draft));
			baseline = JSON.stringify([draftName, draft]);
			message = { tone: 'ok', text: `Saved preset ${target}.` };
		} catch (e) {
			message = { tone: 'crit', text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}
	async function remove(preset: string) {
		if (!confirm(`Delete preset ${preset}? Existing measurements keep their values.`)) return;
		busy = true;
		try {
			await fetchWithConfig(`${url}/${encodeURIComponent(preset)}`, 'DELETE');
			if (selected === preset) discard();
			await load();
			message = { tone: 'ok', text: `Deleted preset ${preset}.` };
		} catch (e) {
			message = { tone: 'crit', text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}
</script>

<div class="space-y-3">
	{#if editor.dirty}<Callout tone="warn"
			>Save the procedure first. Presets use the saved parameter definitions.</Callout
		>{/if}
	{#if schemaChanged}<Callout tone="warn"
			>The saved procedure changed while this draft was open. Discard the draft and reopen a preset
			to use the current parameters.</Callout
		>{/if}
	{#if loadError}<Callout tone="crit"
			>{loadError}
			<button class="underline" onclick={load} disabled={loading}>Retry</button></Callout
		>{/if}
	{#if message}<Callout tone={message.tone}>{message.text}</Callout>{/if}
	<div class="editor-grid">
		<Panel
			title="Presets"
			description="Named parameter values to reuse when creating a measurement."
			flush
		>
			{#snippet actions()}<button
					class="lw-btn lw-btn-sm"
					onclick={() => open(null)}
					disabled={editor.dirty || busy || loading || !data}>New preset</button
				>{/snippet}
			<div class="p-2">
				{#if loading}<p class="editor-empty" role="status">Loading presets…</p>
				{:else if data}
					{#each Object.keys(data.presets) as preset (preset)}
						<button
							class="editor-row"
							aria-pressed={editing && selected === preset}
							onclick={() => open(preset)}
							disabled={busy || editor.dirty}
							><span>{preset}</span><span class="editor-row-summary"
								>{paramLeaves(JSON.parse(editor.savedJson).params).length} parameters</span
							></button
						>
					{/each}
					{#each Object.entries(data.errors) as [preset, error] (preset)}
						<div class="border-b border-line p-3 text-xs">
							<p class="font-semibold">{preset}</p>
							<p class="mt-2 break-words text-crit">{error}</p>
							<button
								class="lw-btn lw-btn-danger lw-btn-sm mt-3"
								onclick={() => remove(preset)}
								disabled={busy}>Delete {preset}</button
							>
						</div>
					{/each}
					{#if !Object.keys(data.presets).length && !Object.keys(data.errors).length}<p
							class="editor-empty"
						>
							No presets yet. Create one from the parameter defaults, then adjust the values for a
							common measurement.
						</p>{/if}
				{/if}
			</div>
		</Panel>
		<aside class="editor-detail" aria-label="Selected preset">
			{#if editing}
				<h3>{selected ? 'Preset settings' : 'New preset'}</h3>
				<p class="mt-1 text-xs text-muted">
					{dirty
						? 'Unsaved draft · retained when switching tabs.'
						: 'Changes apply to future measurements.'}
				</p>
				<fieldset disabled={busy || editor.dirty || schemaChanged}>
					<label class="editor-field"
						><span>Preset name</span><input
							class="lw-input mono"
							bind:value={draftName}
							placeholder="standard"
							aria-label="Preset name"
						/></label
					>
					{#if selected && draftName.trim() !== selected}<p class="mt-2 text-xs text-muted">
							A different name saves a copy. {selected} will be kept.
						</p>{/if}
					{#each leaves as leaf (leaf.name)}
						<div class="editor-field">
							<span>{leaf.name}{leaf.decl.unit ? ` (${leaf.decl.unit})` : ''}</span>
							<ParamValueInput
								type={leaf.decl.type}
								value={getDotted(draft, leaf.name)}
								label={leaf.name}
								onchange={(v) => setDotted(draft, leaf.name, v)}
							/>
							{#if leaf.decl.description}<p class="mt-1 text-xs text-muted">
									{leaf.decl.description}
								</p>{/if}
						</div>
					{/each}
					{#if !leaves.length}<p class="mt-3 text-xs text-muted">
							This procedure has no parameters to override.
						</p>{/if}
				</fieldset>
				<div class="editor-actions">
					<button
						class="lw-btn lw-btn-primary lw-btn-sm"
						onclick={save}
						disabled={busy ||
							loading ||
							editor.dirty ||
							schemaChanged ||
							!draftName.trim() ||
							(!dirty && !!selected)}
						>{busy
							? 'Saving…'
							: selected && draftName.trim() !== selected
								? 'Save copy'
								: 'Save preset'}</button
					>
					<button class="lw-btn lw-btn-sm" onclick={discard} disabled={busy}
						>{dirty ? 'Discard draft' : 'Close'}</button
					>
					{#if selected}<button
							class="lw-btn lw-btn-danger lw-btn-sm"
							onclick={() => remove(selected!)}
							disabled={busy}>Delete preset</button
						>{/if}
				</div>
			{:else}<p class="text-sm text-muted">
					Select a preset to edit its values, or create one from the defaults.
				</p>{/if}
		</aside>
	</div>
</div>
