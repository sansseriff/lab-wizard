<script lang="ts">
	/** An input for one param value of a declared type — a default, or a preset's value.
	 *
	 * A sweep is the one structured type: linear (start/stop/step), an explicit
	 * list, or waypoints (turning points walked at a step, recording each leg) —
	 * the three shapes `SweepParams` accepts.
	 */
	import type { ParamType } from './model';
	import Select from '$lib/components/Select.svelte';

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

	type Sweep =
		| { mode: 'linear'; start: number; stop: number; step: number }
		| { mode: 'explicit'; values: number[] }
		| { mode: 'waypoints'; points: number[]; step: number };

	const sweep = $derived(
		(value && typeof value === 'object'
			? value
			: { mode: 'linear', start: 0, stop: 1, step: 0.01 }) as Sweep
	);

	function num(text: string): number {
		const n = Number(text);
		return Number.isNaN(n) ? 0 : n;
	}

	function numbers(text: string): number[] {
		return text
			.split(/[,\s]+/)
			.filter(Boolean)
			.map(Number)
			.filter((n) => !Number.isNaN(n));
	}

	/** Switch mode, keeping what carries over: the ends, the points, the step. */
	function setSweepMode(mode: string) {
		if (mode === sweep.mode) return;
		const ends =
			sweep.mode === 'linear' ? [sweep.start, sweep.stop]
			: sweep.mode === 'explicit' ? (sweep.values ?? [])
			: (sweep.points ?? []);
		const step = sweep.mode === 'explicit' ? 0.01 : sweep.step;
		if (mode === 'explicit') onchange({ mode, values: ends });
		else if (mode === 'waypoints')
			onchange({ mode, points: ends.length >= 2 ? [...ends, ends[0]] : [0, 1, 0], step });
		else onchange({ mode: 'linear', start: ends[0] ?? 0, stop: ends[1] ?? ends.at(-1) ?? 1, step });
	}
</script>

{#if type === 'bool'}
	<label class="flex items-center gap-1.5 text-xs">
		<input
			type="checkbox"
			aria-label={label}
			checked={Boolean(value)}
			onchange={(e) => onchange(e.currentTarget.checked)}
		/>
		{value ? 'yes' : 'no'}
	</label>
{:else if type === 'str'}
	<input
		class="lw-input mono"
		value={value ?? ''}
		onchange={(e) => onchange(e.currentTarget.value)}
		aria-label={label}
	/>
{:else if type === 'sweep'}
	<div class="grid gap-2">
		<Select
			value={sweep.mode}
			onValueChange={setSweepMode}
			aria-label="{label}: sweep mode"
			options={[
				{ value: 'linear', label: 'Linear range' },
				{ value: 'waypoints', label: 'Waypoints (there and back)' },
				{ value: 'explicit', label: 'Explicit values' }
			]}
		/>
		{#if sweep.mode === 'linear'}
			<div class="grid grid-cols-3 gap-2">
				{#each ['start', 'stop', 'step'] as const as part (part)}
					<label class="flex min-w-0 flex-col gap-1 text-xs text-muted">
						{part}
						<input
							class="lw-input mono w-full"
							value={sweep[part]}
							onchange={(e) => onchange({ ...sweep, [part]: num(e.currentTarget.value) })}
							aria-label="{label}: {part}"
						/>
					</label>
				{/each}
			</div>
		{:else if sweep.mode === 'waypoints'}
			<div class="grid grid-cols-[1fr_6rem] gap-2">
				<label class="flex min-w-0 flex-col gap-1 text-xs text-muted">
					turning points
					<input
						class="lw-input mono w-full"
						value={(sweep.points ?? []).join(', ')}
						onchange={(e) => onchange({ ...sweep, points: numbers(e.currentTarget.value) })}
						placeholder="0, 1.4, 0, -1.4, 0"
						aria-label="{label}: turning points"
					/>
				</label>
				<label class="flex min-w-0 flex-col gap-1 text-xs text-muted">
					step
					<input
						class="lw-input mono w-full"
						value={sweep.step}
						onchange={(e) => onchange({ ...sweep, step: num(e.currentTarget.value) })}
						aria-label="{label}: step"
					/>
				</label>
			</div>
			<p class="text-fine text-muted">
				Walked in straight legs; each point records its leg (0, 1, 2, …) as
				<code>&lt;parameter&gt;_leg</code>, to draw or filter the legs apart.
			</p>
		{:else}
			<input
				class="lw-input mono min-w-0 w-full"
				value={(sweep.values ?? []).join(', ')}
				onchange={(e) => onchange({ mode: 'explicit', values: numbers(e.currentTarget.value) })}
				placeholder="0, 0.5, 1"
				aria-label="{label}: values"
			/>
		{/if}
	</div>
{:else}
	<input
		class="lw-input mono w-28"
		value={value ?? ''}
		onchange={(e) =>
			onchange(
				type === 'int' ? Math.trunc(num(e.currentTarget.value)) : num(e.currentTarget.value)
			)}
		inputmode="decimal"
		aria-label={label}
	/>
{/if}
