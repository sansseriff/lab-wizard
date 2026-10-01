<script lang="ts">
	/** The persistent navigation rail.
	 *
	 * Seven top-level sections, each a noun rather than a task, and nothing
	 * under them. Each goes to its section's most useful page; a section with
	 * more than one page shows them as tabs across the top (SectionTabs).
	 */
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import logo from '$lib/assets/logo.svg';
	import { workstation } from '$lib/stores/workstation.svelte';

	import GaugeIcon from 'phosphor-svelte/lib/Gauge';
	import PulseIcon from 'phosphor-svelte/lib/Pulse';
	import TreeStructureIcon from 'phosphor-svelte/lib/TreeStructure';
	import CircuitryIcon from 'phosphor-svelte/lib/Circuitry';
	import HardDrivesIcon from 'phosphor-svelte/lib/HardDrives';
	import DatabaseIcon from 'phosphor-svelte/lib/Database';
	import GearSixIcon from 'phosphor-svelte/lib/GearSix';
	import CaretDoubleLeftIcon from 'phosphor-svelte/lib/CaretDoubleLeft';

	const storageKey = 'lab-wizard-sidebar-collapsed';
	let collapsed = $state(false);
	let motionReady = $state(false);

	onMount(() => {
		collapsed = localStorage.getItem(storageKey) === 'true';
		requestAnimationFrame(() => {
			motionReady = true;
		});
	});

	function toggleCollapsed() {
		collapsed = !collapsed;
		localStorage.setItem(storageKey, String(collapsed));
	}

	type Section = { href: string; label: string; icon: any };

	const sections: Section[] = [
		{ href: '/', label: 'Overview', icon: GaugeIcon },
		{ href: '/measurements', label: 'Measurements', icon: PulseIcon },
		// Beside Measurements: a procedure is what a measurement is created from.
		{ href: '/procedures', label: 'Procedures', icon: TreeStructureIcon },
		{ href: '/instruments', label: 'Instruments', icon: CircuitryIcon },
		{ href: '/servers', label: 'Servers', icon: HardDrivesIcon },
		{ href: '/data', label: 'Data', icon: DatabaseIcon },
		{ href: '/settings', label: 'Settings', icon: GearSixIcon }
	];

	// `trailingSlash: 'always'` means the router reports `/instruments/`, while
	// the hrefs here are written without one.
	const path = $derived(page.url.pathname.replace(/(.)\/$/, '$1'));

	function isActive(section: Section): boolean {
		if (section.href === '/') return path === '/';
		return path === section.href || path.startsWith(section.href + '/');
	}
</script>

<aside
	class:collapsed
	class:motion-ready={motionReady}
	class="sidebar sticky top-0 flex h-screen shrink-0 flex-col overflow-y-auto border-r border-line bg-surface"
>
	<div class="sidebar-header border-b border-line pb-3 pt-4">
		<a href="/" aria-label="Lab Wizard home" class="brand-link no-underline">
			<img src={logo} alt="" class="size-6 shrink-0" />
			<span class="brand-label text-xs font-semibold uppercase tracking-[0.13em] text-ink"
				>Lab Wizard</span
			>
		</a>
	</div>

	<div class="sidebar-actions border-b border-line px-2 py-1">
		<button
			type="button"
			aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
			title={collapsed ? 'Expand sidebar' : undefined}
			aria-expanded={!collapsed}
			onclick={toggleCollapsed}
			class="nav-link flex w-full cursor-pointer items-center rounded text-body text-muted transition-colors hover:bg-surface-2 hover:text-ink-2"
		>
			<span class="nav-icon"><span class="toggle-icon"><CaretDoubleLeftIcon size={18} /></span></span>
			<span class="nav-label">Collapse sidebar</span>
		</button>
	</div>

	<nav class="flex flex-1 flex-col gap-px p-2" aria-label="Main navigation">
		{#each sections as section (section.href)}
			{@const active = isActive(section)}
			{@const Icon = section.icon}
			<a
				href={section.href}
				aria-label={section.label}
				aria-current={active ? 'page' : undefined}
				title={collapsed ? section.label : undefined}
				class="nav-link flex items-center rounded text-body no-underline transition-colors
					{active
						? 'bg-accent-wash font-semibold text-accent-strong'
						: 'text-ink-2 hover:bg-surface-2 hover:text-ink'}"
			>
				<span class="nav-icon"><Icon size={18} weight={active ? 'fill' : 'regular'} /></span>
				<span class="nav-label">{section.label}</span>
			</a>
		{/each}
	</nav>

	{#if workstation.error}
		<p class="sidebar-error border-t border-line px-4 py-3 text-fine text-crit">
			Wizard backend unreachable.
		</p>
	{/if}

	<div class="workspace-footer border-t border-line text-fine text-muted">
		<span class="workspace-footer-label mono block truncate" title={workstation.workspaceDir}
			>W: {workstation.workspaceName}</span
		>
	</div>
</aside>

<style>
	.sidebar:not(.motion-ready),
	.sidebar:not(.motion-ready) * {
		transition-duration: 0ms !important;
	}

	.sidebar {
		width: 246px;
		transition: width 240ms ease;
	}

	.sidebar.collapsed {
		width: 60px;
	}

	.sidebar-header {
		padding-inline: 16px;
		transition: padding-inline 240ms ease;
	}

	.collapsed .sidebar-header {
		padding-inline: 8px;
	}

	.brand-link {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 10px;
		height: 24px;
		overflow: hidden;
		white-space: nowrap;
		transition: gap 240ms ease;
	}

	.collapsed .brand-link {
		gap: 0;
	}

	.brand-label {
		max-width: 120px;
		overflow: hidden;
		transition: max-width 240ms ease, opacity 180ms ease, transform 240ms ease;
	}

	.collapsed .brand-label {
		max-width: 0;
		opacity: 0;
		transform: translateX(-12px);
	}

	.toggle-icon {
		display: grid;
		place-items: center;
		transition: transform 240ms ease;
	}

	.collapsed .toggle-icon {
		transform: rotate(180deg);
	}

	.nav-link {
		height: 36px;
		min-width: 0;
		overflow: hidden;
		white-space: nowrap;
	}

	.nav-icon {
		display: grid;
		width: 44px;
		height: 36px;
		flex: 0 0 44px;
		place-items: center;
	}

	.nav-label {
		transition: opacity 180ms ease, transform 240ms ease;
	}

	.collapsed .nav-label {
		opacity: 0;
		transform: translateX(-12px);
	}

	.sidebar-error {
		max-height: 52px;
		overflow: hidden;
		white-space: nowrap;
		transition: max-height 240ms ease, opacity 180ms ease, padding 240ms ease;
	}

	.collapsed .sidebar-error {
		max-height: 0;
		opacity: 0;
		padding-block: 0;
	}

	.workspace-footer {
		max-height: 40px;
		padding: 10px 16px;
		overflow: hidden;
		white-space: nowrap;
		transition: max-height 240ms ease, opacity 180ms ease, padding 240ms ease;
	}

	.collapsed .workspace-footer {
		max-height: 0;
		opacity: 0;
		padding-block: 0;
	}

	.workspace-footer-label {
		transition: opacity 180ms ease, transform 240ms ease;
	}

	.collapsed .workspace-footer-label {
		opacity: 0;
		transform: translateX(-12px);
	}

	@media (prefers-reduced-motion: reduce) {
		.sidebar,
		.sidebar-header,
		.brand-link,
		.brand-label,
		.toggle-icon,
		.nav-label,
		.sidebar-error,
		.workspace-footer,
		.workspace-footer-label {
			transition-duration: 0ms;
		}
	}
</style>
