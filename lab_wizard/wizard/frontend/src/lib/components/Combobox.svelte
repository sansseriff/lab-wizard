<!--
  A searchable choice among known options: type to narrow the list, pick one.
  Unlike a text field with suggestions, it only ever holds one of `options`
  (or nothing, when `noneLabel` offers that), so a typo cannot become a value.
  Styled as `Select`, whose list it shares.
-->
<script lang="ts">
	import { Combobox } from 'bits-ui';
	import CaretUpDownIcon from 'phosphor-svelte/lib/CaretUpDown';
	import CheckIcon from 'phosphor-svelte/lib/Check';
	import type { Snippet } from 'svelte';
	import type { SelectOption } from './Select.svelte';

	interface Props {
		value: string | null;
		options: SelectOption[];
		onValueChange?: (value: string | null) => void;
		placeholder?: string;
		/** Offer choosing nothing, under this label. */
		noneLabel?: string;
		/** Shown when nothing matches what was typed. */
		empty?: Snippet<[string]>;
		disabled?: boolean;
		mono?: boolean;
		id?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(),
		options,
		onValueChange,
		placeholder = 'Search…',
		noneLabel,
		empty,
		disabled = false,
		mono = false,
		id,
		'aria-label': ariaLabel
	}: Props = $props();

	// bits-ui reads '' as "nothing selected"; "none" is a real choice here.
	const NONE = '\u0000';
	let search = $state('');
	let open = $state(false);
	let input = $state<HTMLInputElement | null>(null);

	const current = $derived(options.find((o) => o.value === value) ?? null);
	const shown = $derived.by(() => {
		const needle = search.trim().toLowerCase();
		const matching = needle ? options.filter((o) => o.label.toLowerCase().includes(needle)) : options;
		return noneLabel && !needle ? [{ value: NONE, label: noneLabel }, ...matching] : matching;
	});

	function choose(key: string) {
		const next = key === NONE || key === '' ? null : key;
		value = next;
		onValueChange?.(next);
	}

	/** What the field shows when not searching: the choice, never a stray search. */
	function showChoice() {
		search = '';
		if (input) input.value = current?.label ?? '';
	}

	/** Enter with exactly one match picks it, without arrowing down to it first. */
	function onkeydown(event: KeyboardEvent) {
		const matches = shown.filter((o) => o.value !== NONE);
		if (event.key === 'Enter' && search.trim() && matches.length === 1) {
			event.preventDefault();
			choose(matches[0].value);
			open = false;
			queueMicrotask(showChoice);
		}
	}
</script>

<Combobox.Root
	type="single"
	value={current ? current.value : noneLabel && value === null ? NONE : ''}
	onValueChange={choose}
	items={shown}
	inputValue={current?.label ?? ''}
	bind:open
	onOpenChange={(isOpen) => {
		if (!isOpen) showChoice();
	}}
	{disabled}
>
	<div class="relative">
		<Combobox.Input
			bind:ref={input}
			{onkeydown}
			{id}
			aria-label={ariaLabel}
			class="lw-input w-full pr-7 {mono ? 'mono' : ''}"
			placeholder={current ? current.label : (noneLabel ?? placeholder)}
			oninput={(e) => (search = e.currentTarget.value)}
			onfocus={(e) => e.currentTarget.select()}
			onclick={() => (open = true)}
			onblur={() => {
				if (!open) showChoice();
			}}
		/>
		<Combobox.Trigger class="absolute inset-y-0 right-1 flex items-center px-1 text-muted" aria-label="Show all">
			<CaretUpDownIcon class="size-3.5" />
		</Combobox.Trigger>
	</div>
	<Combobox.Portal>
		<Combobox.Content
			sideOffset={4}
			align="start"
			class="lw-select-content {mono ? 'mono' : ''}"
			style="min-width: var(--bits-combobox-anchor-width); max-height: min(20rem, var(--bits-combobox-content-available-height))"
		>
			<Combobox.Viewport class="p-1">
				{#each shown as option (option.value)}
					<Combobox.Item value={option.value} label={option.label} class="lw-select-item">
						{#snippet children({ selected })}
							<span class="min-w-0 flex-1 {option.value === NONE ? 'text-muted' : ''}">{option.label}</span>
							{#if selected}<CheckIcon class="size-3.5 shrink-0 text-accent" />{/if}
						{/snippet}
					</Combobox.Item>
				{:else}
					<div class="px-2 py-1.5 text-xs text-muted">
						{#if empty}{@render empty(search)}{:else}Nothing matches “{search}”.{/if}
					</div>
				{/each}
			</Combobox.Viewport>
		</Combobox.Content>
	</Combobox.Portal>
</Combobox.Root>
