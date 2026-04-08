<script lang="ts">
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type { TreeItem as TreeNodeItem } from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { fetchWithConfig } from '../../api';
	import { Plus, ArrowLeft } from 'phosphor-svelte';
	import type { TreeItem, InstrumentMeta } from './+page.ts';

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
	let chainSteps: { type: string; key: string; action: 'create_new' | 'use_existing'; extra?: Record<string, any> }[] = $state(
		[]
	);
	let currentChainIndex = $state(0);
	let addLoading = $state(false);

	// DBay-specific state
	let dbayIpAddress = $state('127.0.0.1');
	let dbayPort = $state(8345);
	let dbayMode: 'gui' | 'direct' = $state('gui');
	let dbayProbeResult: { reachable: boolean; modules: { slot: number; type: string }[] } | null =
		$state(null);
	let dbayProbing = $state(false);

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
		dbayIpAddress = '127.0.0.1';
		dbayPort = 8345;
		dbayMode = 'gui';
		dbayProbeResult = null;
	}

	function selectTypeForAdd(typeStr: string) {
		selectedType = typeStr;
		const meta = metadata[typeStr];
		if (!meta) return;

		const chain = meta.parent_chain;
		if (chain.length === 0) {
			chainSteps = [{ type: typeStr, key: '', action: 'create_new' }];
			if (typeStr === 'dbay') {
				// DBay gets a custom config step with ip/port/mode and auto-probe
				addStep = 20;
				probeDbay();
			} else if (meta.key_hint) {
				// USBLike / IPLike: must ask for the actual address
				addStep = 2;
			} else {
				// No key policy: use type string as a stable key
				chainSteps[0].key = typeStr;
				addStep = 3;
			}
		} else {
			// Build chain bottom-up: leaf first, then parents
			chainSteps = [
				{ type: typeStr, key: '', action: 'create_new' },
				...chain.map((pt) => ({ type: pt, key: '', action: 'use_existing' as const }))
			];
			currentChainIndex = chain.length; // start from the root (last in chainSteps)
			addStep = 1; // parent selection step
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
		chainSteps[currentChainIndex].action = 'use_existing';
		chainSteps[currentChainIndex].key = key;
		advanceChain();
	}

	function selectCreateNewParent() {
		chainSteps[currentChainIndex].action = 'create_new';
		addStep = 10; // key entry for new parent
	}

	function confirmNewParentKey(key: string) {
		chainSteps[currentChainIndex].key = key;
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
			// This is the leaf — needs a key
			addStep = 2;
		} else {
			addStep = 1; // next parent in chain
		}
	}

	function setLeafKey(key: string) {
		chainSteps[0].key = key;
		addStep = 3; // confirm
	}

	async function probeDbay() {
		dbayProbing = true;
		dbayProbeResult = null;
		try {
			const r = await fetchWithConfig<{
				reachable: boolean;
				modules: { slot: number; type: string }[];
			}>(
				`/api/manage-instruments/probe-dbay?ip_address=${dbayIpAddress}&ip_port=${dbayPort}`,
				'GET'
			);
			dbayProbeResult = r;
		} catch {
			dbayProbeResult = { reachable: false, modules: [] };
		} finally {
			dbayProbing = false;
		}
	}

	function confirmDbayConfig() {
		chainSteps[0].key = `${dbayIpAddress}:${dbayPort}`;
		chainSteps[0].extra = { mode: dbayMode };
		addStep = 3;
	}

	async function executeAdd() {
		addLoading = true;
		statusMessage = null;
		try {
			await fetchWithConfig('/api/manage-instruments/add', 'POST', { chain: chainSteps });
			if (selectedType === 'dbay' && dbayMode === 'gui') {
				await fetchWithConfig('/api/manage-instruments/sync-dbay', 'POST', {
					ip_address: dbayIpAddress,
					ip_port: dbayPort
				});
			}
			statusMessage = { text: `Added ${selectedType}`, ok: true };
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

	// Temp key input
	let tempKey = $state('');
</script>

<section class="space-y-4">
	<div class="flex items-center gap-3">
		<a
			href="/"
			class="rounded p-1 text-gray-500 hover:bg-gray-200 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200"
		>
			<ArrowLeft size={20} />
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
			<Plus size={16} />
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
					onclick={() => (showAddWizard = false)}>Cancel</button
				>
			</div>

			<!-- Step 0: Select instrument type -->
			{#if addStep === 0}
				<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">
					Select the instrument type to add:
				</p>

				{#if topLevelTypes.length > 0}
					<h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
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
						<h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
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
					<h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
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

		<!-- Step 20: DBay-specific config (ip, port, mode, probe) -->
		{#if addStep === 20}
			<p class="mb-3 text-sm text-gray-600 dark:text-gray-300">Configure DBay connection:</p>
			<div class="mb-2 flex gap-2">
				<input
					type="text"
					bind:value={dbayIpAddress}
					placeholder="127.0.0.1"
					class="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
				/>
				<input
					type="number"
					bind:value={dbayPort}
					placeholder="8345"
					class="w-24 rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
				/>
				<button
					class="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
					onclick={probeDbay}
					disabled={dbayProbing}
				>
					{dbayProbing ? 'Checking...' : 'Test'}
				</button>
			</div>

			{#if dbayProbeResult}
				{#if dbayProbeResult.reachable}
					<div class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm dark:bg-green-900/20">
						<p class="font-medium text-green-700 dark:text-green-400">
							Server found — {dbayProbeResult.modules.length} module{dbayProbeResult.modules.length === 1 ? '' : 's'} loaded
						</p>
						<div class="mt-1 flex flex-wrap gap-1">
							{#each dbayProbeResult.modules as m}
								<span
									class="rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-300"
									>{m.type} @ slot {m.slot}</span
								>
							{/each}
						</div>
					</div>
				{:else}
					<p class="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:bg-amber-900/20 dark:text-amber-400">
						No server found at this address
					</p>
				{/if}
			{/if}

			<label class="mb-4 flex cursor-pointer items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
				<input
					type="checkbox"
					checked={dbayMode === 'gui'}
					onchange={(e) => (dbayMode = (e.target as HTMLInputElement).checked ? 'gui' : 'direct')}
					class="h-4 w-4 rounded border-gray-300"
				/>
				GUI mode — connect to running DBay GUI server and sync modules
			</label>

			<button
				class="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500"
				onclick={confirmDbayConfig}
			>
				Next
			</button>
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
						onclick={() => (showAddWizard = false)}>Cancel</button
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
					{actionLoading
						? 'Working...'
						: confirmAction === 'reset'
							? 'Reset'
							: 'Remove'}
				</button>
			</div>
		</div>
	</div>
{/if}
