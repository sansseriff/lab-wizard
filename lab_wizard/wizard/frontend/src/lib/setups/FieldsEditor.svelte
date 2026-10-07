<script lang="ts">
	/** A setup's fields as a tree of fields and groups, like a layers panel.
	 *
	 * One row per field: its name, its value, a unit if it has one, and its kind.
	 * A group's row folds its fields away, and its fields hang off a guide line,
	 * as the procedure composer's outline does; right-click a row for its
	 * actions, which also show on hover. Every run on the setup copies its
	 * fields, and each is a filter on the Data page: `cryostat` is
	 * `setup.cryostat`, a field `fiber` in a group `optics` is
	 * `setup.optics.fiber`, and a value with a unit — `{value: 100, unit: kΩ}`,
	 * because the unit is recorded with the run — is filtered by its value in
	 * its base unit. A picture is a field too, so a run keeps the picture of
	 * the bench it was taken on. Names and values this lab has recorded before
	 * are suggested, so the same thing keeps the same name.
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
	import { errorMessage } from '$lib/api';
	import { isQuantity } from '$lib/data/model';
	import { setupsApi } from './api';
	import { isImage, isImages } from './model';
	import UnitInput from './UnitInput.svelte';

	type Kind = 'text' | 'number' | 'bool' | 'image' | 'group';
	type Node = {
		id: number;
		key: string;
		kind: Kind;
		text: string;
		flag: boolean;
		unit: string;
		/** A picture field's pictures, by the names the backend keeps them under. */
		images: string[];
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
		/** Every setup.* key this lab has recorded, with the values seen. */
		recorded: Record<string, string[]>;
	} = $props();

	const PROBLEM_KEY = 'setup.fields';
	let next = 0;

	function blank(key: string, kind: Kind): Node {
		return { id: next++, key, kind, text: '', flag: false, unit: '', images: [], children: [], open: true };
	}

	function node(key: string, v: unknown): Node {
		const base = blank(key, 'text');
		if (isImage(v)) return { ...base, kind: 'image', images: [v.image] };
		if (isImages(v)) return { ...base, kind: 'image', images: v.map((i) => i.image) };
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
			else if (n.kind === 'image')
				out[n.key] = n.images.length === 1 ? { image: n.images[0] } : n.images.length ? n.images.map((image) => ({ image })) : null;
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
		list.push(blank('', kind));
		emit();
	}

	let uploading = $state<number | null>(null);
	let uploadError = $state('');

	async function addPictures(n: Node, files: FileList | null) {
		if (!files?.length) return;
		uploading = n.id;
		uploadError = '';
		try {
			for (const file of files) n.images.push(await setupsApi.uploadImage(file));
			emit();
		} catch (e) {
			uploadError = errorMessage(e);
		} finally {
			uploading = null;
		}
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
		const here = ['setup', ...path].join('.') + '.';
		return [
			...new Set(Object.keys(recorded).filter((k) => k.startsWith(here)).map((k) => k.slice(here.length).split('.')[0]))
		].sort();
	}
	const listId = (path: string[]) => `setup-keys-${path.join('-') || 'root'}`;

	const KINDS = [
		{ value: 'text', label: 'text' },
		{ value: 'number', label: 'number' },
		{ value: 'bool', label: 'yes/no' },
		{ value: 'image', label: 'picture' },
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
				siblings.splice(siblings.indexOf(n) + 1, 0, blank('', 'text'));
				emit();
			} },
			{ label: `Remove ${n.key || 'this field'}`, danger: true, onselect: () => remove(siblings, n) }
		];
	}

	// A cell reads as text until it is hovered or focused, as in a layers panel.
	// Every control in a row is one height and reads as text until the row is
	// hovered or the control has focus (the .quiet rule below).
	const cell =
		'quiet min-w-0 h-[var(--control-h-sm)] rounded border px-1.5 py-0 text-xs outline-none focus:border-accent';
</script>

{#snippet rows(nodes: Node[], path: string[])}
	<datalist id={listId(path)}>
		{#each keysAt(path) as key (key)}<option value={key}></option>{/each}
	</datalist>
	{#each nodes as n (n.id)}
		{@const problem = problemFor(n, nodes)}
		{@const full = [...path, n.key].join('.')}
		<RowContextMenu items={actions(n, nodes)}>
		<div class="field-row group/row flex items-center gap-1 rounded py-1 pr-0.5 hover:bg-surface-2 focus-within:bg-accent-wash/60">
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
			{:else if n.kind === 'image'}
				<div class="flex flex-[1.6] flex-wrap items-center gap-1.5 px-1.5 py-0.5">
					{#each n.images as image, i (image + i)}
						<span class="group/pic relative">
							<a href={setupsApi.imageUrl(image)} target="_blank" rel="noreferrer">
								<img class="h-12 rounded border border-line object-cover" src={setupsApi.imageUrl(image)} alt="{n.key} {i + 1}" />
							</a>
							<button
								type="button"
								class="absolute -top-1.5 -right-1.5 hidden rounded-full border border-line bg-surface p-0.5 text-muted group-hover/pic:block hover:text-crit"
								onclick={() => {
									n.images.splice(i, 1);
									emit();
								}}
								aria-label="Remove this picture"><XIcon size={10} /></button
							>
						</span>
					{/each}
					<label class="cursor-pointer rounded border border-dashed border-line px-2 py-1 text-fine text-accent hover:bg-accent-wash">
						{uploading === n.id ? 'Adding…' : n.images.length ? 'Add' : 'Add a picture'}
						<input
							class="sr-only"
							type="file"
							accept="image/*"
							multiple
							onchange={(e) => addPictures(n, e.currentTarget.files)}
						/>
					</label>
				</div>
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
					list={`setup-values-${full}`}
					bind:value={n.text}
					oninput={emit}
					aria-label="Value of {n.key}"
				/>
				<datalist id={`setup-values-${full}`}>
					{#each recorded[`setup.${full}`] ?? [] as v (v)}<option value={v}></option>{/each}
				</datalist>
				{#if n.kind === 'number' || n.unit}
				<UnitInput
					class="w-[4.75rem]"
					small
					quiet
					value={n.unit}
					onchange={(unit) => {
						n.unit = unit;
						emit();
					}}
					aria-label="Unit of {n.key}"
				/>
				{:else}
					<span class="w-[4.75rem] shrink-0"></span>
				{/if}
			{/if}
			<Select
				class="lw-select-sm quiet w-[5.5rem] shrink-0 text-xs"
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
			<div class="my-1 ml-3 border-l border-line-2 pl-2">
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
	{#if uploadError}<p class="pl-7 text-fine text-crit" role="alert">{uploadError}</p>{/if}
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

<style>
	/* Controls read as plain text until their row is hovered or one is used. */
	.field-row :global(.quiet) {
		border-color: transparent;
		background: transparent;
	}
	.field-row:hover :global(.quiet),
	.field-row :global(.quiet:focus),
	.field-row :global(.quiet:focus-within),
	.field-row :global(.quiet[data-state='open']) {
		border-color: var(--line-2);
		background: var(--surface);
	}
	.field-row :global(.quiet:focus) {
		border-color: var(--accent);
	}
	.field-row :global(.quiet svg),
	.field-row :global(.quiet-reveal) {
		opacity: 0;
	}
	.field-row:hover :global(.quiet svg),
	.field-row:hover :global(.quiet-reveal),
	.field-row :global(.quiet:focus-within svg),
	.field-row :global(.quiet[data-state='open'] svg) {
		opacity: 1;
	}
</style>
