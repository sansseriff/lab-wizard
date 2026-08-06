<script lang="ts">
	/** This workspace's instrument server: whether it exists, and whether it runs.
	 *
	 * Split out of the old "Server & Permissions" page. Lifecycle and rules were
	 * sharing a screen because they share a file (`server.yaml`), which is a
	 * reason for them to link to each other, not to be the same page: one is
	 * pressed at the start and end of a session, the other is edited rarely and
	 * fails closed when wrong.
	 *
	 * Three states, and they are not a spectrum. Without `server.yaml` this
	 * workspace is a *client* — that is a complete, deliberate configuration, and
	 * the fix is Configure, not Start.
	 */
	import { fetchWithConfig } from '$lib/api';
	import Pill from '$lib/components/Pill.svelte';
	import { workstation, type ServerStatus } from '$lib/stores/workstation.svelte';

	let { data } = $props();

	let serverStatus: ServerStatus | null = $state(data.serverStatus ?? null);
	let serverBusy = $state(false);
	let serverError: string | null = $state(null);
	/** Whether a started server should outlive the wizard (run as a daemon). */
	let keepAsDaemon = $state(data.serverStatus?.detached ?? false);

	let bindDraft = $state(
		data.serverStatus?.has_config ? (data.serverStatus?.bind ?? '') : data.suggestedBind
	);
	let editingBind = $state(false);

	const configured = $derived(serverStatus?.has_config ?? false);
	const running = $derived(serverStatus?.running ?? false);

	/** Keep the chrome's copy in step: the topbar pill reads the same fact. */
	async function apply(next: ServerStatus) {
		serverStatus = next;
		await workstation.refresh();
	}

	async function serverAction(path: string, body?: Record<string, any> | null) {
		serverBusy = true;
		serverError = null;
		try {
			await apply(await fetchWithConfig<ServerStatus>(path, 'POST', body ?? null));
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Server action failed.';
		} finally {
			serverBusy = false;
		}
	}

	const startServer = () => serverAction('/api/server/start', { detached: keepAsDaemon });
	const stopServer = () => serverAction('/api/server/stop');
	const restartServer = () => serverAction('/api/server/restart', { detached: keepAsDaemon });

	/** Become a hardware host, serving this machine over ipc with no bind.
	 *
	 * No address is chosen here. A workspace that only needs to own its own
	 * hardware never needs a port, and asking for one up front implies a
	 * networking decision that most workstations never have to make.
	 */
	async function enableHosting() {
		serverBusy = true;
		serverError = null;
		try {
			await apply(await fetchWithConfig<ServerStatus>('/api/server/enable-hosting', 'POST', null));
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Could not enable hosting.';
		} finally {
			serverBusy = false;
		}
	}

	async function findFreePort() {
		try {
			const res = await fetchWithConfig<{ bind: string }>('/api/server/suggest-port', 'GET');
			bindDraft = res.bind;
			editingBind = true;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Could not find a free port.';
		}
	}

	async function saveBind() {
		serverBusy = true;
		serverError = null;
		try {
			await apply(
				await fetchWithConfig<ServerStatus>('/api/server/bind', 'PUT', { bind: bindDraft.trim() })
			);
			editingBind = false;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Could not save bind.';
		} finally {
			serverBusy = false;
		}
	}
</script>

