<script lang="ts">
	/** Shown after generating a file that opens instruments itself, when a server
	 * on this machine holds that hardware right now. The file is fine; running it
	 * is what would fail, on an exclusive-port error from deep in a driver.
	 *
	 * The fix offered is a release, not a stop: the server disconnects just these
	 * racks and keeps serving everything else. It opens them again the next time
	 * anything uses them through it, so release right before running the file.
	 */
	import Callout from '$lib/components/Callout.svelte';
	import { api, errorMessage, unwrap } from '$lib/api';

	export type HoldingServer = {
		url: string;
		workspace_path: string | null;
		own: boolean;
		roots: { path: string; transport_key: string | null }[];
	};

	let { servers }: { servers: HoldingServer[] } = $props();

	let releasing = $state(false);
	let released = $state(false);
	let error: string | null = $state(null);

	async function releaseAll() {
		releasing = true;
		error = null;
		try {
			for (const server of servers)
				for (const root of server.roots)
					await unwrap(api.POST('/api/local-servers/release', { body: { url: server.url, path: root.path } }));
			released = true;
		} catch (err) {
			error = errorMessage(err) || 'Could not release the hardware';
		} finally {
			releasing = false;
		}
	}
</script>

{#if servers.length > 0 && !released}
	<Callout tone="warn" title="An instrument server has this hardware open">
		<p>
			This file opens its instruments directly, and only one process can hold each of these at a time, so
			running it now would fail.
		</p>
		<ul class="mt-1 space-y-0.5">
			{#each servers as server (server.url)}
				<li>
					{server.own ? "This workspace's server" : `The server for ${server.workspace_path ?? server.url}`} holds
					{#each server.roots as root, i (root.path)}<span class="font-mono">{root.transport_key ?? root.path}</span
						>{i < server.roots.length - 1 ? ', ' : ''}{/each}.
				</li>
			{/each}
		</ul>
		<div class="mt-2 flex flex-wrap items-center gap-2">
			<button class="lw-btn lw-btn-sm" onclick={releaseAll} disabled={releasing}>
				{releasing ? 'Releasing…' : 'Release it from the server'}
			</button>
			<span>The server keeps running, but opens it again the next time anything uses it through the server.</span>
		</div>
		{#if error}<p class="mt-1 text-crit">{error}</p>{/if}
	</Callout>
{:else if released}
	<Callout tone="ok">Released. The hardware is free for this file until something uses it through the server again.</Callout>
{/if}
