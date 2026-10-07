<script lang="ts">
	/** Everything recorded about one run: who, when, on what, asked to do what, with what. */
	import { setupsApi } from '$lib/setups/api';
	import { isImage, isImages, leaves } from '$lib/setups/model';
	import { duration, flatten, localTime, show, type RunDetail } from './model';

	let { detail }: { detail: RunDetail } = $props();

	const run = $derived(detail.run);
	const facts = $derived<[string, string][]>([
		['Procedure', run.procedure],
		['Status', run.status],
		['Started', localTime(run.started_at)],
		['Took', duration(run.started_at, run.ended_at)],
		['Device', run.device ?? '—'],
		['Setup', detail.setup.name ?? '—'],
		['Operator', run.operator ?? '—'],
		['Project', run.project ?? '—'],
		['Points', String(run.points)],
		['Run', `#${run.id}`]
	]);
	const setupFields = $derived(leaves(detail.setup.fields));
	const needs = $derived(Object.entries(detail.setup.declared));
	const params = $derived(flatten(detail.params));
	const instruments = $derived(Object.entries(detail.instruments));
	const columns = $derived(Object.entries(detail.columns));
</script>

{#snippet table(rows: [string, unknown][], empty: string)}
	{#if rows.length}
		<dl class="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-body">
			{#each rows as [key, value] (key)}
				<dt class="mono text-muted">{key}</dt>
				<dd class="mono break-all">{show(value)}</dd>
			{/each}
		</dl>
	{:else}<p class="text-xs text-muted">{empty}</p>{/if}
{/snippet}

<div class="space-y-5 p-4">
	<section>
		<dl class="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
			{#each facts as [label, value] (label)}
				<div>
					<dt class="text-fine text-muted">{label}</dt>
					<dd class="text-body">{value}</dd>
				</div>
			{/each}
		</dl>
		{#if run.notes}<p class="mt-3 max-w-[70ch] text-body whitespace-pre-wrap">{run.notes}</p>{/if}
	</section>

	<section>
		<h3 class="mb-1.5 text-body font-semibold">Setup{#if detail.setup.name}{' '}<span class="font-normal text-muted">— {detail.setup.name}, as it was when the run started</span>{/if}</h3>
		{#if setupFields.length}
			<dl class="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-body">
				{#each setupFields as leaf (leaf.path)}
					<dt class="mono text-muted">{leaf.path}</dt>
					<dd class="mono break-all">
						{#if isImage(leaf.value) || isImages(leaf.value)}
							<span class="flex flex-wrap gap-1.5">
								{#each isImage(leaf.value) ? [leaf.value] : leaf.value as picture (picture.image)}
									<a href={setupsApi.imageUrl(picture.image)} target="_blank" rel="noreferrer">
										<img class="h-16 rounded border border-line object-cover" src={setupsApi.imageUrl(picture.image)} alt={leaf.path} />
									</a>
								{/each}
							</span>
						{:else}{leaf.shown}{/if}
					</dd>
				{/each}
			</dl>
		{:else}<p class="text-xs text-muted">{detail.setup.name ? 'The setup had no fields.' : 'Not taken on a setup.'}</p>{/if}
		{#if needs.length}
			<h4 class="mt-3 mb-1 text-xs font-semibold text-ink-2">What its procedure read from the setup</h4>
			<dl class="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-body">
				{#each needs as [name, need] (name)}
					<dt class="mono text-muted">{name}</dt>
					<dd class="mono">
						{#if name in detail.setup.values}
							{detail.setup.values[name]}{need.unit ? ` ${need.unit}` : ''}
							<span class="text-muted">from {detail.setup.needs[name]}</span>
						{:else}<span class="text-crit">could not be read; its plots are missing what it scales</span>{/if}
					</dd>
				{/each}
			</dl>
		{/if}
	</section>

	<section>
		<h3 class="mb-1.5 text-body font-semibold">Params</h3>
		{@render table(params, 'This run was given no params.')}
	</section>

	<section>
		<h3 class="mb-1.5 text-body font-semibold">Instruments</h3>
		{#each instruments as [role, snapshot] (role)}
			<details class="border-b border-line py-1.5">
				<summary class="cursor-pointer text-body">
					<span class="mono font-medium">{role}</span>
					<span class="text-muted">— {snapshot.type ?? snapshot.class ?? 'unknown'}{snapshot.attribute_name
							? ` (${snapshot.attribute_name})`
							: ''}</span>
				</summary>
				<div class="mt-1.5 pl-3">
					{@render table(flatten(snapshot.params ?? {}), 'Its settings could not be read when the run started.')}
				</div>
			</details>
		{:else}<p class="text-xs text-muted">No instruments recorded.</p>{/each}
	</section>

	<section>
		<h3 class="mb-1.5 text-body font-semibold">Columns</h3>
		{@render table(
			columns.map(([name, meta]) => [name, meta?.unit ?? '']),
			'No columns recorded.'
		)}
		{#if Object.keys(detail.derived).length}
			<h4 class="mt-3 mb-1 text-xs font-semibold text-ink-2">Derived, as recorded with the run</h4>
			{@render table(Object.entries(detail.derived), '')}
		{/if}
	</section>
</div>
