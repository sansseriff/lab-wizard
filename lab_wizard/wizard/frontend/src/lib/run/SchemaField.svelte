<script lang="ts">
	/** One field of a params form, rendered from its JSON schema; groups recurse.
	 *
	 * Laid out like a properties panel: a field is a label beside its input, and
	 * a group is a row that folds away its fields, which hang off a guide line
	 * as the procedure composer's outline does. A unit is shown in the input: a procedure param's
	 * description starts with it — "(V) the bias voltages to visit" — and a
	 * sweep's unit carries down to its start, stop and step.
	 *
	 * Numbers are kept as typed until they parse, so "-" or "1e" on the way to
	 * "-1e-3" is not thrown away; until then the field says what is wrong and
	 * reports it (`onproblem`), which keeps Save disabled.
	 */
	import { onDestroy } from 'svelte';
	import CaretDownIcon from 'phosphor-svelte/lib/CaretDown';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import Select from '$lib/components/Select.svelte';
	import SchemaField from './SchemaField.svelte';
	import {
		defaultFor,
		kindOf,
		label,
		numberProblem,
		parseNumbers,
		pathKey,
		resolve,
		variants,
		type JsonSchema
	} from './schema';

	let {
		name,
		schema: raw,
		defs,
		value,
		path,
		onchange,
		onproblem,
		problems,
		unit: inherited = '',
		depth = 0
	}: {
		name: string;
		schema: JsonSchema;
		defs: Record<string, JsonSchema>;
		value: unknown;
		path: string[];
		onchange: (value: unknown) => void;
		/** A problem this field found as it was typed, or null once it is fixed. */
		onproblem: (key: string, message: string | null) => void;
		/** Problems the backend reported, by dotted path. */
		problems: Record<string, string>;
		/** The unit of the value this field is part of (a sweep's), if its own says none. */
		unit?: string;
		/** How deep in groups: labels narrow by the indent (21px a level), so every input lines up. */
		depth?: number;
	} = $props();

	const schema = $derived(resolve(raw, defs));
	const kind = $derived(kindOf(schema));
	const key = $derived(pathKey(path));
	const id = $derived(`param-${key}`);
	// "(V) the bias voltages to visit" is a unit and a description.
	const described = $derived(/^\(([^)]+)\)\s*(.*)$/s.exec(schema.description ?? ''));
	const unit = $derived(described ? described[1] : inherited);
	const help = $derived(described ? described[2] : (schema.description ?? ''));
	let open = $state(true);

	// Text as typed, for fields whose value only exists once it parses.
	let typed = $state<string | null>(null);
	let local = $state<string | null>(null);
	const shownValue = $derived(
		typed ?? (kind === 'numbers' ? ((value as number[]) ?? []).join(', ') : value === undefined || value === null ? '' : String(value))
	);
	const problem = $derived(local ?? problems[key] ?? null);

	// A field that goes away (another sweep mode chosen) takes its problem with it.
	onDestroy(() => onproblem(key, null));

	function setNumber(text: string) {
		typed = text;
		const message = numberProblem(text, schema, kind === 'integer');
		local = message;
		onproblem(key, message);
		if (!message) onchange(Number(text));
	}

	function setNumbers(text: string) {
		typed = text;
		const numbers = parseNumbers(text);
		local = numbers === null ? 'Numbers, separated by commas.' : null;
		onproblem(key, local);
		if (numbers !== null) onchange(numbers);
	}

	function setJson(text: string) {
		typed = text;
		try {
			onchange(JSON.parse(text));
			local = null;
		} catch {
			local = 'Not valid JSON.';
		}
		onproblem(key, local);
	}

	// A choice (a sweep's mode): the tag picks the variant, whose own fields follow.
	const choices = $derived(kind === 'choice' ? variants(schema, defs) : {});
	const tag = $derived(schema.discriminator?.propertyName ?? '');
	const current = $derived((value as Record<string, unknown> | null)?.[tag] as string | undefined);
	const variant = $derived(current ? choices[current] : undefined);

	function choose(next: string) {
		const target = choices[next];
		if (!target) return;
		onchange({ ...(defaultFor(target, defs) as object), [tag]: next });
	}

	const entries = $derived(Object.entries(schema.properties ?? {}));
	const record = $derived((value ?? {}) as Record<string, unknown>);
</script>

