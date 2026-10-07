<script lang="ts">
	/** A unit: typed, or picked from the common ones.
	 *
	 * Still a text field, because a lab has units nobody lists (dBm, nm, counts);
	 * the menu beside it is the usual ones, grouped by what they measure, so
	 * `kΩ` is one click and spelled the way the unit conversion knows it.
	 */
	import { DropdownMenu } from 'bits-ui';
	import CaretDownIcon from 'phosphor-svelte/lib/CaretDown';

	let {
		value,
		onchange,
		class: klass = '',
		placeholder = 'unit',
		disabled = false,
		small = false,
		quiet = false,
		'aria-label': ariaLabel = 'Unit'
	}: {
		value: string;
		onchange: (unit: string) => void;
		class?: string;
		placeholder?: string;
		disabled?: boolean;
		/** The small control height, as in a table of fields. */
		small?: boolean;
		/** No border until hovered or focused (the caller's `.quiet` rule draws it). */
		quiet?: boolean;
		'aria-label'?: string;
	} = $props();

	const COMMON: [string, string[]][] = [
		['Resistance', ['Ω', 'kΩ', 'MΩ']],
		['Voltage', ['V', 'mV', 'µV']],
		['Current', ['A', 'mA', 'µA', 'nA']],
		['Power', ['W', 'mW', 'µW', 'nW', 'dBm']],
		['Time', ['s', 'ms', 'µs', 'ns']],
		['Frequency', ['Hz', 'kHz', 'MHz', 'GHz']],
		['Temperature', ['K', 'mK']],
		['Length', ['m', 'mm', 'µm', 'nm']],
		['Capacitance', ['F', 'µF', 'nF', 'pF']],
		['Ratio', ['dB', '%']]
	];
</script>

<span class="inline-flex shrink-0 items-stretch {klass}">
	<input
		class="lw-input mono min-w-0 flex-1 rounded-r-none px-1.5 {small ? 'h-[var(--control-h-sm)] py-0 text-xs' : ''} {quiet
			? 'quiet'
			: ''}"
		{placeholder}
		{value}
		{disabled}
		oninput={(e) => onchange(e.currentTarget.value)}
		aria-label={ariaLabel}
	/>
	<DropdownMenu.Root>
		<DropdownMenu.Trigger
			class="grid w-5 place-items-center rounded-r border border-l-0 border-line text-muted hover:bg-surface-2 disabled:opacity-50 {small
				? 'h-[var(--control-h-sm)]'
				: ''} {quiet ? 'quiet quiet-reveal' : ''}"
			aria-label="Common units"
			{disabled}
		>
			<CaretDownIcon size={10} />
		</DropdownMenu.Trigger>
		<DropdownMenu.Portal>
			<DropdownMenu.Content class="lw-menu" align="end" sideOffset={4}>
				{#each COMMON as [group, units] (group)}
					<div class="flex items-center gap-1 px-1 py-0.5">
						<span class="w-24 shrink-0 text-fine text-muted">{group}</span>
						{#each units as unit (unit)}
							<DropdownMenu.Item
								class="lw-menu-item mono min-h-0 justify-center px-1.5 py-0.5 text-xs {unit === value
									? 'text-accent-strong'
									: ''}"
								onSelect={() => onchange(unit)}>{unit}</DropdownMenu.Item
							>
						{/each}
					</div>
				{/each}
			</DropdownMenu.Content>
		</DropdownMenu.Portal>
	</DropdownMenu.Root>
</span>
