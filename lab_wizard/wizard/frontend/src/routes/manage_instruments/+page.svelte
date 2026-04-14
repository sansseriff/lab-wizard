<script lang="ts">
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type { TreeItem as TreeNodeItem } from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { fetchWithConfig } from '../../api';
	import { PlusIcon, ArrowLeftIcon } from 'phosphor-svelte';
	import type {
		TreeItem,
		InstrumentMeta,
		DiscoveryAction,
		DiscoveryResult,
		ChainStep
	} from './+page.ts';

	let { data } = $props();
	let tree: TreeItem[] = $state(data.tree ?? []);
	let metadata: Record<string, InstrumentMeta> = $state(data.metadata ?? {});

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

	function onRemove(node: TreeNodeItem) {
		confirmAction = 'remove';
		confirmTarget = node;
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

	// Add wizard helpers
	const allTypes = $derived(Object.values(metadata));
	const topLevelTypes = $derived(allTypes.filter((m) => m.is_top_level));
	const childTypes = $derived(allTypes.filter((m) => m.is_child && !m.is_top_level));

	const parentGroups = $derived(() => {
		const groups: Record<string, InstrumentMeta[]> = {};
		for (const m of childTypes) {
			const parent = m.parent_type ?? 'unknown';
			if (!groups[parent]) groups[parent] = [];
			groups[parent].push(m);
		}
		return groups;
	});

	function startAddWizard() {
		showAddWizard = true;
		addStep = 0;
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

	async function confirmNewParentKey(key: string) {
		chainSteps[currentChainIndex].key = key;
		chainSteps[currentChainIndex].resolved = true;
		await saveResolvedParent(currentChainIndex);
		advanceChain();
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
</script>

<section class="space-y-4">
	<div class="flex items-center gap-3">
		<a
			href="/"
			class="rounded p-1 text-gray-500 hover:bg-gray-200 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200"
		>
			<ArrowLeftIcon size={20} />
		</a>
		<h1 class="text-2xl font-semibold">Manage Instruments</h1>
	</div>

	{#if statusMessage}
		<div
			class="rounded-lg px-3 py-2 text-sm {statusMessage.ok
				? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300'
				: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'}"
		>
			{statusMessage.text}
		</div>
	{/if}

	<!-- Configured Instruments Tree -->
	<div>
		<h2 class="mb-2 text-lg font-medium">Configured Instruments</h2>
		<div
			class="rounded-xl border border-gray-200 bg-white/70 p-3 shadow-sm dark:border-white/10 dark:bg-gray-800/70"
		>
			{#if tree.length === 0}
				<p class="px-2 py-3 text-sm text-gray-500 dark:text-gray-400">
					No instruments configured yet.
				</p>
			{:else}
				{#each tree as node}
					<TreeNode {node} {onReset} {onRemove} />
				{/each}
			{/if}
		</div>
	</div>

	<!-- Add button -->
	{#if !showAddWizard}
		<button
			class="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 active:bg-indigo-700"
			onclick={startAddWizard}
		>
			<PlusIcon size={16} />
			Add Instrument
		</button>
	{/if}

	<!-- Add Wizard -->
	{#if showAddWizard}
		<div
			class="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 shadow-sm dark:border-indigo-800/50 dark:bg-indigo-950/20"
		>
			<div class="mb-3 flex items-center justify-between">
				<h2 class="text-lg font-medium">Add Instrument</h2>
				<button
					class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
					onclick={cancelAddWizard}>Cancel</button
				>
			</div>

			<!-- Dependency chain sidebar — only shown for instruments with parent chains -->
			{#if chainSteps.length > 1}
				<div class="mb-4 flex items-center gap-2 overflow-x-auto pb-1">
					{#each [...chainSteps].reverse() as step, i}
						{@const isLast = i === chainSteps.length - 1}
						<div class="flex items-center gap-2">
							<!-- Node box -->
							<div
								class="flex-shrink-0 rounded-lg border-2 px-3 py-2 text-xs font-medium transition-colors
									{step.resolved
										? 'border-green-500 bg-green-50 text-green-800 dark:border-green-400 dark:bg-green-900/30 dark:text-green-300'
										: 'border-dashed border-gray-300 bg-white text-gray-500 dark:border-gray-600 dark:bg-gray-800/50 dark:text-gray-400'}"
							>
								<div class="font-semibold">{step.type}</div>
								{#if step.key}
									<div class="mt-0.5 font-mono text-xs opacity-75">{step.key}</div>
								{/if}
							</div>
							<!-- Arrow connector -->
							{#if !isLast}
								<div class="flex-shrink-0 text-gray-400">→</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			<!-- Step 0: Select instrument type -->
			{#if addStep === 0}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">
					Select the instrument type to add:
				</p>

				{#if topLevelTypes.length > 0}
					<h3 class="mb-1 text-xs font-semibold tracking-wide text-gray-500 uppercase">
						Top-level instruments
					</h3>
					<div class="mb-3 space-y-1">
						{#each topLevelTypes as m}
							<button
								class="w-full rounded-md px-3 py-2 text-left text-sm transition hover:bg-indigo-100 dark:hover:bg-indigo-900/40"
								onclick={() => selectTypeForAdd(m.type)}
							>
								<span class="font-medium">{m.type}</span>
								<span class="ml-2 text-xs text-gray-500">{m.class_name}</span>
							</button>
						{/each}
					</div>
				{/if}

				{#if Object.keys(parentGroups()).length > 0}
					{#each Object.entries(parentGroups()) as [parentType, children]}
						<h3 class="mb-1 text-xs font-semibold tracking-wide text-gray-500 uppercase">
							{parentType} modules
						</h3>
						<div class="mb-3 space-y-1">
							{#each children as m}
								<button
									class="w-full rounded-md px-3 py-2 text-left text-sm transition hover:bg-indigo-100 dark:hover:bg-indigo-900/40"
									onclick={() => selectTypeForAdd(m.type)}
								>
									<span class="font-medium">{m.type}</span>
									<span class="ml-2 text-xs text-gray-500">{m.class_name}</span>
								</button>
							{/each}
						</div>
					{/each}
				{/if}
			{/if}

			<!-- Step 1: Parent selection (use existing or create new) -->
			{#if addStep === 1 && currentStepType}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">
					<span class="font-medium">{chainSteps[0].type}</span> requires a
					<span class="font-medium">{currentStepType}</span> parent:
				</p>

				{#if currentExisting.length > 0}
					<h3 class="mb-1 text-xs font-semibold tracking-wide text-gray-500 uppercase">
						Use existing
					</h3>
					<div class="mb-3 space-y-1">
						{#each currentExisting as inst}
							<button
								class="w-full rounded-md px-3 py-2 text-left text-sm transition hover:bg-indigo-100 dark:hover:bg-indigo-900/40"
								onclick={() => selectExistingParent(inst.key)}
							>
								<span class="font-medium">{inst.node.type}</span>
								<span class="ml-2 text-xs text-gray-500">({inst.key})</span>
							</button>
						{/each}
					</div>
				{/if}

				<button
					class="w-full rounded-md border border-dashed border-gray-300 px-3 py-2 text-left text-sm text-gray-600 transition hover:border-indigo-400 hover:bg-indigo-50 dark:border-gray-600 dark:text-gray-300 dark:hover:border-indigo-500 dark:hover:bg-indigo-950/30"
					onclick={selectCreateNewParent}
				>
					+ Create new {currentStepType}
				</button>
			{/if}

			<!-- Step 10: Key entry for a new parent being created -->
			{#if addStep === 10 && currentStepType}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">
					Enter a key for the new <span class="font-medium">{currentStepType}</span>:
				</p>
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={tempKey}
						placeholder={metadata[currentStepType]?.key_hint ?? 'e.g. address or slot'}
						class="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
					/>
					<button
						class="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
						disabled={!tempKey.trim()}
						onclick={() => {
							confirmNewParentKey(tempKey.trim());
							tempKey = '';
						}}>Next</button
					>
				</div>
			{/if}

			<!-- Step 20: Discovery interface (simplified) -->
			{#if addStep === 20 && discoveryActions.length > 0}
				{@const currentAction = discoveryActions[0]}
				<p class="mb-4 text-sm text-gray-600 dark:text-gray-300">
					<span class="font-medium">{discoveryTargetType ?? selectedType}</span> — {currentAction.description}
				</p>

				<!-- Parent dep note: no manual inputs needed -->
				{#if currentAction.parent_dep}
					{@const parentStep = chainSteps.find(s => s.type === currentAction.parent_dep && s.resolved)}
					{#if parentStep}
						<p class="mb-4 rounded-md bg-blue-50 px-3 py-2 text-sm text-blue-700 dark:bg-blue-900/20 dark:text-blue-400">
							Using connection from parent ({parentStep.type}: {parentStep.key})
						</p>
					{/if}
				{/if}

				<!-- Input fields (only show if action has inputs and no parent_dep) -->
				{#if currentAction.inputs.length > 0 && !currentAction.parent_dep}
					<div class="mb-4 space-y-2 rounded-md bg-gray-50 p-3 dark:bg-gray-900/30">
						{#each currentAction.inputs as inp}
							<label class="block text-xs font-medium text-gray-700 dark:text-gray-300">
								{inp.label}
								{#if inp.type === 'number'}
									<input
										type="number"
										value={discoveryInputs[inp.name] ?? inp.default ?? ''}
										onchange={(e) => {
											const newVal = (e.target as HTMLInputElement).value;
											discoveryInputs[inp.name] = newVal;
											discoveryInputsHaveChanged = true;
										}}
										class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
									/>
								{:else}
									<input
										type="text"
										value={discoveryInputs[inp.name] ?? inp.default ?? ''}
										onchange={(e) => {
											const newVal = (e.target as HTMLInputElement).value;
											discoveryInputs[inp.name] = newVal;
											discoveryInputsHaveChanged = true;
										}}
										class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
									/>
								{/if}
							</label>
						{/each}
					</div>
				{/if}

				<!-- Discovery result -->
				{#if discoveryResult}
					{#if discoveryResult.result_type === 'children'}
						{#if discoveryResult.children.length > 0}
							<div class="mb-4 rounded-md bg-green-50 px-3 py-2 text-sm dark:bg-green-900/20">
								<p class="font-medium text-green-700 dark:text-green-400">
									✓ Found {discoveryResult.children.length} device{discoveryResult.children.length === 1 ? '' : 's'}
								</p>
								<div class="mt-2 space-y-1">
									{#each discoveryResult.children as child}
										<div class="rounded bg-white px-2 py-1 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">
											<span class="font-medium">{child.type}</span>
											{#each Object.entries(child.key_fields) as [k, v]}
												<span class="text-gray-500">— {k}: {v}</span>
											{/each}
											{#if child.idn}
												<span class="block text-xs text-gray-500">{child.idn}</span>
											{/if}
										</div>
									{/each}
								</div>
							</div>
						{:else}
							<p class="mb-4 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:bg-amber-900/20 dark:text-amber-400">
								⚠ No devices found. Check connection and try again.
							</p>
						{/if}
					{:else if discoveryResult.result_type === 'probe'}
						{#if discoveryResult.found.length > 0}
							<div class="mb-4 rounded-md bg-green-50 px-3 py-2 text-sm dark:bg-green-900/20">
								<p class="font-medium text-green-700 dark:text-green-400 mb-2">
									Found {discoveryResult.found.length} controller{discoveryResult.found.length === 1 ? '' : 's'}:
								</p>
								<div class="space-y-1">
									{#each discoveryResult.found as port_entry}
										<button
											class="w-full rounded-md border px-3 py-2 text-left text-sm transition bg-white hover:bg-green-100 border-green-200 dark:bg-gray-800 dark:hover:bg-green-900/40 dark:border-green-800"
											onclick={() => resolveDiscoverySelection(port_entry.port)}
										>
											<span class="font-medium font-mono">{port_entry.port}</span>
											{#if port_entry.description}
												<span class="ml-2 text-xs text-gray-500">{port_entry.description}</span>
											{/if}
										</button>
									{/each}
								</div>
							</div>
						{:else}
							<p class="mb-4 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:bg-amber-900/20 dark:text-amber-400">
								⚠ No Prologix controllers found. Check USB connection.
							</p>
						{/if}
					{:else if discoveryResult.result_type === 'self_candidates'}
						{#if discoveryResult.found.length > 0}
							<div class="mb-4 rounded-md bg-green-50 px-3 py-2 text-sm dark:bg-green-900/20">
								<p class="font-medium text-green-700 dark:text-green-400 mb-2">
									Found {discoveryResult.found.length} candidate{discoveryResult.found.length === 1 ? '' : 's'}:
								</p>
								<div class="space-y-1">
									{#each discoveryResult.found as candidate}
										{@const keyValue = Object.values(candidate.key_fields)[0] ?? ''}
										<button
											class="w-full rounded-md border px-3 py-2 text-left text-sm transition bg-white hover:bg-green-100 border-green-200 dark:bg-gray-800 dark:hover:bg-green-900/40 dark:border-green-800"
											onclick={() => resolveDiscoverySelection(String(keyValue))}
										>
											{#each Object.entries(candidate.key_fields) as [k, v]}
												<span class="font-medium">{k}: <span class="font-mono">{v}</span></span>
											{/each}
											{#if candidate.idn}
												<span class="ml-2 text-xs text-gray-500">{candidate.idn}</span>
											{/if}
										</button>
									{/each}
								</div>
							</div>
						{:else}
							<p class="mb-4 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:bg-amber-900/20 dark:text-amber-400">
								⚠ No instruments found on the bus.
							</p>
						{/if}
					{/if}
				{:else}
					<!-- Loading state -->
					{#if discoveryLoading}
						<p class="mb-4 rounded-md bg-blue-50 px-3 py-2 text-sm text-blue-700 dark:bg-blue-900/20 dark:text-blue-400">
							Scanning...
						</p>
					{/if}
				{/if}

				<!-- Action buttons -->
				<div class="flex gap-2">
					{#if discoveryInputsHaveChanged || !discoveryResult}
						<button
							class="flex-1 rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
							onclick={() => runDiscovery(currentAction.name)}
							disabled={discoveryLoading}
						>
							{discoveryLoading ? 'Scanning...' : 'Re-scan'}
						</button>
					{/if}
					{#if discoveryResult?.result_type === 'children' && discoveryResult.children.length > 0}
						<button
							class="flex-1 rounded-md bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-500 disabled:opacity-50"
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
							{addLoading ? 'Adding...' : isParentDiscovery ? `Confirm ${discoveryTargetType}` : `Add ${selectedType} & Modules`}
						</button>
					{:else if discoveryResult !== null && !discoveryLoading}
						<!-- result came back but no valid selection made — show manual entry -->
						<button
							class="flex-1 rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500"
							onclick={() => (addStep = isParentDiscovery ? 10 : 2)}
						>
							Continue (Manual Entry)
						</button>
					{:else if !discoveryResult && !discoveryLoading}
						<!-- No result yet and not scanning — offer manual fallback -->
						<button
							class="flex-1 rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500"
							onclick={() => (addStep = isParentDiscovery ? 10 : 2)}
						>
							Continue (Manual Entry)
						</button>
					{/if}
				</div>
			{/if}

			<!-- Step 2: Key entry for the target leaf/child instrument -->
			{#if addStep === 2}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">
					Enter a key for the new <span class="font-medium">{chainSteps[0].type}</span>:
				</p>
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={tempKey}
						placeholder={metadata[chainSteps[0].type]?.key_hint ?? 'e.g. 1, 5'}
						class="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
					/>
					<button
						class="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
						disabled={!tempKey.trim()}
						onclick={() => {
							setLeafKey(tempKey.trim());
							tempKey = '';
						}}>Next</button
					>
				</div>
			{/if}

			<!-- Step 3: Confirm -->
			{#if addStep === 3}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">Confirm the following:</p>
				<div class="mb-3 space-y-1 rounded-md bg-white/60 p-3 text-sm dark:bg-gray-800/60">
					{#each [...chainSteps].reverse() as step}
						<div class="flex gap-2">
							<span
								class="rounded px-1.5 py-0.5 text-xs {step.action === 'create_new'
									? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
									: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}"
							>
								{step.action === 'create_new' ? 'NEW' : 'EXISTING'}
							</span>
							<span class="font-medium">{step.type}</span>
							<span class="text-gray-500">(key: {step.key})</span>
						</div>
					{/each}
				</div>
				<div class="flex gap-2">
					<button
						class="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
						onclick={executeAdd}
						disabled={addLoading}
					>
						{addLoading ? 'Adding...' : 'Add'}
					</button>
					<button
						class="rounded-md px-4 py-2 text-sm text-gray-600 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-700"
						onclick={cancelAddWizard}>Cancel</button
					>
				</div>
			{/if}
		</div>
	{/if}
</section>

<!-- Confirmation Dialog -->
{#if confirmAction && confirmTarget}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
		<div
			class="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-5 shadow-lg dark:border-gray-700 dark:bg-gray-900"
		>
			<h3 class="text-lg font-semibold">
				{confirmAction === 'reset' ? 'Reset to defaults?' : 'Remove instrument?'}
			</h3>
			<p class="mt-2 text-sm text-gray-600 dark:text-gray-300">
				{#if confirmAction === 'reset'}
					This will reset <strong>{confirmTarget.type}</strong> ({confirmTarget.key}) to factory
					defaults. Children will be preserved.
				{:else}
					This will permanently remove <strong>{confirmTarget.type}</strong> ({confirmTarget.key})
					and all its children from the config.
				{/if}
			</p>
			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
					onclick={cancelConfirm}
					disabled={actionLoading}>Cancel</button
				>
				<button
					class="rounded-md px-3 py-1.5 text-sm text-white {confirmAction === 'remove'
						? 'bg-red-600 hover:bg-red-500'
						: 'bg-indigo-600 hover:bg-indigo-500'} disabled:opacity-50"
					onclick={executeConfirm}
					disabled={actionLoading}
				>
					{actionLoading ? 'Working...' : confirmAction === 'reset' ? 'Reset' : 'Remove'}
				</button>
			</div>
		</div>
	</div>
{/if}
