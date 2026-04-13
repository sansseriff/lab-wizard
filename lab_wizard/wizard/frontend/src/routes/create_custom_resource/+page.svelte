<script lang="ts">
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type { TreeItem, TreePathRef } from '$lib/components/TreeNode.svelte';
	import Trash from 'phosphor-svelte/lib/Trash';
	import Plus from 'phosphor-svelte/lib/Plus';
	import { fetchWithConfig } from '../../api';

	type Selection = {
		id: string;
		variableName: string;
		type: string;
		key: string;
		pathRootToLeaf: TreePathRef[];
		pathLeafToRoot: TreePathRef[];
		pathDisplay: string;
		pathKey: string;
		channelIndex: number | null; // null = whole instrument
	};

	type PendingChannelChoice = {
		node: TreeItem;
		pathRootToLeaf: TreePathRef[];
		channelCount: number;
		mode: 'ask' | 'pick-channels';
		picked: Set<number>;
	};

	let { data } = $props();
	const tree: TreeItem[] = (data?.tree ?? []) as TreeItem[];

	let selections = $state<Selection[]>([]);
	let pickingMode = $state(false);
	let pending = $state<PendingChannelChoice | null>(null);

	let projectPrefix = $state('custom_resource');
	let resourceClassName = $state('CustomResources');
	let generationStyle = $state<'explicit' | 'from_attribute'>('explicit');
	let fileStyle = $state<'dataclass' | 'simple'>('dataclass');

	let creating = $state(false);
	let createError: string | null = $state(null);
	let createResult:
		| null
		| { project_name: string; project_dir: string; yaml_file: string; setup_file: string } =
		$state(null);

	let selectionCounter = 0;
	function nextId(): string {
		selectionCounter += 1;
		return `sel_${selectionCounter}`;
	}

	function pathKey(path: TreePathRef[]): string {
		return path.map((p) => `${p.type}:${p.key}`).join('|');
	}
	function pathDisplay(path: TreePathRef[]): string {
		return path.map((p) => `${p.type}(${p.key})`).join(' -> ');
	}
	function channelCount(node: TreeItem): number {
		const channels = node.fields?.channels;
		return Array.isArray(channels) ? channels.length : 0;
	}
	function shortName(prefix: string): string {
		return `${prefix}_${selections.length + 1}`;
	}

	function startPicking() {
		pickingMode = true;
		pending = null;
	}
	function cancelPicking() {
		pickingMode = false;
		pending = null;
	}

	function addSelectionFromNode(
		node: TreeItem,
		rootToLeaf: TreePathRef[],
		channelIndex: number | null
	) {
		const sel: Selection = {
			id: nextId(),
			variableName: shortName(channelIndex !== null ? `${node.type}_ch${channelIndex}` : node.type),
			type: node.type,
			key: node.key,
			pathRootToLeaf: rootToLeaf,
			pathLeafToRoot: [...rootToLeaf].reverse(),
			pathDisplay: pathDisplay(rootToLeaf),
			pathKey: pathKey(rootToLeaf) + (channelIndex !== null ? `#ch${channelIndex}` : ''),
			channelIndex
		};
		selections.push(sel);
		// If we just added the second selection while file style is "simple", force dataclass
		if (selections.length > 1 && fileStyle === 'simple') fileStyle = 'dataclass';
	}

	function onSelectTreeNode(node: TreeItem, rootToLeaf: TreePathRef[]) {
		if (!pickingMode) return;
		const cc = channelCount(node);
		if (cc > 1) {
			pending = {
				node,
				pathRootToLeaf: rootToLeaf,
				channelCount: cc,
				mode: 'ask',
				picked: new Set()
			};
			return;
		}
		addSelectionFromNode(node, rootToLeaf, null);
		pickingMode = false;
		pending = null;
	}

	function chooseWholeInstrument() {
		if (!pending) return;
		addSelectionFromNode(pending.node, pending.pathRootToLeaf, null);
		pending = null;
		pickingMode = false;
	}
	function choosePerChannel() {
		if (!pending) return;
		pending.mode = 'pick-channels';
	}
	function toggleChannel(idx: number) {
		if (!pending) return;
		if (pending.picked.has(idx)) pending.picked.delete(idx);
		else pending.picked.add(idx);
		// Reassign so reactivity sees the change
		pending.picked = new Set(pending.picked);
	}
	function confirmChannels() {
		if (!pending) return;
		const indices = Array.from(pending.picked).sort((a, b) => a - b);
		for (const idx of indices) {
			addSelectionFromNode(pending.node, pending.pathRootToLeaf, idx);
		}
		pending = null;
		pickingMode = false;
	}

	function removeSelection(id: string) {
		selections = selections.filter((s) => s.id !== id);
	}

	function selectionLabelForAny(_node: TreeItem, path: TreePathRef[]): string | null {
		const k = pathKey(path);
		const labels = selections
			.filter((s) => pathKey(s.pathRootToLeaf) === k)
			.map((s) =>
				s.channelIndex !== null ? `${s.variableName} [ch${s.channelIndex}]` : s.variableName
			);
		return labels.length ? labels.join(', ') : null;
	}

	function canSubmit(): boolean {
		if (selections.length === 0) return false;
		if (creating) return false;
		const names = new Set<string>();
		for (const s of selections) {
			const n = s.variableName.trim();
			if (!n) return false;
			if (names.has(n)) return false;
			names.add(n);
		}
		return true;
	}

	async function onCreate() {
		if (!canSubmit()) return;
		creating = true;
		createError = null;
		createResult = null;
		try {
			const body = {
				selections: selections.map((s) => ({
					variable_name: s.variableName.trim(),
					type: s.type,
					key: s.key,
					path: s.pathLeafToRoot,
					channel_index: s.channelIndex
				})),
				project_prefix: projectPrefix.trim() || 'custom_resource',
				generation_style: generationStyle,
				file_style: fileStyle,
				resource_class_name: resourceClassName.trim() || 'CustomResources'
			};
			const res = await fetchWithConfig('/api/create-custom-resource-project', 'POST', body);
			createResult = {
				project_name: res.project_name,
				project_dir: res.project_dir,
				yaml_file: res.yaml_file,
				setup_file: res.setup_file
			};
		} catch (err: any) {
			createError = err?.message ?? 'Failed to create custom resource project';
		} finally {
			creating = false;
		}
	}
