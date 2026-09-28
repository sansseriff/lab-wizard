<script lang="ts">
	/** A run's metadata as a tree of fields and groups, like a layers panel.
	 *
	 * One row per field: its name, its value, a unit if it has one, and its kind.
	 * A group's row folds its fields away, and its fields hang off a guide line,
	 * as the procedure composer's outline does; right-click a row for its
	 * actions, which also show on hover. Each
	 * field is a filter on the Data page: `cryostat` is `run.cryostat`, a field
	 * `fiber` in a group `optics` is `run.optics.fiber`, and a value with a unit
	 * — written `{value: 0.8, unit: K}` in the YAML, because the unit is recorded
	 * with the run — is filtered by its value and shown with its unit. Names and
	 * values this lab has recorded before are suggested, so the same thing keeps
	 * the same name.
	 */
	import { onDestroy, untrack } from 'svelte';
	import CaretDownIcon from 'phosphor-svelte/lib/CaretDown';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import XIcon from 'phosphor-svelte/lib/X';
	import IconButton from '$lib/components/IconButton.svelte';
	import Select from '$lib/components/Select.svelte';
	import RowContextMenu from '$lib/components/menu/RowContextMenu.svelte';
	import type { MenuAction } from '$lib/components/menu/items';
	import { isQuantity } from '$lib/data/model';

	type Kind = 'text' | 'number' | 'bool' | 'group';
	type Node = {
		id: number;
		key: string;
		kind: Kind;
		text: string;
		flag: boolean;
		unit: string;
		children: Node[];
		open: boolean;
	};

	let {
		value,
		onchange,
		onproblem,
		recorded
	}: {
		value: Record<string, unknown>;
		onchange: (value: Record<string, unknown>) => void;
		/** The tree's first problem, or null; keeps Save disabled while there is one. */
		onproblem: (key: string, message: string | null) => void;
		/** Every run.* key this lab has recorded, with the values seen. */
		recorded: Record<string, string[]>;
	} = $props();

	const PROBLEM_KEY = 'run.metadata';
	let next = 0;

	function node(key: string, v: unknown): Node {
		const base: Node = { id: next++, key, kind: 'text', text: '', flag: false, unit: '', children: [], open: true };
		if (isQuantity(v)) {
			return { ...base, kind: typeof v.value === 'number' ? 'number' : 'text', text: String(v.value ?? ''), unit: v.unit };
		}
		if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
			return { ...base, kind: 'group', children: Object.entries(v).map(([k, c]) => node(k, c)) };
		}
		if (typeof v === 'boolean') return { ...base, kind: 'bool', flag: v };
		if (typeof v === 'number') return { ...base, kind: 'number', text: String(v) };
		return { ...base, text: v === null || v === undefined ? '' : String(v) };
	}

	// Seeded once: the tree is the editor's own from then on, so a field can be
	// renamed without losing its place. The page re-mounts it on every load.
	let root = $state<Node[]>(untrack(() => Object.entries(value).map(([k, v]) => node(k, v))));

	function problemFor(n: Node, siblings: Node[]): string | null {
		if (!n.key.trim()) return 'Needs a name.';
		if (/[.\s]/.test(n.key)) return 'A name cannot contain spaces or dots; use a group.';
		if (siblings.filter((s) => s.key === n.key).length > 1) return `${n.key} is here twice.`;
		if (n.kind === 'number' && (n.text.trim() === '' || !Number.isFinite(Number(n.text)))) return 'Not a number.';
		return null;
	}

	function firstProblem(nodes: Node[]): string | null {
		for (const n of nodes) {
			const own = problemFor(n, nodes) ?? (n.kind === 'group' ? firstProblem(n.children) : null);
			if (own) return own;
		}
		return null;
	}

	function toValue(nodes: Node[]): Record<string, unknown> {
		const out: Record<string, unknown> = {};
		for (const n of nodes) {
			if (!n.key.trim()) continue;
			if (n.kind === 'group') out[n.key] = toValue(n.children);
			else if (n.kind === 'bool') out[n.key] = n.flag;
			else {
				const v = n.kind === 'number' ? Number(n.text) : n.text;
				out[n.key] = n.unit.trim() ? { value: v, unit: n.unit.trim() } : v;
			}
		}
		return out;
	}

	function emit() {
		onproblem(PROBLEM_KEY, firstProblem(root));
		onchange(toValue(root));
	}
	onDestroy(() => onproblem(PROBLEM_KEY, null));

	function add(list: Node[], kind: Kind) {
		list.push({ id: next++, key: '', kind, text: '', flag: false, unit: '', children: [], open: true });
		emit();
	}

	function remove(list: Node[], n: Node) {
		list.splice(list.indexOf(n), 1);
		emit();
	}

	function setKind(n: Node, kind: Kind) {
		n.kind = kind;
		if (kind === 'group') n.open = true;
		emit();
	}

	/** Names this lab has recorded at a depth of the tree. */
	function keysAt(path: string[]): string[] {
		const here = ['run', ...path].join('.') + '.';
		return [
			...new Set(Object.keys(recorded).filter((k) => k.startsWith(here)).map((k) => k.slice(here.length).split('.')[0]))
		].sort();
	}
	const listId = (path: string[]) => `meta-keys-${path.join('-') || 'root'}`;

	const KINDS = [
		{ value: 'text', label: 'text' },
		{ value: 'number', label: 'number' },
		{ value: 'bool', label: 'yes/no' },
		{ value: 'group', label: 'group' }
	];

	function addTo(n: Node, kind: Kind) {
		n.open = true;
		add(n.children, kind);
	}

	/** A row's actions, for its right-click menu. */
	function actions(n: Node, siblings: Node[]): MenuAction[] {
		return [
			...(n.kind === 'group'
				? [
						{ label: 'Add a field inside', onselect: () => addTo(n, 'text') },
						{ label: 'Add a group inside', onselect: () => addTo(n, 'group') }
					]
				: []),
			{ label: 'Add a field after', onselect: () => {
				siblings.splice(siblings.indexOf(n) + 1, 0, { id: next++, key: '', kind: 'text', text: '', flag: false, unit: '', children: [], open: true });
				emit();
			} },
			{ label: `Remove ${n.key || 'this field'}`, danger: true, onselect: () => remove(siblings, n) }
		];
	}

	// A cell reads as text until it is hovered or focused, as in a layers panel.
	const cell =
		'min-w-0 rounded border border-transparent bg-transparent px-1.5 py-[3px] text-xs outline-none hover:border-line focus:border-accent focus:bg-surface';
