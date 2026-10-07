<script lang="ts">
	/** The device mounted in a setup: one the lab has registered, or a new one added here.
	 *
	 * Typing a name nobody has registered offers to add it, so mounting a new
	 * device does not mean leaving the page; Devices… opens the registry, where
	 * a device's properties (its wafer, its width) are given.
	 */
	import { onMount } from 'svelte';
	import { errorMessage } from '$lib/api';
	import Combobox from '$lib/components/Combobox.svelte';
	import DevicesPanel from '$lib/data/DevicesPanel.svelte';
	import { dataApi } from '$lib/data/api';

	let {
		value,
		onchange,
		id,
		disabled = false
	}: { value: string | null; onchange: (device: string | null) => void; id?: string; disabled?: boolean } = $props();

	let devices = $state<string[]>([]);
	let managing = $state(false);
	let error = $state('');

	async function load() {
		try {
			devices = (await dataApi.devices()).devices.map((d) => d.name);
		} catch {
			devices = [];
		}
	}
	onMount(load);

	async function add(name: string) {
		const trimmed = name.trim();
		if (!trimmed) return;
		error = '';
		try {
			await dataApi.saveDevice(trimmed, {}, null);
			await load();
			onchange(trimmed);
		} catch (e) {
			error = errorMessage(e);
		}
	}
</script>

<div class="flex items-center gap-2">
	<div class="min-w-0 flex-1">
		<Combobox
			{id}
			mono
			{value}
			{disabled}
			options={devices.map((d) => ({ value: d, label: d }))}
			onValueChange={onchange}
			noneLabel="No device"
			placeholder="Search devices, or type a new one…"
		>
			{#snippet empty(search)}
				{#if search.trim()}
					<button
						type="button"
						class="w-full rounded px-1 py-0.5 text-left text-accent hover:bg-accent-wash"
						onmousedown={(e) => e.preventDefault()}
						onclick={() => add(search)}>Add device “{search.trim()}”</button
					>
				{:else}No devices yet: type a name to add one.{/if}
			{/snippet}
		</Combobox>
	</div>
	<button type="button" class="lw-btn lw-btn-sm shrink-0" onclick={() => (managing = true)} {disabled}>Devices…</button>
</div>
{#if error}<p class="mt-1 text-fine text-crit" role="alert">{error}</p>{/if}

{#if managing}
	<DevicesPanel onclose={() => (managing = false)} onchanged={load} />
{/if}
