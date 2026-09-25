<script lang="ts">
	import { untrack } from 'svelte';
	import ParameterField from './ParameterField.svelte';
	import type { TreeItem, InstrumentMeta } from '$lib/types/instruments';
	import {
		nodeTitle,
		nodeAddress,
		type NodePath,
		type ParamUpdate,
		type ParamResult
	} from '$lib/instruments/model';
	let {
		node,
		path,
		meta,
		onsave,
		dirty = $bindable(false),
		busy = $bindable(false)
	}: {
		node: TreeItem;
		path: NodePath;
		meta?: InstrumentMeta;
		onsave: (update: ParamUpdate) => Promise<ParamResult>;
		dirty?: boolean;
		busy?: boolean;
	} = $props();
	let baseline = $state(untrack(() => JSON.stringify(node.fields)));
	let draft = $state<Record<string, any>>(untrack(() => JSON.parse(baseline)));
	let savedYaml = $state(untrack(() => node.yaml ?? ''));
	let errors = $state<Record<string, string>>({});
	let message = $state<{ ok: boolean; text: string } | null>(null);
	let tab = $state<'params' | 'yaml'>('params');
	let revision = $state(0);
	const readOnly = $derived(meta?.read_only_fields ?? Object.keys(node.fields));
	const editable = $derived(
		Object.entries(meta?.params_schema?.properties ?? {}).filter(
			([name]) => name !== 'children' && !readOnly.includes(name)
		)
	);
	$effect(() => {
		dirty = JSON.stringify(draft) !== baseline || Object.keys(errors).length > 0;
	});
	function discard() {
		draft = JSON.parse(baseline);
		errors = {};
		message = null;
		revision++;
	}
	async function save() {
		busy = true;
		message = null;
		try {
			const result = await onsave({
				path,
				fields: $state.snapshot(draft),
				expected_fields: JSON.parse(baseline)
			});
			baseline = JSON.stringify(result.fields);
			draft = JSON.parse(baseline);
			savedYaml = result.yaml;
			message = { ok: true, text: 'Parameters saved.' };
		} catch (e) {
			message = { ok: false, text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}
</script>

<div class="space-y-4">
	<div>
		<p class="mb-1 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
			Instrument settings
		</p>
		<h3>{nodeTitle(node)}</h3>
		<p class="mt-1 text-xs text-muted">{node.type} · {nodeAddress(node)}</p>
		<p class="mono mt-2 break-all text-[10.5px] text-muted" title="Instrument path">
			inst://{path.map((part) => part.key).join('/')}
		</p>
	</div>
	<div class="flex gap-1 border-b border-line" aria-label="Inspector view">
		<button class="inspector-tab" aria-pressed={tab === 'params'} onclick={() => (tab = 'params')}
			>Parameters{dirty ? ' •' : ''}</button
		>
		<button class="inspector-tab" aria-pressed={tab === 'yaml'} onclick={() => (tab = 'yaml')}
			>Saved YAML</button
		>
	</div>
	{#if tab === 'params'}
		<p class="text-xs text-muted">Saved configuration, applied when the instrument next opens.</p>
		{#if !meta?.params_schema}<p class="text-xs text-muted">
				This server does not expose editable parameters. Update its lab_wizard installation to
				enable editing.
			</p>{/if}
		{#if meta?.params_schema && !editable.length}<p class="text-xs text-muted">
				This instrument has no additional parameters. Use Add child to configure its modules.
			</p>{/if}
		<form
			onsubmit={(e) => {
				e.preventDefault();
				if (dirty && !busy && !Object.keys(errors).length) save();
			}}
		>
			{#if editable.length}
				<div class="editor-actions inspector-save">
					<button
						class="lw-btn lw-btn-primary"
						type="submit"
						disabled={!dirty || busy || Object.keys(errors).length > 0}
						>{busy ? 'Saving…' : 'Save changes'}</button
					>
					<button class="lw-btn" type="button" onclick={discard} disabled={!dirty || busy}
						>Discard</button
					>
				</div>
			{/if}
			<fieldset disabled={busy}>
				{#key revision}
					{#each editable as [name, schema] (name)}
						<ParameterField
							{schema}
							root={meta!.params_schema!}
							value={draft[name]}
							label={name}
							onchange={(value) => {
								draft[name] = value;
								message = null;
							}}
							onerror={(key, error) => {
								if (error) errors[key] = error;
								else delete errors[key];
							}}
						/>
					{/each}
				{/key}
			</fieldset>
		</form>
		<p class="text-[11px] text-muted">
			Names are used by projects and permission rules. Update those references if you rename an
			instrument or channel.
		</p>
		<details class="border-t border-line pt-3">
			<summary class="cursor-pointer text-xs text-muted">Identity &amp; connection</summary>
			<dl class="mt-3 space-y-2 text-xs">
				{#each readOnly.filter((name) => name in node.fields) as name}
					<div class="flex justify-between gap-3">
						<dt class="text-muted">{name.replaceAll('_', ' ')}</dt>
						<dd class="mono break-all">{String(node.fields[name])}</dd>
					</div>
				{/each}
			</dl>
			<p class="mt-3 text-[11px] text-muted">
				Address fields identify this instrument. To use another address, add an instrument there.
				Availability is managed in the configuration file.
			</p>
		</details>
	{:else}
		<p class="text-xs text-muted">
			Saved parameters for this instrument, including channel settings. Child instruments have their
			own configuration.
		</p>
		{#if dirty}<p class="text-xs text-warn">Unsaved parameter changes are not shown here.</p>{/if}
		<pre class="instrument-yaml">{savedYaml || 'YAML preview is unavailable on this server.'}</pre>
	{/if}
	{#if message}<p class="text-xs {message.ok ? 'text-ok' : 'text-crit'}" role="status">
			{message.text}
		</p>{/if}
</div>
