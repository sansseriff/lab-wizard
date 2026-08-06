<script lang="ts">
	/** Where measurement rows land.
	 *
	 * Browsing the tables themselves is not built yet, and this page does not
	 * pretend otherwise. What it can say truthfully today is which databases are
	 * configured and where they live — which is the question people actually
	 * arrive with ("where did my run go?"), and it needs no new backend.
	 *
	 * Deliberately absent: any claim that a database is healthy, reachable, or
	 * has N rows. A saver config is a path in a YAML file. Whether that path is
	 * writable, or whether anything has ever been written to it, is unknown until
	 * a measurement runs.
	 */
	import Pill from '$lib/components/Pill.svelte';
	import DatabaseIcon from 'phosphor-svelte/lib/Database';
	import type { SaverItem } from './+page.ts';

	let { data } = $props();
	const savers: SaverItem[] = $derived(data.savers ?? []);
</script>

<section class="space-y-4">
	<div>
		<h1 class="text-[22px] font-semibold tracking-tight">Database</h1>
		<p class="mt-1 max-w-[64ch] text-[13px] text-muted">
			One row per integration, tagged with every parameter set at the time. Execution structure and
			storage structure are decoupled, so the data can be sliced by parameters that were never the
			outer loop.
		</p>
	</div>

	{#if data.error}
		<div class="rounded border border-crit/30 bg-crit-wash px-3 py-2 text-[12.5px] text-crit">
			{data.error}
		</div>
	{/if}

	{#if savers.length === 0 && !data.error}
		<div class="rounded border border-line bg-surface px-4 py-10 text-center">
			<DatabaseIcon size={22} class="mx-auto text-muted" />
			<p class="mt-2 text-[13px] font-medium">No database configured</p>
			<p class="mx-auto mt-1 max-w-[46ch] text-xs text-muted">
				Add a <code>database_saver</code> on
				<a class="text-accent hover:underline" href="/data/savers">Savers</a>
				and bind it to a measurement to start recording runs.
			</p>
		</div>
	{:else}
		<div class="grid gap-3 md:grid-cols-2">
			{#each savers as saver (saver.key)}
				<div class="rounded border border-line bg-surface">
					<div class="flex items-center justify-between gap-3 border-b border-line px-3.5 py-2.5">
						<div>
							<h2 class="text-[13.5px] font-semibold">{saver.key}</h2>
							<p class="mono text-[11.5px] text-muted">{saver.type}</p>
						</div>
						<Pill tone="neutral">configured</Pill>
					</div>
					<dl
						class="grid grid-cols-[auto_1fr] items-baseline gap-x-3.5 gap-y-1 px-3.5 py-3 text-[12.5px]"
					>
						{#each Object.entries(saver.fields) as [k, v] (k)}
							{#if k !== 'type' && k !== 'enabled' && k !== 'attribute_name'}
								<dt class="text-[11.5px] text-muted">{k}</dt>
								<dd class="mono truncate">{v}</dd>
							{/if}
						{/each}
					</dl>
				</div>
			{/each}
		</div>
	{/if}

	<div class="rounded border border-line bg-surface-2 px-3.5 py-3">
		<p class="text-[12.5px] font-medium">Browsing runs and measurements is not built yet.</p>
		<p class="mt-1 max-w-[64ch] text-xs text-muted">
			The schema exists — <code>wafers → devices → runs → measurements</code>, with
			<code>cryostats</code> hanging off runs — but nothing in the wizard reads it. This section is
			where that lands, so the navigation does not need rearranging when it does.
		</p>
	</div>
</section>
