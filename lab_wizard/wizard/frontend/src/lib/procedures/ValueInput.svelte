<script lang="ts">
	/** One value a step takes: a literal, a param, or the value a sweep is at.
	 *
	 * This is where "params vs literals" (plan 4.6) is decided, one field at a
	 * time. A literal is frozen into the procedure; a param is exposed in every
	 * project's YAML and in presets. "Make param" turns the literal you typed
	 * into a param with that default, which is the usual way one is born.
	 */
	import type { ProcedureEditor } from './editor.svelte';
	import { type FieldSpec, type Path, getAt, isRef, paramLeaves, parseLiteral } from './model';
	import { valueSummary } from './presentation';

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
	<select
		class="lw-select w-auto shrink-0"
		value={mode}
		onchange={(e) => setMode(e.currentTarget.value)}
		aria-label="{fieldName}: kind of value"
	>
		{#if sweepsOnly}
			<option value="param">Sweep parameter</option>
			<option value="list">fixed list</option>
		{:else}
			<option value="literal">Fixed value</option>
			<option value="param">Parameter</option>
			<option value="swept" disabled={!scope.length}>Current sweep value</option>
		{/if}
	</select>

	{#if mode === 'param'}
		<select
			class="lw-select mono min-w-[10rem] flex-1"
			value={(value as { param: string }).param}
			onchange={(e) => editor.setAt(path, { param: e.currentTarget.value })}
			aria-label="{fieldName}: param"
		>
			{#if !params.some((p) => p.name === (value as { param: string }).param)}
				<option value={(value as { param: string }).param}>
					{(value as { param: string }).param || '— choose —'}
				</option>
			{/if}
			{#each params as p (p.name)}
				<option value={p.name}>{p.name}{p.decl.unit ? ` (${p.decl.unit})` : ''}</option>
			{/each}
		</select>
	{:else if mode === 'swept'}
		<select
			class="lw-select mono min-w-[8rem] flex-1"
			value={(value as { swept: string }).swept}
			onchange={(e) => editor.setAt(path, { swept: e.currentTarget.value })}
			aria-label="{fieldName}: swept value"
		>
			{#if !scope.includes((value as { swept: string }).swept)}
				<option value={(value as { swept: string }).swept}>
					{(value as { swept: string }).swept || '— choose —'}
				</option>
			{/if}
			{#each scope as name (name)}
				<option value={name}>{name}</option>
			{/each}
		</select>
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
			<button class="lw-btn lw-btn-sm" onclick={promote}>Add</button>
			<button class="lw-btn lw-btn-sm" onclick={() => (promoting = false)}>Cancel</button>
		{:else}
			<button
				class="lw-btn lw-btn-sm"
				onclick={startPromote}
				title="Expose this value as a param, with what you typed as its default"
			>
				Make parameter
			</button>
		{/if}
	{/if}
</div>
