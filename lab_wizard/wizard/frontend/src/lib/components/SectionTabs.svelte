<!--
  The pages of one sidebar section, as a row of tabs across the top of each.
  The sidebar goes to a section's home page; these move between its pages.
  Tabs are links, so each page keeps its own URL and Back steps between them.
-->
<script lang="ts">
	import { page } from '$app/state';

	let { tabs, label }: { tabs: { href: string; label: string }[]; label: string } = $props();

	// `trailingSlash: 'always'` means the router reports `/servers/`.
	const path = $derived(page.url.pathname.replace(/(.)\/$/, '$1'));
</script>

<nav class="lw-tabs mb-5" aria-label={label}>
	{#each tabs as tab (tab.href)}
		{@const active = path === tab.href}
		<a
			href={tab.href}
			class="lw-tab no-underline"
			data-state={active ? 'active' : undefined}
			aria-current={active ? 'page' : undefined}>{tab.label}</a
		>
	{/each}
</nav>
