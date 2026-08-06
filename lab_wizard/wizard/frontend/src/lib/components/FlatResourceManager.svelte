<script lang="ts">
	/** CRUD for a flat resource kind — savers and plotters.
	 *
	 * Flat because these have no hierarchy and no discovery: a type, a key you
	 * choose, and a handful of fields. That is the whole model, and the UI should
	 * not imply more structure than exists.
	 *
	 * Nothing here reports liveness. A plotter config is a URL in a YAML file;
	 * whether a Bokeh server is actually listening on it is unknown until a
	 * measurement runs, so there is no status dot to show.
	 */
	import { fetchWithConfig } from '$lib/api';
	import { onMount } from 'svelte';
	import Panel from '$lib/components/Panel.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import PlusIcon from 'phosphor-svelte/lib/Plus';

	type ResourceMeta = {
		type: string;
		class_name: string;
		module: string;
		kind: string;
		defaults: Record<string, any>;
	};

	type ResourceItem = {
		type: string;
		key: string;
		fields: Record<string, any>;
	};

	type ManagePayload = {
		tree: ResourceItem[];
		metadata: Record<string, ResourceMeta>;
	};

	let { kind, label, lede }: { kind: 'saver' | 'plotter'; label: string; lede: string } = $props();

	const apiBase = `/api/manage-${kind}s`;

	let items = $state<ResourceItem[]>([]);
	let metadata = $state<Record<string, ResourceMeta>>({});
	let loadError = $state<string | null>(null);

	// Add form state
	let showAddForm = $state(false);
	let chosenType = $state<string>('');
	let newKey = $state<string>('');
	let formFields = $state<Record<string, any>>({});
	let addError = $state<string | null>(null);

	function refresh() {
		loadError = null;
		fetchWithConfig<ManagePayload>(apiBase, 'GET')
			.then((data) => {
				items = data.tree ?? [];
				metadata = data.metadata ?? {};
			})
			.catch((err) => {
				loadError = err instanceof Error ? err.message : String(err);
			});
	}

	onMount(refresh);

	function startAdd() {
		const types = Object.keys(metadata);
		showAddForm = true;
		addError = null;
		chosenType = types[0] ?? '';
		newKey = '';
		resetFormFields();
	}

	function cancelAdd() {
		showAddForm = false;
	}

	function resetFormFields() {
		const meta = metadata[chosenType];
		if (!meta) {
			formFields = {};
			return;
		}
		const defaults = meta.defaults ?? {};
		const out: Record<string, any> = {};
		for (const [k, v] of Object.entries(defaults)) {
			if (k === 'type' || k === 'enabled' || k === 'attribute_name') continue;
			out[k] = v;
		}
		formFields = out;
	}

	$effect(() => {
		// keep formFields in sync when chosenType changes
		if (showAddForm) resetFormFields();
	});

	async function submitAdd() {
		addError = null;
		if (!chosenType) {
			addError = 'Pick a type.';
			return;
		}
		if (!newKey || !/^[A-Za-z0-9_-]+$/.test(newKey)) {
			addError = 'Key must be letters, digits, underscore or hyphen.';
			return;
		}
		try {
			await fetchWithConfig(`${apiBase}/add`, 'POST', {
				type: chosenType,
				key: newKey,
				fields: formFields
			});
			showAddForm = false;
			refresh();
		} catch (err) {
			addError = err instanceof Error ? err.message : String(err);
		}
	}

	async function resetItem(item: ResourceItem) {
		try {
			await fetchWithConfig(`${apiBase}/reset`, 'POST', { type: item.type, key: item.key });
			refresh();
		} catch (err) {
			loadError = err instanceof Error ? err.message : String(err);
		}
	}

	// A native confirm() cannot be styled and reads as a browser error rather
	// than as part of the app.
	let confirmRemove = $state<ResourceItem | null>(null);

	async function removeItem(item: ResourceItem) {
		try {
			await fetchWithConfig(`${apiBase}/remove`, 'POST', { type: item.type, key: item.key });
			confirmRemove = null;
			refresh();
		} catch (err) {
			loadError = err instanceof Error ? err.message : String(err);
			confirmRemove = null;
		}
	}

	let editingKey = $state<string | null>(null);
	let editFields = $state<Record<string, any>>({});
	let editError = $state<string | null>(null);

	function startEdit(item: ResourceItem) {
		editingKey = item.key;
		editError = null;
		const out: Record<string, any> = {};
		for (const [k, v] of Object.entries(item.fields)) {
			if (k === 'type') continue;
			out[k] = v;
		}
		editFields = out;
	}

	function cancelEdit() {
		editingKey = null;
	}

	async function saveEdit(item: ResourceItem) {
		try {
			await fetchWithConfig(`${apiBase}/update`, 'POST', {
				type: item.type,
				key: item.key,
				fields: editFields
			});
			editingKey = null;
			refresh();
		} catch (err) {
			editError = err instanceof Error ? err.message : String(err);
		}
	}

	/** Fields worth showing. `type` is the panel subtitle, and `enabled` /
	 *  `attribute_name` are plumbing the user does not set here. */
	function fieldKeys(fields: Record<string, any>): string[] {
		return Object.keys(fields).filter(
			(k) => k !== 'type' && k !== 'enabled' && k !== 'attribute_name'
		);
	}

	function display(v: any): string {
		if (typeof v === 'string') return v || '—';
		return JSON.stringify(v);
	}
</script>

