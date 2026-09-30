<script lang="ts">
	import { untrack } from 'svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import { type Claim, busyCounts, busyLabel } from '$lib/claims';
	import type { TreeItem, TreePathRef } from '$lib/components/TreeNode.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import Select from '$lib/components/Select.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import { api, errorMessage, unwrap, type Schemas } from '$lib/api';
	import { nodeAddress } from '$lib/instruments/model';

	type MatchingReq = {
		module: string;
		class_name: string;
		qualname?: string;
		friendly_name?: string;
		file_path?: string;
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
		is_list: boolean;
		matching_instruments: MatchingReq[];
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
		behavior_abc?: string | null;
		channel_behavior_abc?: string | null;
	};
	type AttributeEntry = {
		attribute_name: string;
		path: string;
		behavior_abc: string | null;
		type_hint: string | null;
		/** Set when a run holds this instrument right now. */
		claimed_by?: string | null;
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
		is_own_server?: boolean;
		claims?: Claim[];
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
	const measurementName: string | null = $derived(data?.measurementName ?? null);
	const measurementKind: 'procedure' | 'custom' = $derived(
		data?.measurementKind === 'custom' ? 'custom' : 'procedure'
	);
	const presets: string[] = $derived((data?.presets ?? []) as string[]);
	// A named preset from config/measurements/<name>/, or '' for the defaults.
	let paramsPreset = $state('');
	const reqs: ResourceReq[] = $derived((data?.requirements ?? []) as ResourceReq[]);
	const sources: Source[] = $derived((data?.sources ?? []) as Source[]);
	const ownServer: { name: string; url: string } | null = $derived(data?.ownServer ?? null);

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

	// Every requirement is an instrument role.
	const instrumentReqs = $derived(reqs);

	// Instrument selection state (one per variable). Seeded once and then owned
	// by the user's choices, so the requirement list is read untracked.
	const selected: Record<string, SelectedChoice | null> = $state({});
	for (const r of untrack(() => reqs))
		if (!(r.variable_name in selected))
			selected[r.variable_name] = null;

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

	/** Switch every local selection on a conflicting rack to this workspace's server.
	 *
	 * Only this workspace's own server can take a selection over directly: it
	 * serves the same tree, so the same node has the same keys there. The
	 * attribute_name is what the routed project will address it by.
	 */
	function rerouteToOwnServer(conflictRoot: string) {
		rerouteError = null;
		const own = sources.find((s) => s.is_own_server);
		if (!own) return;
		for (const r of instrumentReqs) {
			const choice = selected[r.variable_name];
			if (!choice || choice.source !== 'local' || choice.pathLeafToRoot.length === 0) continue;
			const root = choice.pathLeafToRoot[choice.pathLeafToRoot.length - 1];
			if (`inst://${root.key}` !== conflictRoot) continue;
			const node = selectedNode(choice);
			const attribute = node
				? attributeOf(node, choice.channelCount > 1 ? choice.channelIndex : null)
				: null;
			if (!attribute) {
				rerouteError = `${r.variable_name} has no attribute_name, so it cannot be used through a server.`;
				continue;
			}
			selected[r.variable_name] = {
				...choice,
				source: own.name,
				sourceLabel: own.label,
				sourceKind: own.kind,
				attribute,
				pathKey: pathKey(own.name, [...choice.pathLeafToRoot].reverse())
			};
		}
	}

	$effect(() => {
		const paths = selectedRootPaths();
		if (paths.length === 0) {
			conflicts = null;
			return;
		}
		let cancelled = false;
		unwrap<ConflictCheck>(api.POST('/api/transport-status/check', { body: { paths } }))
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

	// What a run produces besides its database record (the project's outputs:).
	// Where files go is the workspace's choice, on the Data page.
	let saveFiles = $state(true);
	// The web page by default: it works at the lab computer and over SSH alike.
	let livePlot = $state<'none' | 'window' | 'web'>('web');

	let activeRequirement = $state<string | null>(null);
	let projectPrefix = $state('');
	let generationStyle = $state<'production' | 'pedagogical_embedded'>('production');

	// The embedded style opens every instrument itself, so it cannot use one
	// through a server: the server tabs are greyed while it is chosen,
	// and it cannot be chosen once one is picked. The backend refuses it too.
	const embedded = $derived(generationStyle === 'pedagogical_embedded');
	const routedChosen = $derived(
		Object.entries(selected)
			.filter(([, choice]) => choice && choice.sourceKind !== 'local')
			.map(([variable]) => variable)
	);
	let rerouteError: string | null = $state(null);
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
	/** A tree path as Manage Instruments names it: type and address, not hash keys. */
	function pathDisplay(source: Source, path: TreePathRef[]): string {
		let nodes: TreeItem[] = source.tree ?? [];
		return path
			.map((p) => {
				const node = nodes.find((n) => n.type === p.type && n.key === p.key);
				nodes = Object.values(node?.children ?? {});
				return node ? `${p.type} ${nodeAddress(node)}` : p.type;
			})
			.join(' → ');
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
		const meta = source.metadata?.[type];
		if (!meta) return false;
		const requiredBehavior = shortBaseName(req.base_type);
		if (
			meta.behavior_abc === requiredBehavior ||
			meta.channel_behavior_abc === requiredBehavior
		) return true;
		// Compatibility fallback for servers predating behavior metadata.
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
	function allDone(): boolean {
		if (reqs.length === 0) return false;
		for (const r of reqs) if (!isInstrumentReqComplete(r)) return false;
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
		if (!req) return;
		if (!reqMatchesType(req, node.type, source)) return;
		const cc = channelCount(node);
		const channelIndex = cc > 1 ? null : 0;
		selected[activeRequirement] = {
			source: source.name,
			sourceLabel: source.label,
			sourceKind: source.kind,
			type: node.type,
			key: node.key,
			// A node with at most one channel is addressed by its own name, as the
			// backend does for a local one (channel_index is sent only when cc > 1).
			attribute: source.kind === 'local' ? null : attributeOf(node, cc > 1 ? channelIndex : null),
			pathLeafToRoot: [...rootToNodePath].reverse(),
			pathDisplay: pathDisplay(source, rootToNodePath),
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
			cur.attribute = attributeOf(node, cur.channelCount > 1 ? cur.channelIndex : null);
		}
		if (cur.channelIndex !== null) activeRequirement = nextIncompleteAfter(activeRequirement);
	}
	const busyCount = $derived(busyCounts(sources));

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
					type: c.type,
					key: c.key,
					path: c.pathLeafToRoot,
					channel_index: c.channelCount > 1 ? c.channelIndex : null,
					source: c.source,
					attribute: c.attribute
				});
			}

			const body: Schemas['GenerateProjectRequest'] = {
				measurement_name: measurementName!,
				kind: measurementKind,
				selected_resources,
				generation_style: generationStyle,
				outputs: { files: saveFiles, live_plot: livePlot, plot: '' },
				params_preset: paramsPreset || null,
				project_prefix: projectPrefix.trim() || null
			};
			const res = await unwrap<{ project_name: string; project_dir: string; yaml_file: string; setup_file: string }>(
				api.POST('/api/create-measurement-project', { body })
			);
			createResult = {
				project_name: res.project_name,
				project_dir: res.project_dir,
				yaml_file: res.yaml_file,
				setup_file: res.setup_file
			};
		} catch (err) {
			createError = errorMessage(err) || 'Failed to create project';
		} finally {
			creatingProject = false;
		}
	}
