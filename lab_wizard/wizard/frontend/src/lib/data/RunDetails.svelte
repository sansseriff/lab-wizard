<script lang="ts">
	/** Everything recorded about one run: who, when, on what, asked to do what, with what. */
	import { duration, flatten, localTime, show, type RunDetail } from './model';

	let { detail }: { detail: RunDetail } = $props();

	const run = $derived(detail.run);
	const facts = $derived<[string, string][]>([
		['Procedure', run.procedure],
		['Status', run.status],
		['Started', localTime(run.started_at)],
		['Took', duration(run.started_at, run.ended_at)],
		['Device', run.device ?? '—'],
		['Operator', run.operator ?? '—'],
		['Project', run.project ?? '—'],
		['Points', String(run.points)],
		['Run', `#${run.id}`]
	]);
	const metadata = $derived(flatten(run.metadata));
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
		<h3 class="mb-1.5 text-body font-semibold">Run metadata</h3>
		{@render table(metadata, "Nothing recorded; set it in the project's run: block.")}
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
