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
	import Callout from '$lib/components/Callout.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import ServerActivity from '$lib/overview/ServerActivity.svelte';
	import { workspaceName } from '$lib/types/instruments';

	let { data } = $props();

	const heldCount = $derived(data.roots.filter((r) => r.held_by_server).length);

	// Each count is a way in: the page that holds what it counts.
	const stats = $derived([
		{
			label: 'Instruments',
			value: data.instrumentCount,
			note: `configured · ${data.roots.length} root transport${data.roots.length === 1 ? '' : 's'}`,
			href: '/instruments'
		},
		{
			label: 'Open now',
			value: heldCount,
			note: heldCount === 0 ? 'no server holds a rack' : 'held by a server',
			href: '/servers/hardware'
		},
		{ label: 'Projects', value: data.projectCount, note: 'generated', href: '/measurements' },
		{ label: 'Remote servers', value: data.remoteCount, note: 'in address book', href: '/servers/remote' }
	]);

	function when(iso: string | null): string | null {
		if (!iso) return null;
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return null;
		return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
	}
</script>

<section class="space-y-5">
	<PageHeader title="Overview" lede="What this workspace has, and what its server is doing now.">
		{#snippet actions()}
			<a class="lw-btn no-underline" href="/instruments?add=1">Add instrument</a>
			<a class="lw-btn lw-btn-primary no-underline" href="/measurements/new">New measurement</a>
		{/snippet}
	</PageHeader>

	<!-- Counts. Every label states which kind of fact it is. -->
	<div class="grid grid-cols-2 gap-px overflow-hidden rounded border border-line bg-line md:grid-cols-4">
		{#each stats as stat (stat.label)}
			<a class="flex flex-col gap-0.5 bg-surface px-4 py-3 no-underline hover:bg-surface-2" href={stat.href}>
				<span class="text-2xs uppercase tracking-[0.09em] text-muted">{stat.label}</span>
				<span class="text-xl font-semibold tabular-nums text-ink">{stat.value}</span>
				<span class="text-fine text-muted">{stat.note}</span>
			</a>
		{/each}
	</div>

	<!-- Only what is actually true; no box saying nothing is wrong. -->
	{#if data.duplicates.length > 0}
		<Callout tone="warn" title="Same device configured more than once. ">
			These entries resolve to one physical transport, so whichever process opens it second is refused.
			{#each data.duplicates as [key, rootList] (key)}
				<span class="mt-1 block"><span class="mono">{key}</span> ← {rootList.join(', ')}</span>
			{/each}
			<a class="mt-1.5 inline-block underline" href="/servers/hardware">Hardware ownership →</a>
		</Callout>
	{/if}
	{#if data.otherServers.length > 0}
		<Callout tone="info">
			{data.otherServers.length} other workspace{data.otherServers.length === 1 ? '' : 's'} on this machine
			{data.otherServers.length === 1 ? 'is' : 'are'} running a server:{#each data.otherServers as s, i (s.pid)}{i > 0
					? ', '
					: ' '}<span class="mono">{workspaceName(s.workspace_path)}</span>{/each}. Their instruments are tabs
			on <a class="underline" href="/instruments">Configured instruments</a>.
		</Callout>
	{/if}

	<!-- Two panels of one height: what is happening, and what was made. -->
	<div class="grid gap-4 lg:grid-cols-2 [&>*]:h-[28rem]">
		<ServerActivity />

		<section class="flex min-h-0 flex-col rounded border border-line bg-surface" aria-label="Recent projects">
			<header class="flex items-center justify-between border-b border-line px-3.5 py-2.5">
				<div>
					<h2 class="text-body font-semibold">Recent projects</h2>
					<p class="mt-0.5 text-fine text-muted">Open one to set it up and run it.</p>
				</div>
				<a class="lw-btn lw-btn-sm no-underline" href="/measurements">All projects</a>
			</header>
			{#if data.projects.length === 0}
				<div class="grid flex-1 place-items-center p-6 text-center text-xs text-muted">
					No projects generated from this workspace yet.
				</div>
			{:else}
				<ScrollArea class="min-h-0 flex-1">
					<ul>
						{#each data.projects as p (p.name)}
							<li class="border-b border-line last:border-b-0">
								<a
									class="flex items-baseline gap-3 px-3.5 py-2.5 no-underline hover:bg-surface-2"
									href={`/measurements/run?project=${encodeURIComponent(p.name)}`}
								>
									<span class="min-w-0 flex-1">
										<span class="mono block truncate text-xs font-semibold text-ink">{p.name}</span>
										<span class="block text-fine text-muted">{p.measurement ?? 'unknown measurement'}</span>
									</span>
									<span class="shrink-0 text-fine tabular-nums text-muted">{when(p.created) ?? ''}</span>
								</a>
							</li>
						{/each}
					</ul>
				</ScrollArea>
			{/if}
		</section>
	</div>
</section>
