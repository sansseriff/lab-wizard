<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { workstation } from '$lib/stores/workstation.svelte';

	let { children } = $props();

	onMount(() => {
		workstation.refresh();
	});

	// Breadcrumbs come from a table rather than from the path, because the URL
	// segment and the human name differ where it matters (`/servers` is "This
	// workspace", not "Servers / Servers").
	const crumbs: Record<string, [string] | [string, string]> = {
		'/': ['Overview'],
		'/measurements/new': ['Measurements', 'Create'],
		'/measurements/resources': ['Measurements', 'Create'],
		'/measurements/projects': ['Measurements', 'Projects'],
		'/instruments': ['Instruments', 'Configured'],
		'/instruments/custom': ['Instruments', 'Custom resources'],
		'/servers': ['Servers', 'This workspace'],
		'/servers/permissions': ['Servers', 'Permissions'],
		'/servers/hardware': ['Servers', 'Hardware ownership'],
		'/servers/remote': ['Servers', 'Remote servers'],
		'/plotters': ['Plotters'],
		'/data/savers': ['Data', 'Savers'],
		'/data/database': ['Data', 'Database']
	};

	// `trailingSlash: 'always'` means the router reports `/instruments/`; the
	// table above is keyed without one.
	const trail = $derived.by(() => {
		const p = page.url.pathname;
		const key = p !== '/' && p.endsWith('/') ? p.slice(0, -1) : p;
		return crumbs[key] ?? ['Lab Wizard'];
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="flex min-h-screen items-stretch bg-ground text-ink">
	<Sidebar />

	<main class="flex min-w-0 flex-1 flex-col">
		<header
			class="sticky top-0 z-10 flex h-[46px] shrink-0 items-center gap-3 border-b border-line bg-surface px-6"
		>
			<nav class="flex min-w-0 items-center gap-1.5 text-xs text-muted" aria-label="Breadcrumb">
				{#each trail as part, i}
					{#if i > 0}<span class="opacity-45">/</span>{/if}
					<span class={i === trail.length - 1 ? 'font-semibold text-ink' : ''}>{part}</span>
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

		<div class="w-full max-w-[1180px] px-6 pb-16 pt-6">
			{@render children?.()}
		</div>
	</main>
</div>
