<script lang="ts">
	/** This workspace's own instrument config: the full tree, and the only add
	 * flow that can build a whole parent chain in one go.
	 *
	 * The chain-building is possible here precisely because the config is ours —
	 * a partially-saved parent left behind by an abandoned wizard is our own mess
	 * to clean up. `ServerTree` deliberately refuses the same trick on someone
	 * else's config.
	 */
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type { TreeItem as TreeNodeItem } from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { fetchWithConfig } from '$lib/api';
	import { workstation } from '$lib/stores/workstation.svelte';
	import { PlusIcon } from 'phosphor-svelte';
	import type {
		TreeItem,
		InstrumentMeta,
		DiscoveryAction,
		DiscoveryResult,
		ChainStep,
		RootTransport
	} from '$lib/types/instruments';
	import type { TransportBadge } from '$lib/components/TreeNode.svelte';

	let { data, autoOpenAdd = false }: { data: any; autoOpenAdd?: boolean } = $props();
	let tree: TreeItem[] = $state(data.tree ?? []);
	let metadata: Record<string, InstrumentMeta> = $state(data.metadata ?? {});
	let roots: Record<string, RootTransport> = $state(data.roots ?? {});
	// Read from the shared store rather than this page's load: fetching it here
	// would block navigation on a server probe (see `+page.ts`), and it is the
	// same fact the topbar already shows.
	const hardwareOwner = $derived(workstation.owner?.owner ?? 'wizard');

	// Roots are keyed by inst:// path, which is the config key with a prefix.
	function transportBadge(node: TreeNodeItem): TransportBadge | null {
		const info = roots[`inst://${node.key}`];
		if (!info) return null;
		return {
			transport_sharing: info.transport_sharing,
			state_authority: info.state_authority,
			held_by_server: info.held_by_server
		};
	}

	// Confirmation dialog state
	let confirmAction: 'reset' | 'remove' | null = $state(null);
	let confirmTarget: TreeNodeItem | null = $state(null);
	let actionLoading = $state(false);
	let statusMessage: { text: string; ok: boolean } | null = $state(null);

	// Add wizard state
	let showAddWizard = $state(false);
	let addStep = $state(0);
	let selectedType: string | null = $state(null);
	let chainSteps: ChainStep[] = $state([]);
	let currentChainIndex = $state(0);
	let addLoading = $state(false);

	// Generic discovery state
	let discoveryActions: DiscoveryAction[] = $state([]);
	let discoveryInputs: Record<string, any> = $state({});
	let discoveryInputsHaveChanged = $state(false);
	let discoveryResult: DiscoveryResult | null = $state(null);
	let discoveryLoading = $state(false);
	let discoveryTargetType: string | null = $state(null); // which type discovery is currently for (leaf or parent)

	// Optimistically saved parent keys (for cleanup on cancel)
	let savedParentKeys: string[] = $state([]);

	async function refetchData() {
		const d = await fetchWithConfig<{ tree: TreeItem[]; metadata: Record<string, InstrumentMeta> }>(
			'/api/manage-instruments',
			'GET'
		);
		tree = d.tree ?? [];
		metadata = d.metadata ?? {};
	}

	// Reset / Remove actions
	function onReset(node: TreeNodeItem) {
		confirmAction = 'reset';
		confirmTarget = node;
	}

	// Rules that reference the instrument being removed. A rule left pointing at
	// a vanished attribute fails closed — it denies everything it covered — so
	// removing one instrument can make a different one un-callable.
	type RemovalImpact = {
		attributes: string[];
		rules: {
			id: string;
			description: string;
			referenced_in_condition: string[];
			blocks_methods: string[];
		}[];
	};
	let removalImpact: RemovalImpact | null = $state(null);
	let impactLoading = $state(false);

	function onRemove(node: TreeNodeItem) {
		confirmAction = 'remove';
		confirmTarget = node;
		removalImpact = null;
		impactLoading = true;
		fetchWithConfig<RemovalImpact>('/api/manage-instruments/removal-impact', 'POST', {
			type: node.type,
			key: node.key
		})
			.then((res) => {
				// Ignore a reply for a dialog the user already dismissed.
				if (confirmTarget === node) removalImpact = res;
			})
			.catch(() => {})
			.finally(() => {
				impactLoading = false;
			});
	}

	async function executeConfirm() {
		if (!confirmTarget || !confirmAction) return;
		actionLoading = true;
		statusMessage = null;
		try {
			const body = { type: confirmTarget.type, key: confirmTarget.key };
			const endpoint =
				confirmAction === 'reset'
					? '/api/manage-instruments/reset'
					: '/api/manage-instruments/remove';
			await fetchWithConfig(endpoint, 'POST', body);
			statusMessage = {
				text: `${confirmAction === 'reset' ? 'Reset' : 'Removed'} ${confirmTarget.type} (${confirmTarget.key})`,
				ok: true
			};
			await refetchData();
		} catch (e: any) {
			statusMessage = { text: e.message ?? 'Operation failed', ok: false };
		} finally {
			actionLoading = false;
			confirmAction = null;
			confirmTarget = null;
		}
	}

	function cancelConfirm() {
		confirmAction = null;
		confirmTarget = null;
	}

	// ---- Type picker -------------------------------------------------------
	//
	// A build can expose a few dozen instrument types. Listing them all is fine;
	// making the user *read* all of them to find one is not, so the picker
	// filters as you type and groups by where a type can attach — top-level
	// racks first, then each parent's modules, which is the order in which you
	// would actually build a chain.

	let typeQuery = $state('');

	const allTypes = $derived(Object.values(metadata));

	function matchesQuery(m: InstrumentMeta): boolean {
		const q = typeQuery.trim().toLowerCase();
		if (!q) return true;
		return (
			m.type.toLowerCase().includes(q) ||
			(m.class_name ?? '').toLowerCase().includes(q) ||
			(m.parent_type ?? '').toLowerCase().includes(q)
		);
	}

	const topLevelTypes = $derived(allTypes.filter((m) => m.is_top_level && matchesQuery(m)));
	const childTypes = $derived(
		allTypes.filter((m) => m.is_child && !m.is_top_level && matchesQuery(m))
	);

	const parentGroups = $derived.by(() => {
		const groups: Record<string, InstrumentMeta[]> = {};
		for (const m of childTypes) {
			const parent = m.parent_type ?? 'unknown';
			if (!groups[parent]) groups[parent] = [];
			groups[parent].push(m);
		}
		return groups;
	});

	const noTypeMatches = $derived(topLevelTypes.length === 0 && childTypes.length === 0);

	/** How many instruments of a type already exist, so the picker can say
	 *  "3 configured" — useful for telling a fresh rack from a duplicate. */
	function existingCount(typeStr: string): number {
		return findExistingInstances(typeStr).length;
	}

	function startAddWizard() {
		showAddWizard = true;
		addStep = 0;
		typeQuery = '';
		selectedType = null;
		chainSteps = [];
		currentChainIndex = 0;
		statusMessage = null;
		discoveryActions = [];
		discoveryInputs = {};
		discoveryResult = null;
		discoveryTargetType = null;
		savedParentKeys = [];
	}

	async function saveResolvedParent(stepIndex: number) {
		const step = chainSteps[stepIndex];
		if (step.action !== 'create_new' || !step.key) return;

		// Build a mini-chain for just this parent and its ancestors above it
		const miniChain: ChainStep[] = [];
		// The current step being saved
		miniChain.push({ ...step });
		// All ancestors above this step (higher indices = further up)
		for (let i = stepIndex + 1; i < chainSteps.length; i++) {
			miniChain.push({ ...chainSteps[i] });
		}

		const response = await fetchWithConfig<{ saved_keys: { type: string; key: string }[] }>(
			'/api/manage-instruments/add',
			'POST',
			{ chain: miniChain }
		);

		// The backend stores instruments under a hash key, not the raw port/address.
		// Replace the chainStep key with the real hash so subsequent discovery calls
		// (which send parent_chain) can find the parent in the config.
		const savedForThisStep = response.saved_keys?.find((s) => s.type === step.type);
		if (savedForThisStep) {
			chainSteps[stepIndex].key = savedForThisStep.key;
		}
		// Switch step to use_existing now that it's saved
		chainSteps[stepIndex].action = 'use_existing';
		// Track for cleanup on cancel
		savedParentKeys.push(chainSteps[stepIndex].key);
		await refetchData();
	}

	function selectTypeForAdd(typeStr: string) {
		selectedType = typeStr;
		const meta = metadata[typeStr];
		if (!meta) return;

		// Set up discovery actions from metadata
		discoveryActions = meta.discovery_actions ?? [];
		discoveryTargetType = typeStr;
		discoveryResult = null;
		discoveryInputsHaveChanged = false;

		// Initialize discovery inputs from action defaults
		if (discoveryActions.length > 0) {
			const inputs: Record<string, any> = {};
			for (const action of discoveryActions) {
				for (const inp of action.inputs) {
					if (inp.default !== undefined) inputs[inp.name] = inp.default;
				}
			}
			discoveryInputs = inputs;
		}

		const chain = meta.parent_chain;
		if (chain.length === 0) {
			chainSteps = [{ type: typeStr, key: '', action: 'create_new', resolved: false }];
			if (discoveryActions.length > 0) {
				// Has discovery support — show discovery step
				addStep = 20;
				// Auto-run discovery immediately (no need to wait for user)
				if (discoveryActions.length > 0) {
					runDiscovery(discoveryActions[0].name);
				}
			} else if (meta.key_hint) {
				addStep = 2;
			} else {
				chainSteps[0].key = typeStr;
				addStep = 3;
			}
		} else {
			// Build chain bottom-up: leaf first, then parents
			chainSteps = [
				{ type: typeStr, key: '', action: 'create_new', resolved: false },
				...chain.map((pt) => ({ type: pt, key: '', action: 'use_existing' as const, resolved: false }))
			];
			currentChainIndex = chain.length;
			addStep = 1;
		}
	}

	function findExistingInstances(typeStr: string): { key: string; node: TreeItem }[] {
		const results: { key: string; node: TreeItem }[] = [];
		function walk(nodes: TreeItem[]) {
			for (const n of nodes) {
				if (n.type === typeStr) results.push({ key: n.key, node: n });
				for (const child of Object.values(n.children ?? {})) walk([child]);
			}
		}
		walk(tree);
		return results;
	}

	async function selectExistingParent(key: string) {
		chainSteps[currentChainIndex].action = 'use_existing';
		chainSteps[currentChainIndex].key = key;
		chainSteps[currentChainIndex].resolved = true;
		advanceChain();
	}

	function selectCreateNewParent() {
		chainSteps[currentChainIndex].action = 'create_new';
		const parentType = chainSteps[currentChainIndex].type;
		const parentMeta = metadata[parentType];
		const parentDiscovery = parentMeta?.discovery_actions ?? [];

		if (parentDiscovery.length > 0) {
			// Parent has discovery actions — show discovery UI for the parent
			discoveryTargetType = parentType;
			discoveryActions = parentDiscovery;
			discoveryResult = null;
			discoveryInputsHaveChanged = false;
			const inputs: Record<string, any> = {};
			for (const action of parentDiscovery) {
				for (const inp of action.inputs) {
					if (inp.default !== undefined) inputs[inp.name] = inp.default;
				}
			}
			discoveryInputs = inputs;
			addStep = 20;
			runDiscovery(parentDiscovery[0].name);
		} else {
			addStep = 10; // manual key entry for new parent
		}
	}

	async function confirmNewParentKey(key: string): Promise<boolean> {
		const stepIndex = currentChainIndex;
		const step = chainSteps[stepIndex];
		step.key = key;
		step.resolved = true;
		addLoading = true;
		statusMessage = null;
		try {
			await saveResolvedParent(stepIndex);
			advanceChain();
			return true;
		} catch (e: any) {
			// Keep the entry screen and its value visible so a failed server edit
			// cannot look like the button simply did nothing.
			step.key = '';
			step.resolved = false;
			statusMessage = { text: e.message ?? `Could not add ${step.type}`, ok: false };
			return false;
		} finally {
			addLoading = false;
		}
	}

	function advanceChain() {
		currentChainIndex--;
		if (currentChainIndex < 0) {
			// all parents resolved, but we still need the leaf key if it's a child
			addStep = 2;
			return;
		}
		if (currentChainIndex === 0) {
			// We've resolved all parents and are now at the leaf (index 0).
			const leafType = chainSteps[0].type;
			const leafMeta = metadata[leafType];
			const leafDiscovery = leafMeta?.discovery_actions ?? [];

			if (leafDiscovery.length > 0) {
				// Leaf has discovery — go to step 20
				discoveryTargetType = leafType;
				discoveryActions = leafDiscovery;
				discoveryResult = null;
				discoveryInputsHaveChanged = false;

				// Initialize discovery inputs from defaults
				const inputs: Record<string, any> = {};
				for (const action of leafDiscovery) {
					for (const inp of action.inputs) {
						if (inp.default !== undefined) inputs[inp.name] = inp.default;
					}
				}
				discoveryInputs = inputs;

				addStep = 20;
				runDiscovery(leafDiscovery[0].name);
			} else {
				addStep = 2; // Manual key entry
			}
		} else {
			addStep = 1; // next parent in chain
		}
	}

	const isParentDiscovery = $derived(discoveryTargetType !== null && discoveryTargetType !== selectedType);

	async function resolveDiscoverySelection(key: string) {
		if (isParentDiscovery) {
			// Resolve the current parent chain step and advance
			chainSteps[currentChainIndex].key = key;
			chainSteps[currentChainIndex].resolved = true;
			await saveResolvedParent(currentChainIndex);
			advanceChain();
		} else {
			// Leaf discovery — set leaf key and execute
			chainSteps[0].key = key;
			executeAdd();
		}
	}

	function setLeafKey(key: string) {
		chainSteps[0].key = key;
		addStep = 3; // confirm
	}


	async function runDiscovery(actionName: string) {
		const targetType = discoveryTargetType ?? selectedType;
		if (!targetType) return;
		discoveryLoading = true;
		discoveryResult = null;
		try {
			// Build resolved ancestor chain (root-first) from chainSteps
			// chainSteps is leaf-first: [leaf, parent, grandparent, ...]
			const targetIndex = chainSteps.findIndex(s => s.type === targetType);
			const parentChain: {type: string, key: string}[] = [];
			if (targetIndex >= 0) {
				for (let i = chainSteps.length - 1; i > targetIndex; i--) {
					const step = chainSteps[i];
					if (step.resolved && step.key) {
						parentChain.push({ type: step.type, key: step.key });
					}
				}
			}

			const response = await fetchWithConfig('/api/manage-instruments/discover', 'POST', {
				type: targetType,
				action: actionName,
				params: discoveryInputs,
				...(parentChain.length > 0 ? { parent_chain: parentChain } : {})
			});
			discoveryResult = response;
		} catch (e: any) {
			statusMessage = { text: `Discovery failed: ${e.message ?? e}`, ok: false };
		} finally {
			discoveryLoading = false;
		}
	}

	async function executeAdd() {
		addLoading = true;
		statusMessage = null;
		try {
			// Ensure the leaf key is set from discovery result if not already done
			if (
				!chainSteps[0].key &&
				discoveryResult?.result_type === 'children' &&
				discoveryResult.parent_key
			) {
				chainSteps[0].key = discoveryResult.parent_key;
			}

			// Add the parent first
			await fetchWithConfig('/api/manage-instruments/add', 'POST', { chain: chainSteps });

			// If discovery found children, apply them
			if (
				discoveryResult?.result_type === 'children' &&
				discoveryResult.children.length > 0 &&
				selectedType
			) {
				const parentKey = chainSteps[0].key;
				await fetchWithConfig('/api/manage-instruments/apply-children', 'POST', {
					parent_type: selectedType,
					parent_key: parentKey,
					children: discoveryResult.children
				});
			}

			statusMessage = { text: `Added ${selectedType}`, ok: true };
			savedParentKeys = []; // parents are now permanent
			await refetchData();
			showAddWizard = false;
		} catch (e: any) {
			statusMessage = { text: e.message ?? 'Add failed', ok: false };
		} finally {
			addLoading = false;
		}
	}

	// Current chain step info
	const currentStepType = $derived(
		currentChainIndex >= 0 && currentChainIndex < chainSteps.length
			? chainSteps[currentChainIndex].type
			: null
	);
	const currentExisting = $derived(currentStepType ? findExistingInstances(currentStepType) : []);

	async function cancelAddWizard() {
		// Clean up optimistically saved parents (children first = reverse order)
		for (let i = savedParentKeys.length - 1; i >= 0; i--) {
			const key = savedParentKeys[i];
			// Find the type from chainSteps
			const step = chainSteps.find(s => s.key === key);
			if (step) {
				try {
					await fetchWithConfig('/api/manage-instruments/remove', 'POST', {
						type: step.type, key
					});
				} catch {
					// fire-and-forget cleanup
				}
			}
		}
		savedParentKeys = [];
		showAddWizard = false;
		await refetchData();
	}

	// Temp key input
	let tempKey = $state('');

	// Deep link from Overview's "Add instrument" quick action. Guarded so a later
	// re-render cannot reopen a wizard the user has already dismissed.
	let autoOpened = false;
	$effect(() => {
		if (autoOpenAdd && !autoOpened) {
			autoOpened = true;
			startAddWizard();
		}
	});
