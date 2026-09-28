<script lang="ts">
	/** One value a step takes: a literal, a param, or the value a sweep is at.
	 *
	 * This is where "params vs literals" (plan 4.6) is decided, one field at a
	 * time. A literal is frozen into the procedure; a param is exposed in every
	 * project's YAML and in presets. "Make param" turns the literal you typed
	 * into a param with that default, which is the usual way one is born.
	 */
	import type { ProcedureEditor } from './editor.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { type FieldSpec, type Path, getAt, isRef, paramLeaves, parseLiteral } from './model';
	import { valueSummary } from './presentation';
	import Select, { type SelectOption } from '$lib/components/Select.svelte';

	let {
		editor,
		path,
		field,
		fieldName,
		scope,
		onreference
	}: {
		editor: ProcedureEditor;
		/** Path to the value itself: the step's path plus the field name (or map key). */
		path: Path;
		field: FieldSpec;
		fieldName: string;
		/** Names enclosing sweeps bind here. */
		scope: string[];
		onreference?: (kind: 'params' | 'roles', name: string) => void;
	} = $props();

	const value = $derived(getAt(editor.definition, path));
	const sweepsOnly = $derived(field.kind === 'values');
	const mode = $derived(
		isRef(value, 'param')
			? 'param'
			: isRef(value, 'swept')
				? 'swept'
				: sweepsOnly
					? 'list'
					: 'literal'
	);
	const params = $derived(
		paramLeaves(editor.definition.params).filter((p) =>
			sweepsOnly ? p.decl.type === 'sweep' : p.decl.type !== 'sweep'
		)
	);

	let promoting = $state(false);
	let promoteName = $state('');

	/** A name the definition refers to but no longer offers still shows, so it is not silently lost. */
	function withCurrent(current: string, options: SelectOption[]): SelectOption[] {
		return !current || options.some((o) => o.value === current)
			? options
			: [{ value: current, label: current }, ...options];
	}

	function setMode(next: string) {
		if (next === mode) return;
		if (next === 'param') editor.setAt(path, { param: params[0]?.name ?? '' });
		else if (next === 'swept') editor.setAt(path, { swept: scope[scope.length - 1] ?? '' });
		else if (next === 'list') editor.setAt(path, []);
		else editor.setAt(path, field.default ?? 0);
	}

	function literalText(v: unknown): string {
		return v === undefined || v === null ? '' : String(v);
	}

	function listText(v: unknown): string {
		return Array.isArray(v) ? v.join(', ') : '';
	}

	function setList(text: string) {
		const items = text
			.split(/[,\s]+/)
			.filter(Boolean)
			.map(Number)
			.filter((n) => !Number.isNaN(n));
		editor.setAt(path, items);
	}

	function startPromote() {
		promoteName = fieldName;
		promoting = true;
	}

	function promote() {
		const name = promoteName.trim();
		if (!name) return;
		const current = value;
		const integer =
			typeof current === 'number' &&
			Number.isInteger(current) &&
			typeof field.default === 'number' &&
			Number.isInteger(field.default);
		const type =
			typeof current === 'boolean'
				? 'bool'
				: typeof current === 'string'
					? 'str'
					: integer
						? 'int'
						: 'float';
		const created = editor.addParam(name, {
			type,
			default: current === undefined ? undefined : current
		});
		editor.setAt(path, { param: created });
		promoting = false;
	}
</script>

<div class="flex min-w-0 flex-wrap items-center gap-1.5">
	<Select
		class="w-auto shrink-0"
		value={mode}
		onValueChange={setMode}
		aria-label="{fieldName}: kind of value"
		options={sweepsOnly
			? [
					{ value: 'param', label: 'Sweep parameter' },
					{ value: 'list', label: 'fixed list' }
				]
			: [
					{ value: 'literal', label: 'Fixed value' },
					{ value: 'param', label: 'Parameter' },
					{ value: 'swept', label: 'Current sweep value', disabled: !scope.length }
				]}
	/>

	{#if mode === 'param'}
		<Select
			mono
			class="min-w-[10rem] flex-1"
			value={(value as { param: string }).param}
			onValueChange={(v) => editor.setAt(path, { param: v })}
			aria-label="{fieldName}: param"
			placeholder="— choose —"
			options={withCurrent(
				(value as { param: string }).param,
				params.map((p) => ({ value: p.name, label: p.name + (p.decl.unit ? ` (${p.decl.unit})` : '') }))
			)}
		/>
	{:else if mode === 'swept'}
		<Select
			mono
			class="min-w-[8rem] flex-1"
			value={(value as { swept: string }).swept}
			onValueChange={(v) => editor.setAt(path, { swept: v })}
			aria-label="{fieldName}: swept value"
			placeholder="— choose —"
			options={withCurrent(
				(value as { swept: string }).swept,
				scope.map((name) => ({ value: name, label: name }))
			)}
		/>
	{:else if mode === 'list'}
		<input
			class="lw-input mono min-w-[10rem] flex-1"
			value={listText(value)}
			onchange={(e) => setList(e.currentTarget.value)}
			placeholder="0, 0.5, 1"
			aria-label="{fieldName}: values"
		/>
	{:else if typeof value === 'boolean'}
		<label class="flex items-center gap-1.5 text-xs">
			<input
				type="checkbox"
				checked={value}
				onchange={(e) => editor.setAt(path, e.currentTarget.checked)}
			/>
			{value ? 'yes' : 'no'}
		</label>
	{:else}
		<input
			class="lw-input mono w-28"
			value={literalText(value)}
			onchange={(e) =>
				editor.setAt(
					path,
					e.currentTarget.value === '' ? undefined : parseLiteral(e.currentTarget.value)
				)}
			placeholder="value"
			aria-label="{fieldName}: literal"
		/>
	{/if}

	{#if mode === 'param'}
		<div class="w-full break-words text-xs text-muted">
			{valueSummary(value, editor.definition)}
			{#if onreference}<button
					class="ml-1 text-accent hover:underline"
					onclick={() => onreference?.('params', (value as { param: string }).param)}
					>Edit parameter ↗</button
				>{/if}
		</div>
	{/if}

	{#if mode === 'literal' && value !== undefined}
		{#if promoting}
			<input
				class="lw-input mono w-40"
				bind:value={promoteName}
				onkeydown={(e) => {
					if (e.key === 'Enter') promote();
					if (e.key === 'Escape') promoting = false;
				}}
				placeholder="group.name"
				aria-label="New param name"
			/>
			<button class="lw-btn" onclick={promote}>Add</button>
			<button class="lw-btn" onclick={() => (promoting = false)}>Cancel</button>
		{:else}
			<Tooltip text="Expose this value as a param, with what you typed as its default">{#snippet child({ props })}<button {...props}
				class="lw-btn"
				onclick={startPromote}
			>
				Make parameter
			</button>{/snippet}</Tooltip>
		{/if}
	{/if}
</div>
