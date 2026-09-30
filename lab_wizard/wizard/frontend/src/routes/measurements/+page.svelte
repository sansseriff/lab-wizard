<script lang="ts">
	/** The Measurements section's home: every project this workspace has generated.
	 *
	 * A project's name opens it on the Run page, where it is set up and run;
	 * New measurement starts the Create flow that generates another.
	 */
	import Pill from '$lib/components/Pill.svelte';
	import FolderOpenIcon from 'phosphor-svelte/lib/FolderOpen';
	import type { Project } from './+page.ts';

	let { data } = $props();
	const projects: Project[] = $derived(data.projects ?? []);

	function when(iso: string | null): string {
		if (!iso) return '—';
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return '—';
		return d.toLocaleString(undefined, {
			day: 'numeric',
			month: 'short',
			year: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	/** One line naming what the project was bound to. */
	function boundTo(p: Project): string {
		return p.instruments.length ? p.instruments.join(' · ') : '—';
	}

	/** What a run of the project produces, besides its database record. */
	function produces(p: Project): string {
		const parts = ['database'];
		if (p.outputs.files) parts.push('files');
		if (p.outputs.live_plot === 'window') parts.push('plot window');
		if (p.outputs.live_plot === 'web') parts.push('web plot');
		return parts.join(' · ');
	}
</script>

<section class="space-y-4">
	<div class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-headline font-semibold tracking-tight">Measurements</h1>
			<p class="mt-1 max-w-[64ch] text-body text-muted">
				Every project generated from this workspace, with the instruments it was bound to. Open one to set it up and run it.
			</p>
		</div>
		<a
			href="/measurements/new"
			class="shrink-0 rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent no-underline hover:brightness-110"
		>
			New measurement
		</a>
	</div>

	{#if data.error}
		<div class="rounded border border-crit/30 bg-crit-wash px-3 py-2 text-body text-crit">
			{data.error}
		</div>
	{/if}

	{#if projects.length === 0 && !data.error}
		<div class="rounded border border-line bg-surface px-4 py-10 text-center">
			<FolderOpenIcon size={22} class="mx-auto text-muted" />
			<p class="mt-2 text-body font-medium">No projects yet</p>
			<p class="mx-auto mt-1 max-w-[46ch] text-xs text-muted">
				Creating a measurement writes a project directory here, containing its YAML, its setup file,
				and the measurement itself.
			</p>
		</div>
	{:else if projects.length > 0}
		<div class="overflow-x-auto rounded border border-line bg-surface">
			<table class="w-full border-collapse text-body">
				<thead>
					<tr>
						<th
							class="border-b border-line px-3.5 py-2.5 text-left text-2xs font-semibold uppercase tracking-[0.08em] text-muted"
							>Project</th
						>
						<th
							class="border-b border-line px-3.5 py-2.5 text-left text-2xs font-semibold uppercase tracking-[0.08em] text-muted"
							>Measurement</th
						>
						<th
							class="border-b border-line px-3.5 py-2.5 text-left text-2xs font-semibold uppercase tracking-[0.08em] text-muted"
							>Bound to</th
						>
						<th
							class="border-b border-line px-3.5 py-2.5 text-left text-2xs font-semibold uppercase tracking-[0.08em] text-muted"
							>Outputs</th
						>
						<th
							class="border-b border-line px-3.5 py-2.5 text-left text-2xs font-semibold uppercase tracking-[0.08em] text-muted"
							>Created</th
						>
					</tr>
				</thead>
				<tbody>
					{#each projects as p (p.name)}
						<tr class="hover:bg-surface-2">
							<td class="border-b border-line px-3.5 py-2.5 align-top last:border-b-0">
								<a
									class="mono font-medium text-accent hover:underline"
									href={`/measurements/run?project=${encodeURIComponent(p.name)}`}
									title="Set up and run this project">{p.name}</a
								>
								<div class="mono mt-0.5 text-fine text-muted" title={p.path}>
									{p.setup_file ?? 'no setup file'}
								</div>
							</td>
							<td class="border-b border-line px-3.5 py-2.5 align-top">
								{#if p.measurement}
									{p.measurement}
								{:else if p.readable}
									<span class="text-muted">not recorded</span>
								{:else}
									<Pill tone="warn">unreadable YAML</Pill>
								{/if}
							</td>
							<td class="mono border-b border-line px-3.5 py-2.5 align-top text-fine text-muted">
								{boundTo(p)}
							</td>
							<td class="border-b border-line px-3.5 py-2.5 align-top text-fine text-muted">
								{produces(p)}
							</td>
							<td class="border-b border-line px-3.5 py-2.5 align-top tabular-nums text-muted">
								{when(p.created)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<p class="text-xs text-muted">
			Every project also runs from a terminal:
			<code>uv run &lt;measurement&gt;_setup.py</code> in its folder.
		</p>
	{/if}
</section>
