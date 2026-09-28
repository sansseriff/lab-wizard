<script lang="ts">
	/** The lab's devices, and what each one is.
	 *
	 * A run names only its device; what the device is (its wafer, its width,
	 * its type) lives here, once, and becomes a filter on every run of it, past
	 * runs included. Registering a device before measuring it is fine.
	 */
	import { onMount } from 'svelte';
	import Modal from '$lib/components/Modal.svelte';
	import { whereValue } from '$lib/procedures/model';
	import { dataApi } from './api';
	import { show, type Device } from './model';

	let { onclose, onchanged }: { onclose: () => void; onchanged: () => void } = $props();

	type Row = { key: string; value: string };

	let devices = $state<Device[]>([]);
	let current = $state<string | null>(null);
	let name = $state('');
	let rows = $state<Row[]>([]);
	let notes = $state('');
	let error = $state('');
	let saving = $state(false);

	async function load(select: string | null = current) {
		devices = (await dataApi.devices()).devices;
		const device = devices.find((d) => d.name === select) ?? devices[0] ?? null;
		if (device) edit(device);
		else startNew();
	}

	function edit(device: Device) {
		current = device.name;
		name = device.name;
		rows = Object.entries(device.properties).map(([key, value]) => ({ key, value: show(value) }));
		notes = device.notes ?? '';
		error = '';
	}

	function startNew() {
		current = null;
		name = '';
		rows = [{ key: '', value: '' }];
		notes = '';
		error = '';
	}

	async function save() {
		error = '';
		const properties: Device['properties'] = {};
		for (const row of rows) {
			const key = row.key.trim();
			if (!key) continue;
			if (key in properties) {
				error = `${key} is listed twice.`;
				return;
			}
			properties[key] = row.value.trim() === '' ? null : whereValue(row.value);
		}
		saving = true;
		try {
			const saved = await dataApi.saveDevice(name.trim(), properties, notes.trim() || null);
			await load(saved.name);
			onchanged();
		} catch (e) {
			error = e instanceof Error ? e.message.replace(/^Failed to fetch: HTTP \d+: /, '') : String(e);
		} finally {
			saving = false;
		}
	}

	onMount(() => {
		load(null);
	});
</script>

<Modal
	title="Devices"
	subtitle="What each device is. Every property becomes a filter on all of that device's runs."
	{onclose}
	width="max-w-3xl"
>
	<div class="grid grid-cols-[12rem_1fr] gap-4">
		<ul class="space-y-0.5 border-r border-line pr-3" aria-label="Devices">
			{#each devices as device (device.name)}
				<li>
					<button
						class="flex w-full items-center rounded px-2 py-1 text-left text-body {current === device.name
							? 'bg-accent-wash text-accent-strong'
							: 'hover:bg-surface-2'}"
						onclick={() => edit(device)}
					>
						<span class="truncate">{device.name}</span>
						<span class="ml-auto text-fine text-muted tabular-nums">{device.runs}</span>
					</button>
				</li>
			{/each}
			<li><button class="lw-btn lw-btn-sm mt-2 w-full" onclick={startNew}>New device</button></li>
		</ul>

		<form
			class="space-y-3"
			onsubmit={(e) => {
				e.preventDefault();
				save();
			}}
		>
			<label class="block text-xs text-ink-2"
				>Name
				<input class="lw-input mt-1 w-full" bind:value={name} disabled={current !== null} required />
			</label>
			<div>
				<p class="text-xs text-ink-2">Properties</p>
				{#each rows as row, i (i)}
					<div class="mt-1 flex items-center gap-1">
						<input class="lw-input mono w-40" placeholder="wafer" bind:value={row.key} aria-label="Property name" />
						<span class="text-muted">=</span>
						<input class="lw-input mono flex-1" placeholder="W12" bind:value={row.value} aria-label="Value of {row.key}" />
						<button type="button" class="lw-btn lw-btn-sm" onclick={() => rows.splice(i, 1)}>Remove</button>
					</div>
				{/each}
				<button type="button" class="lw-btn lw-btn-sm mt-1.5" onclick={() => rows.push({ key: '', value: '' })}
					>Add property</button
				>
				<p class="mt-1 text-fine text-muted">
					One value each: text, a number, or true/false. Numbers can be filtered by range.
				</p>
			</div>
			<label class="block text-xs text-ink-2"
				>Notes
				<textarea class="lw-input mt-1 w-full" rows="2" bind:value={notes}></textarea>
			</label>
			{#if error}<p class="text-xs text-crit" role="alert">{error}</p>{/if}
			<button class="lw-btn lw-btn-primary" disabled={saving || !name.trim()}>
				{current === null ? 'Add device' : 'Save'}
			</button>
		</form>
	</div>
</Modal>
