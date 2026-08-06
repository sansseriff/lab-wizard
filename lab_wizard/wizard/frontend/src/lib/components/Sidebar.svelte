<script lang="ts">
	/** The persistent navigation rail.
	 *
	 * Six top-level sections, each a noun rather than a task. A section with
	 * sub-pages expands when it is the active one; expanding is not a navigation
	 * of its own, so clicking the header goes to the section's first page.
	 *
	 * Sub-pages stay hidden until their section is active. The rail is meant to
	 * be readable at a glance, and thirteen always-visible entries is a menu, not
	 * an orientation.
	 */
	import { page } from '$app/state';
	import logo from '$lib/assets/logo.svg';
	import { workstation } from '$lib/stores/workstation.svelte';

	import GaugeIcon from 'phosphor-svelte/lib/Gauge';
	import PulseIcon from 'phosphor-svelte/lib/Pulse';
	import CircuitryIcon from 'phosphor-svelte/lib/Circuitry';
	import HardDrivesIcon from 'phosphor-svelte/lib/HardDrives';
	import ChartLineIcon from 'phosphor-svelte/lib/ChartLine';
	import DatabaseIcon from 'phosphor-svelte/lib/Database';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';

	type SubItem = { href: string; label: string };
	type Section = {
		href: string;
		label: string;
		icon: any;
		/** Every path under this section, for deciding what is active. */
		match: string;
		children?: SubItem[];
	};

	const sections: Section[] = [
		{ href: '/', label: 'Overview', icon: GaugeIcon, match: '/' },
		{
			href: '/measurements/new',
			label: 'Measurements',
			icon: PulseIcon,
			match: '/measurements',
			children: [
				{ href: '/measurements/new', label: 'Create' },
				{ href: '/measurements/projects', label: 'Projects' }
			]
		},
		{
			href: '/instruments',
			label: 'Instruments',
			icon: CircuitryIcon,
			match: '/instruments',
			children: [
				{ href: '/instruments', label: 'Configured' },
				{ href: '/instruments/custom', label: 'Custom resources' }
			]
		},
		{
			href: '/servers',
			label: 'Servers',
			icon: HardDrivesIcon,
			match: '/servers',
			children: [
				{ href: '/servers', label: 'This workspace' },
				{ href: '/servers/permissions', label: 'Permissions' },
				{ href: '/servers/hardware', label: 'Hardware ownership' },
				{ href: '/servers/remote', label: 'Remote servers' }
			]
		},
		{ href: '/plotters', label: 'Plotters', icon: ChartLineIcon, match: '/plotters' },
		{
			href: '/data/savers',
			label: 'Data',
			icon: DatabaseIcon,
			match: '/data',
			children: [
				{ href: '/data/savers', label: 'Savers' },
				{ href: '/data/database', label: 'Database' }
			]
		}
	];

	// `trailingSlash: 'always'` means the router reports `/instruments/`, while
	// the hrefs here are written without one. Normalising once is what keeps
	// both the section highlight and the sub-page highlight honest.
	const path = $derived(normalize(page.url.pathname));

	function normalize(p: string): string {
		return p !== '/' && p.endsWith('/') ? p.slice(0, -1) : p;
	}

	function isActiveSection(section: Section): boolean {
		if (section.match === '/') return path === '/';
		return path === section.match || path.startsWith(section.match + '/');
	}

	// Exact match only: `/instruments` and `/instruments/custom` are siblings in
	// the sub-list, so a prefix test would light both.
	function isActivePage(href: string): boolean {
		return path === normalize(href);
	}
</script>

<aside
	class="sticky top-0 flex h-screen w-[246px] shrink-0 flex-col overflow-y-auto border-r border-line bg-surface"
>
	<div class="flex flex-col gap-2.5 border-b border-line px-4 pb-3 pt-4">
		<a href="/" class="flex items-center gap-2.5 no-underline">
			<img src={logo} alt="" class="h-6 w-6" />
			<span class="text-xs font-semibold uppercase tracking-[0.13em] text-ink">Lab Wizard</span>
		</a>

		<div class="flex flex-col gap-0.5 rounded border border-line bg-surface-2 px-2.5 py-1.5">
			<span class="text-[10px] uppercase tracking-[0.09em] text-muted">Workspace</span>
			<span class="mono truncate text-xs font-semibold" title={workstation.workspaceDir}>
				{workstation.workspaceName}
			</span>
		</div>
	</div>

	<nav class="flex flex-1 flex-col gap-px p-2">
		{#each sections as section (section.href)}
			{@const active = isActiveSection(section)}
			{@const Icon = section.icon}
			<a
				href={section.href}
				aria-current={active ? 'page' : undefined}
				class="flex items-center gap-2.5 rounded px-2.5 py-1.5 text-[13.5px] no-underline transition-colors
					{active
					? 'bg-accent-wash font-semibold text-accent-strong'
					: 'text-ink-2 hover:bg-surface-2 hover:text-ink'}"
			>
				<Icon size={15} weight={active ? 'fill' : 'regular'} />
				{section.label}
				{#if section.children}
					<CaretRightIcon
						size={10}
						class="ml-auto opacity-50 transition-transform {active ? 'rotate-90' : ''}"
					/>
				{/if}
			</a>

			{#if section.children && active}
				<div class="flex flex-col gap-px py-0.5 pl-8">
					{#each section.children as child (child.href)}
						{@const childActive = isActivePage(child.href)}
						<a
							href={child.href}
							aria-current={childActive ? 'page' : undefined}
							class="-ml-px rounded border-l-[1.5px] px-2.5 py-1 text-xs no-underline transition-colors
								{childActive
								? 'border-accent font-semibold text-accent-strong'
								: 'border-line text-muted hover:bg-surface-2 hover:text-ink'}"
						>
							{child.label}
						</a>
					{/each}
				</div>
			{/if}
		{/each}
	</nav>

	<div class="border-t border-line px-4 py-3">
		<p class="text-[11px] leading-relaxed text-muted">
			{#if workstation.error}
				Wizard backend unreachable.
			{:else if workstation.phase === 'running'}
				Instrument server running — it owns this workspace's hardware.
			{:else if workstation.phase === 'stopped'}
				Server stopped. The wizard opens hardware itself, and safety rules are inert.
			{:else}
				Not a hardware host. This workspace is a client of other servers.
			{/if}
		</p>
	</div>
</aside>
