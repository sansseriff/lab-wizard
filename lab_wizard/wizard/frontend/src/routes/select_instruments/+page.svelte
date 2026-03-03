<script lang="ts">
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type { TreeItem, TreePathRef } from '$lib/components/TreeNode.svelte';
	import { Select } from 'bits-ui';
	import CaretUpDown from 'phosphor-svelte/lib/CaretUpDown';
	import CaretDoubleUp from 'phosphor-svelte/lib/CaretDoubleUp';
	import CaretDoubleDown from 'phosphor-svelte/lib/CaretDoubleDown';
	import { fetchWithConfig } from '../../api';

	type MatchingReq = {
		module: string;
		class_name: string;
		qualname?: string;
		friendly_name?: string;
		file_path?: string;
	};
	type OutputReq = {
		variable_name: string;
		base_type: string;
		matching_instruments: MatchingReq[];
	};
	type InstrumentMeta = {
		type: string;
		class_name: string;
		module: string;
		is_top_level: boolean;
		is_child: boolean;
		parent_type: string | null;
		parent_chain: string[];
		child_types: string[];
		defaults: Record<string, any>;
		key_hint: string | null;
	};
	type SelectedChoice = {
		type: string;
		key: string;
		pathLeafToRoot: TreePathRef[];
		pathDisplay: string;
		pathKey: string;
		channelCount: number;
		channelIndex: number | null;
	};

	let { data } = $props();
	const measurementName: string | null = data?.measurementName ?? null;
	const reqs: OutputReq[] = (data?.instruments ?? []) as OutputReq[];
	const tree: TreeItem[] = (data?.tree ?? []) as TreeItem[];
	const metadata: Record<string, InstrumentMeta> = (data?.metadata ?? {}) as Record<string, InstrumentMeta>;

	const selected: Record<string, SelectedChoice | null> = $state({});
	for (const r of reqs) if (!(r.variable_name in selected)) selected[r.variable_name] = null;

	let activeRequirement = $state<string | null>(null);
	let projectPrefix = $state('');
	let creatingProject = $state(false);
	let createError: string | null = $state(null);
	let createResult:
		| null
		| { project_name: string; project_dir: string; yaml_file: string; setup_file: string } = $state(null);

	function shortBaseName(bt: string): string {
		const m = bt?.match(/<class '([^']+)'>/);
		const full = m?.[1] ?? bt ?? '';
		const parts = full.split('.');
		return parts[parts.length - 1] || full;
	}
	function classNameNoParams(name: string): string {
		return name.endsWith('Params') ? name.slice(0, -6) : name;
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
	function reqByVar(variableName: string | null): OutputReq | null {
		if (!variableName) return null;
		return reqs.find((r) => r.variable_name === variableName) ?? null;
	}
	function reqMatchesType(req: OutputReq, type: string): boolean {
		const meta = metadata[type];
		if (!meta) return false;
		const instClass = classNameNoParams(meta.class_name);
		const channelClass = `${instClass}Channel`;
		return req.matching_instruments.some(
			(m) =>
				m.module === meta.module &&
				(m.class_name === instClass || m.class_name === meta.class_name || m.class_name === channelClass)
		);
	}
	function isReqComplete(variableName: string): boolean {
		const s = selected[variableName];
		if (!s) return false;
		if (s.channelCount > 1 && s.channelIndex === null) return false;
		return true;
	}
	function allDone(): boolean {
		return reqs.length > 0 && reqs.every((r) => isReqComplete(r.variable_name));
	}
	function nextIncompleteAfter(variableName: string): string | null {
		const idx = reqs.findIndex((r) => r.variable_name === variableName);
		if (idx < 0) return null;
		for (const r of reqs.slice(idx + 1)) if (!isReqComplete(r.variable_name)) return r.variable_name;
		for (const r of reqs) if (!isReqComplete(r.variable_name)) return r.variable_name;
		return null;
	}
	function setActiveMode(variableName: string) {
		activeRequirement = variableName;
	}
	function onSelectTreeNode(node: TreeItem, rootToNodePath: TreePathRef[]) {
		if (!activeRequirement) return;
		const req = reqByVar(activeRequirement);
		if (!req) return;
		if (!reqMatchesType(req, node.type)) return;
		const cc = channelCount(node);
		selected[activeRequirement] = {
			type: node.type,
			key: node.key,
			pathLeafToRoot: [...rootToNodePath].reverse(),
			pathDisplay: pathDisplay(rootToNodePath),
			pathKey: pathKey(rootToNodePath),
			channelCount: cc,
			channelIndex: cc > 1 ? null : 0
		};
		if (cc <= 1) activeRequirement = nextIncompleteAfter(activeRequirement);
	}
	function setChannelForActive(value: string) {
		if (!activeRequirement) return;
		const cur = selected[activeRequirement];
		if (!cur) return;
		const parsed = Number.parseInt(value, 10);
		cur.channelIndex = Number.isNaN(parsed) ? null : parsed;
		if (cur.channelIndex !== null) activeRequirement = nextIncompleteAfter(activeRequirement);
	}
	function isCompatibleForCurrent(node: TreeItem): boolean {
		const req = reqByVar(activeRequirement);
		if (!req) return false;
		return reqMatchesType(req, node.type);
	}
	function isNodeSelectedForCurrent(_node: TreeItem, path: TreePathRef[]): boolean {
		if (!activeRequirement) return false;
		const sel = selected[activeRequirement];
		if (!sel) return false;
		return sel.pathKey === pathKey(path);
	}
	function selectionLabelForAny(_node: TreeItem, path: TreePathRef[]): string | null {
		const labels: string[] = [];
		const key = pathKey(path);
		for (const r of reqs) {
			if (selected[r.variable_name]?.pathKey === key) labels.push(r.variable_name);
		}
		return labels.length ? labels.join(', ') : null;
	}
	async function onCreateProject() {
		if (!measurementName || !allDone()) return;
		creatingProject = true;
		createError = null;
		createResult = null;
		try {
			const selected_resources = reqs.map((r) => {
				const c = selected[r.variable_name];
				if (!c) throw new Error(`Missing selection for ${r.variable_name}`);
				return {
					variable_name: r.variable_name,
					type: c.type,
					key: c.key,
					path: c.pathLeafToRoot,
					channel_index: c.channelCount > 1 ? c.channelIndex : null
				};
			});
			const body: Record<string, any> = { measurement_name: measurementName, selected_resources };
			if (projectPrefix.trim()) body.project_prefix = projectPrefix.trim();
			const res = await fetchWithConfig('/api/create-measurement-project', 'POST', body);
			createResult = {
				project_name: res.project_name,
				project_dir: res.project_dir,
				yaml_file: res.yaml_file,
				setup_file: res.setup_file
			};
		} catch (err: any) {
			createError = err?.message ?? 'Failed to create project';
		} finally {
			creatingProject = false;
		}
	}
</script>

<section class="space-y-4">
	<h1 class="text-2xl font-semibold">Select instruments</h1>
	{#if !measurementName}
		<div class="text-sm text-gray-600 dark:text-gray-300">
			No measurement selected. <a class="text-indigo-600 underline" href="/get_measurements"
				>Go back</a
			>.
		</div>
	{:else}
		<p class="text-sm text-gray-600 dark:text-gray-300">
			Measurement: <span class="font-medium">{measurementName}</span>
		</p>
	{/if}

	{#if measurementName}
		{#if !reqs || reqs.length === 0}
			<div
				class="rounded-xl border border-gray-200 bg-white/70 p-4 text-sm text-gray-600 dark:border-white/10 dark:bg-gray-800/70 dark:text-gray-300"
			>
				No instrument roles detected for this measurement.
			</div>
		{:else}
			<div class="rounded-xl border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70">
				<label class="mb-1 block text-xs text-gray-600 dark:text-gray-300" for="project-prefix"
					>Project prefix (optional)</label
				>
				<input
					id="project-prefix"
					type="text"
					bind:value={projectPrefix}
					placeholder="iv_curve_run"
					class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				/>
			</div>

			<div class="space-y-4">
				{#each reqs as r}
					<section class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70">
						<div class="flex items-start justify-between gap-3">
							<div>
								<div class="font-medium">{r.variable_name}</div>
								<div class="text-xs text-gray-600 dark:text-gray-300">
									Requires {shortBaseName(r.base_type)}
								</div>
								{#if selected[r.variable_name]}
									<div class="mt-1 text-[11px] text-gray-600 dark:text-gray-300">
										{selected[r.variable_name]?.pathDisplay}
										{#if selected[r.variable_name]?.channelCount && selected[r.variable_name]!.channelCount > 1}
											<span class="ml-1">
												ch:
												{selected[r.variable_name]?.channelIndex === null
													? 'unset'
													: selected[r.variable_name]?.channelIndex}
											</span>
										{/if}
									</div>
								{/if}
							</div>
							<button
								class="rounded-md px-3 py-1.5 text-sm {activeRequirement === r.variable_name
									? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300'
									: 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600'}"
								onclick={() => setActiveMode(r.variable_name)}
							>
								{activeRequirement === r.variable_name
									? 'Selecting...'
									: selected[r.variable_name]
										? 'Change'
										: 'Select'}
							</button>
						</div>

						{#if activeRequirement === r.variable_name && selected[r.variable_name] && selected[r.variable_name]!.channelCount > 1}
							<div class="mt-2 rounded-md bg-indigo-50 p-2 text-sm dark:bg-indigo-950/30">
								<label for={`${r.variable_name}-channel-trigger`} class="mb-1 block text-xs"
									>Choose channel</label
								>
								<Select.Root
									type="single"
									value={selected[r.variable_name]?.channelIndex === null
										? ''
										: String(selected[r.variable_name]?.channelIndex)}
									onValueChange={(v) => setChannelForActive(v)}
									items={Array.from(
										{ length: selected[r.variable_name]!.channelCount },
										(_, i) => ({ value: String(i), label: String(i) })
									)}
								>
									<Select.Trigger
										id={`${r.variable_name}-channel-trigger`}
										class="inline-flex w-[260px] items-center justify-between rounded border border-gray-300 bg-white px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-900"
										aria-label="Select channel"
									>
										<span>
											{selected[r.variable_name]?.channelIndex === null
												? 'Choose channel'
												: `Channel ${selected[r.variable_name]?.channelIndex}`}
										</span>
										<CaretUpDown class="ml-2 size-4 text-gray-500 dark:text-gray-300" />
									</Select.Trigger>
									<Select.Portal>
										<Select.Content
											side="bottom"
											align="center"
											sideOffset={6}
											class="z-50 w-[260px] rounded border border-gray-300 bg-white p-1 shadow dark:border-gray-600 dark:bg-gray-900"
										>
											<Select.ScrollUpButton class="flex items-center justify-center py-1">
												<CaretDoubleUp class="size-3 text-gray-500 dark:text-gray-300" />
											</Select.ScrollUpButton>
											<Select.Viewport>
												{#each Array.from({ length: selected[r.variable_name]!.channelCount }, (_, i) => i) as i}
													<Select.Item
														value={String(i)}
														label={`Channel ${i}`}
														class="rounded px-2 py-1 text-xs data-highlighted:bg-indigo-100 dark:data-highlighted:bg-indigo-900/40"
													>
														{#snippet children()}
															Channel {i}
														{/snippet}
													</Select.Item>
												{/each}
											</Select.Viewport>
											<Select.ScrollDownButton class="flex items-center justify-center py-1">
												<CaretDoubleDown class="size-3 text-gray-500 dark:text-gray-300" />
											</Select.ScrollDownButton>
										</Select.Content>
									</Select.Portal>
								</Select.Root>
							</div>
						{/if}
					</section>
				{/each}
			</div>

			<section class="space-y-2">
				<div class="flex items-center justify-between">
					<h2 class="text-lg font-medium">Configured tree</h2>
					<div class="text-xs text-gray-600 dark:text-gray-300">
						{#if activeRequirement}
							Selection mode: <span class="font-medium">{activeRequirement}</span>
						{:else}
							Pick a requirement above to start selecting
						{/if}
					</div>
				</div>
				<ScrollArea
					class="relative overflow-hidden rounded-xl border border-gray-200 bg-white/70 p-3 shadow-sm dark:border-white/10 dark:bg-gray-800/70"
					orientation="vertical"
					viewportClasses="h-full max-h-[360px] w-full"
				>
					{#if tree.length === 0}
						<div class="px-2 py-3 text-sm text-gray-600 dark:text-gray-300">
							No configured instruments found.
						</div>
					{:else}
						{#each tree as node}
							<TreeNode
								{node}
								isSelectable={Boolean(activeRequirement)}
								isCompatible={(n) => isCompatibleForCurrent(n)}
								isSelected={(n, p) => isNodeSelectedForCurrent(n, p)}
								selectionLabel={(n, p) => selectionLabelForAny(n, p)}
								onSelect={onSelectTreeNode}
							/>
						{/each}
					{/if}
				</ScrollArea>
			</section>

			{#if createResult}
				<div class="rounded-lg bg-green-100 px-3 py-2 text-sm text-green-800 dark:bg-green-900/30 dark:text-green-300">
					Created project <span class="font-medium">{createResult.project_name}</span>
					<div class="mt-1 text-xs">
						<div>{createResult.project_dir}</div>
						<div>{createResult.yaml_file}</div>
						<div>{createResult.setup_file}</div>
					</div>
				</div>
			{/if}

			{#if createError}
				<div class="rounded-lg bg-red-100 px-3 py-2 text-sm text-red-800 dark:bg-red-900/30 dark:text-red-300">
					{createError}
								</div>
			{/if}
		{/if}
	{/if}
</section>

{#if measurementName}
	<div class="mt-6 flex justify-end">
		<button
			class="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-white hover:bg-indigo-500 active:bg-indigo-700 disabled:opacity-50"
			onclick={onCreateProject}
			disabled={!allDone() || creatingProject}
		>
			{creatingProject ? 'Creating...' : 'Create Project'}
		</button>
	</div>
{/if}
