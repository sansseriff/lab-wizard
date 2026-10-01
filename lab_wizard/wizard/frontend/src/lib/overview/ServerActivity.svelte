<!--
  This workspace's instrument server: whether it runs, a switch for it, and
  the instrument calls going through it — the ones in flight, then the most
  recent. Only calls routed through the server are here: a project that opens
  its hardware directly never passes through it, and the footer says so.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage, unwrap } from '$lib/api';
	import Pill from '$lib/components/Pill.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { workstation } from '$lib/stores/workstation.svelte';

	type Call = {
		id: number;
		started: number;
		path: string;
		method: string;
		args: string;
		caller: string | null;
		holder: string | null;
		ok?: boolean;
		error?: string | null;
		duration_ms?: number;
		elapsed_ms?: number;
	};
	type Calls = {
		state: 'ok' | 'stopped' | 'outdated' | 'error';
		active?: Call[];
		recent?: Call[];
		total?: number;
		since?: number;
		error?: string;
	};

	const POLL_MS = 1500;

	let calls = $state<Calls | null>(null);
	let busy = $state(false);
	let switchError = $state<string | null>(null);

	async function load() {
		if (document.hidden || workstation.phase !== 'running') return;
		try {
			calls = await unwrap<Calls>(api.GET('/api/server/calls', { params: { query: { limit: 60 } } }));
		} catch (e) {
			calls = { state: 'error', error: errorMessage(e) };
		}
	}

	onMount(() => {
		load();
		const timer = setInterval(load, POLL_MS);
		return () => clearInterval(timer);
	});

	/** Restart in the same mode it runs in now: a detached server stays detached. */
	async function restart() {
		busy = true;
		switchError = null;
		try {
			await unwrap(api.POST('/api/server/restart', { body: { detached: !!workstation.server?.detached } }));
			await workstation.refresh();
			calls = null;
			load();
		} catch (e) {
			switchError = errorMessage(e) || 'Restart failed.';
		} finally {
			busy = false;
		}
	}

	async function toggle() {
		const stopping = workstation.phase === 'running';
		busy = true;
		switchError = null;
		try {
			if (stopping) await api.POST('/api/server/stop');
			else await api.POST('/api/server/start', { body: { detached: false } });
			await workstation.refresh();
			calls = null;
			load();
		} catch (e) {
			switchError = errorMessage(e) || 'Server action failed.';
		} finally {
			busy = false;
		}
	}

	const clock = (ts: number) =>
		new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
	const ms = (value: number) => (value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`);
	const since = (ts: number) =>
		new Date(ts * 1000).toDateString() === new Date().toDateString()
			? clock(ts)
			: new Date(ts * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
	const where = (path: string) => path.replace(/^inst:\/\//, '');
	const who = (c: Call) => [c.holder, c.caller].filter(Boolean).join(' · ');
	const rules = $derived(workstation.server?.rule_count ?? 0);
</script>

<section class="flex min-h-0 flex-col rounded border border-line bg-surface" aria-label="Instrument server">
	<header class="flex items-center gap-3 border-b border-line px-3.5 py-2.5">
		<div class="min-w-0 flex-1">
			<div class="flex items-center gap-2">
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
			</div>
			{#if workstation.phase !== 'unconfigured'}
				<p class="mt-0.5 truncate text-fine text-muted">
					{#if workstation.server?.bind}<span class="mono">{workstation.server.bind}</span> ·{/if}
					<a class="text-muted hover:underline" href="/servers/permissions"
						>{rules} safety rule{rules === 1 ? '' : 's'}</a
					>
				</p>
			{/if}
		</div>
		{#if workstation.phase === 'unconfigured'}
			<a class="lw-btn lw-btn-sm no-underline" href="/servers">Configure</a>
		{:else}
			<button class="lw-btn lw-btn-sm" onclick={toggle} disabled={busy}>
				{busy ? 'Working…' : workstation.phase === 'running' ? 'Stop' : 'Start'}
			</button>
		{/if}
	</header>

	{#if switchError}
		<p class="border-b border-line px-3.5 py-2 text-xs text-crit" role="alert">{switchError}</p>
	{/if}

	{#if workstation.phase !== 'running'}
		<div class="grid flex-1 place-items-center p-6 text-center text-xs text-muted">
			{workstation.phase === 'unconfigured'
				? 'This workspace does not host a server. Projects open their instruments directly.'
				: 'The instrument calls it routes appear here while it runs.'}
		</div>
	{:else if !calls}
		<p class="p-3.5 text-xs text-muted">Asking the server…</p>
	{:else if calls.state === 'outdated'}
		<div class="grid flex-1 place-items-center p-6 text-center text-xs text-muted">
			<div class="max-w-[40ch] space-y-3">
				<p>
					This server was started before it listed its calls, so it has none to show. Restarting it
					ends any run using it now.
				</p>
				<button class="lw-btn lw-btn-sm" onclick={restart} disabled={busy}>
					{busy ? 'Restarting…' : 'Restart server'}
				</button>
			</div>
		</div>
	{:else if calls.state !== 'ok'}
		<p class="p-3.5 text-xs text-crit" role="alert">{calls.error ?? 'The server did not answer.'}</p>
	{:else}
		<ScrollArea class="min-h-0 flex-1">
			{#if !calls.active?.length && !calls.recent?.length}
				<p class="p-6 text-center text-xs text-muted">No instrument calls yet since the server started.</p>
			{/if}
			<ul class="text-xs" aria-label="Instrument calls">
				{#each calls.active ?? [] as c (c.id)}
					<li class="flex items-baseline gap-3 border-b border-line bg-accent-wash/50 px-3.5 py-1.5">
						<span class="mono w-16 shrink-0 text-accent-strong">now</span>
						<div class="min-w-0 flex-1">
							<p class="mono truncate"><span class="text-muted">{where(c.path)}</span> {c.method}({c.args})</p>
							{#if who(c)}<p class="truncate text-fine text-muted">{who(c)}</p>{/if}
						</div>
						<span class="shrink-0 tabular-nums text-accent-strong">{ms(c.elapsed_ms ?? 0)}…</span>
					</li>
				{/each}
				{#each calls.recent ?? [] as c (c.id)}
					<li class="flex items-baseline gap-3 border-b border-line px-3.5 py-1.5 last:border-b-0" title={c.error ?? undefined}>
						<span class="mono w-16 shrink-0 tabular-nums text-muted">{clock(c.started)}</span>
						<div class="min-w-0 flex-1">
							<p class="mono truncate {c.ok ? '' : 'text-crit'}">
								<span class={c.ok ? 'text-muted' : ''}>{where(c.path)}</span> {c.method}({c.args})
							</p>
							{#if !c.ok}
								<p class="truncate text-fine text-crit">{c.error}</p>
							{:else if who(c)}
								<p class="truncate text-fine text-muted">{who(c)}</p>
							{/if}
						</div>
						<span class="shrink-0 tabular-nums text-muted">{ms(c.duration_ms ?? 0)}</span>
					</li>
				{/each}
			</ul>
		</ScrollArea>
		<p class="border-t border-line px-3.5 py-2 text-fine text-muted">
			{calls.total ?? 0} call{calls.total === 1 ? '' : 's'} since {calls.since ? since(calls.since) : 'it started'}.
			Only calls through the server show here, not a project opening its hardware directly.
		</p>
	{/if}
</section>
