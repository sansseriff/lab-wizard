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
	import LocalTree from '$lib/components/instruments/LocalTree.svelte';
	import ServerTree from '$lib/components/instruments/ServerTree.svelte';
	import Pill from '$lib/components/Pill.svelte';
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
	let activePid = $state<number | null>(null);

	const activeServer = $derived(servers.find((s) => s.pid === activePid) ?? null);
</script>

<section class="space-y-4">
	<div class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-[22px] font-semibold tracking-tight">Configured instruments</h1>
			<p class="mt-1 max-w-[64ch] text-[13px] text-muted">
				This workspace's tree, and the tree of every instrument server running on this machine.
			</p>
		</div>
	</div>

	<div class="flex gap-0 overflow-x-auto border-b border-line" role="tablist">
		<button
			role="tab"
			aria-selected={activePid === null}
			class="flex items-center gap-2 whitespace-nowrap border-b-2 px-3.5 py-2 text-[13px] transition-colors
				{activePid === null
				? 'border-accent font-semibold text-ink'
				: 'border-transparent text-muted hover:text-ink'}"
			onclick={() => (activePid = null)}
		>
			This workspace
		</button>

		{#each servers as server (server.pid)}
			<button
				role="tab"
				aria-selected={activePid === server.pid}
				class="flex items-center gap-2 whitespace-nowrap border-b-2 px-3.5 py-2 text-[13px] transition-colors
					{activePid === server.pid
					? 'border-accent font-semibold text-ink'
					: 'border-transparent text-muted hover:text-ink'}"
				onclick={() => (activePid = server.pid)}
			>
				{workspaceName(server.workspace_path)}
				<span class="mono text-[10.5px] font-normal opacity-70">pid {server.pid}</span>
			</button>
		{/each}
	</div>

	{#if activePid === null}
		<LocalTree {data} autoOpenAdd={autoOpenAdd} />
	{:else if activeServer}
		<!-- Keyed so switching tabs remounts rather than reusing another server's
		     state: the tree, its schema and its event log all belong to one
		     daemon, and carrying any of them across would be wrong, not stale. -->
		{#key activeServer.pid}
			<ServerTree server={activeServer} />
		{/key}
	{:else}
		<p class="py-6 text-sm text-muted">That server is no longer running.</p>
	{/if}

	{#if activePid === null && servers.length > 0}
		<p class="text-xs text-muted">
			<Pill tone="neutral">{servers.length}</Pill>
			other workspace{servers.length === 1 ? '' : 's'} on this machine
			{servers.length === 1 ? 'has' : 'have'} their own instruments — the tabs above.
		</p>
	{/if}
</section>
