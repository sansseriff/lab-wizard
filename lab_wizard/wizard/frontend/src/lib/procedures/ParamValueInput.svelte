<script lang="ts">
	/** An input for one param value of a declared type — a default, or a preset's value.
	 *
	 * A sweep is the one structured type: linear (start/stop/step) or an explicit
	 * list, the two shapes `SweepParams` accepts.
	 */
	import type { ParamType } from './model';

	let {
		type,
		value,
		onchange,
		label
	}: {
		type: ParamType;
		value: unknown;
		onchange: (value: unknown) => void;
		label: string;
	} = $props();

	type Sweep = { mode: 'linear'; start: number; stop: number; step: number } | { mode: 'explicit'; values: number[] };

	const sweep = $derived(
		(value && typeof value === 'object' ? value : { mode: 'linear', start: 0, stop: 1, step: 0.01 }) as Sweep
	);

	function num(text: string): number {
		const n = Number(text);
		return Number.isNaN(n) ? 0 : n;
	}

	function setSweepMode(mode: string) {
		if (mode === sweep.mode) return;
		if (mode === 'explicit') {
			const s = sweep as Extract<Sweep, { mode: 'linear' }>;
			onchange({ mode: 'explicit', values: [s.start, s.stop] });
		} else {
			const values = (sweep as Extract<Sweep, { mode: 'explicit' }>).values ?? [];
			onchange({ mode: 'linear', start: values[0] ?? 0, stop: values[values.length - 1] ?? 1, step: 0.01 });
		}
	}
</script>

{#if type === 'bool'}
	<label class="flex items-center gap-1.5 text-xs">
		<input type="checkbox" checked={Boolean(value)} onchange={(e) => onchange(e.currentTarget.checked)} />
		{value ? 'yes' : 'no'}
	</label>
{:else if type === 'str'}
	<input class="lw-input mono" value={value ?? ''} onchange={(e) => onchange(e.currentTarget.value)} aria-label={label} />
{:else if type === 'sweep'}
	<div class="flex flex-wrap items-center gap-1.5">
		<select
			class="lw-select w-auto"
			value={sweep.mode}
			onchange={(e) => setSweepMode(e.currentTarget.value)}
			aria-label="{label}: sweep mode"
		>
			<option value="linear">linear</option>
			<option value="explicit">explicit</option>
		</select>
		{#if sweep.mode === 'linear'}
			{#each ['start', 'stop', 'step'] as const as part (part)}
				<label class="flex items-center gap-1 text-[11px] text-muted">
					{part}
					<input
						class="lw-input mono w-20"
						value={sweep[part]}
						onchange={(e) => onchange({ ...sweep, [part]: num(e.currentTarget.value) })}
						aria-label="{label}: {part}"
					/>
				</label>
			{/each}
		{:else}
			<input
				class="lw-input mono min-w-[10rem] flex-1"
				value={(sweep.values ?? []).join(', ')}
				onchange={(e) =>
					onchange({
						mode: 'explicit',
						values: e.currentTarget.value
							.split(/[,\s]+/)
							.filter(Boolean)
							.map(Number)
							.filter((n) => !Number.isNaN(n))
					})}
				placeholder="0, 0.5, 1"
				aria-label="{label}: values"
			/>
		{/if}
	</div>
{:else}
	<input
		class="lw-input mono w-28"
		value={value ?? ''}
		onchange={(e) => onchange(type === 'int' ? Math.trunc(num(e.currentTarget.value)) : num(e.currentTarget.value))}
		inputmode="decimal"
		aria-label={label}
	/>
{/if}