</script>

<section class="space-y-4">
	<PageHeader title="Select resources">
		{#snippet actions()}
			{#if measurementName}
				<Pill tone="accent">{measurementName}</Pill>
				<a class="lw-btn lw-btn-sm" href="/measurements/new">Choose another</a>
			{/if}
		{/snippet}
		Bind each role this measurement declares to something real, then generate a runnable project.
	</PageHeader>

	{#if !measurementName}
		<Callout tone="warn">
			No measurement selected.
			<a class="underline" href="/measurements/new">Pick one first →</a>
		</Callout>
	{:else if data?.loadError}
		<Callout tone="crit">
			Could not load what {measurementName} needs: {data.loadError}
			<a class="underline" href="/measurements/new">Choose another →</a>
		</Callout>
	{/if}

	{#if measurementName && !data?.loadError}
		{#if !reqs || reqs.length === 0}
			<Callout tone="warn">
				No resource roles detected for this measurement. Nothing needs binding, so there is nothing
				to generate here.
			</Callout>
		{:else}
			{#if conflicts && (conflicts.held_conflicts.length > 0 || conflicts.configured_conflicts.length > 0)}
				<!-- Two distinct warnings. "In use" means a local run fails now;
				     "served by" means it works today and breaks the moment
				     anything touches that rack through the server. -->
				<div class="space-y-2">
					{#if conflicts.held_conflicts.length > 0}
						<div
							class="rounded-md border border-crit/30 bg-crit-wash p-2.5 text-xs"
						>
							<div class="font-medium text-crit">
								Hardware in use — a local run of this project will refuse to start
							</div>
							<ul class="mt-1 space-y-0.5 text-crit">
								{#each conflicts.held_conflicts as c (c.root)}
									<li>
										<span class="font-mono">{c.transport_key ?? c.root}</span>
										is open in another process.
										{#if sources.some((s) => s.is_own_server)}
											<button class="ml-1 underline" onclick={() => rerouteToOwnServer(c.root)}>
												Use it through this workspace's server
											</button>
										{:else if reroutingOptions(c).length > 0}
											<span class="text-crit">
												Pick it from <span class="font-medium">{reroutingOptions(c).map((o) => o.label).join(' or ')}</span>
												instead and this goes away.
											</span>
										{/if}
									</li>
								{/each}
							</ul>
							<div class="mt-1 text-crit">
								Using the instrument <em>through</em> the server that holds it is the fix — one
								process owns the hardware and this project becomes its client. Otherwise,
								release it on <a class="underline" href="/servers/hardware">Hardware &amp; Servers</a>.
							</div>
						</div>
					{/if}
					{#if conflicts.configured_conflicts.length > 0}
						<div
							class="rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs"
						>
							<div class="font-medium text-warn">
								A server also serves this hardware
							</div>
							<ul class="mt-1 space-y-0.5 text-warn">
								{#each conflicts.configured_conflicts as c (c.root)}
									<li>
										<span class="font-mono">{c.transport_key ?? c.root}</span>
										{#if sources.some((s) => s.is_own_server)}
											<button class="ml-1 underline" onclick={() => rerouteToOwnServer(c.root)}>
												Use it through this workspace's server
											</button>
										{:else if reroutingOptions(c).length > 0}
											<span>— available from {reroutingOptions(c).map((o) => o.label).join(' or ')}</span>
										{/if}
									</li>
								{/each}
							</ul>
							<div class="mt-1 text-warn">
								A local run works right now, but will fail as soon as anything uses that rack through the server.
								</div>
							</div>
						{/if}
						{#if rerouteError}
							<div class="text-xs text-crit">{rerouteError}</div>
						{/if}
				</div>
			{/if}


			{#if instrumentReqs.length > 0}
				<section class="space-y-4">
					<h2 class="text-title font-medium">Instruments</h2>
					{#each instrumentReqs as r}
						<section class="rounded border border-line bg-surface p-3">
							<div class="flex items-start justify-between gap-3">
								<div>
									<div class="font-medium">{r.variable_name}</div>
									<div class="text-xs text-ink-2">
										Requires {shortBaseName(r.base_type)}
									</div>
									{#if selected[r.variable_name]}
										<div class="mt-1 text-fine text-ink-2">
											{#if selected[r.variable_name]?.source !== 'local'}
												<span
													class="mr-1 rounded bg-accent-wash px-1.5 py-0.5 text-2xs font-medium text-accent-strong"
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
									class="rounded-md px-3 py-1.5 text-body {activeRequirement === r.variable_name
										? 'bg-accent-wash text-accent-strong'
										: 'bg-surface-2 text-ink-2 hover:bg-surface-3'}"
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
								<div class="mt-2 rounded-md bg-accent-wash p-2 text-body">
									<label for={`${r.variable_name}-channel-trigger`} class="mb-1 block text-xs">Choose channel</label>
									<Select
										id={`${r.variable_name}-channel-trigger`}
										class="w-[260px] max-w-full"
										value={selected[r.variable_name]?.channelIndex === null
											? undefined
											: String(selected[r.variable_name]?.channelIndex)}
										onValueChange={(v) =>
											setChannelForActive(v, selectedNode(selected[r.variable_name]))}
										placeholder="Choose channel"
										options={Array.from({ length: selected[r.variable_name]!.channelCount }, (_, i) => ({
											value: String(i),
											label: `Channel ${i}`
										}))}
									/>
								</div>
							{/if}
						</section>
					{/each}

					<section class="space-y-2">
						<div class="flex items-center justify-between">
							<h3 class="text-md font-medium">Where instruments come from</h3>
							<div class="text-xs text-ink-2">
								{#if activeRequirement}
									Selection mode: <span class="font-medium">{activeRequirement}</span>
								{:else}
									Pick a requirement above to start selecting
								{/if}
							</div>
						</div>

						<!-- One tab per source: this workspace, the same instruments through
						     its server, other workspaces' daemons on this machine, and remote
						     machines. Tree sources are drawn with their own schema, because
						     which classes exist depends on the build running there. -->
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
														title={source.is_own_server
															? "The same instruments as 'This workspace', used through its server instead of opened by the project. Choose per instrument."
															: 'A daemon on this machine. Its instruments are used through it, so they never contend with your local hardware.'}
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
														Opened by the project itself{embedded ? ". The other sources are off with embedded params." : ""}
													</span>
												{/if}
												{#if busyCount.get(source.name)}
													<span
														class="rounded bg-warn-wash px-1.5 py-0.5 text-2xs font-medium text-warn"
														title="A measurement is running against these. Binding them is allowed; running at the same time is not."
													>
														{busyCount.get(source.name)} in use
													</span>
												{/if}
											</div>
											{#if source.tree !== null}
												<!-- Both land on Configured instruments now; a same-machine
												     server is a tab there rather than a separate page. -->
												<a class="shrink-0 text-xs text-accent hover:underline" href="/instruments">
													{source.kind === 'machine' && !source.is_own_server ? 'Edit that workspace →' : 'Manage instruments →'}
												</a>
											{:else}
												<span class="shrink-0 font-mono text-2xs text-muted">{source.url}</span>
											{/if}
										</div>
										{#if source.tree !== null}
											<ScrollArea
												class="relative overflow-hidden p-3"
												orientation="vertical"
												viewportClasses="h-full max-h-[300px] w-full"
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
															isSelectable={Boolean(activeRequirement)}
															isCompatible={(n) => isCompatibleForCurrent(n, source)}
															isSelected={(_n, p) => isNodeSelectedForCurrent(source, p)}
															selectionLabel={(_n, p) => selectionLabelForAny(source, p)}
															busyBadge={(_n, p) => busyLabel(source.claims, p)}
															onSelect={(n, p) => onSelectTreeNode(source, n, p)}
														/>
													{/each}
												{/if}
											</ScrollArea>
										{:else}
											<!-- A remote machine: named leaves only. A tcp peer gets read +
											     call and never reconfiguration, so there is no tree to offer. -->
											<ScrollArea viewportClasses="max-h-[300px] p-3">
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
															{@const req = reqByVar(activeRequirement)}
															{@const compatible = Boolean(req) && reqMatchesAttribute(req!, entry)}
															<button
																class="flex items-start gap-2 rounded border px-3 py-2 text-left text-body transition {isAttributeSelectedForCurrent(
																	source,
																	entry
																)
																	? 'border-accent bg-accent-wash'
																	: 'border-line'} {activeRequirement && !compatible
																	? 'opacity-45'
																	: 'hover:border-accent'}"
																disabled={!activeRequirement || !compatible}
																onclick={() => onSelectAttribute(source, entry)}
															>
																<div class="flex-1">
																	<div class="flex flex-wrap items-center gap-1.5">
																		<span class="font-mono text-xs font-medium">{entry.attribute_name}</span>
																		{#if entry.claimed_by}
																			<span
																				class="rounded bg-warn-wash px-1.5 py-0.5 text-2xs font-medium text-warn"
																				title="A running measurement holds this. You can still bind it — the run may be over by the time this project runs — but the two cannot run at once."
																			>
																				in use by {entry.claimed_by}
																			</span>
																		{/if}
																	</div>
																	<div class="text-fine text-muted">
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
				</section>
			{/if}

			<section class="space-y-2">
				<h2 class="text-title font-medium">Project</h2>
				<div class="rounded border border-line bg-surface p-3.5">
					<label class="lw-label" for="project-prefix">Project prefix — optional</label>
					<input
						id="project-prefix"
						type="text"
						bind:value={projectPrefix}
						placeholder="iv_curve_run"
						class="lw-input mono"
					/>
					{#if presets.length > 0}
						<div class="mt-3">
							<label class="lw-label" for="params-preset">Params preset</label>
							<Select
								id="params-preset"
								bind:value={paramsPreset}
								options={[
									{ value: '', label: 'Defaults' },
									...presets.map((preset) => ({ value: preset, label: preset }))
								]}
							/>
							<p class="mt-1 text-fine text-muted">
								Copied into the project when it is generated; editing the preset later changes no
								existing project.
							</p>
						</div>
					{/if}

					<div class="mt-3">
						<div class="mb-1 text-xs text-ink-2">Generated setup style</div>
						<div class="grid gap-2 text-body sm:grid-cols-2">
							<label class="flex items-start gap-2 rounded border border-line px-3 py-2">
								<input type="radio" bind:group={generationStyle} value="production" />
								<span>
									<span class="block font-medium">Production</span>
									<span class="block text-xs text-muted">
										Names each instrument; its settings come from this workspace's instrument config
										when the project runs, so a readdressed rack needs no regeneration.
									</span>
								</span>
							</label>
							<label
								class="flex items-start gap-2 rounded border border-line px-3 py-2 {routedChosen.length
									? 'opacity-60'
									: ''}"
							>
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
										Every setting written into the Python file, to run outside this workspace or to
										learn from. Breaks when an instrument is readdressed; cannot use instruments
										through a server.
									</span>
									{#if routedChosen.length}
										<span class="block text-xs text-warn">
											Not available: {routedChosen.join(', ')}
											{routedChosen.length === 1 ? 'is' : 'are'} used through a server.
										</span>
									{/if}
								</span>
							</label>
						</div>
					</div>

					<div class="mt-3">
						<div class="mb-1 text-xs text-ink-2">Outputs</div>
						<p class="mb-2 text-fine text-muted">
							Every run is recorded in the lab database and shows on the Data page. Both of these can
							be changed later in the project's YAML, under <code>outputs:</code>.
						</p>
						<label class="flex items-start gap-2 rounded border border-line px-3 py-2 text-body">
							<input type="checkbox" bind:checked={saveFiles} />
							<span>
								<span class="block font-medium">Also save each run as files</span>
								<span class="block text-xs text-muted">
									A folder of CSV and YAML per run, laid out as set under
									<a class="text-accent hover:underline" href="/settings#files">Settings → Saving files</a>.
								</span>
							</span>
						</label>
						<div class="mt-2">
							<label class="lw-label" for="live-plot">Live plot when run from a terminal</label>
							<Select
								id="live-plot"
								bind:value={livePlot}
								options={[
									{ value: 'web', label: 'Web page (a window here, or a link over SSH)' },
									{ value: 'window', label: 'Window (matplotlib)' },
									{ value: 'none', label: 'None' }
								]}
							/>
							<p class="mt-1 text-fine text-muted">
								Draws the procedure's first plot as the run goes. A run started from the wizard is
								plotted in the wizard instead.
							</p>
						</div>
					</div>
				</div>
			</section>

			{#if createResult}
				<div class="rounded border border-ok/30 bg-ok-wash px-3 py-2 text-body text-ok">
					Created project <span class="font-medium">{createResult.project_name}</span>
					<div class="mt-1 text-xs">
						<div>{createResult.project_dir}</div>
						<div>{createResult.yaml_file}</div>
						<div>{createResult.setup_file}</div>
					</div>
					<a
						class="mt-2 inline-block font-medium text-accent hover:underline"
						href={`/measurements/run?project=${encodeURIComponent(createResult.project_name)}`}
						>Set it up and run it →</a
					>
				</div>
			{/if}

			{#if createError}
				<div class="rounded border border-crit/30 bg-crit-wash px-3 py-2 text-body text-crit">
					{createError}
				</div>
			{/if}
		{/if}
	{/if}
</section>

{#if measurementName && !data?.loadError && reqs.length > 0}
	<div class="mt-6 flex justify-end">
		<button
			class="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
			onclick={onCreateProject}
			disabled={!allDone() || creatingProject}
		>
			{creatingProject ? 'Creating...' : 'Create Project'}
		</button>
	</div>
{/if}
