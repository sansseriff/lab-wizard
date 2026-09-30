<script lang="ts">
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import IconButton from '$lib/components/IconButton.svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import { type Claim, busyCounts, busyLabel } from '$lib/claims';
	import type { TreeItem, TreePathRef } from '$lib/components/TreeNode.svelte';
	import Trash from 'phosphor-svelte/lib/Trash';
	import Plus from 'phosphor-svelte/lib/Plus';
	import { api, errorMessage, unwrap } from '$lib/api';

	type AttributeEntry = {
		attribute_name: string;
		path: string;
		behavior_abc: string | null;
		type_hint: string | null;
		/** Set when a run holds this instrument right now. */
		claimed_by?: string | null;
	};
	type InstrumentMeta = { type: string; class_name: string };
	type Source = {
		name: string;
		kind: 'local' | 'machine' | 'remote';
		label: string;
		url: string | null;
		tree: TreeItem[] | null;
		metadata: Record<string, InstrumentMeta>;
		attributes: AttributeEntry[];
		reachable: boolean;
		error: string | null;
		claims?: Claim[];
	};
	type Selection = {
		id: string;
		variableName: string;
		source: string;
		sourceLabel: string;
		sourceKind: 'local' | 'machine' | 'remote';
		// Set for a routed selection; a local one is resolved from its tree path.
		attribute: string | null;
		behaviorAbc: string | null;
		type: string;
		key: string;
		pathRootToLeaf: TreePathRef[];
		pathLeafToRoot: TreePathRef[];
		pathDisplay: string;
		pathKey: string;
		channelIndex: number | null; // null = whole instrument
	};

	type PendingChannelChoice = {
		source: Source;
		node: TreeItem;
		pathRootToLeaf: TreePathRef[];
		channelCount: number;
		mode: 'ask' | 'pick-channels';
		picked: Set<number>;
	};

	let { data } = $props();
	const sources: Source[] = $derived((data?.sources ?? []) as Source[]);
	// The source whose tab is open. The embedded style opens every instrument
	// itself, so while it is chosen only this workspace's tab can be open: the
	// others are greyed, and whoever was on one lands back on this workspace.
	// Falls back the same way when a source disappears on reload.
	let sourceTab = $state('');
	const tabDisabled = (s: Source) => embedded && s.kind !== 'local';
	const activeSource = $derived(
		sources.find((s) => s.name === sourceTab && !tabDisabled(s)) ??
			sources.find((s) => s.kind === 'local') ??
			sources[0] ??
			null
	);
	const busyCount = $derived(busyCounts(sources));

	let selections = $state<Selection[]>([]);
	let pickingMode = $state(false);
	let pending = $state<PendingChannelChoice | null>(null);

	let projectPrefix = $state('custom_resource');
	let resourceClassName = $state('CustomResources');
	let generationStyle = $state<
		'production' | 'pedagogical_embedded'
	>('production');
	let fileStyle = $state<'dataclass' | 'simple'>('dataclass');

	// The embedded style opens every instrument itself, so it cannot use one
	// through a server: the server tabs are greyed while it is chosen,
	// and it cannot be chosen once one is picked. The backend refuses it too.
	const embedded = $derived(generationStyle === 'pedagogical_embedded');
	const routedChosen = $derived(
		selections.filter((s) => s.sourceKind !== 'local').map((s) => s.variableName)
	);
	let persistAttributeNames = $state(false);

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

	// Source-qualified: two workspaces can hold the same instrument at the same
	// hash key, and comparing paths alone would confuse one for the other.
	function pathKey(source: string, path: TreePathRef[]): string {
		return `${source}::${path.map((p) => `${p.type}:${p.key}`).join('|')}`;
	}

	/** attribute_name of a tree node, or of one of its channels. */
	function attributeOf(node: TreeItem, channelIndex: number | null): string | null {
		if (channelIndex !== null) {
			const channels = node.fields?.channels;
			const channel = channels?.[channelIndex] ?? channels?.[String(channelIndex)];
			return channel?.attribute_name || null;
		}
		return node.fields?.attribute_name || null;
	}
	function pathDisplay(path: TreePathRef[]): string {
		return path.map((p) => `${p.type}(${p.key})`).join(' -> ');
	}
	function channelCount(node: TreeItem): number {
		return typeof node.num_channels === 'number' ? node.num_channels : 0;
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
		source: Source,
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
			pathKey:
				pathKey(source.name, rootToLeaf) + (channelIndex !== null ? `#ch${channelIndex}` : ''),
			channelIndex,
			source: source.name,
			sourceLabel: source.label,
			sourceKind: source.kind,
			attribute: source.kind === 'local' ? null : attributeOf(node, channelIndex),
			behaviorAbc: null
		};
		selections.push(sel);
		// If we just added the second selection while file style is "simple", force dataclass
		if (selections.length > 1 && fileStyle === 'simple') fileStyle = 'dataclass';
	}

	function onSelectTreeNode(source: Source, node: TreeItem, rootToLeaf: TreePathRef[]) {
		if (!pickingMode) return;
		const cc = channelCount(node);
		if (cc > 1) {
			pending = {
				source,
				node,
				pathRootToLeaf: rootToLeaf,
				channelCount: cc,
				mode: 'ask',
				picked: new Set()
			};
			return;
		}
		addSelectionFromNode(source, node, rootToLeaf, null);
		pickingMode = false;
		pending = null;
	}

	/** A named leaf from a remote machine: no tree position, nothing to expand. */
	function onSelectAttribute(source: Source, entry: AttributeEntry) {
		if (!pickingMode) return;
		selections.push({
			id: nextId(),
			variableName: shortName(entry.attribute_name),
			source: source.name,
			sourceLabel: source.label,
			sourceKind: source.kind,
			attribute: entry.attribute_name,
			behaviorAbc: entry.behavior_abc,
			type: entry.type_hint ?? '',
			key: '',
			pathRootToLeaf: [],
			pathLeafToRoot: [],
			pathDisplay: `${entry.attribute_name} on ${source.label}`,
			pathKey: `${source.name}::@${entry.attribute_name}`,
			channelIndex: null
		});
		if (selections.length > 1 && fileStyle === 'simple') fileStyle = 'dataclass';
		pickingMode = false;
	}

	function chooseWholeInstrument() {
		if (!pending) return;
		addSelectionFromNode(pending.source, pending.node, pending.pathRootToLeaf, null);
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
			addSelectionFromNode(pending.source, pending.node, pending.pathRootToLeaf, idx);
		}
		pending = null;
		pickingMode = false;
	}

	function removeSelection(id: string) {
		selections = selections.filter((s) => s.id !== id);
	}

	function selectionLabelForAny(source: Source, path: TreePathRef[]): string | null {
		const k = pathKey(source.name, path);
		const labels = selections
			.filter((s) => pathKey(s.source, s.pathRootToLeaf) === k)
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
					channel_index: s.channelIndex,
					source: s.source,
					attribute: s.attribute,
					behavior_abc: s.behaviorAbc
				})),
				project_prefix: projectPrefix.trim() || 'custom_resource',
				generation_style: generationStyle,
				file_style: fileStyle,
				resource_class_name: resourceClassName.trim() || 'CustomResources',
				persist_attribute_names: generationStyle === 'production' && persistAttributeNames
			};
			const res = await unwrap<{ project_name: string; project_dir: string; yaml_file: string; setup_file: string }>(
				api.POST('/api/create-custom-resource-project', { body })
			);
			createResult = {
				project_name: res.project_name,
				project_dir: res.project_dir,
				yaml_file: res.yaml_file,
				setup_file: res.setup_file
			};
		} catch (err) {
			createError = errorMessage(err) || 'Failed to create custom resource project';
		} finally {
			creating = false;
		}
	}
