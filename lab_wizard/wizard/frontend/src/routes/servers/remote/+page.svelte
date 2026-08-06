<script lang="ts">
	/** The address book of servers on *other* machines.
	 *
	 * A client-side list and nothing more. It has no counterpart on the server
	 * side — a server keeps no registry of who dials it — so every number here
	 * describes us, never them.
	 *
	 * These entries are flat by design. A remote peer gets read and call, never
	 * reconfiguration, so what a test returns is a list of named attributes
	 * rather than a tree. That is also why they get no tab on Configured
	 * instruments.
	 */
	import { fetchWithConfig } from '$lib/api';
	import Panel from '$lib/components/Panel.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import TrashIcon from 'phosphor-svelte/lib/Trash';
	import PlugIcon from 'phosphor-svelte/lib/Plug';
	import type { RemoteServer, TestResult } from './+page.ts';

	let { data } = $props();

	let servers: RemoteServer[] = $state(data.servers ?? []);
	let newName = $state('');
	let newUrl = $state('');
	let statusMessage: { text: string; ok: boolean } | null = $state(null);
	let busy = $state(false);

	// Per-server live test results, keyed by server name.
	let testResults: Record<string, TestResult> = $state({});
	let testing: Record<string, boolean> = $state({});

	async function addServer() {
		const name = newName.trim();
		const url = newUrl.trim();
		if (!name || !url) {
			statusMessage = { text: 'Both a name and a URL are required.', ok: false };
			return;
		}
		busy = true;
		statusMessage = null;
		try {
			const res = await fetchWithConfig<{ servers: RemoteServer[] }>('/api/remote-servers', 'POST', {
				name,
				url
			});
			servers = res.servers;
			newName = '';
			newUrl = '';
			statusMessage = { text: `Registered "${name}".`, ok: true };
		} catch (e) {
			statusMessage = { text: e instanceof Error ? e.message : 'Failed to add server.', ok: false };
		} finally {
			busy = false;
		}
	}

	async function removeServer(name: string) {
		busy = true;
		statusMessage = null;
		try {
			const res = await fetchWithConfig<{ servers: RemoteServer[] }>(
				`/api/remote-servers/${encodeURIComponent(name)}`,
				'DELETE'
			);
			servers = res.servers;
			delete testResults[name];
			testResults = { ...testResults };
		} catch (e) {
			statusMessage = { text: e instanceof Error ? e.message : 'Failed to remove.', ok: false };
		} finally {
			busy = false;
		}
	}

	async function testServer(server: RemoteServer) {
		testing[server.name] = true;
		testing = { ...testing };
		try {
			testResults[server.name] = await fetchWithConfig<TestResult>(
				'/api/remote-servers/test',
				'POST',
				{ url: server.url }
			);
		} catch (e) {
			testResults[server.name] = {
				ok: false,
				error: e instanceof Error ? e.message : 'Test failed.'
			};
		} finally {
			testResults = { ...testResults };
			testing[server.name] = false;
			testing = { ...testing };
		}
	}
</script>

<section class="space-y-4">
	<PageHeader
		title="Remote servers"
		lede="Servers on other machines whose named instruments this workstation's measurements can use."
	/>

	<Callout tone="info">
		This is an address book and only that. It is saved to
		<code>config/remote/servers.yaml</code> and does not affect this machine's own permission rules.
		A server keeps no matching list of who dials it, so nothing here is visible from the other end.
	</Callout>

	{#if statusMessage}
		<Callout tone={statusMessage.ok ? 'ok' : 'crit'}>{statusMessage.text}</Callout>
	{/if}

	<Panel title="Register a server" description="Give it a name your projects will refer to it by.">
		<div class="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
			<div>
				<label class="lw-label" for="srv-name">Name</label>
				<input id="srv-name" type="text" bind:value={newName} placeholder="cryo-rack" class="lw-input" />
			</div>
			<div>
				<label class="lw-label" for="srv-url">URL</label>
				<input
					id="srv-url"
					type="text"
					bind:value={newUrl}
					placeholder="tcp://10.0.0.5:12300"
					class="lw-input mono"
				/>
			</div>
			<button class="lw-btn lw-btn-primary" onclick={addServer} disabled={busy}>Add</button>
		</div>
		<p class="mt-2 text-[11px] text-muted">
			Projects record the <em>name</em>, not the address, and resolve it through this book at run
			time — so a project stays readable and an address can change in one place.
		</p>
	</Panel>

	<div>
		<p class="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.11em] text-muted">
			Registered
		</p>

		{#if servers.length === 0}
			<div class="rounded border border-line bg-surface px-4 py-8 text-center">
				<p class="text-[13px] font-medium">No remote servers registered</p>
				<p class="mx-auto mt-1 max-w-[46ch] text-xs text-muted">
					Add one to use another machine's instruments when creating a measurement.
				</p>
			</div>
		{:else}
			<div class="grid gap-3 md:grid-cols-2">
				{#each servers as server (server.name)}
					{@const res = testResults[server.name]}
					<Panel title={server.name}>
						{#snippet actions()}
							<button
								class="lw-btn lw-btn-sm"
								onclick={() => testServer(server)}
								disabled={testing[server.name]}
							>
								<PlugIcon size={13} />
								{testing[server.name] ? 'Testing…' : 'Test'}
							</button>
							<button
								class="lw-btn lw-btn-sm"
								title="Remove from the address book"
								onclick={() => removeServer(server.name)}
								disabled={busy}
							>
								<TrashIcon size={13} />
							</button>
						{/snippet}

						<p class="mono mb-2 truncate text-[11.5px] text-muted" title={server.url}>
							{server.url}
						</p>

						{#if !res}
							<p class="text-xs text-muted">
								Not contacted yet. Testing dials it and lists what it offers.
							</p>
						{:else if res.ok}
							<div class="space-y-1.5">
								<Pill tone="ok" dot>
									answered · {res.attributes?.length ?? 0} attribute{(res.attributes?.length ?? 0) ===
									1
										? ''
										: 's'}
								</Pill>
								{#if res.attributes && res.attributes.length > 0}
									<dl
										class="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-0.5 text-[12px]"
									>
										{#each res.attributes as a (a.attribute_name)}
											<dt class="mono">{a.attribute_name}</dt>
											<dd class="truncate text-muted">
												{a.behavior_abc ?? 'opaque'}{a.type_hint ? ` (${a.type_hint})` : ''}
											</dd>
										{/each}
									</dl>
								{/if}
							</div>
						{:else}
							<Callout tone="warn" title="No answer. ">
								{res.error}. The entry stays — a rack that is switched off must not stop you
								authoring against the others.
							</Callout>
						{/if}
					</Panel>
				{/each}
			</div>
		{/if}
	</div>
</section>
