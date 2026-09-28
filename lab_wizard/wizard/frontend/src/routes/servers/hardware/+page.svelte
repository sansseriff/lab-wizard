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
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { ask } from '$lib/confirm.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { untrack } from 'svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ArrowClockwiseIcon from 'phosphor-svelte/lib/ArrowClockwise';
	import EjectSimpleIcon from 'phosphor-svelte/lib/EjectSimple';
	import type { HardwareStatusData, RootStatus, ServerClaims } from './+page.ts';

	let { data }: { data: HardwareStatusData } = $props();

	// Seeded from the load payload, then refreshed together by `refresh()` below,
	// so only the initial value is wanted here.
	let owner = $state(untrack(() => data.owner));
	let transport = $state(untrack(() => data.transport));
	let servers = $state(untrack(() => data.servers));
	let claims: ServerClaims[] = $state(untrack(() => data.claims));
	let error: string | null = $state(untrack(() => data.error ?? null));
	let busy = $state(false);
	let message: { text: string; ok: boolean } | null = $state(null);

	const roots = $derived(Object.values(transport.roots) as RootStatus[]);
	const heldRoots = $derived(roots.filter((r) => r.held_by_server));
	const duplicates = $derived(Object.entries(transport.duplicate_transports));
	const claimCount = $derived(claims.reduce((n, s) => n + s.claims.length, 0));

	async function refresh() {
		busy = true;
		error = null;
		try {
			const [o, t, s, c] = await Promise.all([
				fetchWithConfig<typeof owner>('/api/hardware-owner', 'GET'),
				fetchWithConfig<typeof transport>('/api/transport-status', 'GET'),
				fetchWithConfig<{ servers: typeof servers }>('/api/local-servers', 'GET'),
				fetchWithConfig<{ servers: ServerClaims[] }>('/api/local-servers/claims', 'GET')
			]);
			owner = o;
			transport = t;
			servers = s.servers;
			claims = c.servers;
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

	async function forceRelease(url: string, unit: string, holder: string) {
		const yes = await ask({
			title: `End ${holder}'s claim on ${unit}?`,
			description: 'If that run is still going, its next write will be refused.',
			confirmLabel: 'End claim',
			tone: 'danger'
		});
		if (!yes) return;
		busy = true;
		message = null;
		try {
			const res = await fetchWithConfig<{ released: string[] }>(
				'/api/local-servers/claims/force-release',
				'POST',
				{ url, unit }
			);
			message = {
				text: `Ended the claim on ${res.released.join(', ') || unit}. It is reset to baseline before anyone else can claim it.`,
				ok: true
			};
			await refresh();
		} catch (e) {
			message = { text: e instanceof Error ? e.message : 'Force release failed.', ok: false };
		} finally {
			busy = false;
		}
	}

	function expiry(seconds: number | null): string {
		if (seconds === null) return 'resetting';
		return seconds < 1 ? '<1 s' : `${Math.round(seconds)} s`;
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
									<td class="mono text-fine text-muted">{r.transport_key ?? '—'}</td>
									<td>
										{#if r.held_by_server && r.held_by && r.transport_sharing === 'exclusive'}
											<Tooltip text="Disconnect this rack so another process can use it. The server reopens it on the next call.">{#snippet child({ props })}<button {...props}
												class="lw-btn lw-btn-sm"
												onclick={() => release(r.held_by!, r.root)}
												disabled={busy}
											>
												<EjectSimpleIcon size={11} />
												Release
											</button>{/snippet}</Tooltip>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				{#if heldRoots.length > 0}
					<p class="border-t border-line px-3.5 py-2.5 text-fine text-muted">
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
				<p class="mono mt-2 break-all text-fine text-muted">{owner.url}</p>
				{:else}
					<Callout tone="warn" title="The wizard owns this workspace's hardware. ">
						No server is running, so it opens hardware in its own process. That works, but the
						permission gate only sees calls made through a server — anything done here is invisible
						to it.
					</Callout>
					<p class="mt-2 text-fine text-muted">
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
								<span class="truncate text-body font-medium" title={s.workspace_path}>
									{workspaceName(s.workspace_path)}
								</span>
								<span class="mono shrink-0 text-fine text-muted">pid {s.pid}</span>
							</div>
							<p class="mono mt-0.5 break-all text-2xs text-muted">
								{s.endpoints[0] ?? '—'}
							</p>
							<p class="mt-0.5 text-fine tabular-nums text-muted">
								holding {heldCount(s.pid)} rack{heldCount(s.pid) === 1 ? '' : 's'}
							</p>
						</li>
					{/each}
				</ul>
				{/if}
			</Panel>
		</div>
	</div>
	<!-- Which *run* holds which instruments. Distinct from "in use" above: a
	     server can have a rack open that nobody has claimed, and a run can hold
	     a claim on an instrument with no handle open this instant. -->
	<Panel
		title="Run claims"
		description="A running measurement claims the instruments it drives, so no other run can write to them. Claims lapse on their own if a client stops renewing them."
		flush
	>
		{#if claimCount === 0 && claims.every((s) => !s.error)}
			<p class="px-3.5 py-5 text-center text-xs text-muted">No run holds a claim right now.</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="lw-table">
					<thead>
						<tr>
							<th>Run</th>
							<th>Instruments</th>
							<th>Server</th>
							<th>Expires</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						{#each claims as s (s.url)}
							{#if s.error}
								<tr>
									<td colspan="5" class="text-fine text-muted">
										{workspaceName(s.workspace_path)} did not answer: {s.error}
									</td>
								</tr>
							{/if}
							{#each s.claims as c (c.units.join('|'))}
								<tr>
									<td>
										<span class="font-medium">{c.holder}</span>
										<span class="mono block text-2xs text-muted">{c.peer}</span>
									</td>
									<td class="mono text-fine">
										{#each c.units as unit (unit)}
											<span class="block">{unit}</span>
										{/each}
									</td>
									<td class="text-fine">{workspaceName(s.workspace_path)}</td>
									<td class="tabular-nums">
										{#if c.restoring}
											<Pill tone="accent" title="Released; being reset to baseline before anyone else can claim it">resetting</Pill>
										{:else}
											{expiry(c.expires_in_s)}
										{/if}
									</td>
									<td>
										{#if !c.restoring}
											<Tooltip text="End this claim. For a run that is stuck, or whose client has gone.">{#snippet child({ props })}<button {...props}
												class="lw-btn lw-btn-sm"
												onclick={() => forceRelease(s.url, c.units[0], c.holder)}
												disabled={busy}
											>
												<EjectSimpleIcon size={11} />
												Force release
											</button>{/snippet}</Tooltip>
										{/if}
									</td>
								</tr>
							{/each}
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Panel>
</section>
