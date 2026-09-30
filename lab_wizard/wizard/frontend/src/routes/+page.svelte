<script lang="ts">
	/** Overview.
	 *
	 * Two rules govern everything on this page.
	 *
	 * First: say *configured* unless something actually reported in. Instruments
	 * are entries in `config/instruments`, not open connections. The one genuinely live number here is how many roots a server
	 * is currently holding, because a server really does report that.
	 *
	 * Second: nothing here may describe other machines' relationship to us. A
	 * server keeps no registry of its clients. "2 remote servers" counts *our*
	 * address book — a list we wrote — and it is labelled as such.
	 */
	import { api, errorMessage } from '$lib/api';
	import Pill from '$lib/components/Pill.svelte';
	import { workstation } from '$lib/stores/workstation.svelte';
	import { workspaceName } from '$lib/types/instruments';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import CircuitryIcon from 'phosphor-svelte/lib/Circuitry';
	import WarningIcon from 'phosphor-svelte/lib/Warning';
	import ArrowRightIcon from 'phosphor-svelte/lib/ArrowRight';

	let { data } = $props();

	const heldCount = $derived(data.roots.filter((r) => r.held_by_server).length);

	function when(iso: string | null): string | null {
		if (!iso) return null;
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return null;
		return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
	}

	let serverBusy = $state(false);
	let serverError: string | null = $state(null);

	async function toggleServer() {
		const stopping = workstation.phase === 'running';
		serverBusy = true;
		serverError = null;
		try {
			if (stopping) await api.POST('/api/server/stop');
			else await api.POST('/api/server/start', { body: { detached: false } });
			await workstation.refresh();
		} catch (e) {
			serverError = errorMessage(e) || 'Server action failed.';
		} finally {
			serverBusy = false;
		}
	}
</script>

