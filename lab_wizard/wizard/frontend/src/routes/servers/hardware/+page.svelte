<script lang="ts">
	/** Which process owns each transport, and what is open right now.
	 *
	 * A diagnostic page. It keeps a sidebar entry so it is findable, but the
	 * intended route in is the warning that sent you — a duplicate-transport
	 * chip on the instrument tree, or the conflict banner while picking
	 * resources.
	 *
	 * The declared/held distinction is the whole basis of arbitration and is
	 * what this page exists to make visible: *declared* is what the config says
	 * a transport is, *held* is whether a process actually has it open.
	 */
	import { fetchWithConfig } from '$lib/api';
	import Panel from '$lib/components/Panel.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ArrowClockwiseIcon from 'phosphor-svelte/lib/ArrowClockwise';
	import EjectSimpleIcon from 'phosphor-svelte/lib/EjectSimple';
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

	function workspaceName(path: string | null): string {
		if (!path) return 'unknown';
		const parts = path.replace(/\/$/, '').split('/');
		return parts[parts.length - 1] || path;
	}

	function heldCount(pid: number): number {
		return transport.local_servers.find((ls) => ls.pid === pid)?.held_roots.length ?? 0;
	}
</script>

<section class="space-y-4">
	<PageHeader
		title="Hardware ownership"
		lede="Which process owns each transport on this machine, and what is currently open."
	>
		{#snippet actions()}
			<button class="lw-btn" onclick={refresh} disabled={busy}>
				<ArrowClockwiseIcon size={13} />
				{busy ? 'Refreshing…' : 'Refresh'}
			</button>
		{/snippet}
	</PageHeader>

	{#if error}
		<Callout tone="crit">{error}</Callout>
	{/if}
	{#if message}
		<Callout tone={message.ok ? 'ok' : 'crit'}>{message.text}</Callout>
	{/if}

	{#if duplicates.length > 0}
		<!-- Two config entries resolving to one device: an easy mistake, and one
		     that produces confusing failures if it goes unnoticed. -->
		<Callout tone="warn" title="Same device configured more than once. ">
			These entries resolve to one physical transport, so they will contend with each other —
			whichever process opens it second is refused.
			<ul class="mt-1.5 space-y-0.5">
				{#each duplicates as [key, rootList] (key)}
					<li>
						<span class="mono">{key}</span>
						<span class="opacity-80"> ← {rootList.join(', ')}</span>
					</li>
				{/each}
			</ul>
		</Callout>
	{/if}

	<div class="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
		<Panel
			title="Transports in this workspace"
			description="Exclusive admits one process at a time. Shared sits behind something that already multiplexes it."
			flush
		>
			{#if roots.length === 0}
				<p class="px-3.5 py-6 text-center text-xs text-muted">
					No instruments configured yet.
					<a class="text-accent hover:underline" href="/instruments">Add some →</a>
				</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="lw-table">
						<thead>
							<tr>
								<th>Root</th>
								<th>Sharing</th>
								<th>State</th>
								<th>Transport key</th>
								<th></th>
							</tr>
						</thead>
						<tbody>
							{#each roots as r (r.root)}
								<tr>
									<td class="mono">{r.root}</td>
									<td>
										{#if r.transport_sharing === 'shared'}
											<Pill tone="ok">shared</Pill>
										{:else}
											<Pill tone="warn">exclusive</Pill>
										{/if}
										{#if r.state_authority === 'subscribed'}
											<Pill
												tone="accent"
												title="State is read from an external authority rather than inferred from our own commands"
											>
												subscribed
											</Pill>
										{/if}
									</td>
									<td>
										{#if r.held_by_server}
											<Pill tone="crit" dot>in use</Pill>
										{:else}
											<span class="text-muted">free</span>
										{/if}
									</td>
									<td class="mono text-[11px] text-muted">{r.transport_key ?? '—'}</td>
									<td>
										{#if r.held_by_server && r.held_by && r.transport_sharing === 'exclusive'}
											<button
												class="lw-btn lw-btn-sm"
												title="Disconnect this rack so another process can use it. The server reopens it on the next call."
												onclick={() => release(r.held_by!, r.root)}
												disabled={busy}
											>
												<EjectSimpleIcon size={11} />
												Release
											</button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				{#if heldRoots.length > 0}
					<p class="border-t border-line px-3.5 py-2.5 text-[11.5px] text-muted">
						<em>In use</em> means a server has actually opened that rack — not merely that it is
						configured. A locally-run project needing an exclusive rack that is in use will refuse
						to start and say so.
					</p>
				{/if}
			{/if}
		</Panel>

		<div class="space-y-4">
			<!-- Who owns hardware right now. The single most useful fact here: it
			     explains why discovery behaves the way it does. -->
			<Panel title="Hardware owner">
				{#if owner.owner === 'server'}
					<Callout tone="ok" title="The instrument server owns this workspace's hardware. ">
						The wizard routes discovery and instrument calls through it, so exactly one process
						holds each transport.
					</Callout>
					<!-- An ipc socket path is long and has no spaces, so it needs an
				     explicit break rule or it runs straight out of the panel. -->
				<p class="mono mt-2 break-all text-[11px] text-muted">{owner.url}</p>
				{:else}
					<Callout tone="warn" title="The wizard owns this workspace's hardware. ">
						No server is running, so it opens hardware in its own process. That works, but the
						permission gate only sees calls made through a server — anything done here is invisible
						to it.
					</Callout>
					<p class="mt-2 text-[11.5px] text-muted">
						<a class="text-accent hover:underline" href="/servers">Start one on This workspace →</a>
					</p>
				{/if}
			</Panel>

			<!-- Every server on the machine, not just this workspace's: one started
			     elsewhere holds real hardware and cannot be found by path alone. -->
			<Panel title="Servers on this machine" description="{servers.length} running" flush>
				{#if servers.length === 0}
					<p class="px-3.5 py-5 text-xs text-muted">
						None running. Servers advertise themselves when they start, so any workspace on this
						machine can find them.
					</p>
				{:else}
				<!-- A list rather than a table: this is the narrow column, and an
				     ipc endpoint is far too long to sit in a table cell here. -->
				<ul>
					{#each servers as s (s.pid)}
						<li class="border-b border-line px-3.5 py-2.5 last:border-b-0">
							<div class="flex items-baseline justify-between gap-2">
								<span class="truncate text-[12.5px] font-medium" title={s.workspace_path}>
									{workspaceName(s.workspace_path)}
								</span>
								<span class="mono shrink-0 text-[11px] text-muted">pid {s.pid}</span>
							</div>
							<p class="mono mt-0.5 break-all text-[10.5px] text-muted">
								{s.endpoints[0] ?? '—'}
							</p>
							<p class="mt-0.5 text-[11.5px] tabular-nums text-muted">
								holding {heldCount(s.pid)} rack{heldCount(s.pid) === 1 ? '' : 's'}
							</p>
						</li>
					{/each}
				</ul>
				{/if}
			</Panel>
		</div>
	</div>
</section>