</script>

<section class="space-y-4">
	<h1 class="text-headline font-semibold">Create Custom Resource</h1>
	<p class="text-body text-ink-2">
		Pick any instruments or channels from your configured tree and generate a standalone setup file.
	</p>

	<!-- Top controls -->
	<div
		class="grid gap-3 rounded border border-line bg-surface p-4 sm:grid-cols-2"
	>
		<label class="block">
			<span class="text-xs text-ink-2">Project prefix</span>
			<input
				class="mt-1 w-full rounded-md border border-line-2 px-3 py-2 text-body"
				bind:value={projectPrefix}
				placeholder="custom_resource"
			/>
		</label>
		{#if fileStyle === 'dataclass'}
			<label class="block">
				<span class="text-xs text-ink-2">Resource class name</span>
				<input
					class="mt-1 w-full rounded-md border border-line-2 px-3 py-2 text-body"
					bind:value={resourceClassName}
					placeholder="CustomResources"
				/>
			</label>
		{/if}

		<div class="block">
			<span class="text-xs text-ink-2">Codegen style</span>
			<div class="mt-1 grid gap-2 text-body">
				<label class="flex items-start gap-2">
					<input type="radio" bind:group={generationStyle} value="production" />
					<span>
						<span class="block font-medium">Production</span>
						<span class="block text-xs text-muted">
							Names each instrument; its settings come from the config tree that owns it — this
							workspace's, or a server's — when the file runs.
						</span>
					</span>
				</label>
				<label class="flex items-start gap-2 {routedChosen.length ? 'opacity-60' : ''}">
					<input
						type="radio"
						bind:group={generationStyle}
						value="pedagogical_embedded"
						onchange={() => (sourceTab = '')}
						disabled={routedChosen.length > 0}
					/>
					<span>
						<span class="block font-medium">Escape hatch: embedded params</span>
						<span class="block text-xs text-muted">
							Every setting written into the Python file, to run outside this workspace or to learn
							from. Breaks when an instrument is readdressed; cannot use instruments through a
							server.
						</span>
						{#if routedChosen.length}
							<span class="block text-xs text-warn">
								Not available: {routedChosen.join(', ')} {routedChosen.length === 1 ? 'is' : 'are'} used through a
								server.
							</span>
						{/if}
					</span>
				</label>
			</div>
		</div>

		{#if generationStyle === 'production'}
			<label class="flex items-start gap-2 sm:col-span-2">
				<input type="checkbox" class="mt-0.5" bind:checked={persistAttributeNames} />
				<span class="text-xs text-ink-2">
					Save auto-generated attribute names back to the instrument config
					<span class="block text-fine text-muted">
						Leave off for one-off/project-specific names. Turn on to make them persistent
						"favorites" reachable from any future project.
					</span>
				</span>
			</label>
		{/if}

		<div class="block">
			<span class="text-xs text-ink-2">File style</span>
			<div class="mt-1 flex gap-3 text-body">
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
		class="space-y-2 rounded border border-line bg-surface p-3"
	>
		<div class="flex items-center justify-between">
			<h2 class="text-title font-medium">Selected resources</h2>
			<button
				class="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-body text-white hover:brightness-110 disabled:opacity-50"
				onclick={startPicking}
				disabled={pickingMode}
			>
				<Plus size={16} />
				Add selection
			</button>
		</div>

		{#if selections.length === 0}
			<div class="px-2 py-3 text-body text-ink-2">
				No resources selected yet.
			</div>
		{:else}
			<div class="space-y-2">
				{#each selections as s (s.id)}
					<div
						class="flex items-start gap-3 rounded-md border border-line bg-surface p-2/40"
					>
						<div class="flex-1 space-y-1">
							<input
								class="w-full rounded border border-line-2 px-2 py-1 text-body"
								bind:value={s.variableName}
								placeholder="variable_name"
							/>
							<div class="text-fine text-ink-2">
								{#if s.source !== 'local'}
									<span
										class="mr-1 rounded bg-accent-wash px-1.5 py-0.5 text-2xs font-medium text-accent-strong"
										title="Used through this server rather than opened by the generated file"
									>
										{s.sourceLabel}
									</span>
								{/if}
								{s.pathDisplay}
								{#if s.channelIndex !== null}
									<span class="ml-1">ch: {s.channelIndex}</span>
								{:else}
									<span class="ml-1">whole instrument</span>
								{/if}
							</div>
						</div>
						<IconButton ghost label="Remove" onclick={() => removeSelection(s.id)}><Trash size={16} /></IconButton>
					</div>
				{/each}
			</div>
		{/if}
	</section>

	<!-- Channel mode prompt -->
	{#if pending}
		<div
			class="rounded-lg border border-accent bg-accent-wash p-3 text-body"
		>
			{#if pending.mode === 'ask'}
				<div class="mb-2 font-medium">
					{pending.node.type} ({pending.node.key}) has {pending.channelCount} channels.
				</div>
				<div class="mb-2">Export the whole instrument or pick individual channel(s)?</div>
				<div class="flex gap-2">
					<button
						class="rounded-md bg-accent px-3 py-1.5 text-white hover:brightness-110"
						onclick={chooseWholeInstrument}
					>
						Whole instrument
					</button>
					<button
						class="rounded-md bg-surface-2 px-3 py-1.5 text-ink hover:bg-surface-3"
						onclick={choosePerChannel}
					>
						Individual channel(s)
					</button>
					<button class="ml-auto text-xs text-ink-2 underline" onclick={cancelPicking}>
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
							class="flex items-center gap-1 rounded border border-line-2 px-2 py-1"
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
						class="rounded-md bg-accent px-3 py-1.5 text-white hover:brightness-110 disabled:opacity-50"
						onclick={confirmChannels}
						disabled={pending.picked.size === 0}
					>
						Add {pending.picked.size} channel{pending.picked.size === 1 ? '' : 's'}
					</button>
					<button class="ml-auto text-xs text-ink-2 underline" onclick={cancelPicking}>
						Cancel
					</button>
				</div>
			{/if}
		</div>
	{/if}

	<!-- Sources -->
	<section class="space-y-2" class:opacity-40={!pickingMode} class:pointer-events-none={!pickingMode}>
		<div class="flex items-center justify-between">
			<h2 class="text-title font-medium">Where instruments come from</h2>
			<div class="text-xs text-ink-2">
				{#if pickingMode}
					Selection mode: <span class="font-medium">click any node</span>
				{:else}
					Click "Add selection" above to start picking
				{/if}
			</div>
		</div>

		<!-- One tab per source: this workspace, the same instruments through its
		     server, other workspaces' daemons on this machine, and remote machines. -->
		{#if sources.length > 0}
			<div class="rounded border border-line bg-surface shadow-sm">
				<Tabs
					value={activeSource?.name ?? ''}
					onValueChange={(v) => (sourceTab = v)}
					tabs={sources.map((s) => ({
						value: s.name,
						label: s.label,
						disabled: tabDisabled(s),
						title: tabDisabled(s) ? 'Not with embedded params: the generated file opens every instrument itself.' : undefined
					}))}
					label="Instrument sources"
					size="sm"
					class="[&>.lw-tabs]:px-2"
				>
					{#if activeSource}
						{@const source = activeSource}
						<div class="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
							<div class="flex flex-wrap items-center gap-2">
								{#if source.kind === 'machine'}
									<span
										class="rounded bg-accent-wash px-1.5 py-0.5 text-2xs font-medium text-accent-strong"
										title="Used through that workspace's server, so it never contends with local hardware."
									>
										through server
									</span>
								{:else if source.kind === 'remote'}
									<span
										class="rounded bg-surface-2 px-1.5 py-0.5 text-2xs font-medium text-ink-2"
										title="Another machine. Its instruments can be used, but its configuration can only be changed there."
									>
										read &amp; control only
									</span>
								{:else}
									<span class="text-xs text-ink-2">
										Opened by the generated file itself{embedded ? ". The other sources are off with embedded params." : ""}
									</span>
								{/if}
								{#if busyCount.get(source.name)}
									<span
										class="rounded bg-warn-wash px-1.5 py-0.5 text-2xs font-medium text-warn"
										title="A measurement is running against these. Building a resource for them is allowed; using them at the same time is not."
									>
										{busyCount.get(source.name)} in use
									</span>
								{/if}
							</div>
							{#if source.url && source.tree === null}
								<span class="shrink-0 font-mono text-2xs text-muted">{source.url}</span>
							{/if}
						</div>
						{#if source.tree !== null}
							<ScrollArea
								class="relative overflow-hidden p-3"
								orientation="vertical"
								viewportClasses="h-full max-h-[320px] w-full"
							>
								{#if !source.reachable}
									<div class="px-2 py-3 text-body text-warn">
										Not reachable: {source.error ?? 'no answer'}
									</div>
								{:else if source.tree.length === 0}
									<div class="px-2 py-3 text-body text-ink-2">
										No instruments configured here.
									</div>
								{:else}
									{#each source.tree as node (node.key)}
										<TreeNode
											{node}
											isSelectable={pickingMode && pending === null}
											isCompatible={() => true}
											selectionLabel={(_n, p) => selectionLabelForAny(source, p)}
											busyBadge={(_n, p) => busyLabel(source.claims, p)}
											onSelect={(n, p) => onSelectTreeNode(source, n, p)}
										/>
									{/each}
								{/if}
							</ScrollArea>
						{:else}
							<!-- A remote machine: named leaves only. A tcp peer gets read + call,
							     so there is no tree to browse and nothing to reconfigure. -->
							<ScrollArea viewportClasses="max-h-[320px] p-3">
								{#if !source.reachable}
									<div class="px-2 py-2 text-body text-warn">
										Not reachable: {source.error ?? 'no answer'}
									</div>
								{:else if source.attributes.length === 0}
									<div class="px-2 py-2 text-body text-ink-2">
										No named instruments there.
									</div>
								{:else}
									<div class="grid gap-1.5 sm:grid-cols-2">
										{#each source.attributes as entry (entry.attribute_name)}
											<button
												class="rounded border border-line px-3 py-2 text-left text-body hover:border-accent disabled:opacity-50"
												disabled={!pickingMode || pending !== null}
												onclick={() => onSelectAttribute(source, entry)}
											>
												<div class="flex flex-wrap items-center gap-1.5">
													<span class="font-mono text-xs font-medium">{entry.attribute_name}</span>
													{#if entry.claimed_by}
														<span
															class="rounded bg-warn-wash px-1.5 py-0.5 text-2xs font-medium text-warn"
															title="A running measurement holds this. Building a resource for it is allowed; using it at the same time is not."
														>
															in use by {entry.claimed_by}
														</span>
													{/if}
												</div>
												<div class="text-fine text-muted">
													{entry.type_hint ?? 'instrument'}
													{#if entry.behavior_abc}· {entry.behavior_abc}{/if}
												</div>
											</button>
										{/each}
									</div>
								{/if}
							</ScrollArea>
						{/if}
					{/if}
				</Tabs>
			</div>
		{:else}
			<div class="rounded-xl border border-line px-3 py-4 text-body text-ink-2">
				No instrument sources found. Add instruments in
				<a class="text-accent hover:underline" href="/instruments">Manage Instruments</a>,
				or register a server on
				<a class="text-accent hover:underline" href="/servers/remote">Remote Servers</a>.
			</div>
		{/if}
	</section>

	{#if createResult}
		<div
			class="rounded-lg bg-ok-wash px-3 py-2 text-body text-ok"
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
			class="rounded-lg bg-crit-wash px-3 py-2 text-body text-crit"
		>
			{createError}
		</div>
	{/if}
</section>

<div class="mt-6 flex justify-end">
	<button
		class="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 active:bg-accent disabled:opacity-50"
		onclick={onCreate}
		disabled={!canSubmit()}
	>
		{creating ? 'Creating...' : 'Create Custom Resource'}
	</button>
</div>