<section class="space-y-5">
	<div>
		<h1 class="text-headline font-semibold tracking-tight">Overview</h1>
	</div>

	<!-- Counts. Every label states which kind of fact it is. -->
	<div class="grid grid-cols-2 overflow-hidden rounded border border-line bg-surface md:grid-cols-4">
		<div class="flex flex-col gap-0.5 border-b border-r border-line px-4 py-3 md:border-b-0">
			<span class="text-2xs uppercase tracking-[0.09em] text-muted">Instruments</span>
			<span class="text-xl font-semibold tabular-nums">{data.instrumentCount}</span>
			<span class="text-fine text-muted">
				configured · {data.roots.length} root transport{data.roots.length === 1 ? '' : 's'}
			</span>
		</div>
		<div class="flex flex-col gap-0.5 border-b border-line px-4 py-3 md:border-b-0 md:border-r">
			<span class="text-2xs uppercase tracking-[0.09em] text-muted">Open now</span>
			<span class="text-xl font-semibold tabular-nums">{heldCount}</span>
			<span class="text-fine text-muted">
				{heldCount === 0 ? 'no server holds a rack' : 'held by a server'}
			</span>
		</div>
		<div class="flex flex-col gap-0.5 border-r border-line px-4 py-3">
			<span class="text-2xs uppercase tracking-[0.09em] text-muted">Projects</span>
			<span class="text-xl font-semibold tabular-nums">{data.projectCount}</span>
			<span class="text-fine text-muted">generated</span>
		</div>
		<div class="flex flex-col gap-0.5 px-4 py-3">
			<span class="text-2xs uppercase tracking-[0.09em] text-muted">Remote servers</span>
			<span class="text-xl font-semibold tabular-nums">{data.remoteCount}</span>
			<span class="text-fine text-muted">in our address book</span>
		</div>
	</div>

	<div class="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
		<div class="space-y-4">
			<!-- Server state. What running it changes is explained on /servers;
			     here it is a status and a switch. -->
			<div class="rounded border border-line bg-surface">
				<div class="flex items-center justify-between gap-3 px-3.5 py-2.5">
					<div class="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
						<h2 class="text-body font-semibold">
							<a class="text-ink no-underline hover:underline" href="/servers">Instrument server</a>
						</h2>
						{#if workstation.phase === 'running'}
							<Pill tone="ok" dot>Running</Pill>
						{:else if workstation.phase === 'stopped'}
							<Pill tone="warn" dot>Stopped</Pill>
						{:else}
							<Pill tone="neutral">Not configured</Pill>
						{/if}
						{#if workstation.phase !== 'unconfigured'}
							<span class="text-fine text-muted">
								{#if workstation.server?.bind}<span class="mono">{workstation.server.bind}</span> ·{/if}
								<a class="text-muted hover:underline" href="/servers/permissions"
									>{workstation.server?.rule_count ?? 0} safety rule{(workstation.server
										?.rule_count ?? 0) === 1
										? ''
										: 's'}</a
								>
							</span>
						{/if}
					</div>
					{#if workstation.phase === 'unconfigured'}
						<a
							href="/servers"
							class="shrink-0 rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent no-underline hover:brightness-110"
						>
							Configure
						</a>
					{:else}
						<button
							class="shrink-0 rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
							onclick={toggleServer}
							disabled={serverBusy}
						>
							{serverBusy
								? 'Working…'
								: workstation.phase === 'running'
									? 'Stop server'
									: 'Start server'}
						</button>
					{/if}
				</div>
				{#if serverError}
					<div class="border-t border-line px-3.5 py-2.5 text-xs text-crit">{serverError}</div>
				{/if}
			</div>

			<!-- Attention. Renders only when something is genuinely true; the empty
			     state here is a real answer, not a placeholder. -->
			<div class="rounded border border-line bg-surface">
				<div class="border-b border-line px-3.5 py-2.5">
					<h2 class="text-body font-semibold">Needs attention</h2>
				</div>
				<div class="space-y-2 p-3.5">
					{#if data.duplicates.length > 0}
						<div
							class="flex gap-2 rounded border border-warn/30 bg-warn-wash px-3 py-2 text-xs text-warn"
						>
							<WarningIcon size={14} class="mt-0.5 shrink-0" />
							<div>
								<strong>Same device configured more than once.</strong>
								These entries resolve to one physical transport, so whichever process opens it second
								is refused.
								{#each data.duplicates as [key, rootList] (key)}
									<div class="mt-1">
										<span class="mono">{key}</span>
										<span class="opacity-80"> ← {rootList.join(', ')}</span>
									</div>
								{/each}
								<div class="mt-1.5">
									<a class="underline" href="/servers/hardware">Hardware ownership →</a>
								</div>
							</div>
						</div>
					{/if}

					{#if data.otherServers.length > 0}
						<div class="rounded border border-line bg-surface-2 px-3 py-2 text-xs text-ink-2">
							{data.otherServers.length} other workspace{data.otherServers.length === 1 ? '' : 's'} on
							this machine
							{data.otherServers.length === 1 ? 'is' : 'are'} running a server:{#each data.otherServers as s, i (s.pid)}{i >
								0
									? ', '
									: ' '}<span class="mono">{workspaceName(s.workspace_path)}</span>{/each}. Their
							instruments are tabs on
							<a class="text-accent underline" href="/instruments">Configured instruments</a>.
						</div>
					{/if}

					{#if data.duplicates.length === 0 && data.otherServers.length === 0}
						<p class="text-xs text-muted">Nothing outstanding.</p>
					{/if}
				</div>
			</div>
		</div>

		<div class="space-y-4">
			<!-- Quick actions start a task; the sidebar reaches a place. Start/stop
			     is a toggle, so it lives on the status panel above rather than
			     pretending to be a destination. -->
			<div>
				<p class="mb-2 text-2xs font-semibold uppercase tracking-[0.11em] text-muted">
					Quick actions
				</p>
				<div class="space-y-2">
					<a
						href="/measurements/new"
						class="flex w-full items-center gap-3 rounded border border-line bg-surface px-3.5 py-3 no-underline transition-colors hover:border-accent hover:bg-accent-wash"
					>
						<span
							class="grid h-7 w-7 shrink-0 place-items-center rounded border border-line bg-surface-2 text-accent"
						>
							<PlusIcon size={14} weight="bold" />
						</span>
						<span class="min-w-0">
							<span class="block text-body font-semibold text-ink">New measurement</span>
							<span class="block text-fine text-muted">
								Pick a type, assign resources, generate a project.
							</span>
						</span>
						<ArrowRightIcon size={13} class="ml-auto shrink-0 text-muted" />
					</a>

					<a
						href="/instruments?add=1"
						class="flex w-full items-center gap-3 rounded border border-line bg-surface px-3.5 py-3 no-underline transition-colors hover:border-accent hover:bg-accent-wash"
					>
						<span
							class="grid h-7 w-7 shrink-0 place-items-center rounded border border-line bg-surface-2 text-accent"
						>
							<CircuitryIcon size={14} />
						</span>
						<span class="min-w-0">
							<span class="block text-body font-semibold text-ink">Add instrument</span>
							<span class="block text-fine text-muted">
								Build a parent chain on this workspace's tree.
							</span>
						</span>
						<ArrowRightIcon size={13} class="ml-auto shrink-0 text-muted" />
					</a>
				</div>
			</div>

			<div class="rounded border border-line bg-surface">
				<div class="flex items-center justify-between border-b border-line px-3.5 py-2.5">
					<h2 class="text-body font-semibold">Recent projects</h2>
					<a class="text-xs text-accent no-underline hover:underline" href="/measurements/projects">
						All →
					</a>
				</div>
				{#if data.projects.length === 0}
					<p class="px-3.5 py-4 text-xs text-muted">No projects generated from this workspace yet.</p>
				{:else}
					<ul>
						{#each data.projects as p (p.name)}
							<li class="border-b border-line last:border-b-0">
								<a
									class="block px-3.5 py-2.5 no-underline hover:bg-surface-2"
									href={`/measurements/run?project=${encodeURIComponent(p.name)}`}
								>
									<div class="mono truncate text-xs font-semibold text-ink">{p.name}</div>
									<div class="text-fine text-muted">
										{p.measurement ?? 'unknown measurement'}{when(p.created)
											? ` · ${when(p.created)}`
											: ''}
									</div>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</div>
	</div>
</section>
