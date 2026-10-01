<!--
  The date filter: a day or a span of days, typed or picked from a calendar.
  Days on which runs were recorded are marked in the calendar, so an empty
  week is visible before choosing it. One day is a range from it to itself:
  click it twice.
-->
<script lang="ts">
	import { DateRangePicker, Portal } from 'bits-ui';
	import { parseDate, type DateValue } from '@internationalized/date';
	import CalendarBlankIcon from 'phosphor-svelte/lib/CalendarBlank';
	import CaretLeftIcon from 'phosphor-svelte/lib/CaretLeft';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import XIcon from 'phosphor-svelte/lib/X';
	import type { Filter } from './model';

	let {
		days,
		filter,
		onchange
	}: {
		/** Every date runs were recorded on, as `YYYY-MM-DD`. */
		days: string[];
		filter: Filter | undefined;
		onchange: (range: [string, string] | null) => void;
	} = $props();

	type Range = { start: DateValue | undefined; end: DateValue | undefined };

	function fromFilter(f: Filter | undefined): Range {
		if (!f || Array.isArray(f) || typeof f.range[0] !== 'string') return { start: undefined, end: undefined };
		try {
			return { start: parseDate(f.range[0]), end: parseDate(f.range[1] as string) };
		} catch {
			return { start: undefined, end: undefined };
		}
	}

	// What the picker shows, which may be half a range while it is being chosen;
	// only a whole one becomes the filter. Reset whenever the filter changes
	// from outside (its chip removed, Clear all).
	let draft = $state<Range>({ start: undefined, end: undefined });
	$effect(() => {
		draft = fromFilter(filter);
	});

	const recorded = $derived(new Set(days));
	const latest = $derived(days.length ? [...days].sort().at(-1)! : null);

	function changed(next: Range) {
		draft = next;
		if (next.start && next.end) onchange([next.start.toString(), next.end.toString()]);
	}
</script>

<DateRangePicker.Root
	value={draft}
	onValueChange={(v) => changed(v as Range)}
	placeholder={latest ? parseDate(latest) : undefined}
	weekdayFormat="short"
	fixedWeeks
	locale="en-CA"
>
	<div class="mt-1 flex items-center gap-1 rounded border border-line-2 bg-surface px-2 py-1 text-body focus-within:border-accent">
		{#each ['start', 'end'] as const as type (type)}
			<DateRangePicker.Input {type} class="mono flex items-center text-xs tabular-nums">
				{#snippet children({ segments })}
					{#each segments as { part, value }, i (part + i)}
						<DateRangePicker.Segment
							{part}
							class="rounded px-px focus:bg-accent-wash focus:outline-none data-placeholder:text-muted {part === 'literal' ? 'text-muted' : ''}"
							>{value}</DateRangePicker.Segment
						>
					{/each}
				{/snippet}
			</DateRangePicker.Input>
			{#if type === 'start'}<span class="px-0.5 text-muted">–</span>{/if}
		{/each}
		<span class="ml-auto flex items-center">
			{#if draft.start || draft.end}
				<button
					class="grid size-5 place-items-center rounded text-muted hover:bg-surface-2 hover:text-ink"
					aria-label="Clear the date filter"
					onclick={() => {
						draft = { start: undefined, end: undefined };
						onchange(null);
					}}><XIcon size={11} /></button
				>
			{/if}
			<DateRangePicker.Trigger
				class="grid size-5 place-items-center rounded text-muted hover:bg-surface-2 hover:text-ink"
				aria-label="Pick dates"
			>
				<CalendarBlankIcon size={13} />
			</DateRangePicker.Trigger>
		</span>
	</div>

	<Portal>
		<DateRangePicker.Content
			sideOffset={6}
			align="start"
			class="z-60 rounded border border-line-2 bg-surface p-3 text-ink shadow-[0_4px_16px_rgb(22_22_43/0.14)]"
		>
			<DateRangePicker.Calendar>
				{#snippet children({ months, weekdays })}
					<DateRangePicker.Header class="mb-2 flex items-center justify-between">
						<DateRangePicker.PrevButton class="grid size-7 place-items-center rounded hover:bg-surface-2">
							<CaretLeftIcon size={13} />
						</DateRangePicker.PrevButton>
						<DateRangePicker.Heading class="text-body font-medium" />
						<DateRangePicker.NextButton class="grid size-7 place-items-center rounded hover:bg-surface-2">
							<CaretRightIcon size={13} />
						</DateRangePicker.NextButton>
					</DateRangePicker.Header>
					{#each months as month (month.value.toString())}
						<DateRangePicker.Grid class="border-collapse">
							<DateRangePicker.GridHead>
								<DateRangePicker.GridRow class="flex">
									{#each weekdays as day (day)}
										<DateRangePicker.HeadCell class="w-8 pb-1 text-center text-fine font-normal text-muted"
											>{day.slice(0, 2)}</DateRangePicker.HeadCell
										>
									{/each}
								</DateRangePicker.GridRow>
							</DateRangePicker.GridHead>
							<DateRangePicker.GridBody>
								{#each month.weeks as week, w (w)}
									<DateRangePicker.GridRow class="flex">
										{#each week as date (date.toString())}
											<DateRangePicker.Cell {date} month={month.value} class="p-0">
												<DateRangePicker.Day
													class="relative grid size-8 place-items-center text-xs tabular-nums
														hover:bg-surface-2
														data-outside-month:text-muted data-outside-month:opacity-50
														data-today:font-semibold
														data-highlighted:bg-accent-wash
														data-range-middle:bg-accent-wash
														data-selection-start:rounded-l data-selection-start:bg-accent data-selection-start:text-on-accent
														data-selection-end:rounded-r data-selection-end:bg-accent data-selection-end:text-on-accent"
												>
													{date.day}
													{#if recorded.has(date.toString())}
														<span class="absolute bottom-1 size-1 rounded-full bg-current opacity-70" aria-hidden="true"
														></span>
													{/if}
												</DateRangePicker.Day>
											</DateRangePicker.Cell>
										{/each}
									</DateRangePicker.GridRow>
								{/each}
							</DateRangePicker.GridBody>
						</DateRangePicker.Grid>
					{/each}
					<p class="mt-2 text-fine text-muted">A dot: runs were recorded that day.</p>
				{/snippet}
			</DateRangePicker.Calendar>
		</DateRangePicker.Content>
	</Portal>
</DateRangePicker.Root>
