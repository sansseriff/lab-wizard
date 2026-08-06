<script lang="ts">
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { goto, preloadData } from '$app/navigation';
	type MeasurementInfo = { name: string; description: string; measurement_dir: string };
	let loading = false;
	let { data } = $props();
	const measurements: MeasurementInfo[] = (data?.measurements ?? []) as MeasurementInfo[];
	let selectedName = $state<string | null>(null);
	// let selected: MeasurementInfo | null = $state(null);

	function onNext() {
		if (!selectedName) return;
		goto(`/measurements/resources?name=${encodeURIComponent(selectedName)}`);
	}

	async function onSelectionChange(name: string) {
		selectedName = name;
		// Prefetch the resource-selection page data when a measurement is selected
		try {
			await preloadData(`/measurements/resources?name=${encodeURIComponent(name)}`);
		} catch (error) {
			// Silently fail if prefetching doesn't work - it's just a performance optimization
			console.debug('Failed to prefetch resource-selection page:', error);
		}
	}
</script>

<section class="space-y-4">
	<h1 class="text-2xl font-semibold">Choose a measurement</h1>
	<p class="text-sm text-ink-2">
		Pick a measurement type, then continue to select instruments.
	</p>

	<ScrollArea
		type="hover"
		class="relative overflow-hidden rounded border border-line bg-surface p-3 shadow-sm"
		orientation="vertical"
		viewportClasses="h-full max-h-[360px] w-full"
	>
		<ul class="divide-y divide-line">
			{#each measurements as m}
				<li>
					<button
						class={`w-full rounded-md px-3 py-3 text-left transition ${selectedName === m.name ? 'bg-accent-wash ring-1 ring-accent' : ''} hover:bg-accent-wash active:scale-[.99] active:bg-accent-wash`}
						onclick={() => onSelectionChange(m.name)}
					>
						<div class="flex items-start gap-3">
							<div>
								<div class="font-medium">{m.name}</div>
								<div class="text-xs text-ink-2">{m.description}</div>
								<div class="truncate text-[10px] text-muted">
									{m.measurement_dir}
								</div>
							</div>
						</div>
					</button>
				</li>
			{/each}
		</ul>
	</ScrollArea>

	<div class="flex justify-end">
		<button
			class="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
			onclick={onNext}
			disabled={!selectedName || loading}
		>
			Next
		</button>
	</div>
</section>
