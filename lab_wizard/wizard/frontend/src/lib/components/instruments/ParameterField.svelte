<script lang="ts">
	import Self from './ParameterField.svelte';
	import Select from '$lib/components/Select.svelte';
	import { resolveSchema, type ParamSchema } from '$lib/instruments/model';
	let {
		schema,
		root,
		value,
		label,
		onchange,
		onerror
	}: {
		schema: ParamSchema;
		root: ParamSchema;
		value: any;
		label: string;
		onchange: (value: any) => void;
		onerror: (key: string, message: string | null) => void;
	} = $props();
	const resolved = $derived(resolveSchema(schema, root));
	const nullable = $derived(resolved.anyOf?.some((s) => s.type === 'null'));
	const spec = $derived(
		resolveSchema(resolved.anyOf?.find((s) => s.type !== 'null') ?? resolved, root)
	);
	const title = $derived(schema.title ?? label.split('.').at(-1)?.replaceAll('_', ' ') ?? label);
	let error = $state('');
	function parseJson(text: string) {
		try {
			const parsed = JSON.parse(text);
			onchange(parsed);
			error = '';
		} catch {
			error = 'Enter valid JSON.';
		}
		onerror(label, error || null);
	}
</script>

<div class="parameter-field">
	{#if spec.type === 'object' && value && typeof value === 'object' && !Array.isArray(value)}
		<details open={!/^channels\.\d+$/.test(label)} class="parameter-group">
			<summary>{title}</summary>
			{#each Object.entries(value) as [name, entry] (name)}
				<Self
					schema={spec.properties?.[name] ?? {
						...(typeof spec.additionalProperties === 'object' ? spec.additionalProperties : {}),
						title: label === 'channels' ? `Channel ${name}` : name
					}}
					{root}
					value={entry}
					label={`${label}.${name}`}
					{onerror}
					onchange={(next) => onchange({ ...value, [name]: next })}
				/>
			{/each}
			{#if Object.keys(value).length === 0}<p class="text-xs text-muted">
					No overrides configured.
				</p>{/if}
		</details>
	{:else}
		<label class="block">
			<span class="mb-1.5 block text-xs text-ink-2">{title}</span>
			{#if spec.enum}
				<Select
					aria-label={label}
					value={JSON.stringify(value)}
					onValueChange={(v) => onchange(JSON.parse(v))}
					options={[
						...(nullable ? [{ value: 'null', label: 'Not set' }] : []),
						...spec.enum.map((option) => ({ value: JSON.stringify(option), label: String(option) }))
					]}
				/>
			{:else if spec.type === 'boolean'}
				<Select
					aria-label={label}
					value={JSON.stringify(value)}
					onValueChange={(v) => onchange(JSON.parse(v))}
					options={[
						{ value: 'true', label: 'Yes' },
						{ value: 'false', label: 'No' },
						...(nullable ? [{ value: 'null', label: 'Not set' }] : [])
					]}
				/>
			{:else if spec.type === 'string'}
				<input
					class="lw-input mono"
					aria-label={label}
					value={value ?? ''}
					disabled={value === null && nullable}
					oninput={(e) => onchange(e.currentTarget.value)}
				/>
			{:else if spec.type === 'number' || spec.type === 'integer'}
				<input
					class="lw-input mono"
					type="number"
					step={spec.type === 'integer' ? '1' : 'any'}
					min={spec.minimum}
					max={spec.maximum}
					aria-label={label}
					{value}
					disabled={value === null && nullable}
					oninput={(e) => {
						const input = e.currentTarget;
						const valid =
							input.value !== '' && input.validity.valid && Number.isFinite(input.valueAsNumber);
						error = valid ? '' : `Enter a valid ${spec.type}.`;
						onerror(label, error || null);
						if (valid) onchange(input.valueAsNumber);
					}}
				/>
			{:else}
				<textarea
					class="lw-input mono"
					rows="5"
					aria-label={label}
					value={JSON.stringify(value, null, 2)}
					oninput={(e) => parseJson(e.currentTarget.value)}
				></textarea>
			{/if}
		</label>
		{#if nullable && ['string', 'number', 'integer'].includes(spec.type ?? '')}
			<label class="mt-1.5 flex items-center gap-2 text-xs text-muted">
				<input
					type="checkbox"
					checked={value === null}
					onchange={(e) => {
						error = '';
						onerror(label, null);
						onchange(
							e.currentTarget.checked
								? null
								: (resolved.default ?? (spec.type === 'string' ? '' : 0))
						);
					}}
				/> Not set
			</label>
		{/if}
		{#if resolved.description}<p class="mt-1 text-fine text-muted">{resolved.description}</p>{/if}
		{#if error}<p class="mt-1 text-xs text-crit" role="alert">{error}</p>{/if}
	{/if}
</div>
