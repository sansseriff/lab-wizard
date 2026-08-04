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
	type ConfiguredResource = {
		type: string;
		key: string;
		fields: Record<string, any>;
	};
	type RemoteMatch = {
		server_name: string;
		url: string;
		attribute: string;
		behavior_abc: string | null;
		type_hint: string | null;
	};
	type ResourceReq = {
		variable_name: string;
		base_type: string;
		resource_kind: 'instrument' | 'saver' | 'plotter';
		is_list: boolean;
		matching_instruments: MatchingReq[];
		matching_resources: ConfiguredResource[];
		matching_remote?: RemoteMatch[];
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
	type AttributeEntry = {
		attribute_name: string;
		path: string;
		behavior_abc: string | null;
		type_hint: string | null;
	};
	type Source = {
		name: string;
		kind: 'local' | 'machine' | 'remote';
		label: string;
		url: string | null;
		config_dir: string | null;
		tree: TreeItem[] | null;
		metadata: Record<string, InstrumentMeta>;
		attributes: AttributeEntry[];
		editable: boolean;
		reachable: boolean;
		error: string | null;
	};
	type SelectedChoice = {
		source: string;
		sourceLabel: string;
		sourceKind: 'local' | 'machine' | 'remote';
		type: string;
		key: string;
		// The handle a routed instrument is referenced by. Null only for a local
		// selection, where the backend reads it off the tree itself.
		attribute: string | null;
		pathLeafToRoot: TreePathRef[];
		pathDisplay: string;
		pathKey: string;
		channelCount: number;
		channelIndex: number | null;
	};

	let { data } = $props();
	const measurementName: string | null = data?.measurementName ?? null;
	const reqs: ResourceReq[] = (data?.requirements ?? []) as ResourceReq[];
	const sources: Source[] = (data?.sources ?? []) as Source[];
	const ownServer: { name: string; url: string } | null = data?.ownServer ?? null;

	// Sources offering a browsable tree: this workspace, and other workspaces'
	// daemons on this machine. A remote machine is flat by design.
	const treeSources = $derived(sources.filter((s) => s.tree !== null));
	const flatSources = $derived(sources.filter((s) => s.tree === null));

	const instrumentReqs = $derived(reqs.filter((r) => r.resource_kind === 'instrument'));
	const saverReqs = $derived(reqs.filter((r) => r.resource_kind === 'saver'));
	const plotterReqs = $derived(reqs.filter((r) => r.resource_kind === 'plotter'));

	// Instrument selection state (one per variable)
	const selected: Record<string, SelectedChoice | null> = $state({});
	for (const r of reqs) if (r.resource_kind === 'instrument' && !(r.variable_name in selected)) selected[r.variable_name] = null;

	// --- Transport conflicts for a locally-run project ---------------------
	//
	// Asked while the user is still choosing, because a conflict discovered at
	// run time is a 2am failure. Only exclusive transports can conflict; a rack
	// behind its own multiplexing server is fine for everyone at once.
	type ServedBy = {
		url: string;
		config_dir: string | null;
		workspace_path: string | null;
		root: string;
	};
	type ConflictRoot = {
		root: string;
		transport_key: string | null;
		held_by: string | null;
		served_by: ServedBy[];
	};
	type ConflictCheck = {
		local_servers: { url: string; workspace_path: string | null }[];
		held_conflicts: ConflictRoot[];
		configured_conflicts: ConflictRoot[];
	};
	let conflicts: ConflictCheck | null = $state(null);

	// Roots the current selection would open **in this process**. Only local
	// selections qualify: an instrument routed through a server cannot contend
	// with it, because the server is its single owner and we are its client.
	// pathLeafToRoot is leaf-first, so the root is its last element.
	function selectedRootPaths(): string[] {
		const out = new Set<string>();
		for (const choice of Object.values(selected)) {
			if (!choice || choice.source !== 'local') continue;
			if (choice.pathLeafToRoot.length === 0) continue;
			const root = choice.pathLeafToRoot[choice.pathLeafToRoot.length - 1];
			out.add(`inst://${root.key}`);
		}
		return [...out];
	}

	/** Sources that could serve a conflicting rack instead of opening it here.
	 *
	 * Re-routing is the fix for a transport conflict, not merely a different
	 * choice — so the warning names the source to switch to. Matched on url,
	 * since the conflict check reports servers and the picker reports sources.
	 */
	function reroutingOptions(conflict: ConflictRoot): { name: string; label: string }[] {
		const out: { name: string; label: string }[] = [];
		for (const server of conflict.served_by ?? []) {
			const source = sources.find((s) => s.url === server.url);
			if (source) {
				out.push({ name: source.name, label: source.label });
			} else if (ownServer && ownServer.url === server.url) {
				out.push({ name: ownServer.name, label: "this workspace's server" });
			}
		}
		return out;
	}

	$effect(() => {
		const paths = selectedRootPaths();
		if (paths.length === 0) {
			conflicts = null;
			return;
		}
		let cancelled = false;
		fetchWithConfig<ConflictCheck>('/api/transport-status/check', 'POST', { paths })
			.then((res) => {
				// A stale reply must not overwrite a newer one.
				if (!cancelled) conflicts = res;
			})
			.catch(() => {
				if (!cancelled) conflicts = null;
			});
		return () => {
			cancelled = true;
		};
	});

	// Saver / plotter selection state — multi-select (set of "type:key" strings) per variable
	const flatSelected: Record<string, Set<string>> = $state({});
	for (const r of reqs) {
		if (r.resource_kind !== 'instrument' && !(r.variable_name in flatSelected)) {
			flatSelected[r.variable_name] = new Set();
		}
	}

	let activeRequirement = $state<string | null>(null);
	let projectPrefix = $state('');
	let generationStyle = $state<
		'production' | 'from_attribute' | 'pedagogical_yaml_expanded' | 'pedagogical_embedded'
	>('production');
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
	// Source-qualified, because two workspaces can hold the same instrument at
	// the same hash key — comparing paths alone would make one look selected in
	// the other's tree.
	function pathKey(source: string, path: TreePathRef[]): string {
		return `${source}::${path.map((p) => `${p.type}:${p.key}`).join('|')}`;
	}
	function pathDisplay(path: TreePathRef[]): string {
		return path.map((p) => `${p.type}(${p.key})`).join(' -> ');
	}
	function channelCount(node: TreeItem): number {
		return typeof node.num_channels === 'number' ? node.num_channels : 0;
	}
	function reqByVar(variableName: string | null): ResourceReq | null {
		if (!variableName) return null;
		return reqs.find((r) => r.variable_name === variableName) ?? null;
	}
	// Metadata comes from the source that owns the tree, never from this build:
	// which classes exist and what they are called depends on the lab_wizard
	// running there, which can differ from ours.
	function reqMatchesType(req: ResourceReq, type: string, source: Source): boolean {
		if (req.resource_kind !== 'instrument') return false;
		const meta = source.metadata?.[type];
		if (!meta) return false;
		const instClass = classNameNoParams(meta.class_name);
		const channelClass = `${instClass}Channel`;
		return req.matching_instruments.some(
			(m) =>
				m.module === meta.module &&
				(m.class_name === instClass || m.class_name === meta.class_name || m.class_name === channelClass)
		);
	}
	function isInstrumentReqComplete(req: ResourceReq): boolean {
		const s = selected[req.variable_name];
		if (!s) return false;
		if (s.channelCount > 1 && s.channelIndex === null) return false;
		return true;
	}
	function isFlatReqComplete(req: ResourceReq): boolean {
		const set = flatSelected[req.variable_name];
		if (!set) return false;
		// list-typed savers/plotters can be empty (zero is allowed); single-valued require one.
		if (req.is_list) return true;
		return set.size === 1;
	}
	function allDone(): boolean {
		if (reqs.length === 0) return false;
		for (const r of reqs) {
			if (r.resource_kind === 'instrument') {
				if (!isInstrumentReqComplete(r)) return false;
			} else {
				if (!isFlatReqComplete(r)) return false;
			}
		}
		return true;
	}
	function nextIncompleteAfter(variableName: string): string | null {
		const idx = instrumentReqs.findIndex((r) => r.variable_name === variableName);
		if (idx < 0) return null;
		for (const r of instrumentReqs.slice(idx + 1)) if (!isInstrumentReqComplete(r)) return r.variable_name;
		for (const r of instrumentReqs) if (!isInstrumentReqComplete(r)) return r.variable_name;
		return null;
	}
	function setActiveMode(variableName: string) {
		activeRequirement = variableName;
	}
	/** attribute_name of a tree node, or of one of its channels.
	 *
	 * Only needed for a source other than `local`: the backend reads a local
	 * selection's name off its own params, but a routed instrument has no params
	 * here, so the handle has to travel with the selection.
	 */
	function attributeOf(node: TreeItem, channelIndex: number | null): string | null {
		if (channelIndex !== null) {
			const channels = node.fields?.channels;
			const channel = channels?.[channelIndex] ?? channels?.[String(channelIndex)];
			return channel?.attribute_name || null;
		}
		return node.fields?.attribute_name || null;
	}

	function onSelectTreeNode(source: Source, node: TreeItem, rootToNodePath: TreePathRef[]) {
		if (!activeRequirement) return;
		const req = reqByVar(activeRequirement);
		if (!req || req.resource_kind !== 'instrument') return;
		if (!reqMatchesType(req, node.type, source)) return;
		const cc = channelCount(node);
		const channelIndex = cc > 1 ? null : 0;
		selected[activeRequirement] = {
			source: source.name,
			sourceLabel: source.label,
			sourceKind: source.kind,
			type: node.type,
			key: node.key,
			attribute: source.kind === 'local' ? null : attributeOf(node, channelIndex),
			pathLeafToRoot: [...rootToNodePath].reverse(),
			pathDisplay: pathDisplay(rootToNodePath),
			pathKey: pathKey(source.name, rootToNodePath),
			channelCount: cc,
			channelIndex
		};
		if (cc <= 1) activeRequirement = nextIncompleteAfter(activeRequirement);
	}

	/** Match a flat leaf to a requirement by behavior ABC.
	 *
	 * A remote machine sends no class metadata, so the behavior ABC the server
	 * reports is the contract — the same one measurement binding already uses.
	 */
	function reqMatchesAttribute(req: ResourceReq, entry: AttributeEntry): boolean {
		if (req.resource_kind !== 'instrument') return false;
		return Boolean(entry.behavior_abc) && entry.behavior_abc === shortBaseName(req.base_type);
	}

	function onSelectAttribute(source: Source, entry: AttributeEntry) {
		if (!activeRequirement) return;
		const req = reqByVar(activeRequirement);
		if (!req || !reqMatchesAttribute(req, entry)) return;
		selected[activeRequirement] = {
			source: source.name,
			sourceLabel: source.label,
			sourceKind: source.kind,
			type: entry.type_hint ?? '',
			key: '',
			attribute: entry.attribute_name,
			// A named leaf already identifies one instrument or channel, so there
			// is no tree position to record and nothing further to choose.
			pathLeafToRoot: [],
			pathDisplay: `${entry.attribute_name} on ${source.label}`,
			pathKey: `${source.name}::@${entry.attribute_name}`,
			channelCount: 0,
			channelIndex: null
		};
		activeRequirement = nextIncompleteAfter(activeRequirement);
	}

	function isAttributeSelectedForCurrent(source: Source, entry: AttributeEntry): boolean {
		if (!activeRequirement) return false;
		return selected[activeRequirement]?.pathKey === `${source.name}::@${entry.attribute_name}`;
	}
	function setChannelForActive(value: string, node: TreeItem | null = null) {
		if (!activeRequirement) return;
		const cur = selected[activeRequirement];
		if (!cur) return;
		const parsed = Number.parseInt(value, 10);
		cur.channelIndex = Number.isNaN(parsed) ? null : parsed;
		// The channel carries its own attribute_name, so a routed selection's
		// handle changes with the channel.
		if (cur.sourceKind !== 'local' && node) {
			cur.attribute = attributeOf(node, cur.channelIndex);
		}
		if (cur.channelIndex !== null) activeRequirement = nextIncompleteAfter(activeRequirement);
	}
	function isCompatibleForCurrent(node: TreeItem, source: Source): boolean {
		const req = reqByVar(activeRequirement);
		if (!req) return false;
		return reqMatchesType(req, node.type, source);
	}
	function isNodeSelectedForCurrent(source: Source, path: TreePathRef[]): boolean {
		if (!activeRequirement) return false;
		const sel = selected[activeRequirement];
		if (!sel) return false;
		return sel.pathKey === pathKey(source.name, path);
	}
	function selectionLabelForAny(source: Source, path: TreePathRef[]): string | null {
		const labels: string[] = [];
		const key = pathKey(source.name, path);
		for (const r of instrumentReqs) {
			if (selected[r.variable_name]?.pathKey === key) labels.push(r.variable_name);
		}
		return labels.length ? labels.join(', ') : null;
	}
	/** The tree node a selection points at, for re-deriving a channel attribute. */
	function selectedNode(choice: SelectedChoice | null): TreeItem | null {
		if (!choice || choice.pathLeafToRoot.length === 0) return null;
		const source = sources.find((s) => s.name === choice.source);
		if (!source?.tree) return null;
		const rootToLeaf = [...choice.pathLeafToRoot].reverse();
		let nodes: TreeItem[] = source.tree;
		let found: TreeItem | null = null;
		for (const step of rootToLeaf) {
			found = nodes.find((n) => n.type === step.type && n.key === step.key) ?? null;
			if (!found) return null;
			nodes = Object.values(found.children ?? {});
		}
		return found;
	}
	function toggleFlatSelection(variableName: string, type: string, key: string, isList: boolean) {
		const id = `${type}:${key}`;
		const set = flatSelected[variableName];
		if (set.has(id)) {
			set.delete(id);
		} else {
			if (!isList) set.clear();
			set.add(id);
		}
		flatSelected[variableName] = new Set(set); // trigger reactivity
	}
	function isFlatSelected(variableName: string, type: string, key: string): boolean {
		return flatSelected[variableName]?.has(`${type}:${key}`) ?? false;
	}

	async function onCreateProject() {
		if (!measurementName || !allDone()) return;
		creatingProject = true;
		createError = null;
		createResult = null;
		try {
			const selected_resources: any[] = [];
			for (const r of instrumentReqs) {
				const c = selected[r.variable_name];
				if (!c) throw new Error(`Missing selection for ${r.variable_name}`);
				selected_resources.push({
					variable_name: r.variable_name,
					resource_kind: 'instrument',
					type: c.type,
					key: c.key,
					path: c.pathLeafToRoot,
					channel_index: c.channelCount > 1 ? c.channelIndex : null,
					source: c.source,
					attribute: c.attribute
				});
			}
			for (const r of saverReqs) {
				for (const id of flatSelected[r.variable_name]) {
					const [type, key] = id.split(':');
					selected_resources.push({
						variable_name: r.variable_name,
						resource_kind: 'saver',
						type,
						key
					});
				}
			}
			for (const r of plotterReqs) {
				for (const id of flatSelected[r.variable_name]) {
					const [type, key] = id.split(':');
					selected_resources.push({
						variable_name: r.variable_name,
						resource_kind: 'plotter',
						type,
						key
					});
				}
			}

			const body: Record<string, any> = {
				measurement_name: measurementName,
				selected_resources,
				generation_style: generationStyle
			};
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
	<h1 class="text-2xl font-semibold">Select resources</h1>
	{#if !measurementName}
		<div class="text-sm text-gray-600 dark:text-gray-300">
			No measurement selected. <a class="text-indigo-600 underline" href="/get_measurements">Go back</a>.
		</div>
	{:else}
		<p class="text-sm text-gray-600 dark:text-gray-300">
			Measurement: <span class="font-medium">{measurementName}</span>
		</p>
	{/if}

	{#if measurementName}
		{#if !reqs || reqs.length === 0}
			<div class="rounded-xl border border-gray-200 bg-white/70 p-4 text-sm text-gray-600 dark:border-white/10 dark:bg-gray-800/70 dark:text-gray-300">
				No resource roles detected for this measurement.
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
				{#if conflicts && (conflicts.held_conflicts.length > 0 || conflicts.configured_conflicts.length > 0)}
					<!-- Two distinct warnings. "In use" means a local run fails now;
					     "served by" means it works today and breaks the moment
					     anything touches that rack through the server. -->
					<div class="mt-3 space-y-2">
						{#if conflicts.held_conflicts.length > 0}
							<div
								class="rounded-md border border-red-300 bg-red-50 p-2.5 text-xs dark:border-red-900 dark:bg-red-950/25"
							>
								<div class="font-medium text-red-900 dark:text-red-300">
									Hardware in use — a local run of this project will refuse to start
								</div>
								<ul class="mt-1 space-y-0.5 text-red-800 dark:text-red-400">
									{#each conflicts.held_conflicts as c (c.root)}
										<li>
											<span class="font-mono">{c.transport_key ?? c.root}</span>
											is open in another process.
											{#if reroutingOptions(c).length > 0}
												<span class="text-red-700 dark:text-red-500">
													Pick it from <span class="font-medium">{reroutingOptions(c).map((o) => o.label).join(' or ')}</span>
													instead and this goes away.
												</span>
											{/if}
										</li>
									{/each}
								</ul>
								<div class="mt-1 text-red-700 dark:text-red-500">
									Using the instrument <em>through</em> the server that holds it is the fix — one
									process owns the hardware and this project becomes its client. Otherwise,
									release it on <a class="underline" href="/hardware_status">Hardware &amp; Servers</a>.
								</div>
							</div>
						{/if}
						{#if conflicts.configured_conflicts.length > 0}
							<div
								class="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs dark:border-amber-900 dark:bg-amber-950/20"
							>
								<div class="font-medium text-amber-900 dark:text-amber-300">
									A server also serves this hardware
								</div>
								<ul class="mt-1 space-y-0.5 text-amber-800 dark:text-amber-400">
									{#each conflicts.configured_conflicts as c (c.root)}
										<li>
											<span class="font-mono">{c.transport_key ?? c.root}</span>
											{#if reroutingOptions(c).length > 0}
												<span>— available from {reroutingOptions(c).map((o) => o.label).join(' or ')}</span>
											{/if}
										</li>
									{/each}
								</ul>
								<div class="mt-1 text-amber-700 dark:text-amber-500">
									A local run works right now, but will fail as soon as anything uses that rack
									through the server.
								</div>
							</div>
						{/if}
					</div>
				{/if}

				<div class="mt-3">
					<div class="mb-1 text-xs text-gray-600 dark:text-gray-300">Generated setup style</div>
					<div class="grid gap-2 text-sm sm:grid-cols-2">
						<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 dark:border-gray-600">
							<input type="radio" bind:group={generationStyle} value="production" />
							<span>
								<span class="block font-medium">Production</span>
								<span class="block text-xs text-gray-500">Short setup file; resolves selected resources from the project YAML.</span>
							</span>
						</label>
						<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 dark:border-gray-600">
							<input type="radio" bind:group={generationStyle} value="from_attribute" />
							<span>
								<span class="block font-medium">Remote attribute</span>
								<span class="block text-xs text-gray-500">Looks up instruments by attribute name; use with remote servers.</span>
							</span>
						</label>
						<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 dark:border-gray-600">
							<input type="radio" bind:group={generationStyle} value="pedagogical_yaml_expanded" />
							<span>
								<span class="block font-medium">Teaching: YAML expanded</span>
								<span class="block text-xs text-gray-500">Shows hash-key traversal and explicit parent/child creation.</span>
							</span>
						</label>
						<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 dark:border-gray-600">
							<input type="radio" bind:group={generationStyle} value="pedagogical_embedded" />
							<span>
								<span class="block font-medium">Teaching: embedded params</span>
								<span class="block text-xs text-gray-500">Embeds selected instrument params directly in Python.</span>
							</span>
						</label>
					</div>
				</div>
			</div>

			{#if saverReqs.length > 0}
				<section class="space-y-2">
					<h2 class="text-lg font-medium">Savers</h2>
					{#each saverReqs as r}
						<div class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70">
							<div class="flex items-center justify-between">
								<div>
									<div class="font-medium">{r.variable_name}</div>
									<div class="text-xs text-gray-600 dark:text-gray-300">
										{r.is_list ? 'Pick one or more configured savers' : 'Pick one configured saver'}
									</div>
								</div>
								<a class="text-xs text-indigo-600 hover:underline" href="/manage_savers"
									>Manage configured savers →</a
								>
							</div>
							{#if r.matching_resources.length === 0}
								<div class="mt-3 rounded-md bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
									No savers configured. Add one in <a href="/manage_savers" class="underline">Manage Savers</a> first.
								</div>
							{:else}
								<div class="mt-3 grid gap-2 sm:grid-cols-2">
									{#each r.matching_resources as item}
										<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 text-sm hover:border-indigo-300 dark:border-gray-600">
											<input
												type={r.is_list ? 'checkbox' : 'radio'}
												name={`flat-${r.variable_name}`}
												checked={isFlatSelected(r.variable_name, item.type, item.key)}
												onchange={() => toggleFlatSelection(r.variable_name, item.type, item.key, r.is_list)}
											/>
											<div class="flex-1">
												<div class="font-medium">{item.key}</div>
												<div class="text-xs text-gray-500">type: {item.type}</div>
											</div>
										</label>
									{/each}
								</div>
							{/if}
						</div>
					{/each}
				</section>
			{/if}

			{#if plotterReqs.length > 0}
				<section class="space-y-2">
					<h2 class="text-lg font-medium">Plotters</h2>
					{#each plotterReqs as r}
						<div class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70">
							<div class="flex items-center justify-between">
								<div>
									<div class="font-medium">{r.variable_name}</div>
									<div class="text-xs text-gray-600 dark:text-gray-300">
										{r.is_list ? 'Pick one or more configured plotters' : 'Pick one configured plotter'}
									</div>
								</div>
								<a class="text-xs text-indigo-600 hover:underline" href="/manage_plotters"
									>Manage configured plotters →</a
								>
							</div>
							{#if r.matching_resources.length === 0}
								<div class="mt-3 rounded-md bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
									No plotters configured. Add one in <a href="/manage_plotters" class="underline">Manage Plotters</a> first.
								</div>
							{:else}
								<div class="mt-3 grid gap-2 sm:grid-cols-2">
									{#each r.matching_resources as item}
										<label class="flex items-start gap-2 rounded border border-gray-200 px-3 py-2 text-sm hover:border-indigo-300 dark:border-gray-600">
											<input
												type={r.is_list ? 'checkbox' : 'radio'}
												name={`flat-${r.variable_name}`}
												checked={isFlatSelected(r.variable_name, item.type, item.key)}
												onchange={() => toggleFlatSelection(r.variable_name, item.type, item.key, r.is_list)}
											/>
											<div class="flex-1">
												<div class="font-medium">{item.key}</div>
												<div class="text-xs text-gray-500">type: {item.type}</div>
											</div>
										</label>
									{/each}
								</div>
							{/if}
						</div>
					{/each}
				</section>
			{/if}

			{#if instrumentReqs.length > 0}
				<section class="space-y-4">
					<h2 class="text-lg font-medium">Instruments</h2>
					{#each instrumentReqs as r}
						<section class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70">
							<div class="flex items-start justify-between gap-3">
								<div>
									<div class="font-medium">{r.variable_name}</div>
									<div class="text-xs text-gray-600 dark:text-gray-300">
										Requires {shortBaseName(r.base_type)}
									</div>
									{#if selected[r.variable_name]}
										<div class="mt-1 text-[11px] text-gray-600 dark:text-gray-300">
											{#if selected[r.variable_name]?.source !== 'local'}
												<span
													class="mr-1 rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300"
													title="Used through this server rather than opened by the project"
												>
													{selected[r.variable_name]?.sourceLabel}
												</span>
											{/if}
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
									<label for={`${r.variable_name}-channel-trigger`} class="mb-1 block text-xs">Choose channel</label>
									<Select.Root
										type="single"
										value={selected[r.variable_name]?.channelIndex === null
											? ''
											: String(selected[r.variable_name]?.channelIndex)}
										onValueChange={(v) =>
											setChannelForActive(v, selectedNode(selected[r.variable_name]))}
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

					<section class="space-y-2">
						<div class="flex items-center justify-between">
							<h3 class="text-md font-medium">Where instruments come from</h3>
							<div class="text-xs text-gray-600 dark:text-gray-300">
								{#if activeRequirement}
									Selection mode: <span class="font-medium">{activeRequirement}</span>
								{:else}
									Pick a requirement above to start selecting
								{/if}
							</div>
						</div>

						<!-- Tree sources: this workspace, and other workspaces' daemons on
						     this machine. Each is drawn with its own schema, because which
						     classes exist depends on the build running there. -->
						{#each treeSources as source (source.name)}
							<div class="rounded-xl border border-gray-200 bg-white/70 shadow-sm dark:border-white/10 dark:bg-gray-800/70">
								<div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-700">
									<div class="flex items-center gap-2">
										<span class="text-sm font-medium">{source.label}</span>
										{#if source.kind === 'machine'}
											<span
												class="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300"
												title="A daemon on this machine. Its instruments are used through it, so they never contend with your local hardware."
											>
												through server
											</span>
										{/if}
									</div>
									{#if source.kind === 'machine'}
										<a class="text-xs text-indigo-600 hover:underline" href="/remote_tree"
											>Edit that workspace →</a
										>
									{:else}
										<a class="text-xs text-indigo-600 hover:underline" href="/manage_instruments"
											>Manage instruments →</a
										>
									{/if}
								</div>
								<ScrollArea
									class="relative overflow-hidden p-3"
									orientation="vertical"
									viewportClasses="h-full max-h-[300px] w-full"
								>
									{#if !source.reachable}
										<div class="px-2 py-3 text-sm text-amber-700 dark:text-amber-400">
											Not reachable: {source.error ?? 'no answer'}
										</div>
									{:else if (source.tree ?? []).length === 0}
										<div class="px-2 py-3 text-sm text-gray-600 dark:text-gray-300">
											No instruments configured here.
										</div>
									{:else}
										{#each source.tree ?? [] as node (node.key)}
											<TreeNode
												{node}
												isSelectable={Boolean(activeRequirement)}
												isCompatible={(n) => isCompatibleForCurrent(n, source)}
												isSelected={(_n, p) => isNodeSelectedForCurrent(source, p)}
												selectionLabel={(_n, p) => selectionLabelForAny(source, p)}
												onSelect={(n, p) => onSelectTreeNode(source, n, p)}
											/>
										{/each}
									{/if}
								</ScrollArea>
							</div>
						{/each}

						<!-- Remote machines: named leaves only. A tcp peer gets read + call
						     and never reconfiguration, so there is no tree to offer. -->
						{#each flatSources as source (source.name)}
							<div class="rounded-xl border border-gray-200 bg-white/70 shadow-sm dark:border-white/10 dark:bg-gray-800/70">
								<div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-700">
									<div class="flex items-center gap-2">
										<span class="text-sm font-medium">{source.label}</span>
										<span
											class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300"
											title="Another machine. Its instruments can be used, but its configuration can only be changed there."
										>
											read &amp; control only
										</span>
									</div>
									<span class="font-mono text-[10px] text-gray-400">{source.url}</span>
								</div>
								<div class="max-h-[300px] overflow-y-auto p-3">
									{#if !source.reachable}
										<div class="px-2 py-2 text-sm text-amber-700 dark:text-amber-400">
											Not reachable: {source.error ?? 'no answer'}
										</div>
									{:else if source.attributes.length === 0}
										<div class="px-2 py-2 text-sm text-gray-600 dark:text-gray-300">
											No named instruments there.
										</div>
									{:else}
										<div class="grid gap-1.5 sm:grid-cols-2">
											{#each source.attributes as entry (entry.attribute_name)}
												{@const req = reqByVar(activeRequirement)}
												{@const compatible = Boolean(req) && reqMatchesAttribute(req!, entry)}
												<button
													class="flex items-start gap-2 rounded border px-3 py-2 text-left text-sm transition {isAttributeSelectedForCurrent(
														source,
														entry
													)
														? 'border-indigo-400 bg-indigo-50 dark:border-indigo-600 dark:bg-indigo-950/30'
														: 'border-gray-200 dark:border-gray-600'} {activeRequirement && !compatible
														? 'opacity-45'
														: 'hover:border-indigo-300'}"
													disabled={!activeRequirement || !compatible}
													onclick={() => onSelectAttribute(source, entry)}
												>
													<div class="flex-1">
														<div class="font-mono text-xs font-medium">{entry.attribute_name}</div>
														<div class="text-[11px] text-gray-500">
															{entry.type_hint ?? 'instrument'}
															{#if entry.behavior_abc}
																· {entry.behavior_abc}
															{/if}
														</div>
													</div>
												</button>
											{/each}
										</div>
									{/if}
								</div>
							</div>
						{/each}

						{#if treeSources.length === 0 && flatSources.length === 0}
							<div class="rounded-xl border border-gray-200 px-3 py-4 text-sm text-gray-600 dark:border-white/10 dark:text-gray-300">
								No instrument sources found. Add instruments in
								<a class="text-indigo-600 hover:underline" href="/manage_instruments">Manage Instruments</a>,
								or register a server on
								<a class="text-indigo-600 hover:underline" href="/manage_remote_servers">Remote Servers</a>.
							</div>
						{/if}
					</section>
				</section>
			{/if}

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
