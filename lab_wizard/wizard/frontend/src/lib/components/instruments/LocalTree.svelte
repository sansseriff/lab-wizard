<script lang="ts">
	/** Build a complete instrument chain in a backend draft, then save it once. */
	import InstrumentWorkbench from './InstrumentWorkbench.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import {
		existingParentChain,
		parentCandidates,
		nodeAddress,
		type NodePath,
		type ParamUpdate,
		type ParamResult
	} from '$lib/instruments/model';
	import type { TreeItem as TreeNodeItem } from '$lib/components/TreeNode.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import Callout from '$lib/components/Callout.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { api, errorMessage, unwrap } from '$lib/api';
	import { workstation } from '$lib/stores/workstation.svelte';
	import type {
		TreeItem,
		InstrumentMeta,
		DiscoveryAction,
		DiscoveryResult,
		ChainStep,
		RootTransport
	} from '$lib/types/instruments';
	import { untrack } from 'svelte';
	import type { TransportBadge } from '$lib/components/TreeNode.svelte';

	let {
		data,
		autoOpenAdd = false,
		dirty = $bindable(false),
		busy = $bindable(false)
	}: { data: any; autoOpenAdd?: boolean; dirty?: boolean; busy?: boolean } = $props();
	let addParent = $state<{ node: TreeItem; path: NodePath } | null>(null);
	// Seeded from the page's load, then refetched in place after each edit, so
	// only the initial value is wanted here.
	let tree: TreeItem[] = $state(untrack(() => data.tree ?? []));
	let metadata: Record<string, InstrumentMeta> = $state(untrack(() => data.metadata ?? {}));
	let roots: Record<string, RootTransport> = $state(untrack(() => data.roots ?? {}));
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
	let confirmPath = $state<NodePath>([]);
	let actionLoading = $state(false);
	let statusMessage: { text: string; ok: boolean } | null = $state(null);

	// Add wizard state
	let showAddWizard = $state(false);
	type AddStep = 'choose-type' | 'choose-parent' | 'parent-key' | 'discover' | 'leaf-key' | 'confirm';
	let addStep = $state<AddStep>('choose-type');
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

	let draftId = $state<string | null>(null);
	let draftTree = $state<TreeItem[] | null>(null);
	let draftPath = $state<NodePath>([]);
	let draftReadyToCommit = $state(false);

	// Back: every step's state as it was on arrival, newest last. Back restores
	// the one before; the draft is re-sent whole on the next step, so anything
	// staged from a step undone is simply overwritten.
	type Visit = {
		step: AddStep;
		index: number;
		selectedType: string | null;
		chainSteps: ChainStep[];
		discoveryActions: DiscoveryAction[];
		discoveryInputs: Record<string, any>;
		discoveryTargetType: string | null;
		draftReadyToCommit: boolean;
	};
	let visits = $state<Visit[]>([]);
	const visitKey = (step: AddStep, index: number) => `${step}:${index}`;

	$effect(() => {
		if (!showAddWizard) return;
		const key = visitKey(addStep, currentChainIndex);
		untrack(() => {
			const last = visits.at(-1);
			if (last && visitKey(last.step, last.index) === key) return;
			visits.push({
				step: addStep,
				index: currentChainIndex,
				selectedType,
				chainSteps: $state.snapshot(chainSteps),
				discoveryActions: $state.snapshot(discoveryActions),
				discoveryInputs: $state.snapshot(discoveryInputs),
				discoveryTargetType,
				draftReadyToCommit
			});
		});
	});

	function back() {
		if (visits.length < 2 || addLoading || discoveryLoading) return;
		visits.pop();
		const to = visits.at(-1)!;
		// Put back what was typed into the box being returned to.
		tempKey =
			to.step === 'leaf-key'
				? chainSteps[0].key
				: to.step === 'parent-key'
					? (chainSteps[to.index]?.key ?? '')
					: '';
		addStep = to.step;
		currentChainIndex = to.index;
		selectedType = to.selectedType;
		chainSteps = structuredClone(to.chainSteps);
		discoveryActions = structuredClone(to.discoveryActions);
		discoveryInputs = structuredClone(to.discoveryInputs);
		discoveryInputsHaveChanged = false;
		discoveryTargetType = to.discoveryTargetType;
		discoveryResult = null;
		draftReadyToCommit = to.draftReadyToCommit;
		statusMessage = null;
		if (to.step === 'choose-type') typeQuery = '';
		if (to.step === 'discover' && discoveryActions.length) runDiscovery(discoveryActions[0].name);
	}

	async function stageDraft(chain: ChainStep[]) {
		if (!draftId) {
			const draft = await unwrap<{ id: string }>(api.POST('/api/manage-instruments/drafts'));
			draftId = draft.id;
		}
		const result = await unwrap<{ tree: TreeItem[]; path: NodePath }>(
			api.PUT('/api/manage-instruments/drafts/{draft_id}', { params: { path: { draft_id: draftId } }, body: { chain } })
		);
		draftTree = result.tree;
		draftPath = result.path;
	}

	async function refetchData() {
		const d = await unwrap<{ tree: TreeItem[]; metadata: Record<string, InstrumentMeta> }>(
			api.GET('/api/manage-instruments')
		);
		tree = d.tree ?? [];
		metadata = d.metadata ?? {};
	}

	async function saveParams(update: ParamUpdate): Promise<ParamResult> {
		const result = await unwrap<ParamResult>(api.POST('/api/manage-instruments/update', { body: update }));
		await refetchData();
		statusMessage = { text: 'Instrument parameters saved.', ok: true };
		return result;
	}

	// Reset / Remove actions
	function onReset(node: TreeNodeItem, path: NodePath) {
		confirmPath = path;
		confirmAction = 'reset';
		confirmTarget = node;
	}

	// Rules that reference the instrument being removed. A rule left pointing at
	// a vanished attribute fails closed — it denies everything it covered — so
	// removing one instrument can make a different one un-callable.
	type RemovalImpact = {
		attributes: string[];
		// Projects that name an instrument under this node and resolve it at run time.
		projects?: { name: string; path: string; attributes: string[] }[];
		rules: {
			id: string;
			description: string;
			referenced_in_condition: string[];
			blocks_methods: string[];
		}[];
	};
	let removalImpact: RemovalImpact | null = $state(null);
	let impactLoading = $state(false);

	function onRemove(node: TreeNodeItem, path: NodePath) {
		confirmPath = path;
		confirmAction = 'remove';
		confirmTarget = node;
		removalImpact = null;
		impactLoading = true;
		unwrap<RemovalImpact>(
			api.POST('/api/manage-instruments/removal-impact', { body: { type: node.type, key: node.key, path } })
		)
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
			const body = { type: confirmTarget.type, key: confirmTarget.key, path: confirmPath };
			if (confirmAction === 'reset') await api.POST('/api/manage-instruments/reset', { body });
			else await api.POST('/api/manage-instruments/remove', { body });
			statusMessage = {
				text: `${confirmAction === 'reset' ? 'Reset' : 'Removed'} ${confirmTarget.type} (${confirmTarget.key})`,
				ok: true
			};
			await refetchData();
		} catch (e) {
			statusMessage = { text: errorMessage(e) || 'Operation failed', ok: false };
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

	const allTypes = $derived(
		Object.values(metadata).filter((m) => !addParent || m.parent_type === addParent.node.type)
	);

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

	function startAddWizard(parent: { node: TreeItem; path: NodePath } | null = null) {
		addParent = parent;
		tempKey = '';
		showAddWizard = true;
		visits = [];
		addStep = 'choose-type';
		typeQuery = '';
		selectedType = null;
		chainSteps = [];
		currentChainIndex = 0;
		statusMessage = null;
		discoveryActions = [];
		discoveryInputs = {};
		discoveryResult = null;
		discoveryTargetType = null;
		draftId = null;
		draftTree = null;
		draftPath = [];
		draftReadyToCommit = false;
	}

	async function stageResolvedParent(stepIndex: number) {
		await stageDraft(chainSteps.slice(stepIndex));
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

		if (addParent) {
			chainSteps = [
				{ type: typeStr, key: '', action: 'create_new', resolved: false },
				...existingParentChain(addParent.path)
			];
			currentChainIndex = 1;
			advanceChain();
			return;
		}

		const chain = meta.parent_chain;
		if (chain.length === 0) {
			chainSteps = [{ type: typeStr, key: '', action: 'create_new', resolved: false }];
			if (discoveryActions.length > 0) {
				// Has discovery support — show discovery step
				addStep = 'discover';
				// Auto-run discovery immediately (no need to wait for user)
				if (discoveryActions.length > 0) {
					runDiscovery(discoveryActions[0].name);
				}
			} else if (meta.key_hint) {
				addStep = 'leaf-key';
			} else {
				chainSteps[0].key = typeStr;
				addStep = 'confirm';
			}
		} else {
			// Build chain bottom-up: leaf first, then parents
			chainSteps = [
				{ type: typeStr, key: '', action: 'create_new', resolved: false },
				...chain.map((pt) => ({
					type: pt,
					key: '',
					action: 'use_existing' as const,
					resolved: false
				}))
			];
			currentChainIndex = chain.length;
			addStep = 'choose-parent';
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

	function selectExistingParent(key: string) {
		// Ignore a stale click from a previous step rather than assigning its
		// controller key to a mainframe or module.
		if (!currentExisting.some((candidate) => candidate.key === key)) return;
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
			addStep = 'discover';
			runDiscovery(parentDiscovery[0].name);
		} else {
			addStep = 'parent-key'; // manual key entry for new parent
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
			await stageResolvedParent(stepIndex);
			advanceChain();
			return true;
		} catch (e) {
			// Keep the entry screen and its value visible so a failed server edit
			// cannot look like the button simply did nothing.
			step.key = '';
			step.resolved = false;
			statusMessage = { text: errorMessage(e) || `Could not add ${step.type}`, ok: false };
			return false;
		} finally {
			addLoading = false;
		}
	}

	function advanceChain() {
		// Results belong to one step; a parent's discovered children must never
		// be applied to the leaf when its address is entered manually.
		discoveryResult = null;
		discoveryTargetType = null;
		discoveryActions = [];
		currentChainIndex--;
		if (currentChainIndex < 0) {
			// all parents resolved, but we still need the leaf key if it's a child
			addStep = 'leaf-key';
			return;
		}
		if (currentChainIndex === 0) {
			// We've resolved all parents and are now at the leaf (index 0).
			const leafType = chainSteps[0].type;
			const leafMeta = metadata[leafType];
			const leafDiscovery = leafMeta?.discovery_actions ?? [];

			if (leafDiscovery.length > 0) {
				// Leaf has discovery — scan before choosing an address
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

				addStep = 'discover';
				runDiscovery(leafDiscovery[0].name);
			} else {
				addStep = 'leaf-key'; // Manual key entry
			}
		} else {
			addStep = 'choose-parent'; // next parent in chain
		}
	}

	const isParentDiscovery = $derived(
		discoveryTargetType !== null && discoveryTargetType !== selectedType
	);

	async function resolveDiscoverySelection(key: string) {
		if (addLoading || discoveryLoading) return;
		if (isParentDiscovery) {
			if (discoveryResult?.result_type === 'children') {
				chainSteps[currentChainIndex].children = discoveryResult.children;
			}
			await confirmNewParentKey(key);
		} else {
			chainSteps[0].key = key;
			await executeAdd();
		}
	}

	function setLeafKey(key: string) {
		chainSteps[0].key = key;
		addStep = 'confirm'; // confirm
	}

	function submitLeafKey() {
		if (!tempKey.trim()) return;
		setLeafKey(tempKey.trim());
		tempKey = '';
	}

	async function submitParentKey() {
		if (!tempKey.trim() || addLoading) return;
		if (await confirmNewParentKey(tempKey.trim())) tempKey = '';
	}

	async function runDiscovery(actionName: string) {
		const targetType = discoveryTargetType ?? selectedType;
		if (!targetType) return;
		discoveryLoading = true;
		discoveryResult = null;
		statusMessage = null;
		try {
			const targetIndex = chainSteps.findIndex((s) => s.type === targetType);
			const ancestors = chainSteps.slice(targetIndex + 1);
			if (ancestors.length) await stageDraft(ancestors);
			const response = await unwrap<DiscoveryResult>(
				api.POST('/api/manage-instruments/discover', {
					body: {
						type: targetType,
						action: actionName,
						params: discoveryInputs,
						...(ancestors.length ? { draft_id: draftId } : {})
					}
				})
			);
			discoveryResult = response;
			discoveryInputsHaveChanged = false;
		} catch (e) {
			statusMessage = { text: `Discovery failed: ${errorMessage(e)}`, ok: false };
		} finally {
			discoveryLoading = false;
		}
	}

	async function executeAdd() {
		if (addLoading || discoveryLoading) return;
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

			if (!draftReadyToCommit) {
				if (discoveryResult?.result_type === 'children') {
					chainSteps[0].children = discoveryResult.children;
				}
				await stageDraft(chainSteps);
				draftReadyToCommit = true;
			}
			await api.POST('/api/manage-instruments/drafts/{draft_id}/commit', { params: { path: { draft_id: draftId! } } });

			statusMessage = { text: `Added ${selectedType}`, ok: true };
			draftReadyToCommit = true;
			await refetchData();
			showAddWizard = false;
		} catch (e) {
			statusMessage = { text: errorMessage(e) || 'Add failed', ok: false };
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
	const currentExisting = $derived(
		currentStepType
			? parentCandidates(draftTree ?? tree, currentStepType,
				chainSteps.slice(currentChainIndex + 1).reverse().map((step) => ({
					type: step.type,
					key: step.action === 'create_new'
						? (draftPath.find((p) => p.type === step.type)?.key ?? step.key) : step.key
				})))
					.filter((node) => node.fields?.enabled !== false)
					.map((node) => ({ key: node.key, node }))
			: []
	);

	async function cancelAddWizard() {
		if (addLoading || discoveryLoading) return;
		addLoading = true;
		try {
			if (draftId) await api.DELETE('/api/manage-instruments/drafts/{draft_id}', { params: { path: { draft_id: draftId } } });
			draftId = null;
			showAddWizard = false;
		} catch (e) {
			statusMessage = { text: errorMessage(e) || 'Could not close the draft. Please retry.', ok: false };
		} finally {
			addLoading = false;
		}
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
			class="rounded-lg px-3 py-2 text-body {statusMessage.ok
				? 'bg-ok-wash text-ok'
				: 'bg-crit-wash text-crit'}"
		>
			{statusMessage.text}
		</div>
	{/if}

	<InstrumentWorkbench
		{tree}
		{metadata}
		{transportBadge}
		bind:dirty
		bind:busy
		onadd={() => startAddWizard()}
		onaddparent={(node, path) => startAddWizard({ node, path })}
		onreset={onReset}
		onremove={onRemove}
		onsave={saveParams}
		onrefresh={refetchData}
	/>
</section>

<!-- Add wizard. In a dialog rather than inline: the tree above is routinely
     several screens tall, and a multi-step form that begins below it means
     scrolling to find the step you are already on. -->
{#if showAddWizard}
	<Modal
		title={addParent ? `Add under ${addParent.node.type}` : 'Add instrument'}
		subtitle={addStep === 'choose-type'
			? addParent
				? `${nodeAddress(addParent.node)} · Choose a compatible module. Its parent is already selected.`
				: 'Pick what to add. Modules list the parent they attach to.'
			: (selectedType ?? undefined)}
		onclose={cancelAddWizard}
		width={addStep === 'choose-type' ? 'max-w-3xl' : 'max-w-2xl'}
	>
		{#if statusMessage && !statusMessage.ok}
			<Callout tone="crit">{statusMessage.text}</Callout>
		{/if}
		<!-- Dependency chain — only meaningful once a type with parents is chosen. -->
		{#if chainSteps.length > 1}
			<div class="mb-4 flex items-center gap-2 overflow-x-auto pb-1">
				{#each [...chainSteps].reverse() as step, i (step.type + i)}
					{@const isLast = i === chainSteps.length - 1}
					<div class="flex items-center gap-2">
						<div
							class="shrink-0 rounded border px-2.5 py-1.5 text-fine transition-colors
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

		<!-- Choose type: pick a type.
		     Filtered rather than paged, and grouped by where a type can attach —
		     a rack you add on its own, a module you add under one. That grouping
		     is the same order you would build a chain in. -->
		{#if addStep === 'choose-type'}
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
						<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
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
										<span class="block truncate text-body font-semibold">{m.type}</span>
										<span class="mono block truncate text-fine text-muted">{m.class_name}</span>
									</span>
									{#if count > 0}
										<span class="shrink-0 text-2xs text-muted">{count} configured</span>
									{/if}
								</button>
							{/each}
						</div>
					</div>
				{/if}

				{#each Object.entries(parentGroups) as [parentType, children] (parentType)}
					<div>
						<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
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
										<span class="block truncate text-body font-semibold">{m.type}</span>
										<span class="mono block truncate text-fine text-muted">{m.class_name}</span>
									</span>
									{#if count > 0}
										<span class="shrink-0 text-2xs text-muted">{count} configured</span>
									{/if}
								</button>
							{/each}
						</div>
					</div>
				{/each}
			</div>
		{/if}

			<!-- Step 1: Parent selection (use existing or create new) -->
			{#if addStep === 'choose-parent' && currentStepType}
				<p class="mb-3 text-body text-ink-2">
					Choose the <span class="mono font-semibold">{currentStepType}</span> for this
					<span class="mono font-semibold">{chainSteps[0].type}</span> instrument chain.
				</p>

				{#if currentExisting.length > 0}
					<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
						Use existing
					</p>
					<div class="mb-3 space-y-1.5">
						{#each currentExisting as inst (inst.key)}
							<button
								class="flex w-full items-center gap-2 rounded border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-accent-wash"
								onclick={() => selectExistingParent(inst.key)}
							>
								<span class="text-body font-semibold">{inst.node.type}</span>
								<span class="text-fine text-muted">{nodeAddress(inst.node)}</span>
								<span class="mono text-fine text-muted">{inst.key}</span>
							</button>
						{/each}
					</div>
				{/if}

				<button
					class="w-full rounded border border-dashed border-line-2 px-2.5 py-2 text-left text-body text-ink-2 transition-colors hover:border-accent hover:bg-accent-wash"
					onclick={selectCreateNewParent}
				>
					+ Create a new {currentStepType}
				</button>
			{/if}

			<!-- Parent address: Key entry for a new parent being created -->
			{#if addStep === 'parent-key' && currentStepType}
				<p class="mb-3 text-body text-ink-2">
					<span class="mono font-semibold">{chainSteps[0].type}</span> needs a new
					<span class="mono font-semibold">{currentStepType}</span> above it. Enter its
					{metadata[currentStepType]?.key_hint?.toLowerCase() ?? 'address or slot'}.
				</p>
				<input
					type="text"
					bind:value={tempKey}
					onkeydown={(e) => e.key === 'Enter' && submitParentKey()}
					placeholder={metadata[currentStepType]?.key_hint ?? 'e.g. address or slot'}
					class="lw-input mono"
				/>
				<p class="mt-2 text-fine text-muted">
				This parent stays in your draft. The complete chain is saved when you finish adding the instrument.
				</p>
			{/if}

			<!-- Discover: Discovery — ask the hardware instead of typing an address. -->
			{#if addStep === 'discover' && discoveryActions.length > 0}
				{@const currentAction = discoveryActions[0]}
				<p class="mb-3 text-body text-ink-2">{currentAction.description}</p>

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
								<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
									Found {discoveryResult.children.length} device{discoveryResult.children.length === 1
										? ''
										: 's'}
								</p>
								<ul class="divide-y divide-line rounded border border-line bg-surface">
									{#each discoveryResult.children as child, i (i)}
										<li class="flex flex-wrap items-baseline gap-x-2 px-2.5 py-1.5 text-xs">
											<span class="font-semibold">{child.type}</span>
											{#each Object.entries(child.key_fields) as [k, v] (k)}
												<span class="mono text-muted">{k}: {v}</span>
											{/each}
											{#if child.idn}
												<span class="mono w-full truncate text-fine text-muted">{child.idn}</span>
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
								title="{discoveryResult.warnings.length} module{discoveryResult.warnings.length ===
								1
										? ''
										: 's'} this build cannot drive. "
								>
									They are left out of the config; everything else is added normally.
									<ul class="mono mt-1 space-y-0.5 text-fine">
										{#each discoveryResult.warnings as warning, i (i)}
											<li>{warning}</li>
										{/each}
									</ul>
								</Callout>
							</div>
						{/if}
					{:else if discoveryResult.result_type === 'probe'}
						{#if discoveryResult.found.length > 0}
							<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
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
										<span class="mono text-body font-semibold">{port_entry.port}</span>
										{#if port_entry.description}
											<span class="truncate text-fine text-muted">{port_entry.description}</span>
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
							<p class="mb-1.5 text-2xs font-semibold uppercase tracking-[0.09em] text-muted">
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
											<span class="text-body">
												{k}: <span class="mono font-semibold">{v}</span>
											</span>
										{/each}
										{#if candidate.idn}
											<span class="mono w-full truncate text-fine text-muted">{candidate.idn}</span>
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

			<!-- Leaf address: Key entry for the target leaf/child instrument -->
			{#if addStep === 'leaf-key'}
				<p class="mb-3 text-body text-ink-2">
					Enter the key for the new <span class="mono font-semibold">{chainSteps[0].type}</span>.
				</p>
				<input
					type="text"
					bind:value={tempKey}
					onkeydown={(e) => e.key === 'Enter' && submitLeafKey()}
					placeholder={metadata[chainSteps[0].type]?.key_hint ?? 'e.g. 1, 5'}
					class="lw-input mono"
				/>
			{/if}

			<!-- Confirm: Confirm. The chain is shown root-first, which is the order
			     it will appear in the tree. -->
			{#if addStep === 'confirm'}
				<p class="mb-2 text-body text-ink-2">This is what will be written:</p>
				<ul class="divide-y divide-line rounded border border-line bg-surface">
					{#each [...chainSteps].reverse() as step, i (step.type + i)}
						<li class="flex items-center gap-2 px-2.5 py-2 text-body">
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
			<!-- Same order on every step: Back and any step-specific way out on the
			     left; Cancel and the step's primary action on the right. -->
			{#if visits.length > 1}
				<button class="lw-btn" onclick={back} disabled={addLoading || discoveryLoading}>Back</button>
			{/if}
			{#if addStep === 'discover' && discoveryActions.length > 0}
				<button
					class="lw-btn"
					onclick={() => {
						addStep = isParentDiscovery ? 'parent-key' : 'leaf-key';
						discoveryResult = null;
						statusMessage = null;
					}}
					disabled={discoveryLoading || addLoading}
				>
					Enter it manually
				</button>
			{/if}
			<span class="mr-auto"></span>
			<button class="lw-btn" onclick={cancelAddWizard} disabled={addLoading}>Cancel</button>
			{#if addStep === 'discover' && discoveryActions.length > 0}
				{@const currentAction = discoveryActions[0]}
				{#if discoveryInputsHaveChanged || !discoveryResult}
					<button
						class="lw-btn"
						onclick={() => runDiscovery(currentAction.name)}
						disabled={discoveryLoading || addLoading}
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
			{:else if addStep === 'parent-key'}
				<button
					class="lw-btn lw-btn-primary"
					onclick={submitParentKey}
					disabled={!tempKey.trim() || addLoading}
				>
					{addLoading ? 'Checking…' : 'Next'}
				</button>
			{:else if addStep === 'leaf-key'}
				<button class="lw-btn lw-btn-primary" onclick={submitLeafKey} disabled={!tempKey.trim()}>
					Next
				</button>
			{:else if addStep === 'confirm'}
				<button class="lw-btn lw-btn-primary" onclick={executeAdd} disabled={addLoading}>
					{addLoading ? 'Adding…' : 'Add instrument'}
				</button>
			{/if}
		{/snippet}
	</Modal>
{/if}

<!-- Confirmation Dialog -->
<ConfirmDialog
	open={confirmAction !== null && confirmTarget !== null}
	title={confirmAction === 'reset' ? 'Reset to defaults?' : 'Remove instrument?'}
	confirmLabel={confirmAction === 'reset' ? 'Reset' : 'Remove'}
	tone={confirmAction === 'reset' ? 'primary' : 'danger'}
	busy={actionLoading}
	onconfirm={executeConfirm}
	oncancel={cancelConfirm}
>
	{#if confirmAction === 'reset'}
		This will reset <strong>{confirmTarget?.type}</strong> ({confirmTarget?.key}) to factory defaults.
		Children will be preserved.
	{:else}
		This will permanently remove <strong>{confirmTarget?.type}</strong> ({confirmTarget?.key}) and all
		its children from the config.
	{/if}
	{#if confirmAction === 'remove'}
		{#if impactLoading}
			<p class="mt-3 text-xs text-muted">Checking permission rules and projects…</p>
		{/if}
		{#if !impactLoading && removalImpact && (removalImpact.projects ?? []).length > 0}
			<div class="mt-3 rounded-md border border-crit/30 bg-crit-wash p-2.5 text-xs">
				<div class="font-medium text-crit">
					{removalImpact.projects!.length} project(s) use this instrument
				</div>
				<ul class="mt-1 space-y-0.5 text-crit">
					{#each removalImpact.projects! as project (project.path)}
						<li>
							<span class="font-mono">{project.name}</span>
							— <span class="font-mono">{project.attributes.join(', ')}</span>
						</li>
					{/each}
				</ul>
				<div class="mt-1.5 text-crit">
					A project finds its instruments by name when it runs, so these will stop at startup
					until the instrument is added back under the same name, or they are regenerated.
				</div>
			</div>
		{/if}
		{#if !impactLoading && removalImpact && removalImpact.rules.length > 0}
			<div class="mt-3 rounded-md border border-warn/30 bg-warn-wash p-2.5 text-xs">
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
					A rule whose instrument no longer exists fails closed: it denies every call it covers,
					which may block instruments you did not remove. Edit these rules on
					<a class="underline" href="/servers/permissions">Server &amp; Permissions</a> first.
				</div>
			</div>
		{/if}
	{/if}
</ConfirmDialog>