{#snippet children(fields: [string, JsonSchema][], childUnit: string)}
	<div class="ml-3 border-l border-line-2 pl-2">
		{#each fields as [child, childSchema] (child)}
			<SchemaField
				name={child}
				schema={childSchema}
				{defs}
				value={record[child]}
				path={[...path, child]}
				onchange={(v) => onchange({ ...record, [child]: v })}
				{onproblem}
				{problems}
				unit={childUnit}
				depth={depth + 1}
			/>
		{/each}
	</div>
{/snippet}

{#snippet header(extra?: import('svelte').Snippet)}
	<div class="flex items-center gap-1 rounded py-0.5 hover:bg-surface-2">
		<button
			type="button"
			class="flex min-w-0 flex-1 items-center gap-1 text-left text-xs font-semibold text-ink-2"
			onclick={() => (open = !open)}
			aria-expanded={open}
			aria-label="{open ? 'Collapse' : 'Expand'} {label(name)}"
		>
			<span class="grid h-6 w-6 shrink-0 place-items-center text-muted">
				{#if open}<CaretDownIcon size={12} />{:else}<CaretRightIcon size={12} />{/if}
			</span>
			<span class="truncate">{label(name)}</span>
			{#if unit && kind === 'choice'}<span class="font-normal text-muted">({unit})</span>{/if}
		</button>
		{#if extra}{@render extra()}{/if}
	</div>
{/snippet}

{#if kind === 'group'}
	<div class="py-0.5">
		{@render header()}
		{#if open}{@render children(entries, '')}{/if}
	</div>
{:else if kind === 'choice'}
	<div class="py-0.5">
		{#snippet mode()}
			<Select
				id={`${id}-${tag}`}
				class="lw-select-sm w-40"
				value={current ?? ''}
				onValueChange={choose}
				options={Object.keys(choices).map((v) => ({ value: v, label: v }))}
			/>
		{/snippet}
		{@render header(mode)}
		{#if open}
			{#if help}<p class="mb-1 ml-7 text-fine text-muted">{help}</p>{/if}
			{#if variant}
				{@render children(
					Object.entries(variant.properties ?? {}).filter(([k]) => k !== tag),
					unit
				)}
			{/if}
		{/if}
		{#if problems[key]}<p class="ml-7 text-fine text-crit">{problems[key]}</p>{/if}
	</div>
{:else}
	<div
		class="grid items-center gap-x-2 rounded py-0.5"
		style="grid-template-columns: calc(9.5rem - {depth * 21}px) minmax(0, 1fr)"
	>
		<label class="truncate text-xs text-ink-2" for={id} title={help || label(name)}>{label(name)}</label>
		{#if kind === 'boolean'}
			<label class="flex items-center gap-1.5 text-xs">
				<input {id} type="checkbox" checked={Boolean(value)} onchange={(e) => onchange(e.currentTarget.checked)} />
				{value ? 'yes' : 'no'}
			</label>
		{:else if kind === 'enum'}
			<Select
				{id}
				class="lw-select-sm"
				value={String(value ?? '')}
				onValueChange={(v) => onchange(v)}
				options={(schema.enum ?? []).map((v) => ({ value: String(v), label: String(v) }))}
			/>
		{:else}
			<div class="relative">
				<input
					{id}
					class="lw-input lw-input-sm {kind === 'text' ? '' : 'mono'} {unit ? 'pr-10' : ''} {problem ? 'border-crit' : ''}"
					inputmode={kind === 'number' || kind === 'integer' ? 'decimal' : undefined}
					value={kind === 'other' ? (typed ?? JSON.stringify(value)) : shownValue}
					aria-invalid={problem ? true : undefined}
					oninput={(e) => {
						const text = e.currentTarget.value;
						if (kind === 'number' || kind === 'integer') setNumber(text);
						else if (kind === 'numbers') setNumbers(text);
						else if (kind === 'other') setJson(text);
						else onchange(text);
					}}
					onblur={() => {
						if (!local) typed = null;
					}}
				/>
				{#if unit}
					<span class="pointer-events-none absolute inset-y-0 right-2 flex items-center text-fine text-muted">{unit}</span>
				{/if}
			</div>
		{/if}
		{#if problem}
			<p class="col-start-2 text-fine text-crit" role="alert">{problem}</p>
		{:else if help}
			<p class="col-start-2 text-fine leading-snug text-muted">{help}</p>
		{/if}
	</div>
{/if}
