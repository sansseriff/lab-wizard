<script lang="ts">
	/** Configured instruments — this workspace's tree and every other one on
	 * this machine, behind one tab row.
	 *
	 * These used to be two pages, and the split was the wrong seam: the question
	 * "where is that instrument configured?" was answered by remembering which
	 * page you were on. A tab row makes the source the visible axis instead.
	 *
	 * Servers on *other machines* deliberately get no tab. They expose a flat
	 * list of named attributes rather than a tree — a remote peer gets read and
	 * call, never reconfiguration — so a hierarchy here would promise an edit the
	 * wire refuses. They live on Servers ▸ Remote servers.
	 */
	import { page } from '$app/state';
	import { ask } from '$lib/confirm.svelte';
	import LocalTree from '$lib/components/instruments/LocalTree.svelte';
	import ServerTree from '$lib/components/instruments/ServerTree.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import { setQuery } from '$lib/url';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { workspaceName, type LocalServer } from '$lib/types/instruments';

	let { data } = $props();

	/** `?add=1` opens the add wizard immediately.
	 *
	 * Overview's "Add instrument" quick action is a *verb* — it promises to start
	 * a task, not merely to show a page. Landing on the tree and making the user
	 * hunt for the button would break that promise. */
	const autoOpenAdd = $derived(page.url.searchParams.get('add') === '1');

	const servers: LocalServer[] = $derived(data.servers ?? []);

	/** `null` is this workspace; otherwise the pid of the daemon being shown. */
	// In the URL (`?workspace=<pid>`) so a reload stays on the same tab.
	let activePid = $state<number | null>(Number(page.url.searchParams.get('workspace')) || null);
	$effect(() => setQuery({ workspace: activePid === null ? null : String(activePid) }));
	let dirty = $state(false);
	let busy = $state(false);
	async function switchWorkspace(pid: number | null) {
		if (pid === activePid || busy) return;
		const discard = { title: 'Discard unsaved instrument parameters?', confirmLabel: 'Discard', tone: 'danger' } as const;
		if (dirty && !(await ask(discard))) return;
		dirty = false;
		activePid = pid;
	}

	const activeServer = $derived(servers.find((s) => s.pid === activePid) ?? null);
</script>

<section class="space-y-4">
	<PageHeader
		title="Configured instruments"
		lede="Build your instrument tree, add modules under their parents, and edit settings in one place."
	/>

	<Tabs
		value={activePid === null ? 'local' : String(activePid)}
		onValueChange={(v) => switchWorkspace(v === 'local' ? null : Number(v))}
		tabs={[
			{ value: 'local', label: 'This workspace' },
			...servers.map((server) => ({
				value: String(server.pid),
				label: `${workspaceName(server.workspace_path)} · pid ${server.pid}`
			}))
		]}
		label="Workspace"
	>
		<div class="pt-4">
			{#if activePid === null}
				<LocalTree {data} {autoOpenAdd} bind:dirty bind:busy />
			{:else if activeServer}
				<!-- Keyed so switching tabs remounts rather than reusing another server's
				     state: the tree, its schema and its event log all belong to one
				     daemon, and carrying any of them across would be wrong, not stale. -->
				{#key activeServer.pid}
					<ServerTree server={activeServer} bind:dirty bind:busy />
				{/key}
			{:else}
				<p class="py-6 text-body text-muted">That server is no longer running.</p>
			{/if}
		</div>
	</Tabs>

	{#if activePid === null && servers.length > 0}
		<p class="text-xs text-muted">
			<Pill tone="neutral">{servers.length}</Pill>
			other workspace{servers.length === 1 ? '' : 's'} on this machine
			{servers.length === 1 ? 'has' : 'have'} their own instruments — the tabs above.
		</p>
	{/if}
</section>
