<script lang="ts">
	import { onMount } from 'svelte';
	import TreeNode from '$lib/components/TreeNode.svelte';
	import type {
		TreeItem as TreeNodeItem,
		TransportBadge
	} from '$lib/components/TreeNode.svelte';
	import ScrollArea from '$lib/components/ScrollArea.svelte';
	import { fetchWithConfig } from '../../api';
	import { ArrowClockwise, Trash } from 'phosphor-svelte';

	type LocalServer = {
		pid: number;
		workspace_path: string;
		config_dir: string;
		endpoints: string[];
	};
	type RootTransport = {
		transport_sharing: 'exclusive' | 'shared';
		state_authority: 'inferred' | 'subscribed';
	};
	type RemoteTree = {
		tree: TreeNodeItem[];
		roots: Record<string, RootTransport>;
		held_roots: string[];
		config_dir: string;
		metadata: Record<string, any>;
		events: { ts: number; kind: string; message: string; actor: string }[];
		config_status: { diverged?: boolean };
	};

	let servers: LocalServer[] = $state([]);
	let selected: LocalServer | null = $state(null);
	let data: RemoteTree | null = $state(null);
	let loading = $state(false);
	let message: { text: string; ok: boolean } | null = $state(null);
	let confirmTarget: TreeNodeItem | null = $state(null);

	onMount(loadServers);

	async function loadServers() {
		try {
			const res = await fetchWithConfig<{ servers: LocalServer[] }>(
				'/api/local-servers',
				'GET'
			);
			servers = res.servers;
			if (servers.length === 1) await select(servers[0]);
		} catch (e) {
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		}
	}

	async function select(server: LocalServer) {
		selected = server;
		await loadTree();
	}

	async function loadTree() {
		if (!selected) return;
		loading = true;
		message = null;
		try {
			data = await fetchWithConfig<RemoteTree>('/api/remote-tree', 'POST', {
				config_dir: selected.config_dir
			});
		} catch (e) {
			data = null;
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		} finally {
			loading = false;
		}
	}

	async function edit(operation: string, payload: Record<string, any>) {
		if (!selected) return;
		loading = true;
		message = null;
		try {
			await fetchWithConfig('/api/remote-tree/edit', 'POST', {
				config_dir: selected.config_dir,
				operation,
				payload
			});
			message = { text: `Applied ${operation}.`, ok: true };
			await loadTree();
		} catch (e) {
			// The server refuses a remote peer, or a rack that is currently open.
			// Both are its decision, so its wording is what the user sees.
			message = { text: e instanceof Error ? e.message : String(e), ok: false };
		} finally {
			loading = false;
			confirmTarget = null;
		}
	}

	function transportBadge(node: TreeNodeItem): TransportBadge | null {
		const info = data?.roots?.[`inst://${node.key}`];
		if (!info) return null;
		return {
			transport_sharing: info.transport_sharing,
			state_authority: info.state_authority,
			held_by_server: (data?.held_roots ?? []).includes(`inst://${node.key}`)
		};
	}

	function workspaceName(path: string): string {
		const parts = path.replace(/\/$/, '').split('/');
		return parts[parts.length - 1] || path;
	}

	function when(ts: number): string {
		return new Date(ts * 1000).toLocaleTimeString();
	}
</script>

<section class="space-y-4">
	<div class="flex items-start justify-between">
		<div>
			<h1 class="text-2xl font-semibold">Another Workspace's Instruments</h1>
			<p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
				Servers on this machine expose their instrument tree. Because they are local, you can also
				edit them — the tree and its schema come from that server, never from this workspace.
			</p>
		</div>
		<button
			class="flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-800"
			onclick={loadTree}
			disabled={loading || !selected}
		>
			<ArrowClockwise size={14} />
			Refresh
		</button>
	</div>

	{#if message}
		<div
			class="rounded-md border p-3 text-sm {message.ok
				? 'border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300'
				: 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300'}"
		>
			{message.text}
		</div>
	{/if}

	{#if servers.length === 0}
		<p class="text-sm text-gray-500 dark:text-gray-400">
			No instrument servers running on this machine.
			<a class="text-indigo-600 hover:underline" href="/hardware_status">Hardware &amp; Servers →</a>
		</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			{#each servers as s (s.pid)}
				<button
					class="rounded-md border px-3 py-1.5 text-sm {selected?.pid === s.pid
						? 'border-indigo-400 bg-indigo-50 text-indigo-900 dark:border-indigo-600 dark:bg-indigo-950/30 dark:text-indigo-300'
						: 'border-gray-300 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-800'}"
					onclick={() => select(s)}
				>
					{workspaceName(s.workspace_path)}
					<span class="ml-1 text-xs text-gray-500">pid {s.pid}</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if data}
		{#if data.config_status?.diverged}
			<div
				class="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
			>
				That server's config on disk no longer matches what it loaded — someone edited the files
				directly. It needs a restart to pick the change up.
			</div>
		{/if}

		<div class="grid gap-4 lg:grid-cols-3">
			<div class="lg:col-span-2">
				<h2 class="mb-2 text-sm font-medium">Instrument tree</h2>
				<ScrollArea
					type="hover"
					class="relative overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
					orientation="vertical"
					viewportClasses="h-full max-h-[28rem] w-full"
				>
					<div class="p-2">
						{#if data.tree.length === 0}
							<p class="px-2 py-3 text-sm text-gray-500">No instruments configured there.</p>
						{:else}
							{#each data.tree as node (node.key)}
								<TreeNode
									{node}
									{transportBadge}
									onRemove={(n) => (confirmTarget = n)}
								/>
							{/each}
						{/if}
					</div>
				</ScrollArea>
			</div>

			<div>
				<h2 class="mb-2 text-sm font-medium">Recent activity</h2>
				<div
					class="max-h-[28rem] space-y-1.5 overflow-y-auto rounded-lg border border-gray-200 p-2 text-xs dark:border-gray-700"
				>
					{#if data.events.length === 0}
						<p class="px-1 py-2 text-gray-500">Nothing recorded yet.</p>
					{:else}
						{#each data.events as e (e.ts + e.kind)}
							<div class="border-b border-gray-100 pb-1.5 last:border-0 dark:border-gray-800">
								<div class="text-gray-800 dark:text-gray-200">{e.message}</div>
								<div class="text-[10px] text-gray-500">
									{when(e.ts)} · {e.actor}
								</div>
							</div>
						{/each}
					{/if}
				</div>
			</div>
		</div>
	{/if}
</section>

{#if confirmTarget}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
		<div class="w-full max-w-md rounded-lg bg-white p-5 shadow-xl dark:bg-gray-900">
			<h3 class="text-lg font-semibold">Remove from that workspace?</h3>
			<p class="mt-2 text-sm text-gray-600 dark:text-gray-300">
				This removes <strong>{confirmTarget.type}</strong> ({confirmTarget.key}) and its children
				from the other workspace's config — not this one.
			</p>
			<div class="mt-4 flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
					onclick={() => (confirmTarget = null)}
					disabled={loading}>Cancel</button
				>
				<button
					class="flex items-center gap-1 rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
					onclick={() => edit('remove', { type: confirmTarget!.type, key: confirmTarget!.key })}
					disabled={loading}
				>
					<Trash size={14} />
					Remove
				</button>
			</div>
		</div>
	</div>
{/if}
