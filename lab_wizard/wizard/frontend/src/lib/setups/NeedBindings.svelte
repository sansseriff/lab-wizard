<script lang="ts">
	/** Which of a setup's fields fills each of a procedure's needs.
	 *
	 * A procedure declares what its plots need from the setup (`bias_resistance`,
	 * in ohms); the measurement says which field of its setup that is. Each need
	 * gets a search over the setup's fields: those that read in the need's unit
	 * can be chosen, the rest are listed greyed with why. A field the setup does
	 * not have yet can be added from here. With `editable`, a bound field's value
	 * can be changed in place, which changes the setup: the bench changed, not
	 * one run.
	 */
	import Combobox from '$lib/components/Combobox.svelte';
	import { isQuantity } from '$lib/data/model';
	import { bindProblem, leaves, type Bindings, type Leaf, type NeedDecl } from './model';
	import { compatible } from './units';
	import UnitInput from './UnitInput.svelte';

	let {
		needs,
		fields,
		bindings,
		onchange,
		onsetfield,
		editable = false,
		disabled = false
	}: {
		needs: Record<string, NeedDecl>;
		/** The chosen setup's fields; null when no setup is chosen. */
		fields: Record<string, unknown> | null;
		bindings: Bindings;
		onchange: (bindings: Bindings) => void;
		/** Set a field of the setup: one added here, or a bound one edited in place. */
		onsetfield: (path: string, value: unknown) => void | Promise<void>;
		editable?: boolean;
		disabled?: boolean;
	} = $props();

	const all = $derived(fields ? leaves(fields) : []);

	function options(need: NeedDecl) {
		const fits = (l: Leaf) => !bindProblem(l, need);
		return [...all.filter(fits), ...all.filter((l) => !fits(l))].map((l) => ({
			value: l.path,
			label: l.path,
			hint: fits(l) ? l.shown : (bindProblem(l, need) ?? ''),
			disabled: !fits(l)
		}));
	}

	function bind(name: string, path: string | null) {
		const next = { ...bindings };
		if (path) next[name] = path;
		else delete next[name];
		onchange(next);
	}

	// ---- adding a field the setup does not have ----
	let adding = $state<string | null>(null);
	let newPath = $state('');
	let newValue = $state('');
	let newUnit = $state('');
	let addError = $state('');

	function startAdding(name: string, need: NeedDecl) {
		adding = name;
		newPath = name;
		newValue = '';
		newUnit = need.unit ?? '';
		addError = '';
	}

	async function add(name: string, need: NeedDecl) {
		const path = newPath.trim();
		const number = Number(newValue);
		if (!path || /\s/.test(path) || path.split('.').some((p) => !p)) {
			addError = 'A field name without spaces; dots make groups, like channel2.bias_resistor.';
			return;
		}
		if (newValue.trim() === '' || !Number.isFinite(number)) {
			addError = 'The value is a number.';
			return;
		}
		if (!compatible(newUnit, need.unit)) {
			addError = `${newUnit} cannot be read as ${need.unit}.`;
			return;
		}
		if (all.some((l) => l.path === path)) {
			addError = `The setup already has ${path}; choose it above.`;
			return;
		}
		try {
			await onsetfield(path, newUnit.trim() ? { value: number, unit: newUnit.trim() } : number);
			bind(name, path);
			adding = null;
		} catch (e) {
			addError = e instanceof Error ? e.message : String(e);
		}
	}

	function editValue(leaf: Leaf, text: string) {
		const number = Number(text);
		if (text.trim() === '' || !Number.isFinite(number)) return;
		onsetfield(leaf.path, isQuantity(leaf.value) ? { ...leaf.value, value: number } : number);
	}
</script>

<div class="space-y-3">
	{#each Object.entries(needs) as [name, need] (name)}
		{@const path = bindings[name] ?? null}
		{@const leaf = all.find((l) => l.path === path)}
		{@const problem = path ? bindProblem(leaf, need) : null}
		<div class="rounded border border-line p-3">
			<div class="flex flex-wrap items-baseline gap-x-2">
				<span class="mono text-body font-medium">{name}</span>
				{#if need.unit}<span class="mono text-fine text-muted">in {need.unit}</span>{/if}
				{#if need.description}<span class="text-xs text-ink-2">— {need.description}</span>{/if}
			</div>
			<div class="mt-2 flex flex-wrap items-center gap-2">
				<div class="min-w-[14rem] flex-1">
					<Combobox
						mono
						value={path}
						options={options(need)}
						onValueChange={(p) => bind(name, p)}
						placeholder={fields ? `Search the setup's fields for ${name}…` : 'Choose a setup first'}
						disabled={disabled || !fields}
						aria-label="Setup field for {name}"
					>
						{#snippet empty(search)}No field matches “{search}”.{/snippet}
					</Combobox>
				</div>
				{#if leaf && !problem && editable}
					<label class="flex items-center gap-1 text-xs text-ink-2">
						=
						<input
							class="lw-input mono w-28"
							inputmode="decimal"
							value={String(isQuantity(leaf.value) ? leaf.value.value : leaf.value)}
							onchange={(e) => editValue(leaf, e.currentTarget.value)}
							{disabled}
							aria-label="Value of {leaf.path}"
						/>
						{#if leaf.unit}<span class="mono">{leaf.unit}</span>{/if}
					</label>
				{:else if leaf && !problem}
					<span class="mono text-xs text-ok">= {leaf.shown}</span>
				{/if}
				{#if fields && adding !== name && !disabled}
					<button type="button" class="text-xs text-accent hover:underline" onclick={() => startAdding(name, need)}
						>Add to setup…</button
					>
				{/if}
			</div>
			{#if problem}
				<p class="mt-1 text-fine text-crit">{path}: {problem}. Choose another field.</p>
			{:else if !path && fields}
				<p class="mt-1 text-fine text-warn">Not bound: a run will not start until it is.</p>
			{/if}
			{#if adding === name}
				<form
					class="mt-2 flex flex-wrap items-end gap-2 rounded bg-surface-2 p-2"
					onsubmit={(e) => {
						e.preventDefault();
						add(name, need);
					}}
				>
					<label class="text-fine text-ink-2"
						>Field
						<input class="lw-input mono mt-0.5 w-56" bind:value={newPath} placeholder="channel2.bias_resistor" />
					</label>
					<label class="text-fine text-ink-2"
						>Value
						<input class="lw-input mono mt-0.5 w-28" inputmode="decimal" bind:value={newValue} placeholder="100" />
					</label>
					<div class="text-fine text-ink-2">
						Unit
						<UnitInput class="mt-0.5 flex w-24" value={newUnit} onchange={(u) => (newUnit = u)} placeholder="kΩ" />
					</div>
					<button class="lw-btn lw-btn-sm lw-btn-primary">Add and bind</button>
					<button type="button" class="lw-btn lw-btn-sm" onclick={() => (adding = null)}>Cancel</button>
					{#if addError}<p class="basis-full text-fine text-crit">{addError}</p>{/if}
				</form>
			{/if}
		</div>
	{/each}
</div>