</script>

{#snippet rows(nodes: Node[], path: string[])}
	<datalist id={listId(path)}>
		{#each keysAt(path) as key (key)}<option value={key}></option>{/each}
	</datalist>
	{#each nodes as n (n.id)}
		{@const problem = problemFor(n, nodes)}
		{@const full = [...path, n.key].join('.')}
		<RowContextMenu items={actions(n, nodes)}>
		<div class="group/row flex items-center gap-1 rounded py-px pr-0.5 hover:bg-surface-2 focus-within:bg-accent-wash/60">
			{#if n.kind === 'group'}
				<button
					type="button"
					class="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-surface-2"
					onclick={() => (n.open = !n.open)}
					aria-label="{n.open ? 'Collapse' : 'Expand'} {n.key || 'group'}"
					aria-expanded={n.open}
				>
					{#if n.open}<CaretDownIcon size={12} />{:else}<CaretRightIcon size={12} />{/if}
				</button>
			{:else}
				<span class="w-6 shrink-0"></span>
			{/if}
			<input
				class="{cell} mono flex-[1.1] {n.kind === 'group' ? 'font-semibold' : ''} {problem ? 'border-crit' : ''}"
				placeholder={n.kind === 'group' ? 'group name' : 'name'}
				list={listId(path)}
				bind:value={n.key}
				oninput={emit}
				aria-label="Name"
				aria-invalid={problem ? true : undefined}
			/>
			{#if n.kind === 'group'}
				<span class="flex-[1.6] truncate px-1.5 text-fine text-muted">
					{n.children.length} field{n.children.length === 1 ? '' : 's'}
				</span>
			{:else if n.kind === 'bool'}
				<label class="flex flex-[1.6] items-center gap-1.5 px-1.5 text-xs">
					<input type="checkbox" bind:checked={n.flag} onchange={emit} aria-label="Value of {n.key}" />
					{n.flag ? 'yes' : 'no'}
				</label>
			{:else}
				<input
					class="{cell} flex-1 {n.kind === 'number' ? 'mono' : ''} {problem && n.kind === 'number' ? 'border-crit' : ''}"
					placeholder="value"
					inputmode={n.kind === 'number' ? 'decimal' : undefined}
					list={`meta-values-${full}`}
					bind:value={n.text}
					oninput={emit}
					aria-label="Value of {n.key}"
				/>
				<datalist id={`meta-values-${full}`}>
					{#each recorded[`run.${full}`] ?? [] as v (v)}<option value={v}></option>{/each}
				</datalist>
				<input class="{cell} mono w-12 shrink-0" placeholder="unit" bind:value={n.unit} oninput={emit} aria-label="Unit of {n.key}" />
			{/if}
			<Select
				class="lw-select-sm w-[5.5rem] shrink-0"
				value={n.kind}
				onValueChange={(v) => setKind(n, v as Kind)}
				options={KINDS}
				aria-label="Kind of {n.key || 'field'}"
			/>
			<span class="flex w-14 shrink-0 justify-end opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100">
				{#if n.kind === 'group'}
					<IconButton small ghost label="Add a field to {n.key || 'this group'}" onclick={() => addTo(n, 'text')}>
						<PlusIcon size={12} />
					</IconButton>
				{/if}
				<IconButton small ghost label="Remove {n.key || 'this field'}" onclick={() => remove(nodes, n)}>
					<XIcon size={12} />
				</IconButton>
			</span>
		</div>
		</RowContextMenu>
		{#if problem}
			<p class="pl-7 text-fine text-crit">{problem}</p>
		{/if}
		{#if n.kind === 'group' && n.open}
			<div class="ml-3 border-l border-line-2 pl-2">
				{#if n.children.length}
					{@render rows(n.children, [...path, n.key])}
				{:else}
					<p class="py-1 pl-7 text-fine text-muted">Empty: add a field with + or a right-click.</p>
				{/if}
			</div>
		{/if}
	{/each}
{/snippet}

<div class="-mx-1">
	{@render rows(root, [])}
	<!-- As the composer's "Add step": quiet, in the accent colour. -->
	<div class="mt-0.5 flex flex-wrap gap-1 pl-6">
		<button type="button" class="flex items-center gap-[5px] rounded px-2 py-[5px] text-xs text-accent hover:bg-accent-wash" onclick={() => add(root, 'text')}>
			<PlusIcon size={12} /> Add field
		</button>
		<button type="button" class="flex items-center gap-[5px] rounded px-2 py-[5px] text-xs text-accent hover:bg-accent-wash" onclick={() => add(root, 'group')}>
			<PlusIcon size={12} /> Add group
		</button>
	</div>
</div>
