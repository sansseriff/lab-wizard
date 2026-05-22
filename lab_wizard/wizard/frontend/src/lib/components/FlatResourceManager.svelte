<script lang="ts">
	import { fetchWithConfig } from '../../api';
	import ScrollArea from './ScrollArea.svelte';
	import { onMount } from 'svelte';

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

	let { kind, label }: { kind: 'saver' | 'plotter'; label: string } = $props();

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
			addError = 'Pick a type';
			return;
		}
		if (!newKey || !/^[A-Za-z0-9_-]+$/.test(newKey)) {
			addError = 'Key must be alphanumeric (letters, digits, _ or -).';
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

	async function removeItem(item: ResourceItem) {
		if (!confirm(`Remove ${kind} '${item.key}' (${item.type})?`)) return;
		try {
			await fetchWithConfig(`${apiBase}/remove`, 'POST', { type: item.type, key: item.key });
			refresh();
		} catch (err) {
			loadError = err instanceof Error ? err.message : String(err);
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

	function fieldKeys(fields: Record<string, any>): string[] {
		return Object.keys(fields).filter((k) => k !== 'type' && k !== 'enabled' && k !== 'attribute_name');
	}
</script>

<section class="space-y-4">
	<header class="flex items-center justify-between">
		<div>
			<h1 class="text-2xl font-semibold">{label}</h1>
			<p class="text-sm text-gray-600 dark:text-gray-300">
				Configured {kind}s. Pick from these when creating a measurement project.
			</p>
		</div>
		<div class="flex items-center gap-2">
			<button
				onclick={startAdd}
				class="rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white shadow hover:bg-indigo-500"
			>
				Add {kind}
			</button>
		</div>
	</header>

	{#if loadError}
		<div class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
			{loadError}
		</div>
	{/if}

	{#if showAddForm}
		<div class="rounded-md border border-gray-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-gray-800">
			<h2 class="text-lg font-medium">New {kind}</h2>
			<div class="mt-3 grid gap-3 sm:grid-cols-2">
				<label class="text-sm">
					Type
					<select bind:value={chosenType} class="mt-1 w-full rounded-md border-gray-300 dark:bg-gray-700">
						{#each Object.keys(metadata) as t}
							<option value={t}>{t}</option>
						{/each}
					</select>
				</label>
				<label class="text-sm">
					Key (your name for this instance)
					<input
						type="text"
						bind:value={newKey}
						placeholder="e.g. main_db"
						class="mt-1 w-full rounded-md border-gray-300 dark:bg-gray-700"
					/>
				</label>
			</div>
			<div class="mt-3 grid gap-2">
				{#each Object.keys(formFields) as key}
					<label class="text-sm">
						{key}
						{#if typeof formFields[key] === 'boolean'}
							<input type="checkbox" bind:checked={formFields[key]} />
						{:else if typeof formFields[key] === 'number'}
							<input type="number" bind:value={formFields[key]} class="ml-2 w-40 rounded border-gray-300" />
						{:else}
							<input type="text" bind:value={formFields[key]} class="ml-2 w-72 rounded border-gray-300" />
						{/if}
					</label>
				{/each}
			</div>
			{#if addError}
				<div class="mt-3 text-sm text-red-600">{addError}</div>
			{/if}
			<div class="mt-4 flex gap-2">
				<button onclick={submitAdd} class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white">Save</button>
				<button onclick={cancelAdd} class="rounded border px-3 py-1.5 text-sm">Cancel</button>
			</div>
		</div>
	{/if}

	<ScrollArea
		orientation="vertical"
		viewportClasses="max-h-[60vh] w-full"
		class="rounded-md border border-gray-200 bg-white dark:border-white/10 dark:bg-gray-800"
	>
		{#if items.length === 0}
			<div class="p-6 text-center text-sm text-gray-500">
				No {kind}s configured yet. Click "Add {kind}" above to add one.
			</div>
		{:else}
			<ul class="divide-y divide-gray-100 dark:divide-white/10">
				{#each items as item (item.key)}
					<li class="p-4">
						<div class="flex items-center justify-between">
							<div>
								<div class="text-sm font-medium">{item.key}</div>
								<div class="text-xs text-gray-500">type: {item.type}</div>
							</div>
							<div class="flex gap-2">
								{#if editingKey === item.key}
									<button onclick={() => saveEdit(item)} class="rounded bg-indigo-600 px-2 py-1 text-xs text-white">Save</button>
									<button onclick={cancelEdit} class="rounded border px-2 py-1 text-xs">Cancel</button>
								{:else}
									<button onclick={() => startEdit(item)} class="rounded border px-2 py-1 text-xs">Edit</button>
									<button onclick={() => resetItem(item)} class="rounded border px-2 py-1 text-xs">Reset</button>
									<button onclick={() => removeItem(item)} class="rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">Remove</button>
								{/if}
							</div>
						</div>
						{#if editingKey === item.key}
							<div class="mt-3 grid gap-2">
								{#each fieldKeys(editFields) as key}
									<label class="text-sm">
										<span class="inline-block w-32 align-top">{key}</span>
										{#if typeof editFields[key] === 'boolean'}
											<input type="checkbox" bind:checked={editFields[key]} />
										{:else if typeof editFields[key] === 'number'}
											<input type="number" bind:value={editFields[key]} class="ml-2 w-40 rounded border-gray-300" />
										{:else}
											<input type="text" bind:value={editFields[key]} class="ml-2 w-72 rounded border-gray-300" />
										{/if}
									</label>
								{/each}
							</div>
							{#if editError}
								<div class="mt-2 text-sm text-red-600">{editError}</div>
							{/if}
						{:else}
							<dl class="mt-2 grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 text-xs">
								{#each fieldKeys(item.fields) as key}
									<dt class="text-gray-500">{key}</dt>
									<dd class="text-gray-800 dark:text-gray-200">{JSON.stringify(item.fields[key])}</dd>
								{/each}
							</dl>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</ScrollArea>
</section>