<section class="space-y-4">
	<PageHeader title={label} {lede}>
		{#snippet actions()}
			{#if !showAddForm}
				<button class="lw-btn lw-btn-primary" onclick={startAdd}>
					<PlusIcon size={13} weight="bold" />
					Add {kind}
				</button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loadError}
		<Callout tone="crit">{loadError}</Callout>
	{/if}

	{#if showAddForm}
		<Panel title="New {kind}">
			<div class="grid gap-3 sm:grid-cols-2">
				<div>
					<label class="lw-label" for="new-type">Type</label>
					<select id="new-type" bind:value={chosenType} class="lw-select">
						{#each Object.keys(metadata) as t (t)}
							<option value={t}>{t}</option>
						{/each}
					</select>
				</div>
				<div>
					<label class="lw-label" for="new-key">Key — your name for this instance</label>
					<input
						id="new-key"
						type="text"
						bind:value={newKey}
						placeholder="e.g. main_db"
						class="lw-input mono"
					/>
				</div>
			</div>

			{#if Object.keys(formFields).length > 0}
				<div class="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-2">
					{#each Object.keys(formFields) as key (key)}
						<div>
							<label class="lw-label" for="new-f-{key}">{key}</label>
							{#if typeof formFields[key] === 'boolean'}
								<label class="flex items-center gap-2 text-xs">
									<input id="new-f-{key}" type="checkbox" bind:checked={formFields[key]} />
									enabled
								</label>
							{:else if typeof formFields[key] === 'number'}
								<input
									id="new-f-{key}"
									type="number"
									bind:value={formFields[key]}
									class="lw-input mono"
								/>
							{:else}
								<input id="new-f-{key}" type="text" bind:value={formFields[key]} class="lw-input mono" />
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			{#if addError}
				<div class="mt-3"><Callout tone="crit">{addError}</Callout></div>
			{/if}

			<div class="mt-4 flex gap-2">
				<button class="lw-btn lw-btn-primary" onclick={submitAdd}>Save {kind}</button>
				<button class="lw-btn" onclick={cancelAdd}>Cancel</button>
			</div>
		</Panel>
	{/if}

	{#if items.length === 0 && !showAddForm}
		<div class="rounded border border-line bg-surface px-4 py-10 text-center">
			<p class="text-[13px] font-medium">No {kind}s configured</p>
			<p class="mx-auto mt-1 max-w-[46ch] text-xs text-muted">
				Add one to make it selectable when you bind a measurement's {kind} role.
			</p>
		</div>
	{:else if items.length > 0}
		<div class="grid gap-3 md:grid-cols-2">
			{#each items as item (item.key)}
				<Panel title={item.key} description={item.type}>
					{#snippet actions()}
						{#if editingKey === item.key}
							<button class="lw-btn lw-btn-sm lw-btn-primary" onclick={() => saveEdit(item)}>
								Save
							</button>
							<button class="lw-btn lw-btn-sm" onclick={cancelEdit}>Cancel</button>
						{:else}
							<button class="lw-btn lw-btn-sm" onclick={() => startEdit(item)}>Edit</button>
							<button
								class="lw-btn lw-btn-sm"
								title="Restore this {kind}'s default field values"
								onclick={() => resetItem(item)}
							>
								Reset
							</button>
							<button class="lw-btn lw-btn-sm" onclick={() => (confirmRemove = item)}>Remove</button>
						{/if}
					{/snippet}

					{#if editingKey === item.key}
						<div class="grid gap-3">
							{#each fieldKeys(editFields) as key (key)}
								<div>
									<label class="lw-label" for="edit-{item.key}-{key}">{key}</label>
									{#if typeof editFields[key] === 'boolean'}
										<input id="edit-{item.key}-{key}" type="checkbox" bind:checked={editFields[key]} />
									{:else if typeof editFields[key] === 'number'}
										<input
											id="edit-{item.key}-{key}"
											type="number"
											bind:value={editFields[key]}
											class="lw-input mono"
										/>
									{:else}
										<input
											id="edit-{item.key}-{key}"
											type="text"
											bind:value={editFields[key]}
											class="lw-input mono"
										/>
									{/if}
								</div>
							{/each}
						</div>
						{#if editError}
							<div class="mt-3"><Callout tone="crit">{editError}</Callout></div>
						{/if}
					{:else}
						<dl class="grid grid-cols-[auto_1fr] items-baseline gap-x-3.5 gap-y-1 text-[12.5px]">
							{#each fieldKeys(item.fields) as key (key)}
								<dt class="text-[11.5px] text-muted">{key}</dt>
								<dd class="mono truncate" title={display(item.fields[key])}>
									{display(item.fields[key])}
								</dd>
							{/each}
						</dl>
					{/if}
				</Panel>
			{/each}
		</div>
	{/if}
</section>

{#if confirmRemove}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
		<div class="w-full max-w-sm rounded border border-line bg-surface p-5 shadow-lg">
			<h3 class="text-[15px] font-semibold">Remove this {kind}?</h3>
			<p class="mt-2 text-[12.5px] text-ink-2">
				<span class="mono font-medium">{confirmRemove.key}</span>
				({confirmRemove.type}) will be deleted from the config. Projects already generated keep their
				own copy of its settings and are unaffected.
			</p>
			<div class="mt-4 flex justify-end gap-2">
				<button class="lw-btn" onclick={() => (confirmRemove = null)}>Cancel</button>
				<button class="lw-btn lw-btn-danger" onclick={() => removeItem(confirmRemove!)}>
					Remove
				</button>
			</div>
		</div>
	</div>
{/if}
