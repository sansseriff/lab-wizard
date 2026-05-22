<script lang="ts">
	import { fetchWithConfig } from '../../api';
	import { TrashIcon, PlugIcon } from 'phosphor-svelte';
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
			const res = await fetchWithConfig<{ servers: RemoteServer[] }>(
				'/api/remote-servers',
				'POST',
				{ name, url }
			);
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
			const res = await fetchWithConfig<TestResult>('/api/remote-servers/test', 'POST', {
				url: server.url
			});
			testResults[server.name] = res;
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
	<div class="flex items-center gap-3">
		<h1 class="text-2xl font-semibold tracking-tight">Remote Servers</h1>
	</div>
	<p class="text-sm text-gray-600 dark:text-gray-300">
		Register lab_wizard servers this workstation's measurements can <em>consume</em>. Their named
		attributes become available during measurement creation. Saved to
		<code>config/remote/servers.yaml</code>. This is a client address book only — it does not affect
		this machine's own permission rules.
	</p>

	{#if statusMessage}
		<div
			class="rounded-md p-3 text-sm {statusMessage.ok
				? 'bg-green-50 text-green-800 dark:bg-green-900/30 dark:text-green-300'
				: 'bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-300'}"
		>
			{statusMessage.text}
		</div>
	{/if}

	<!-- Add form -->
	<section
		class="space-y-3 rounded-xl border border-gray-200 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-800/70"
	>
		<h2 class="text-lg font-medium">Register a server</h2>
		<div class="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
			<div>
				<label class="mb-1 block text-xs text-gray-600 dark:text-gray-300" for="srv-name">Name</label>
				<input
					id="srv-name"
					type="text"
					bind:value={newName}
					placeholder="cryo-rack"
					class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				/>
			</div>
			<div>
				<label class="mb-1 block text-xs text-gray-600 dark:text-gray-300" for="srv-url">URL</label>
				<input
					id="srv-url"
					type="text"
					bind:value={newUrl}
					placeholder="tcp://10.0.0.5:12300"
					class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				/>
			</div>
			<button
				class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
				onclick={addServer}
				disabled={busy}
			>
				Add
			</button>
		</div>
	</section>

	<!-- Server list -->
	<section class="space-y-2">
		<h2 class="text-lg font-medium">Registered</h2>
		{#if servers.length === 0}
			<div
				class="rounded-xl border border-gray-200 bg-white/70 p-4 text-sm text-gray-600 dark:border-white/10 dark:bg-gray-800/70 dark:text-gray-300"
			>
				No remote servers registered.
			</div>
		{:else}
			{#each servers as server}
				<div
					class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70"
				>
					<div class="flex items-center justify-between gap-3">
						<div class="min-w-0">
							<div class="font-medium">{server.name}</div>
							<div class="truncate text-xs text-gray-500">{server.url}</div>
						</div>
						<div class="flex shrink-0 items-center gap-1">
							<button
								class="inline-flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-xs hover:border-indigo-300 dark:border-gray-600"
								onclick={() => testServer(server)}
								disabled={testing[server.name]}
							>
								<PlugIcon size={14} />
								{testing[server.name] ? 'Testing…' : 'Test'}
							</button>
							<button
								class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
								title="Remove"
								onclick={() => removeServer(server.name)}
							>
								<TrashIcon size={18} />
							</button>
						</div>
					</div>

					{#if testResults[server.name]}
						{@const res = testResults[server.name]}
						{#if res.ok}
							<div class="mt-2 rounded-md bg-green-50 p-2 text-xs dark:bg-green-900/20">
								<div class="font-medium text-green-800 dark:text-green-300">
									Connected — {res.attributes?.length ?? 0} attribute(s)
								</div>
								{#if res.attributes && res.attributes.length > 0}
									<ul class="mt-1 space-y-0.5">
										{#each res.attributes as a}
											<li class="text-gray-700 dark:text-gray-300">
												<span class="font-mono">{a.attribute_name}</span>
												<span class="text-gray-500"
													>— {a.behavior_abc ?? 'opaque'}{a.type_hint
														? ` (${a.type_hint})`
														: ''}</span
												>
											</li>
										{/each}
									</ul>
								{/if}
							</div>
						{:else}
							<div
								class="mt-2 rounded-md bg-red-50 p-2 text-xs text-red-800 dark:bg-red-900/20 dark:text-red-300"
							>
								Unreachable: {res.error}
							</div>
						{/if}
					{/if}
				</div>
			{/each}
		{/if}
	</section>
</section>