</script>

<section class="space-y-4">
	<h1 class="text-2xl font-semibold">Create Custom Resource</h1>
	<p class="text-sm text-gray-600 dark:text-gray-300">
		Pick any instruments or channels from your configured tree and generate a standalone setup file.
	</p>

	<!-- Top controls -->
	<div
		class="grid gap-3 rounded-xl border border-gray-200 bg-white/70 p-4 sm:grid-cols-2 dark:border-white/10 dark:bg-gray-800/70"
	>
		<label class="block">
			<span class="text-xs text-gray-600 dark:text-gray-300">Project prefix</span>
			<input
				class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				bind:value={projectPrefix}
				placeholder="custom_resource"
			/>
		</label>
		{#if fileStyle === 'dataclass'}
			<label class="block">
				<span class="text-xs text-gray-600 dark:text-gray-300">Resource class name</span>
				<input
					class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
					bind:value={resourceClassName}
					placeholder="CustomResources"
				/>
			</label>
		{/if}

		<div class="block">
			<span class="text-xs text-gray-600 dark:text-gray-300">Codegen style</span>
			<div class="mt-1 flex gap-3 text-sm">
				<label class="flex items-center gap-1">
					<input type="radio" bind:group={generationStyle} value="explicit" />
					Explicit imports
				</label>
				<label class="flex items-center gap-1">
					<input type="radio" bind:group={generationStyle} value="from_attribute" />
					Attribute lookup
				</label>
			</div>
		</div>

		<div class="block">
			<span class="text-xs text-gray-600 dark:text-gray-300">File style</span>
			<div class="mt-1 flex gap-3 text-sm">
				<label class="flex items-center gap-1">
					<input type="radio" bind:group={fileStyle} value="dataclass" />
					Dataclass wrapper
				</label>
				<label class="flex items-center gap-1" class:opacity-50={selections.length !== 1}>
					<input
						type="radio"
						bind:group={fileStyle}
						value="simple"
						disabled={selections.length !== 1}
					/>
					Simple (single resource)
				</label>
			</div>
		</div>
	</div>

	<!-- Selections list -->
	<section
		class="space-y-2 rounded-xl border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70"
	>
		<div class="flex items-center justify-between">
			<h2 class="text-lg font-medium">Selected resources</h2>
			<button
				class="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
				onclick={startPicking}
				disabled={pickingMode}
			>
				<Plus size={16} />
				Add selection
			</button>
		</div>

		{#if selections.length === 0}
			<div class="px-2 py-3 text-sm text-gray-600 dark:text-gray-300">
				No resources selected yet.
			</div>
		{:else}
			<div class="space-y-2">
				{#each selections as s (s.id)}
					<div
						class="flex items-start gap-3 rounded-md border border-gray-200 bg-white/60 p-2 dark:border-white/10 dark:bg-gray-900/40"
					>
						<div class="flex-1 space-y-1">
							<input
								class="w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-900"
								bind:value={s.variableName}
								placeholder="variable_name"
							/>
							<div class="text-[11px] text-gray-600 dark:text-gray-300">
								{s.pathDisplay}
								{#if s.channelIndex !== null}
									<span class="ml-1">ch: {s.channelIndex}</span>
								{:else}
									<span class="ml-1">whole instrument</span>
								{/if}
							</div>
						</div>
						<button
							class="rounded p-1 text-gray-500 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/40"
							title="Remove"
							onclick={() => removeSelection(s.id)}
						>
							<Trash size={16} />
						</button>
					</div>
				{/each}
			</div>
		{/if}
	</section>

	<!-- Channel mode prompt -->
	{#if pending}
		<div
			class="rounded-lg border border-indigo-300 bg-indigo-50 p-3 text-sm dark:border-indigo-700 dark:bg-indigo-950/30"
		>
			{#if pending.mode === 'ask'}
				<div class="mb-2 font-medium">
					{pending.node.type} ({pending.node.key}) has {pending.channelCount} channels.
				</div>
				<div class="mb-2">Export the whole instrument or pick individual channel(s)?</div>
				<div class="flex gap-2">
					<button
						class="rounded-md bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-500"
						onclick={chooseWholeInstrument}
					>
						Whole instrument
					</button>
					<button
						class="rounded-md bg-gray-200 px-3 py-1.5 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-100 dark:hover:bg-gray-600"
						onclick={choosePerChannel}
					>
						Individual channel(s)
					</button>
					<button class="ml-auto text-xs text-gray-600 underline" onclick={cancelPicking}>
						Cancel
					</button>
				</div>
			{:else}
				<div class="mb-2 font-medium">
					Choose channel(s) of {pending.node.type} ({pending.node.key})
				</div>
				<div class="mb-2 flex flex-wrap gap-2">
					{#each Array.from({ length: pending.channelCount }, (_, i) => i) as i}
						<label
							class="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600"
						>
							<input
								type="checkbox"
								checked={pending.picked.has(i)}
								onchange={() => toggleChannel(i)}
							/>
							ch {i}
						</label>
					{/each}
				</div>
				<div class="flex gap-2">
					<button
						class="rounded-md bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-500 disabled:opacity-50"
						onclick={confirmChannels}
						disabled={pending.picked.size === 0}
					>
						Add {pending.picked.size} channel{pending.picked.size === 1 ? '' : 's'}
					</button>
					<button class="ml-auto text-xs text-gray-600 underline" onclick={cancelPicking}>
						Cancel
					</button>
				</div>
			{/if}
		</div>
	{/if}

	<!-- Tree -->
	<section class="space-y-2" class:opacity-40={!pickingMode} class:pointer-events-none={!pickingMode}>
		<div class="flex items-center justify-between">
			<h2 class="text-lg font-medium">Configured tree</h2>
			<div class="text-xs text-gray-600 dark:text-gray-300">
				{#if pickingMode}
					Selection mode: <span class="font-medium">click any node</span>
				{:else}
					Click "Add selection" above to start picking
				{/if}
			</div>
		</div>
		<ScrollArea
			class="relative overflow-hidden rounded-xl border border-gray-200 bg-white/70 p-3 shadow-sm transition-opacity dark:border-white/10 dark:bg-gray-800/70"
			orientation="vertical"
			viewportClasses="h-full max-h-[420px] w-full"
		>
			{#if tree.length === 0}
				<div class="px-2 py-3 text-sm text-gray-600 dark:text-gray-300">
					No configured instruments found.
				</div>
			{:else}
				{#each tree as node}
					<TreeNode
						{node}
						isSelectable={pickingMode && pending === null}
						isCompatible={() => true}
						selectionLabel={(n, p) => selectionLabelForAny(n, p)}
						onSelect={onSelectTreeNode}
					/>
				{/each}
			{/if}
		</ScrollArea>
	</section>

	{#if createResult}
		<div
			class="rounded-lg bg-green-100 px-3 py-2 text-sm text-green-800 dark:bg-green-900/30 dark:text-green-300"
		>
			Created project <span class="font-medium">{createResult.project_name}</span>
			<div class="mt-1 text-xs">
				<div>{createResult.project_dir}</div>
				<div>{createResult.yaml_file}</div>
				<div>{createResult.setup_file}</div>
			</div>
		</div>
	{/if}

	{#if createError}
		<div
			class="rounded-lg bg-red-100 px-3 py-2 text-sm text-red-800 dark:bg-red-900/30 dark:text-red-300"
		>
			{createError}
		</div>
	{/if}
</section>

<div class="mt-6 flex justify-end">
	<button
		class="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-white hover:bg-indigo-500 active:bg-indigo-700 disabled:opacity-50"
		onclick={onCreate}
		disabled={!canSubmit()}
	>
		{creating ? 'Creating...' : 'Create Custom Resource'}
	</button>
</div>
