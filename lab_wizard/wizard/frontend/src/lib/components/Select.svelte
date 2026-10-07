<!--
  The one dropdown for choosing a value. Native <select> popups are drawn by the
  browser and look different in each one; this draws its own list, styled like
  the rest of the controls. The trigger is `.lw-select`, so it is the same
  height as `.lw-btn` and `.lw-input` beside it.
-->
<script lang="ts" module>
	/** `hint` is quieter text beside the label (a Combobox shows it, e.g. a field's value). */
	export type SelectOption = { value: string; label: string; disabled?: boolean; hint?: string };
</script>

<script lang="ts">
	import { Select } from 'bits-ui';
	import CaretUpDownIcon from 'phosphor-svelte/lib/CaretUpDown';
	import CheckIcon from 'phosphor-svelte/lib/Check';

	interface Props {
		value: string | undefined;
		options: SelectOption[];
		onValueChange?: (value: string) => void;
		/** Shown when `value` matches no option. */
		placeholder?: string;
		disabled?: boolean;
		mono?: boolean;
		id?: string;
		class?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(),
		options,
		onValueChange,
		placeholder = 'Choose…',
		disabled = false,
		mono = false,
		id,
		class: className = '',
		'aria-label': ariaLabel
	}: Props = $props();

	// bits-ui reads '' as "nothing selected", but '' is a real option in a few
	// places (e.g. "no series"), so it travels under a stand-in key.
	const EMPTY = '\u0000';
	const toKey = (v: string) => (v === '' ? EMPTY : v);
	const fromKey = (k: string) => (k === EMPTY ? '' : k);

	const current = $derived(options.find((o) => o.value === value));
</script>

<Select.Root
	type="single"
	value={current ? toKey(current.value) : ''}
	onValueChange={(k) => {
		const next = fromKey(k);
		value = next;
		onValueChange?.(next);
	}}
	items={options.map((o) => ({ ...o, value: toKey(o.value) }))}
	{disabled}
>
	<Select.Trigger
		{id}
		aria-label={ariaLabel}
		class="lw-select lw-select-trigger {mono ? 'mono' : ''} {className}"
	>
		<span class="truncate {current ? '' : 'text-muted'}">{current?.label ?? placeholder}</span>
		<CaretUpDownIcon class="size-3.5 shrink-0 text-muted" />
	</Select.Trigger>
	<Select.Portal>
		<Select.Content
			sideOffset={4}
			class="lw-select-content {mono ? 'mono' : ''}"
		>
			<Select.Viewport class="p-1">
				{#each options as option (option.value)}
					<Select.Item
						value={toKey(option.value)}
						label={option.label}
						disabled={option.disabled}
						class="lw-select-item"
					>
						{#snippet children({ selected })}
							<span class="min-w-0 flex-1">{option.label}</span>
							{#if selected}<CheckIcon class="size-3.5 shrink-0 text-accent" />{/if}
						{/snippet}
					</Select.Item>
				{/each}
			</Select.Viewport>
		</Select.Content>
	</Select.Portal>
</Select.Root>
