<script lang="ts">
	import { fetchWithConfig } from '../../api';
	import { ArrowClockwise, EjectSimple, Warning, Plugs, Desktop } from 'phosphor-svelte';
	import type { HardwareStatusData, RootStatus } from './+page.ts';

	let { data }: { data: HardwareStatusData } = $props();

	let owner = $state(data.owner);
	let transport = $state(data.transport);
	let servers = $state(data.servers);
	let error: string | null = $state(data.error ?? null);
	let busy = $state(false);
	let message: { text: string; ok: boolean } | null = $state(null);

	const roots = $derived(Object.values(transport.roots) as RootStatus[]);
	const heldRoots = $derived(roots.filter((r) => r.held_by_server));
	const duplicates = $derived(Object.entries(transport.duplicate_transports));

	async function refresh() {
		busy = true;
		error = null;
		try {
			const [o, t, s] = await Promise.all([
				fetchWithConfig<typeof owner>('/api/hardware-owner', 'GET'),
				fetchWithConfig<typeof transport>('/api/transport-status', 'GET'),
				fetchWithConfig<{ servers: typeof servers }>('/api/local-servers', 'GET')
			]);
			owner = o;
			transport = t;
			servers = s.servers;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	async function release(url: string, path: string) {
		busy = true;
		message = null;
		try {
			const res = await fetchWithConfig<{ released: string[] }>(
				'/api/local-servers/release',
				'POST',
				{ url, path }
			);
			message = {
				text: `Released ${res.released.length} instrument(s) under ${path}.`,
				ok: true
			};
			await refresh();
		} catch (e) {
			message = { text: e instanceof Error ? e.message : 'Release failed.', ok: false };
		} finally {
			busy = false;
		}
	}

	function shortKey(key: string | null): string {
		return key ?? '—';
	}

	function workspaceName(path: string | null): string {
		if (!path) return 'unknown';
		const parts = path.replace(/\/$/, '').split('/');
		return parts[parts.length - 1] || path;
	}
</script>

<div class="mx-auto max-w-4xl space-y-6 p-6">
	<div class="flex items-start justify-between">
		<div>
			<h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">Hardware & Servers</h1>
			<p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
				Which process owns each transport on this machine, and what is currently open.
			</p>
		</div>
		<button
			class="flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
			onclick={refresh}
			disabled={busy}
		>
			<ArrowClockwise size={14} />
			Refresh
		</button>
	</div>

	{#if error}
		<div
			class="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
		>
			{error}
		</div>
	{/if}

	{#if message}
		<div
			class="rounded-md border p-3 text-sm {message.ok
				? 'border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300'
				: 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300'}"
		>
			{message.text}
		</div>
	{/if}

	<!-- Who owns hardware right now. This is the single most useful fact on the
	     page: it explains why discovery behaves the way it does. -->
	<section class="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
		<h2 class="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
			<Desktop size={16} />
			Hardware owner
		</h2>
		{#if owner.owner === 'server'}
			<p class="mt-2 text-sm text-gray-700 dark:text-gray-300">
				The <strong>instrument server</strong> owns this workspace's hardware. The wizard routes
				discovery and instrument calls through it, so exactly one process holds each transport.
			</p>
			<p class="mt-1 font-mono text-xs text-gray-500 dark:text-gray-400">{owner.url}</p>
		{:else}
			<p class="mt-2 text-sm text-gray-700 dark:text-gray-300">
				No server is running for this workspace, so the <strong>wizard</strong> opens hardware in its
				own process.
			</p>
			<p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
				That works, but the permission gate only sees calls made through a server — anything done
				here is invisible to it. Configure a bind on
				<a class="text-indigo-600 hover:underline" href="/manage_permissions">Server &amp; Permissions</a>
				to create the server config.
			</p>
		{/if}
	</section>

	<!-- Every server on the machine, not just this workspace's: one started
	     elsewhere holds real hardware and cannot be found by path alone. -->
	<section class="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
		<h2 class="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
			<Plugs size={16} />
			Servers on this machine ({servers.length})
		</h2>
		{#if servers.length === 0}
			<p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
				None running. Servers advertise themselves when they start, so any workspace on this machine
				can find them.
			</p>
		{:else}
			<div class="mt-3 overflow-x-auto">
				<table class="w-full text-left text-sm">
					<thead class="text-xs text-gray-500 dark:text-gray-400">
						<tr>
							<th class="pb-1 pr-3 font-medium">Workspace</th>
							<th class="pb-1 pr-3 font-medium">PID</th>
							<th class="pb-1 pr-3 font-medium">Endpoint</th>
							<th class="pb-1 font-medium">Holding</th>
						</tr>
					</thead>
					<tbody class="text-gray-700 dark:text-gray-300">
						{#each servers as s (s.pid)}
							<tr class="border-t border-gray-100 dark:border-gray-800">
								<td class="py-1.5 pr-3" title={s.workspace_path}>
									{workspaceName(s.workspace_path)}
								</td>
								<td class="py-1.5 pr-3 font-mono text-xs">{s.pid}</td>
								<td class="py-1.5 pr-3 font-mono text-xs">
									{s.endpoints[0] ?? '—'}
								</td>
								<td class="py-1.5 text-xs">
									{transport.local_servers.find((ls) => ls.pid === s.pid)?.held_roots.length ?? 0} rack(s)
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>

	<!-- Transport declarations per root, plus whether anything holds them. The
	     declared/held distinction is the whole basis of arbitration. -->
	<section class="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
		<h2 class="text-sm font-medium text-gray-900 dark:text-gray-100">
			Transports in this workspace ({roots.length})
		</h2>
		<p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
			<strong>Exclusive</strong> transports admit one process at a time.
			<strong>Shared</strong> ones sit behind something that already multiplexes them, so a local
			project and a server can both use them.
		</p>

		{#if roots.length === 0}
			<p class="mt-3 text-sm text-gray-500 dark:text-gray-400">
				No instruments configured yet.
				<a class="text-indigo-600 hover:underline" href="/manage_instruments">Add some →</a>
			</p>
		{:else}
			<div class="mt-3 space-y-2">
				{#each roots as r (r.root)}
					<div
						class="flex flex-wrap items-center gap-2 rounded-md border border-gray-100 px-3 py-2 dark:border-gray-800"
					>
						<span class="font-mono text-xs text-gray-700 dark:text-gray-300">{r.root}</span>

						<span
							class="rounded px-1.5 py-0.5 text-[10px] font-medium {r.transport_sharing ===
							'shared'
								? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
								: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'}"
						>
							{r.transport_sharing}
						</span>

						{#if r.state_authority === 'subscribed'}
							<span
								class="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-800 dark:bg-sky-900/40 dark:text-sky-300"
								title="State is read from an external authority rather than inferred from our own commands"
							>
								subscribed
							</span>
						{/if}

						{#if r.held_by_server}
							<span
								class="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-800 dark:bg-red-900/40 dark:text-red-300"
							>
								in use
							</span>
						{:else}
							<span class="text-[10px] text-gray-400 dark:text-gray-500">free</span>
						{/if}

						<span class="ml-auto font-mono text-[10px] text-gray-400 dark:text-gray-500">
							{shortKey(r.transport_key)}
						</span>

						{#if r.held_by_server && r.held_by && r.transport_sharing === 'exclusive'}
							<button
								class="flex items-center gap-1 rounded border border-gray-300 px-2 py-0.5 text-[10px] text-gray-600 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
								title="Disconnect this rack so another process can use it. The server reopens it on the next call."
								onclick={() => release(r.held_by!, r.root)}
								disabled={busy}
							>
								<EjectSimple size={11} />
								Release
							</button>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		{#if heldRoots.length > 0}
			<p class="mt-3 text-xs text-gray-500 dark:text-gray-400">
				A rack marked <em>in use</em> means a server has actually opened it — not merely that it is
				configured. A locally-run project needing an exclusive rack that is in use will refuse to
				start and say so.
			</p>
		{/if}
	</section>

	{#if duplicates.length > 0}
		<!-- Two config entries resolving to one device: an easy mistake, and one
		     that produces confusing failures if it goes unnoticed. -->
		<section
			class="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20"
		>
			<h2
				class="flex items-center gap-2 text-sm font-medium text-amber-900 dark:text-amber-300"
			>
				<Warning size={16} />
				Same device configured more than once
			</h2>
			<p class="mt-1 text-xs text-amber-800 dark:text-amber-400">
				These config entries resolve to one physical transport, so they will contend with each other.
			</p>
			<ul class="mt-2 space-y-1 text-xs text-amber-900 dark:text-amber-300">
				{#each duplicates as [key, rootList] (key)}
					<li>
						<span class="font-mono">{key}</span>
						<span class="text-amber-700 dark:text-amber-500"> ← {rootList.join(', ')}</span>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
</div>
