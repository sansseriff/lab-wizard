<script lang="ts">
	import SectionTabs from '$lib/components/SectionTabs.svelte';

	let { children } = $props();
</script>

<SectionTabs
	label="Servers"
	tabs={[
		{ href: '/servers', label: 'This workspace' },
		{ href: '/servers/permissions', label: 'Permissions' },
		{ href: '/servers/hardware', label: 'Hardware ownership' },
		{ href: '/servers/remote', label: 'Remote servers' }
	]}
/>
{@render children()}
