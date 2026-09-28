<script lang="ts">
	/** Every filter the lab's runs offer, with how many runs each value leaves.
	 *
	 * Nothing here is configured: a filter appears the first time a run records
	 * the fact behind it (a new instrument type, a param, a device property).
	 * A value's count ignores its own key's choice, so choosing one procedure
	 * still shows how many runs the others have.
	 */
	import { SvelteSet } from 'svelte/reactivity';
	import XIcon from 'phosphor-svelte/lib/X';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import {
		describeFilter,
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
		runs,
		onchange
	}: { facets: Facet[]; filters: Filters; runs: number; onchange: (next: Filters) => void } = $props();

	const SHOWN = 6;
	// Long lists of values are better searched or ranged than scrolled.
	const RANGE_FROM = 9;
	// Sections that can hold hundreds of keys start folded.
	const collapsed = new SvelteSet(['Instruments', 'Params', 'Columns']);
	const expanded = new SvelteSet<string>();
	let search = $state('');

	const groups = $derived(groupFacets(facets, search));
	const active = $derived(Object.entries(filters));

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
		return chosen && !Array.isArray(chosen) ? chosen.range : (facet.range ?? [0, 0]);
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
		<p class="mt-2 text-xs text-muted">
			<span class="font-semibold text-ink tabular-nums">{runs}</span>
			{runs === 1 ? 'run' : 'runs'}{active.length ? ' match' : ' recorded'}
		</p>
		{#if active.length}
			<ul class="mt-2 flex flex-wrap gap-1" aria-label="Chosen filters">
				{#each active as [key, filter] (key)}
					<li>
						<button
							class="flex items-center gap-1 rounded border border-accent/30 bg-accent-wash px-1.5 py-0.5 text-left text-fine text-accent-strong"
							onclick={() => {
								const { [key]: _removed, ...rest } = filters;
								onchange(rest);
							}}
							title="Remove this filter"
						>
							<span class="mono break-all">{describeFilter(key, filter)}</span><XIcon size={11} />
						</button>
					</li>
				{/each}
			</ul>
			<button class="mt-1.5 text-xs text-accent hover:underline" onclick={() => onchange({})}>Clear all</button>
		{/if}
	</div>

	<div class="min-h-0 flex-1 overflow-y-auto px-2.5 pb-4">
		{#each groups as { group, facets: keys } (group)}
			{@const open = !collapsed.has(group) || !!search}
			<section class="border-b border-line py-2 last:border-b-0">
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
							{#if facet.numeric && facet.values.length >= RANGE_FROM}
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
	</div>
</div>
