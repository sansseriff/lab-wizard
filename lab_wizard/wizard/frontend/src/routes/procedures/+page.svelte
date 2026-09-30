<script lang="ts">
	/** Every procedure this workspace can run: built into lab_wizard, or its own.
	 *
	 * A workspace procedure with a built-in's name takes its place, which is how a
	 * lab adapts one without editing the package; deleting the workspace copy
	 * brings the built-in back.
	 */
	import { invalidateAll } from '$app/navigation';
	import RowMenu from '$lib/components/menu/RowMenu.svelte';
	import RowContextMenu from '$lib/components/menu/RowContextMenu.svelte';
	import type { MenuAction } from '$lib/components/menu/items';
	import { ask } from '$lib/confirm.svelte';
	import { api, errorMessage } from '$lib/api';
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import type { ProcedureSummary } from './+page';

	let { data } = $props();
	const procedures = $derived((data.procedures ?? []) as ProcedureSummary[]);
	let actionError = $state<string | null>(null);

	/** Everything a row can do; the first (Edit) is also a visible button. */
	function actions(p: ProcedureSummary): MenuAction[] {
		const name = encodeURIComponent(p.name);
		return [
			{ label: 'Edit', href: `/procedures/edit?name=${name}` },
			{ label: 'Duplicate', href: `/procedures/edit?from=${name}` },
			...(p.problems.length
				? []
				: [{ label: 'Create measurement', href: `/measurements/resources?name=${name}&kind=procedure` }]),
			...(p.origin === 'workspace'
				? [{ label: p.overrides_builtin ? 'Revert to built-in' : 'Delete', danger: true, onselect: () => remove(p) }]
				: [])
		];
	}

	async function remove(p: ProcedureSummary) {
		const yes = await ask({
			title: p.overrides_builtin ? `Delete this workspace's ${p.name}?` : `Delete ${p.name}?`,
			description: p.overrides_builtin
				? `The built-in ${p.name} will be used again.`
				: 'Projects already generated from it keep their copy.',
			confirmLabel: 'Delete',
			tone: 'danger'
		});
		if (!yes) return;
		try {
			await api.DELETE('/api/procedures/{name}', { params: { path: { name: p.name } } });
			actionError = null;
			await invalidateAll();
		} catch (e) {
			actionError = errorMessage(e);
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
					<RowContextMenu items={actions(p)}>
						{#snippet child({ props })}
						<tr {...props}>
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
									<div class="mt-0.5 text-fine text-muted">
										Presets: <span class="mono">{p.presets.join(', ')}</span>
									</div>
								{/if}
							</td>
							<td>
								<div class="flex flex-col gap-0.5">
									{#each Object.entries(p.roles) as [role, behavior] (role)}
										<span class="mono text-fine">{role} <span class="text-muted">{behavior}</span></span>
									{/each}
								</div>
							</td>
							<td class="mono text-fine text-muted">{p.records.join(', ')}</td>
							<td>
								<div class="flex justify-end gap-1.5 whitespace-nowrap">
									<a class="lw-btn lw-btn-sm" href="/procedures/edit?name={encodeURIComponent(p.name)}">Edit</a>
									<RowMenu small label="More actions for {p.name}" items={actions(p).slice(1)} />
								</div>
							</td>
						</tr>
						{/snippet}
					</RowContextMenu>
				{:else}
					<tr><td colspan="4" class="text-muted">No procedures yet.</td></tr>
				{/each}
			</tbody>
		</table>
	</Panel>
</div>
