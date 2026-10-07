<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { afterNavigate } from '$app/navigation';
	import CaretLeftIcon from 'phosphor-svelte/lib/CaretLeft';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import favicon from '$lib/assets/favicon.svg';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Tooltip } from 'bits-ui';
	import { answer, pending } from '$lib/confirm.svelte';
	import { workstation } from '$lib/stores/workstation.svelte';

	let { children } = $props();

	onMount(() => {
		workstation.refresh();
	});

	// Breadcrumbs come from a table rather than from the path, because the URL
	// segment and the human name differ where it matters (`/servers` is "This
	// workspace"). Every crumb but the last is a link; a section's crumb goes to
	// its home, the page the sidebar opens.
	type Crumb = { label: string; href?: string };
	const MEASUREMENTS: Crumb = { label: 'Measurements', href: '/measurements' };
	const NEW: Crumb = { label: 'New measurement', href: '/measurements/new' };
	const PROCEDURES: Crumb = { label: 'Procedures', href: '/procedures' };
	const INSTRUMENTS: Crumb = { label: 'Instruments', href: '/instruments' };
	const SERVERS: Crumb = { label: 'Servers', href: '/servers' };
	const crumbs: Record<string, Crumb[]> = {
		'/': [{ label: 'Overview' }],
		'/measurements': [MEASUREMENTS],
		'/measurements/new': [MEASUREMENTS, NEW],
		'/measurements/resources': [MEASUREMENTS, NEW, { label: 'Instruments and setup' }],
		'/measurements/run': [MEASUREMENTS],
		'/procedures': [PROCEDURES],
		'/procedures/edit': [PROCEDURES, { label: 'New procedure' }],
		'/instruments': [INSTRUMENTS],
		'/instruments/custom': [INSTRUMENTS, { label: 'Custom resources' }],
		'/setups': [{ label: 'Setups' }],
		'/servers': [SERVERS],
		'/servers/permissions': [SERVERS, { label: 'Permissions' }],
		'/servers/hardware': [SERVERS, { label: 'Hardware ownership' }],
		'/servers/remote': [SERVERS, { label: 'Remote servers' }],
		'/data': [{ label: 'Data' }],
		'/settings': [{ label: 'Settings' }]
	};

	// `trailingSlash: 'always'` means the router reports `/instruments/`; the
	// table above is keyed without one.
	const key = $derived(page.url.pathname.replace(/(.)\/$/, '$1'));
	const trail = $derived.by(() => {
		const base = crumbs[key] ?? [{ label: 'Lab Wizard' }];
		// A page about one thing ends in that thing.
		const named =
			key === '/measurements/run'
				? page.url.searchParams.get('project')
				: key === '/procedures/edit'
					? page.url.searchParams.get('name')
					: null;
		return named ? [...base.slice(0, 1), { label: named }] : base;
	});
	const wide = $derived(['/data', '/measurements/run'].includes(key));
	// The Create list runs the height of the window, so it has no foot padding to spare.
	const fill = $derived(key === '/measurements/new');

	// Back and forward are the browser's own history, which the pages keep
	// meaningful: a page's tabs and filters replace their entry instead of
	// pushing one, so Back leaves the page. The buttons are for windows with no
	// browser chrome. `navigation` tells whether there is anywhere to go; where
	// it does not exist (Firefox, Safari) they stay enabled.
	let canBack = $state(true);
	let canForward = $state(true);
	function syncHistory() {
		const nav = (window as unknown as { navigation?: { canGoBack: boolean; canGoForward: boolean } }).navigation;
		if (!nav) return;
		canBack = nav.canGoBack;
		canForward = nav.canGoForward;
	}
	afterNavigate(syncHistory);
	onMount(syncHistory);
	// The live page a web plotter opens shows one run and nothing of the wizard.
	const bare = $derived(page.url.pathname.startsWith('/live'));
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<Tooltip.Provider delayDuration={300}>
{#if bare}
	<div class="min-h-screen bg-ground text-ink">{@render children?.()}</div>
{:else}
<div class="flex min-h-screen items-stretch bg-ground text-ink">
	<Sidebar />

	<main class="flex min-w-0 flex-1 flex-col">
		<header
			class="sticky top-0 z-10 flex h-[46px] shrink-0 items-center gap-3 border-b border-line bg-surface px-6"
		>
			<div class="flex shrink-0 items-center">
				<button
					class="grid size-6 place-items-center rounded text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-35 disabled:hover:bg-transparent"
					aria-label="Back"
					title="Back"
					disabled={!canBack}
					onclick={() => history.back()}><CaretLeftIcon size={14} /></button
				>
				<button
					class="grid size-6 place-items-center rounded text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-35 disabled:hover:bg-transparent"
					aria-label="Forward"
					title="Forward"
					disabled={!canForward}
					onclick={() => history.forward()}><CaretRightIcon size={14} /></button
				>
			</div>

			<nav class="flex min-w-0 items-center gap-1.5 text-xs text-muted" aria-label="Breadcrumb">
				{#each trail as part, i}
					{#if i > 0}<span class="opacity-45">/</span>{/if}
					{#if i === trail.length - 1}
						<span class="font-semibold text-ink" aria-current="page">{part.label}</span>
					{:else if part.href}
						<a href={part.href} class="no-underline hover:text-ink hover:underline">{part.label}</a>
					{:else}
						<span>{part.label}</span>
					{/if}
				{/each}
			</nav>

			<div class="ml-auto flex items-center gap-2">
				<!-- Three states, not two. A stopped server is a normal steady state;
				     an unconfigured workspace is a different thing entirely, and the
				     fix for it is Configure rather than Start. -->
				{#if workstation.error}
					<Pill tone="crit" dot title={workstation.error}>Backend unreachable</Pill>
				{:else if workstation.phase === 'running'}
					<Pill tone="ok" dot>Server running</Pill>
				{:else if workstation.phase === 'stopped'}
					<Pill tone="warn" dot>Server stopped</Pill>
				{:else}
					<Pill tone="neutral">No server configured</Pill>
				{/if}

				<Pill
					tone="neutral"
					title={workstation.gateActive
						? 'Calls route through the server, so permission rules apply.'
						: 'The wizard opens hardware directly; permission rules are not consulted.'}
				>
					Hardware: {workstation.owner?.owner ?? '—'}
				</Pill>
			</div>
		</header>

		<!-- The Data page is a workbench of side-by-side panels and needs the
		     whole window; everything else reads best at a comfortable width. -->
		<div
			class={wide
				? 'w-full px-4 pt-4'
				: fill
					? 'w-full max-w-[1180px] px-6 pb-4 pt-6'
					: 'w-full max-w-[1180px] px-6 pb-16 pt-6'}
		>
			{@render children?.()}
		</div>
	</main>
</div>
{/if}
</Tooltip.Provider>

{#if pending.current}
	<ConfirmDialog
		open
		title={pending.current.title}
		description={pending.current.description}
		confirmLabel={pending.current.confirmLabel}
		tone={pending.current.tone}
		onconfirm={() => answer(true)}
		oncancel={() => answer(false)}
	/>
{/if}
