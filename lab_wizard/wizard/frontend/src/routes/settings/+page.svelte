<script lang="ts">
	/** Settings that belong to the whole workspace, not to one project.
	 *
	 * A project carries its own choices in its YAML (its instruments, params and
	 * outputs); what is here applies to every project at once.
	 */
	import { onMount } from 'svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import FileSettingsForm from '$lib/settings/FileSettingsForm.svelte';
	import { settingsApi, type WorkspacePaths } from '$lib/settings/api';

	let paths = $state<WorkspacePaths | null>(null);
	let pathsError = $state('');

	const rows = $derived(
		paths
			? [
					['Workspace', paths.root],
					['Manifest', paths.manifest],
					['Configuration', paths.config_dir],
					['Projects', paths.projects_dir],
					['Recorded runs', paths.data_dir],
					['Lab database', paths.database],
					['Logs', paths.logs_dir]
				]
			: []
	);

	onMount(async () => {
		try {
			paths = await settingsApi.workspace();
		} catch (e) {
			pathsError = e instanceof Error ? e.message : String(e);
		}
	});
</script>

<section class="space-y-4">
	<PageHeader
		title="Settings"
		lede="Choices that apply to every project in this workspace. A project's own choices live in its YAML."
	/>

	<FileSettingsForm />

	<Panel
		title="Workspace"
		description="Where this workspace keeps each kind of thing. The folders are set in lab-wizard.toml."
		flush
	>
		{#if pathsError}
			<p class="px-3.5 py-2.5 text-xs text-crit" role="alert">{pathsError}</p>
		{:else}
			<table class="w-full border-collapse text-body">
				<tbody>
					{#each rows as [label, path] (label)}
						<tr>
							<th
								class="w-40 border-b border-line px-3.5 py-2 text-left text-xs font-normal text-ink-2"
								>{label}</th
							>
							<td class="mono break-all border-b border-line px-3.5 py-2 text-fine">{path}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</Panel>
</section>
