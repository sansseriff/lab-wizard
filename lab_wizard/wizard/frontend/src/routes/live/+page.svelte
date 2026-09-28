<script lang="ts">
	/** One run, live, on its own: what a web plotter opens (backend/live_server.py).
	 *
	 * `?run=41&plot=IV` — the run in this lab database, and the plot to open on.
	 * Nothing else of the wizard is here; the page only follows the run.
	 */
	import { page } from '$app/state';
	import LiveRunView from '$lib/live/LiveRunView.svelte';

	const runId = $derived(Number(page.url.searchParams.get('run')));
	const plot = $derived(page.url.searchParams.get('plot') ?? '');
</script>

<svelte:head><title>Run {runId} · Lab Wizard</title></svelte:head>

<div class="flex h-screen flex-col p-4">
	{#if Number.isInteger(runId) && runId > 0}
		<LiveRunView {runId} {plot} />
	{:else}
		<p class="text-body text-muted">No run given: open this page as <code>/live/?run=&lt;id&gt;</code>.</p>
	{/if}
</div>
