<script lang="ts">
	/** Every procedure this workspace can run: built into lab_wizard, or its own.
	 *
	 * A workspace procedure with a built-in's name takes its place, which is how a
	 * lab adapts one without editing the package; deleting the workspace copy
	 * brings the built-in back.
	 */
	import { invalidateAll } from '$app/navigation';
	import { fetchWithConfig } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import type { ProcedureSummary } from './+page';

	let { data } = $props();
	const procedures = $derived((data.procedures ?? []) as ProcedureSummary[]);
	let actionError = $state<string | null>(null);

	async function remove(p: ProcedureSummary) {
		const question = p.overrides_builtin
			? `Delete this workspace's ${p.name}? The built-in ${p.name} will be used again.`
			: `Delete ${p.name} from this workspace? Projects already generated from it keep their copy.`;
		if (!confirm(question)) return;
		try {
			await fetchWithConfig(`/api/procedures/${encodeURIComponent(p.name)}`, 'DELETE');
			actionError = null;
			await invalidateAll();
		} catch (e) {
			actionError = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<div class="space-y-4">
	<PageHeader
		title="Procedures"
		lede="Reusable measurements written as definitions: roles, params, and a step tree. Each can be run as a measurement on any instruments that fill its roles."
	>
		{#snippet actions()}
			<a class="lw-btn lw-btn-primary" href="/procedures/edit">New procedure</a>
		{/snippet}
	</PageHeader>

	{#if data.error}
		<Callout tone="crit">{data.error}</Callout>
	{/if}
	{#if actionError}
		<Callout tone="crit">{actionError}</Callout>
	{/if}

	<Panel flush>
		<table class="lw-table">
			<thead>
				<tr>
					<th>Procedure</th>
					<th>Roles</th>
					<th>Records</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each procedures as p (p.name)}
					<tr>
						<td>
							<div class="flex flex-wrap items-center gap-1.5">
								<a class="mono font-semibold text-ink hover:underline" href="/procedures/edit?name={encodeURIComponent(p.name)}">
									{p.name}
								</a>
								{#if p.origin === 'builtin'}
									<Pill title="Ships with lab_wizard">built in</Pill>
								{:else if p.overrides_builtin}
									<Pill tone="accent" title="This workspace's copy replaces the built-in">overrides built-in</Pill>
								{:else}
									<Pill tone="accent">this workspace</Pill>
								{/if}
								{#if p.problems.length}
									<Pill tone="crit" title={p.problems.join('\n')}>does not check</Pill>
								{/if}
							</div>
							{#if p.description}
								<div class="mt-0.5 text-xs text-muted">{p.description}</div>
							{/if}
							{#if p.presets.length}
								<div class="mt-0.5 text-[11px] text-muted">
									Presets: <span class="mono">{p.presets.join(', ')}</span>
								</div>
							{/if}
						</td>
						<td>
							<div class="flex flex-col gap-0.5">
								{#each Object.entries(p.roles) as [role, behavior] (role)}
									<span class="mono text-[11.5px]">{role} <span class="text-muted">{behavior}</span></span>
								{/each}
							</div>
						</td>
						<td class="mono text-[11.5px] text-muted">{p.records.join(', ')}</td>
						<td>
							<div class="flex justify-end gap-1.5 whitespace-nowrap">
								<a class="lw-btn lw-btn-sm" href="/procedures/edit?name={encodeURIComponent(p.name)}">Edit</a>
								<a class="lw-btn lw-btn-sm" href="/procedures/edit?from={encodeURIComponent(p.name)}">Duplicate</a>
								{#if !p.problems.length}
									<a
										class="lw-btn lw-btn-sm"
										href="/measurements/resources?name={encodeURIComponent(p.name)}&kind=procedure"
										>Create measurement</a
									>
								{/if}
								{#if p.origin === 'workspace'}
									<button class="lw-btn lw-btn-danger lw-btn-sm" onclick={() => remove(p)}>
										{p.overrides_builtin ? 'Revert' : 'Delete'}
									</button>
								{/if}
							</div>
						</td>
					</tr>
				{:else}
					<tr><td colspan="4" class="text-muted">No procedures yet.</td></tr>
				{/each}
			</tbody>
		</table>
	</Panel>
</div>
