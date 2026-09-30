<script lang="ts">
	/** The persistent navigation rail.
	 *
	 * Seven top-level sections, each a noun rather than a task, and nothing
	 * under them. Each goes to its section's most useful page; a section with
	 * more than one page shows them as tabs across the top (SectionTabs).
	 */
	import { page } from '$app/state';
	import logo from '$lib/assets/logo.svg';
	import { workstation } from '$lib/stores/workstation.svelte';

	import GaugeIcon from 'phosphor-svelte/lib/Gauge';
	import PulseIcon from 'phosphor-svelte/lib/Pulse';
	import TreeStructureIcon from 'phosphor-svelte/lib/TreeStructure';
	import CircuitryIcon from 'phosphor-svelte/lib/Circuitry';
	import HardDrivesIcon from 'phosphor-svelte/lib/HardDrives';
	import DatabaseIcon from 'phosphor-svelte/lib/Database';
	import GearSixIcon from 'phosphor-svelte/lib/GearSix';

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
	class="sticky top-0 flex h-screen w-[246px] shrink-0 flex-col overflow-y-auto border-r border-line bg-surface"
>
	<div class="flex flex-col gap-2.5 border-b border-line px-4 pb-3 pt-4">
		<a href="/" class="flex items-center gap-2.5 no-underline">
			<img src={logo} alt="" class="h-6 w-6" />
			<span class="text-xs font-semibold uppercase tracking-[0.13em] text-ink">Lab Wizard</span>
		</a>

		<div class="flex flex-col gap-0.5 rounded border border-line bg-surface-2 px-2.5 py-1.5">
			<span class="text-2xs uppercase tracking-[0.09em] text-muted">Workspace</span>
			<span class="mono truncate text-xs font-semibold" title={workstation.workspaceDir}>
				{workstation.workspaceName}
			</span>
		</div>
	</div>

	<nav class="flex flex-1 flex-col gap-px p-2">
		{#each sections as section (section.href)}
			{@const active = isActive(section)}
			{@const Icon = section.icon}
			<a
				href={section.href}
				aria-current={active ? 'page' : undefined}
				class="flex items-center gap-2.5 rounded px-2.5 py-1.5 text-body no-underline transition-colors
					{active
					? 'bg-accent-wash font-semibold text-accent-strong'
					: 'text-ink-2 hover:bg-surface-2 hover:text-ink'}"
			>
				<Icon size={15} weight={active ? 'fill' : 'regular'} />
				{section.label}
			</a>
		{/each}
	</nav>

	{#if workstation.error}
		<p class="border-t border-line px-4 py-3 text-fine text-crit">Wizard backend unreachable.</p>
	{/if}
</aside>
