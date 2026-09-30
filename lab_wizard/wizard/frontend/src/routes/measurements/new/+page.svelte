<script lang="ts">
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { goto, preloadData } from '$app/navigation';
	import type { MeasurementChoice } from './+page';

	let { data } = $props();
	const choices: MeasurementChoice[] = $derived((data?.choices ?? []) as MeasurementChoice[]);
	let selected = $state<MeasurementChoice | null>(null);

	function resourcesUrl(choice: MeasurementChoice): string {
		return `/measurements/resources?name=${encodeURIComponent(choice.name)}&kind=${choice.kind}`;
	}

	function onNext() {
		if (selected) goto(resourcesUrl(selected));
	}

	async function onSelectionChange(choice: MeasurementChoice) {
		if (choice.error) return;
		selected = choice;
		// Prefetching is only a speed-up; a failure here is not worth reporting.
		try {
			await preloadData(resourcesUrl(choice));
		} catch (error) {
			console.debug('Failed to prefetch resource-selection page:', error);
		}
	}
</script>

<!-- Fills the window, so the list is as long as the page allows and Next sits
     with the title rather than under a list of unknown length. -->
<section class="flex h-[calc(100dvh-46px-2.5rem)] min-h-[360px] flex-col gap-4">
	<PageHeader title="Choose a measurement">
		Pick what to run, then continue to bind instruments. Procedures are composed in the wizard —
		built into lab_wizard, or saved in this workspace. Custom measurements are Python files in this
		workspace's <code>measurements/</code> folder, for what a composed procedure cannot do; the
		examples there show how to write one.
		{#snippet actions()}
			<button class="lw-btn lw-btn-primary" onclick={onNext} disabled={!selected}>Next</button>
		{/snippet}
	</PageHeader>

	{#if data?.error}
		<Callout tone="crit">{data.error}</Callout>
	{/if}

	<!-- The list pads inside its own scroll area, so the selection ring has room
	     to draw instead of being clipped at the edge. -->
	<div class="min-h-0 flex-1 overflow-y-auto rounded border border-line bg-surface shadow-sm">
		<ul class="divide-y divide-line p-2">
			{#each choices as c (`${c.kind}:${c.name}`)}
				<li>
					<button
						class={`w-full rounded-md px-3 py-3 text-left transition ${selected?.name === c.name && selected?.kind === c.kind ? 'bg-accent-wash ring-1 ring-accent' : ''} ${c.error ? 'cursor-not-allowed opacity-60' : 'hover:bg-accent-wash active:scale-[.99]'}`}
						onclick={() => onSelectionChange(c)}
						disabled={Boolean(c.error)}
					>
						<div class="flex items-center gap-2">
							<span class="font-medium">{c.name}</span>
							{#if c.kind === 'procedure'}
								<Pill tone="accent">procedure</Pill>
								{#if c.origin === 'workspace'}
									<Pill title="Saved in this workspace's config/procedures">this workspace</Pill>
								{/if}
							{:else}
								<Pill title="A Python file in this workspace's measurements folder">custom</Pill>
							{/if}
							{#if c.presets.length > 0}
								<span class="text-fine text-muted">
									{c.presets.length} preset{c.presets.length === 1 ? '' : 's'}
								</span>
							{/if}
						</div>
						{#if c.description}
							<div class="mt-0.5 text-xs text-ink-2">{c.description}</div>
						{/if}
						{#if c.roles}
							<div class="mt-1 flex flex-wrap gap-1.5 text-fine text-muted">
								{#each Object.entries(c.roles) as [role, behavior] (role)}
									<span class="mono">{role}: {behavior}</span>
								{/each}
							</div>
						{/if}
						{#if c.error}
							<div class="mt-1 text-xs text-crit">
								{c.kind === 'custom' ? `${c.name}.py` : 'This definition'} does not load: {c.error}
							</div>
						{/if}
					</button>
				</li>
			{/each}
		</ul>
	</div>
</section>
