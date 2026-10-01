<script lang="ts">
	/** Every filter the lab's runs offer, with how many runs each value leaves.
	 *
	 * Nothing here is configured: a filter appears the first time a run records
	 * the fact behind it (a new instrument type, a param, a device property).
	 * A value's count ignores its own key's choice, so choosing one procedure
	 * still shows how many runs the others have. The filters chosen are shown,
	 * and removed, above the run list (RunList), so choosing one never moves
	 * the checkbox just clicked.
	 */
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import DateFilter from './DateFilter.svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import {
		facetLabel,
		groupFacets,
		isChosen,
		setRange,
		toggleValue,
		type Facet,
		type Filters
	} from './model';

	let {
		facets,
		filters,
		onchange
	}: { facets: Facet[]; filters: Filters; onchange: (next: Filters) => void } = $props();

	const SHOWN = 6;
	// Long lists of values are better searched or ranged than scrolled.
	const RANGE_FROM = 9;
	// Sections that can hold hundreds of keys start folded.
	const collapsed = new SvelteSet(['Instruments', 'Params', 'Columns']);
	const expanded = new SvelteSet<string>();
	let search = $state('');

	const groups = $derived(groupFacets(facets, search));

	function toggleGroup(group: string) {
		if (collapsed.has(group)) collapsed.delete(group);
		else collapsed.add(group);
	}

	function applyRange(facet: Facet, form: HTMLFormElement) {
		const data = new FormData(form);
		const lo = Number(data.get('lo'));
		const hi = Number(data.get('hi'));
		if (Number.isFinite(lo) && Number.isFinite(hi)) onchange(setRange(filters, facet.key, [Math.min(lo, hi), Math.max(lo, hi)]));
	}

	function rangeOf(facet: Facet): [number, number] {
		const chosen = filters[facet.key];
		return chosen && !Array.isArray(chosen) ? (chosen.range as [number, number]) : (facet.range ?? [0, 0]);
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<div class="border-b border-line p-2.5">
		<input
			class="lw-input w-full"
			type="search"
			placeholder="Search filters"
			bind:value={search}
			aria-label="Search filters and their values"
		/>
	</div>

	<ScrollArea class="min-h-0 flex-1" viewportClasses="px-2.5 pb-4">
		{#each groups as { group, facets: keys } (group)}
			{@const open = !collapsed.has(group) || !!search}
			<section class="border-b border-line py-2 [contain-intrinsic-size:auto_160px] [content-visibility:auto] last:border-b-0">
				<button
					class="flex w-full items-center gap-1 text-left text-fine font-semibold tracking-wide text-muted uppercase"
					onclick={() => toggleGroup(group)}
					aria-expanded={open}
				>
					<CaretRightIcon size={10} class={open ? 'rotate-90 transition-transform' : 'transition-transform'} />
					{group}
					<span class="ml-auto font-normal normal-case">{keys.length > 1 ? keys.length : ''}</span>
				</button>
				{#if open}
					{#each keys as facet (facet.key)}
						{@const label = facetLabel(facet.key)}
						{@const showAll = expanded.has(facet.key)}
						<div class="mt-1.5">
							{#if label !== facet.key || keys.length > 1}
								<p class="mono truncate text-fine text-ink-2" title={facet.key}>
									{label}{#if facet.unit}<span class="text-muted"> ({facet.unit})</span>{/if}
								</p>
							{/if}
							{#if facet.key === 'date'}
								<DateFilter
									days={facet.values.map((v) => v.value)}
									filter={filters.date}
									onchange={(range) => onchange(setRange(filters, 'date', range))}
								/>
							{:else if facet.numeric && facet.values.length >= RANGE_FROM}
								{@const [lo, hi] = rangeOf(facet)}
								<form
									class="mt-1 flex items-center gap-1"
									onsubmit={(e) => {
										e.preventDefault();
										applyRange(facet, e.currentTarget);
									}}
								>
									<input class="lw-input mono w-full px-1 text-xs" name="lo" value={lo} aria-label="{facet.key} from" />
									<span class="text-muted">–</span>
									<input class="lw-input mono w-full px-1 text-xs" name="hi" value={hi} aria-label="{facet.key} to" />
									<button class="lw-btn lw-btn-sm">Set</button>
								</form>
								<p class="mt-0.5 text-fine text-muted">{facet.values.length} values, {facet.range?.[0]} to {facet.range?.[1]}</p>
							{:else}
								<ul>
									{#each showAll ? facet.values : facet.values.slice(0, SHOWN) as v (v.value)}
										<li>
											<label
												class="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-body hover:bg-surface-2"
											>
												<input
													type="checkbox"
													checked={isChosen(filters, facet.key, v.value)}
													onchange={() => onchange(toggleValue(filters, facet.key, v.value))}
												/>
												<span class="min-w-0 flex-1 truncate" title={v.value}>{v.value}</span>
												<span class="text-fine text-muted tabular-nums">{v.runs}</span>
											</label>
										</li>
									{/each}
								</ul>
								{#if facet.values.length > SHOWN}
									<button
										class="ml-1 text-fine text-accent hover:underline"
										onclick={() => (showAll ? expanded.delete(facet.key) : expanded.add(facet.key))}
									>
										{showAll ? 'Fewer' : `${facet.values.length - SHOWN} more`}
									</button>
								{/if}
							{/if}
						</div>
					{/each}
				{/if}
			</section>
		{:else}
			<p class="py-4 text-xs text-muted">{search ? 'No filter matches.' : 'Filters appear once runs are recorded.'}</p>
		{/each}
	</ScrollArea>
</div>