<section class="space-y-4">
	<div>
		<h1 class="text-[22px] font-semibold tracking-tight">This workspace's server</h1>
		<p class="mt-1 max-w-[64ch] text-[13px] text-muted">
			The process that owns this workstation's hardware and enforces its safety rules. Saved to
			<code class="text-[12px]">config/server/server.yaml</code>.
		</p>
	</div>

	{#if serverError}
		<pre
			class="overflow-x-auto rounded border border-crit/30 bg-crit-wash p-2.5 text-xs text-crit">{serverError}</pre>
	{/if}

	<div class="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
		<div class="rounded border border-line bg-surface">
			<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3.5 py-2.5">
				<div class="flex items-center gap-2.5">
					<h2 class="text-[13.5px] font-semibold">Lifecycle</h2>
					{#if !configured}
						<Pill tone="neutral">Not configured</Pill>
					{:else if running}
						<Pill tone="ok" dot>Running</Pill>
					{:else}
						<Pill tone="warn" dot>Stopped</Pill>
					{/if}
				</div>

				{#if configured}
					<div class="flex items-center gap-2">
						{#if running}
							<button
								class="rounded border border-line-2 px-3 py-1.5 text-xs font-medium hover:bg-surface-2 disabled:opacity-50"
								onclick={restartServer}
								disabled={serverBusy}
								title="Stop and start, which is what applies edited rules"
							>
								{serverBusy ? '…' : 'Restart'}
							</button>
							<button
								class="rounded bg-crit px-3 py-1.5 text-xs font-medium text-white hover:brightness-110 disabled:opacity-50"
								onclick={stopServer}
								disabled={serverBusy}
							>
								Stop
							</button>
						{:else}
							<button
								class="rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
								onclick={startServer}
								disabled={serverBusy}
							>
								{serverBusy ? 'Starting…' : 'Start server'}
							</button>
						{/if}
					</div>
				{/if}
			</div>

			<div class="space-y-3 p-3.5">
				{#if !configured}
					<!-- Creating the config is the opt-in to being a host. A workspace
					     without one is a client, which is what keeps a cloned workspace
					     from racing the real host for the same instruments. -->
					<p class="text-[12.5px] text-ink-2">
						This workspace has no server config, so it acts as a <strong>client</strong> of other
						servers. Enable hosting to make it own its own hardware — it will serve this machine over
						<code>ipc://</code>, with no port to choose.
					</p>
					<button
						class="rounded bg-accent px-3 py-1.5 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
						onclick={enableHosting}
						disabled={serverBusy}
					>
						{serverBusy ? 'Enabling…' : 'Enable hosting'}
					</button>
				{:else}
					<dl class="grid grid-cols-[auto_1fr] items-baseline gap-x-3.5 gap-y-1 text-[12.5px]">
						<dt class="text-[11.5px] text-muted">Serving</dt>
						<dd>
							this machine over <code>ipc://</code>{#if serverStatus?.bind}, and the network on
								<span class="mono">{serverStatus.bind}</span>{/if}
						</dd>
						{#if running}
							<dt class="text-[11.5px] text-muted">Process</dt>
							<dd>
								pid <span class="mono">{serverStatus?.pid}</span>
								{#if serverStatus?.detached}
									<Pill tone="accent">daemon</Pill>
								{/if}
							</dd>
						{/if}
						<dt class="text-[11.5px] text-muted">Rules</dt>
						<dd>
							{serverStatus?.rule_count ?? 0} loaded
							<a class="ml-1 text-accent hover:underline" href="/servers/permissions">Edit →</a>
						</dd>
					</dl>

					<hr class="border-line" />

					<label class="flex items-center gap-2 text-xs text-ink-2">
						<input type="checkbox" bind:checked={keepAsDaemon} disabled={running} />
						Keep running after the wizard closes (run as a daemon)
					</label>

					<!-- A network bind is optional and additive. Each workstation needs a
					     distinct port, so several servers on one machine cannot collide. -->
					<div class="space-y-1.5">
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<span class="text-muted">Network bind</span>
							<input
								type="text"
								bind:value={bindDraft}
								oninput={() => (editingBind = true)}
								disabled={running}
								placeholder="tcp://0.0.0.0:12300"
								class="mono w-56 rounded border border-line-2 bg-surface px-2 py-1 text-xs disabled:opacity-60"
							/>
							<button
								class="rounded border border-line-2 px-2 py-1 hover:bg-surface-2 disabled:opacity-50"
								onclick={findFreePort}
								disabled={running || serverBusy}
							>
								Find free port
							</button>
							{#if editingBind && !running}
								<button
									class="rounded bg-accent px-2 py-1 font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
									onclick={saveBind}
									disabled={serverBusy}
								>
									Save bind
								</button>
							{/if}
							{#if running}
								<span class="text-muted">(stop the server to change)</span>
							{/if}
						</div>
						<p class="text-[11px] text-muted">
							Optional. Leaving it unset is a complete configuration — the server still serves this
							machine. Add one only when another computer needs to connect, then register that exact
							address in its
							<a class="text-accent hover:underline" href="/servers/remote">Remote servers</a> page.
						</p>
					</div>
				{/if}
			</div>
		</div>

		<div class="rounded border border-line bg-surface">
			<div class="border-b border-line px-3.5 py-2.5">
				<h2 class="text-[13.5px] font-semibold">What running it changes</h2>
			</div>
			<ul class="space-y-2 p-3.5 text-[12.5px] text-ink-2">
				<li class="flex gap-2">
					<span class="text-accent">→</span>
					<span>
						The server, not the wizard, opens every transport — so exactly one process holds each one.
					</span>
				</li>
				<li class="flex gap-2">
					<span class="text-accent">→</span>
					<span>
						Safety rules begin to apply. They are read <strong>at start</strong>, so an edit needs a
						restart.
					</span>
				</li>
				<li class="flex gap-2">
					<span class="text-accent">→</span>
					<span>Discovery scans run in the server process rather than the wizard's.</span>
				</li>
				<li class="flex gap-2">
					<span class="text-accent">→</span>
					<span>Other workspaces on this machine can see and edit this instrument tree.</span>
				</li>
			</ul>
		</div>
	</div>
</section>