</script>

<section class="space-y-4">
	<!-- Which process will actually touch hardware when you hit Discover. Worth
	     stating: it changes where the scan runs and whether the permission gate
	     observes it. -->
	<div class="text-xs text-muted">
		{#if hardwareOwner === 'server'}
			Discovery runs on this workspace's <strong>instrument server</strong>, which owns the
			hardware.
		{:else}
			No server is running, so discovery opens hardware in the <strong>wizard</strong> process.
		{/if}
		<a class="text-accent hover:underline" href="/servers/hardware">Hardware ownership →</a>
	</div>

	{#if statusMessage}
		<div
			class="rounded-lg px-3 py-2 text-sm {statusMessage.ok
				? 'bg-ok-wash text-ok'
				: 'bg-crit-wash text-crit'}"
		>
			{statusMessage.text}
		</div>
	{/if}

	<!-- No heading here: the page already says "Configured instruments", and the
	     tab row above says which workspace's. -->
	<div>
		<div class="rounded border border-line bg-surface p-3">
			{#if tree.length === 0}
				<p class="px-2 py-3 text-sm text-muted">No instruments configured yet.</p>
			{:else}
				{#each tree as node}
					<TreeNode {node} {onReset} {onRemove} {transportBadge} />
				{/each}
			{/if}
		</div>
	</div>

	<!-- Add button -->
	<button class="lw-btn lw-btn-primary" onclick={startAddWizard}>
		<PlusIcon size={14} weight="bold" />
		Add instrument
	</button>
</section>

<!-- Add wizard. In a dialog rather than inline: the tree above is routinely
     several screens tall, and a multi-step form that begins below it means
     scrolling to find the step you are already on. -->
{#if showAddWizard}
	<Modal
		title="Add instrument"
		subtitle={addStep === 0
			? 'Pick what to add. Modules list the parent they attach to.'
			: (selectedType ?? undefined)}
		onclose={cancelAddWizard}
		width={addStep === 0 ? 'max-w-3xl' : 'max-w-2xl'}
	>
		<!-- Dependency chain — only meaningful once a type with parents is chosen. -->
		{#if chainSteps.length > 1}
			<div class="mb-4 flex items-center gap-2 overflow-x-auto pb-1">
				{#each [...chainSteps].reverse() as step, i (step.type + i)}
					{@const isLast = i === chainSteps.length - 1}
					<div class="flex items-center gap-2">
						<div
							class="shrink-0 rounded border px-2.5 py-1.5 text-[11.5px] transition-colors
								{step.resolved
								? 'border-ok/40 bg-ok-wash text-ok'
								: 'border-dashed border-line-2 bg-surface-2 text-muted'}"
						>
							<div class="font-semibold">{step.type}</div>
							{#if step.key}
								<div class="mono mt-0.5 opacity-75">{step.key}</div>
							{/if}
						</div>
						{#if !isLast}
							<div class="shrink-0 text-muted">→</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		<!-- Step 0: pick a type.
		     Filtered rather than paged, and grouped by where a type can attach —
		     a rack you add on its own, a module you add under one. That grouping
		     is the same order you would build a chain in. -->
		{#if addStep === 0}
			<div class="space-y-3">
				<input
					type="text"
					bind:value={typeQuery}
					placeholder="Filter by name, class or parent…"
					class="lw-input"
					aria-label="Filter instrument types"
				/>

				{#if noTypeMatches}
					<p class="py-8 text-center text-xs text-muted">
						Nothing matches <span class="mono">{typeQuery}</span>. This build exposes
						{allTypes.length} instrument types.
					</p>
				{/if}

				{#if topLevelTypes.length > 0}
					<div>
						<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
							Racks &amp; standalone instruments
						</p>
						<div class="grid gap-1.5 sm:grid-cols-2">
							{#each topLevelTypes as m (m.type)}
								{@const count = existingCount(m.type)}
								<button
									class="flex items-start gap-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
									onclick={() => selectTypeForAdd(m.type)}
								>
									<span class="min-w-0 flex-1">
										<span class="block truncate text-[12.5px] font-semibold">{m.type}</span>
										<span class="mono block truncate text-[11px] text-muted">{m.class_name}</span>
									</span>
									{#if count > 0}
										<span class="shrink-0 text-[10.5px] text-muted">{count} configured</span>
									{/if}
								</button>
							{/each}
						</div>
					</div>
				{/if}

				{#each Object.entries(parentGroups) as [parentType, children] (parentType)}
					<div>
						<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
							Modules under <span class="mono normal-case">{parentType}</span>
						</p>
						<div class="grid gap-1.5 sm:grid-cols-2">
							{#each children as m (m.type)}
								{@const count = existingCount(m.type)}
								<button
									class="flex items-start gap-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
									onclick={() => selectTypeForAdd(m.type)}
								>
									<span class="min-w-0 flex-1">
										<span class="block truncate text-[12.5px] font-semibold">{m.type}</span>
										<span class="mono block truncate text-[11px] text-muted">{m.class_name}</span>
									</span>
									{#if count > 0}
										<span class="shrink-0 text-[10.5px] text-muted">{count} configured</span>
									{/if}
								</button>
							{/each}
						</div>
					</div>
				{/each}
			</div>
		{/if}

			<!-- Step 1: Parent selection (use existing or create new) -->
			{#if addStep === 1 && currentStepType}
				<p class="mb-3 text-[12.5px] text-ink-2">
					<span class="mono font-semibold">{chainSteps[0].type}</span> attaches to a
					<span class="mono font-semibold">{currentStepType}</span>. Pick the one it sits under.
				</p>

				{#if currentExisting.length > 0}
					<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
						Use existing
					</p>
					<div class="mb-3 space-y-1.5">
						{#each currentExisting as inst (inst.key)}
							<button
								class="flex w-full items-center gap-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
								onclick={() => selectExistingParent(inst.key)}
							>
								<span class="text-[12.5px] font-semibold">{inst.node.type}</span>
								<span class="mono text-[11.5px] text-muted">{inst.key}</span>
							</button>
						{/each}
					</div>
				{/if}

				<button
					class="w-full rounded border border-dashed border-line-2 px-2.5 py-2 text-left text-[12.5px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-wash"
					onclick={selectCreateNewParent}
				>
					+ Create a new {currentStepType}
				</button>
			{/if}

			<!-- Step 10: Key entry for a new parent being created -->
			{#if addStep === 10 && currentStepType}
				<p class="mb-3 text-[12.5px] text-ink-2">
					<span class="mono font-semibold">{chainSteps[0].type}</span> needs a new
					<span class="mono font-semibold">{currentStepType}</span> above it. Enter its
					{metadata[currentStepType]?.key_hint?.toLowerCase() ?? 'address or slot'}.
				</p>
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={tempKey}
						placeholder={metadata[currentStepType]?.key_hint ?? 'e.g. address or slot'}
						class="lw-input mono flex-1"
					/>
					<button
						class="lw-btn lw-btn-primary shrink-0"
						disabled={!tempKey.trim() || addLoading}
						onclick={async () => {
							if (await confirmNewParentKey(tempKey.trim())) tempKey = '';
						}}
					>
						{addLoading ? 'Saving…' : 'Next'}
					</button>
				</div>
				<p class="mt-2 text-[11px] text-muted">
					This parent is written to the config as soon as you continue, so its hash key exists for
					the next step. Cancelling the wizard removes it again.
				</p>
			{/if}

			<!-- Step 20: Discovery — ask the hardware instead of typing an address. -->
			{#if addStep === 20 && discoveryActions.length > 0}
				{@const currentAction = discoveryActions[0]}
				<p class="mb-3 text-[12.5px] text-ink-2">{currentAction.description}</p>

				<!-- When the action borrows its parent's connection there is nothing
				     to fill in, so say where the scan is going instead. -->
				{#if currentAction.parent_dep}
					{@const parentStep = chainSteps.find(
						(s) => s.type === currentAction.parent_dep && s.resolved
					)}
					{#if parentStep}
						<div class="mb-3">
							<Callout tone="info">
								Scanning through <span class="mono">{parentStep.type}</span>
								(<span class="mono">{parentStep.key}</span>), which already has the connection.
							</Callout>
						</div>
					{/if}
				{/if}

				{#if currentAction.inputs.length > 0 && !currentAction.parent_dep}
					<div class="mb-3 grid gap-3 rounded border border-line bg-surface-2 p-3 sm:grid-cols-2">
						{#each currentAction.inputs as inp (inp.name)}
							<div>
								<label class="lw-label" for="disc-{inp.name}">{inp.label}</label>
								<input
									id="disc-{inp.name}"
									type={inp.type === 'number' ? 'number' : 'text'}
									value={discoveryInputs[inp.name] ?? inp.default ?? ''}
									onchange={(e) => {
										discoveryInputs[inp.name] = (e.target as HTMLInputElement).value;
										discoveryInputsHaveChanged = true;
									}}
									class="lw-input mono"
								/>
							</div>
						{/each}
					</div>
				{/if}

				{#if discoveryLoading}
					<Callout tone="info">Scanning…</Callout>
				{:else if discoveryResult}
					{#if discoveryResult.result_type === 'children'}
						{#if discoveryResult.children.length > 0}
							<div class="mb-3">
								<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
									Found {discoveryResult.children.length} device{discoveryResult.children.length === 1
										? ''
										: 's'}
								</p>
								<ul class="divide-y divide-line rounded border border-line bg-surface">
									{#each discoveryResult.children as child, i (i)}
										<li class="flex flex-wrap items-baseline gap-x-2 px-2.5 py-1.5 text-[12px]">
											<span class="font-semibold">{child.type}</span>
											{#each Object.entries(child.key_fields) as [k, v] (k)}
												<span class="mono text-muted">{k}: {v}</span>
											{/each}
											{#if child.idn}
												<span class="mono w-full truncate text-[11px] text-muted">{child.idn}</span>
											{/if}
										</li>
									{/each}
								</ul>
							</div>
						{:else}
							<Callout tone="warn">No devices found. Check the connection and re-scan.</Callout>
						{/if}

						{#if discoveryResult.warnings && discoveryResult.warnings.length > 0}
							<div class="mt-3">
								<Callout
									tone="warn"
									title="{discoveryResult.warnings.length} module{discoveryResult.warnings.length === 1
										? ''
										: 's'} this build cannot drive. "
								>
									They are left out of the config; everything else is added normally.
									<ul class="mono mt-1 space-y-0.5 text-[11px]">
										{#each discoveryResult.warnings as warning, i (i)}
											<li>{warning}</li>
										{/each}
									</ul>
								</Callout>
							</div>
						{/if}
					{:else if discoveryResult.result_type === 'probe'}
						{#if discoveryResult.found.length > 0}
							<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
								Found {discoveryResult.found.length} controller{discoveryResult.found.length === 1
									? ''
									: 's'} — pick one
							</p>
							<div class="space-y-1.5">
								{#each discoveryResult.found as port_entry (port_entry.port)}
									<button
										class="flex w-full items-baseline gap-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
										onclick={() => resolveDiscoverySelection(port_entry.port)}
									>
										<span class="mono text-[12.5px] font-semibold">{port_entry.port}</span>
										{#if port_entry.description}
											<span class="truncate text-[11.5px] text-muted">{port_entry.description}</span>
										{/if}
									</button>
								{/each}
							</div>
						{:else}
							<Callout tone="warn">
								No controllers found. Check the USB connection and re-scan.
							</Callout>
						{/if}
					{:else if discoveryResult.result_type === 'self_candidates'}
						{#if discoveryResult.found.length > 0}
							<p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
								Found {discoveryResult.found.length} candidate{discoveryResult.found.length === 1
									? ''
									: 's'} — pick one
							</p>
							<div class="space-y-1.5">
								{#each discoveryResult.found as candidate, i (i)}
									{@const keyValue = Object.values(candidate.key_fields)[0] ?? ''}
									<button
										class="flex w-full flex-wrap items-baseline gap-x-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
										onclick={() => resolveDiscoverySelection(String(keyValue))}
									>
										{#each Object.entries(candidate.key_fields) as [k, v] (k)}
											<span class="text-[12.5px]">
												{k}: <span class="mono font-semibold">{v}</span>
											</span>
										{/each}
										{#if candidate.idn}
											<span class="mono w-full truncate text-[11px] text-muted">{candidate.idn}</span>
										{/if}
									</button>
								{/each}
							</div>
						{:else}
							<Callout tone="warn">No instruments answered on the bus.</Callout>
						{/if}
					{/if}
				{/if}
			{/if}

			<!-- Step 2: Key entry for the target leaf/child instrument -->
			{#if addStep === 2}
				<p class="mb-3 text-[12.5px] text-ink-2">
					Enter the key for the new <span class="mono font-semibold">{chainSteps[0].type}</span>.
				</p>
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={tempKey}
						placeholder={metadata[chainSteps[0].type]?.key_hint ?? 'e.g. 1, 5'}
						class="lw-input mono flex-1"
					/>
					<button
						class="lw-btn lw-btn-primary shrink-0"
						disabled={!tempKey.trim()}
						onclick={() => {
							setLeafKey(tempKey.trim());
							tempKey = '';
						}}
					>
						Next
					</button>
				</div>
			{/if}

			<!-- Step 3: Confirm. The chain is shown root-first, which is the order
			     it will appear in the tree. -->
			{#if addStep === 3}
				<p class="mb-2 text-[12.5px] text-ink-2">This is what will be written:</p>
				<ul class="divide-y divide-line rounded border border-line bg-surface">
					{#each [...chainSteps].reverse() as step, i (step.type + i)}
						<li class="flex items-center gap-2 px-2.5 py-2 text-[12.5px]">
							{#if step.action === 'create_new'}
								<Pill tone="ok">new</Pill>
							{:else}
								<Pill tone="neutral">existing</Pill>
							{/if}
							<span class="font-semibold">{step.type}</span>
							<span class="mono text-muted">{step.key}</span>
						</li>
					{/each}
				</ul>
			{/if}

		{#snippet footer()}
			{#if addStep === 20 && discoveryActions.length > 0}
				{@const currentAction = discoveryActions[0]}
				<button
					class="lw-btn mr-auto"
					onclick={() => (addStep = isParentDiscovery ? 10 : 2)}
					disabled={discoveryLoading}
				>
					Enter it manually
				</button>
				{#if discoveryInputsHaveChanged || !discoveryResult}
					<button
						class="lw-btn"
						onclick={() => runDiscovery(currentAction.name)}
						disabled={discoveryLoading}
					>
						{discoveryLoading ? 'Scanning…' : 'Re-scan'}
					</button>
				{/if}
				{#if discoveryResult?.result_type === 'children' && discoveryResult.children.length > 0}
					<button
						class="lw-btn lw-btn-primary"
						onclick={() => {
							if (
								isParentDiscovery &&
								discoveryResult?.result_type === 'children' &&
								discoveryResult.parent_key
							) {
								resolveDiscoverySelection(discoveryResult.parent_key);
							} else {
								executeAdd();
							}
						}}
						disabled={addLoading}
					>
						{addLoading
							? 'Adding…'
							: isParentDiscovery
								? `Use this ${discoveryTargetType}`
								: `Add ${selectedType} and its modules`}
					</button>
				{/if}
			{:else if addStep === 3}
				<button class="lw-btn" onclick={cancelAddWizard} disabled={addLoading}>Cancel</button>
				<button class="lw-btn lw-btn-primary" onclick={executeAdd} disabled={addLoading}>
					{addLoading ? 'Adding…' : 'Add instrument'}
				</button>
			{:else}
				<button class="lw-btn" onclick={cancelAddWizard} disabled={addLoading}>Cancel</button>
			{/if}
		{/snippet}
	</Modal>
{/if}

<!-- Confirmation Dialog -->
{#if confirmAction && confirmTarget}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
		<div
			class="w-full max-w-sm rounded border border-line bg-surface p-5 shadow-2xl"
		>
			<h3 class="text-lg font-semibold">
				{confirmAction === 'reset' ? 'Reset to defaults?' : 'Remove instrument?'}
			</h3>
			<p class="mt-2 text-sm text-ink-2">
				{#if confirmAction === 'reset'}
					This will reset <strong>{confirmTarget.type}</strong> ({confirmTarget.key}) to factory
					defaults. Children will be preserved.
				{:else}
					This will permanently remove <strong>{confirmTarget.type}</strong> ({confirmTarget.key})
					and all its children from the config.
				{/if}
			</p>

			{#if confirmAction === 'remove'}
				{#if impactLoading}
					<p class="mt-3 text-xs text-muted">Checking permission rules…</p>
				{:else if removalImpact && removalImpact.rules.length > 0}
					<div
						class="mt-3 rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs"
					>
						<div class="font-medium text-warn">
							{removalImpact.rules.length} permission rule(s) reference this instrument
						</div>
						<ul class="mt-1 space-y-1 text-warn">
							{#each removalImpact.rules as rule (rule.id)}
								<li>
									<span class="font-mono">{rule.id}</span>
									{#if rule.blocks_methods.length > 0}
										— blocks <span class="font-mono">{rule.blocks_methods.join(', ')}</span>
									{/if}
								</li>
							{/each}
						</ul>
						<div class="mt-1.5 text-warn">
							A rule whose instrument no longer exists fails closed: it denies every call it
							covers, which may block instruments you did not remove. Edit these rules on
							<a class="underline" href="/servers/permissions">Server &amp; Permissions</a> first.
						</div>
					</div>
				{/if}
			{/if}
			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2"
					onclick={cancelConfirm}
					disabled={actionLoading}>Cancel</button
				>
				<button
					class="rounded-md px-3 py-1.5 text-sm text-white {confirmAction === 'remove'
						? 'bg-crit hover:brightness-110'
						: 'bg-accent hover:brightness-110'} disabled:opacity-50"
					onclick={executeConfirm}
					disabled={actionLoading}
				>
					{actionLoading ? 'Working...' : confirmAction === 'reset' ? 'Reset' : 'Remove'}
				</button>
			</div>
		</div>
	</div>
{/if}
