<script lang="ts">
	import SectionTabs from '$lib/components/SectionTabs.svelte';

	let { children } = $props();
</script>

<SectionTabs
	label="Instruments"
	tabs={[
		{ href: '/instruments', label: 'Configured' },
		{ href: '/instruments/custom', label: 'Custom resources' }
	]}
/>
{@render children()}
