<script lang="ts">
	import { fetchWithConfig } from '../../api';
	import { TrashIcon, PlusIcon } from 'phosphor-svelte';
	import type {
		PermInstrument,
		Permissions,
		Rule,
		Condition,
		DenyClause,
		Operator,
		ServerStatus
	} from './+page.ts';

	let { data } = $props();

	let instruments: PermInstrument[] = $state(data.instruments ?? []);
	let rules: Rule[] = $state((data.permissions?.rules ?? []).map((r) => ({ ...r })));
	let stateDefaults: Record<string, Record<string, any>> = $state(
		data.permissions?.state_defaults ?? {}
	);

	let statusMessage: { text: string; ok: boolean } | null = $state(null);
	let saving = $state(false);

	// ---- Server lifecycle ---------------------------------------------------

	let serverStatus: ServerStatus | null = $state(data.serverStatus ?? null);
	let serverBusy = $state(false);
	// Whether a started server should outlive the wizard (run as a daemon).
	let keepAsDaemon = $state(data.serverStatus?.detached ?? false);
	let serverError: string | null = $state(null);

	// Two-stage UI: until server.yaml exists, this workstation is "not configured"
	// and we only show the configure step — no Stopped/Running state, no Start.
	const configured = $derived(serverStatus?.has_config ?? false);

	async function refreshServer() {
		try {
			serverStatus = await fetchWithConfig<ServerStatus>('/api/server/status', 'GET');
		} catch {
			/* leave previous status */
		}
	}

	async function serverAction(path: string, body?: Record<string, any>) {
		serverBusy = true;
		serverError = null;
		try {
			serverStatus = await fetchWithConfig<ServerStatus>(path, 'POST', body ?? null);
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Server action failed.';
		} finally {
			serverBusy = false;
		}
	}

	const startServer = () => serverAction('/api/server/start', { detached: keepAsDaemon });
	const stopServer = () => serverAction('/api/server/stop');
	const restartServer = () => serverAction('/api/server/restart', { detached: keepAsDaemon });

	// Bind address: pre-filled with a known-good suggestion before configuring,
	// or the configured bind afterwards.
	let bindDraft = $state(
		data.serverStatus?.has_config
			? (data.serverStatus?.bind ?? data.suggestedBind)
			: data.suggestedBind
	);
	let editingBind = $state(false);

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
			serverStatus = await fetchWithConfig<ServerStatus>('/api/server/bind', 'PUT', {
				bind: bindDraft.trim()
			});
			editingBind = false;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Could not save bind.';
		} finally {
			serverBusy = false;
		}
	}

	// Stage 1 → Stage 2: writing the bind creates server.yaml, so `has_config`
	// flips true and the control panel appears.
	async function configureServer() {
		serverBusy = true;
		serverError = null;
		try {
			serverStatus = await fetchWithConfig<ServerStatus>('/api/server/bind', 'PUT', {
				bind: bindDraft.trim()
			});
			editingBind = false;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Could not configure server.';
		} finally {
			serverBusy = false;
		}
	}

	// A friendly label + the reference handle to author rules with.
	function instLabel(i: PermInstrument): string {
		const name = i.attribute ?? i.path;
		const ty = i.type_hint ? ` (${i.type_hint})` : '';
		return `${name}${ty}`;
	}
	// Prefer the stable attribute handle; fall back to the raw path.
	function instRef(i: PermInstrument): { attribute?: string; path?: string } {
		return i.attribute ? { attribute: i.attribute } : { path: i.path };
	}
	function findInst(ref: { attribute?: string; path?: string }): PermInstrument | undefined {
		return instruments.find((i) =>
			ref.attribute ? i.attribute === ref.attribute : i.path === ref.path
		);
	}

	// ---- Draft rule being built --------------------------------------------

	type DraftCond = { instKey: string; key: string; op: Operator; value: string };
	type DraftDeny = { instKey: string; methods: Set<string> };

	let draftId = $state('');
	let draftMessage = $state('');
	let draftConds: DraftCond[] = $state([]);
	let draftDeny: DraftDeny[] = $state([]);

	// instKey identifies an instrument in the dropdown: attribute or path.
	function instByKey(key: string): PermInstrument | undefined {
		return instruments.find((i) => (i.attribute ?? i.path) === key);
	}

	function addCondition() {
		draftConds = [...draftConds, { instKey: '', key: '', op: 'greater_than', value: '' }];
	}
	function removeCondition(idx: number) {
		draftConds = draftConds.filter((_, i) => i !== idx);
	}
	function addDeny() {
		draftDeny = [...draftDeny, { instKey: '', methods: new Set<string>() }];
	}
	function removeDeny(idx: number) {
		draftDeny = draftDeny.filter((_, i) => i !== idx);
	}
	function toggleDenyMethod(idx: number, method: string) {
		const d = draftDeny[idx];
		if (d.methods.has(method)) d.methods.delete(method);
		else d.methods.add(method);
		draftDeny = [...draftDeny]; // trigger reactivity
	}

	function coerceValue(op: Operator, raw: string): any {
		if (op === 'in') {
			return raw
				.split(',')
				.map((s) => s.trim())
				.filter(Boolean)
				.map((s) => (isNaN(Number(s)) ? s : Number(s)));
		}
		if (op === 'greater_than' || op === 'less_than') return Number(raw);
		// equals / not_equals: number if it parses, else string, else boolean keywords
		if (raw === 'true') return true;
		if (raw === 'false') return false;
		if (raw !== '' && !isNaN(Number(raw))) return Number(raw);
		return raw;
	}

	function buildCondition(c: DraftCond): Condition {
		const inst = instByKey(c.instKey)!;
		const ref = instRef(inst);
		const leaf: Condition = { ...ref, key: c.key };
		leaf[c.op] = coerceValue(c.op, c.value) as never;
		return leaf;
	}

	function draftValid(): string | null {
		if (!draftId.trim()) return 'Rule needs an id.';
		if (draftConds.length === 0) return 'Add at least one "when" condition.';
		for (const c of draftConds) {
			if (!c.instKey || !c.key) return 'Each condition needs an instrument and a state key.';
		}
		if (draftDeny.length === 0) return 'Add at least one "deny" clause.';
		for (const d of draftDeny) {
			if (!d.instKey || d.methods.size === 0)
				return 'Each deny clause needs an instrument and at least one method.';
		}
		return null;
	}

	function addRule() {
		const err = draftValid();
		if (err) {
			statusMessage = { text: err, ok: false };
			return;
		}
		const when: Condition =
			draftConds.length === 1
				? buildCondition(draftConds[0])
				: { all: draftConds.map(buildCondition) };
		const deny: DenyClause[] = draftDeny.map((d) => {
			const inst = instByKey(d.instKey)!;
			return { ...instRef(inst), methods: Array.from(d.methods) };
		});
		const rule: Rule = {
			id: draftId.trim(),
			message: draftMessage.trim() || undefined,
			when,
			deny
		};
		rules = [...rules, rule];
		// reset draft
		draftId = '';
		draftMessage = '';
		draftConds = [];
		draftDeny = [];
		statusMessage = { text: `Added rule "${rule.id}". Remember to Save.`, ok: true };
	}

	function deleteRule(idx: number) {
		rules = rules.filter((_, i) => i !== idx);
	}

	async function save() {
		saving = true;
		statusMessage = null;
		try {
			const permissions: Permissions = { state_defaults: stateDefaults, rules };
			await fetchWithConfig('/api/permissions', 'PUT', { permissions });
			await refreshServer();
			statusMessage = {
				text: serverStatus?.running
					? 'Saved to server.yaml. Restart the server to apply the new rules.'
					: 'Permissions saved to server.yaml.',
				ok: true
			};
		} catch (e) {
			statusMessage = { text: e instanceof Error ? e.message : 'Save failed.', ok: false };
		} finally {
			saving = false;
		}
	}

	// ---- Read-only rendering helpers for existing rules --------------------

	function summarizeCondition(c: Condition): string {
		if (c.all) return c.all.map(summarizeCondition).join(' AND ');
		if (c.any) return c.any.map(summarizeCondition).join(' OR ');
		if (c.not) return `NOT (${summarizeCondition(c.not)})`;
		const who = c.attribute ?? c.path ?? '?';
		const ops: [Operator, string][] = [
			['equals', '='],
			['not_equals', '≠'],
			['greater_than', '>'],
			['less_than', '<'],
			['in', 'in']
		];
		for (const [op, sym] of ops) {
			if (c[op] !== undefined) return `${who}.${c.key} ${sym} ${JSON.stringify(c[op])}`;
		}
		return `${who}.${c.key}`;
	}
	function summarizeDeny(d: DenyClause): string {
		const who = d.attribute ?? d.path ?? d.path_glob ?? '?';
		return `${who}: ${d.methods.join(', ')}`;
	}

	const selectedCondInst = (c: DraftCond) => instByKey(c.instKey);
	const selectedDenyInst = (d: DraftDeny) => instByKey(d.instKey);
</script>

<section class="space-y-4">
	<div class="flex items-center gap-3">
		<h1 class="text-2xl font-semibold tracking-tight">Server &amp; Permissions</h1>
	</div>
	<p class="text-sm text-gray-600 dark:text-gray-300">
		This workstation's instrument server hosts its local instruments and enforces safety rules over
		them. A rule blocks methods (<strong>deny</strong>) while a state condition holds
		(<strong>when</strong>). Rules are read when the server starts, so
		<strong>restart the server to apply edits</strong>. Saved to
		<code>config/server/server.yaml</code>.
	</p>

	<!-- Server control panel -->
	{#if !configured}
		<!-- Stage 1: not configured yet — only the configure step is shown. -->
		<section
			class="space-y-3 rounded-xl border border-gray-200 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-800/70"
		>
			<div>
				<h2 class="text-base font-medium">Configure this workstation's server</h2>
				<p class="text-xs text-gray-500 dark:text-gray-400">
					Pick the address other machines will connect to. The default port is offered when free;
					otherwise a free one is chosen for you. You can change it later.
				</p>
			</div>
			<div class="flex flex-wrap items-center gap-2 text-sm">
				<span class="text-xs text-gray-600 dark:text-gray-300">Bind address</span>
				<input
					type="text"
					bind:value={bindDraft}
					class="w-56 rounded-md border border-gray-300 px-2 py-1 font-mono text-xs dark:border-gray-600 dark:bg-gray-900"
				/>
				<button
					class="rounded border border-gray-300 px-2 py-1 text-xs hover:border-indigo-300 disabled:opacity-50 dark:border-gray-600"
					onclick={findFreePort}
					disabled={serverBusy}
				>
					Find free port
				</button>
				<button
					class="rounded-md bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
					onclick={configureServer}
					disabled={serverBusy}
				>
					{serverBusy ? 'Configuring…' : 'Configure'}
				</button>
			</div>
			{#if serverError}
				<pre
					class="overflow-x-auto rounded-md bg-red-50 p-2 text-xs text-red-800 dark:bg-red-900/30 dark:text-red-300">{serverError}</pre>
			{/if}
		</section>
	{:else}
		<!-- Stage 2: configured — full status + lifecycle controls. -->
		<section
			class="space-y-3 rounded-xl border border-gray-200 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-800/70"
		>
			<div class="flex flex-wrap items-center justify-between gap-3">
				<div class="flex items-center gap-3">
					<span
						class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium {serverStatus?.running
							? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
							: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}"
					>
						<span
							class="h-2 w-2 rounded-full {serverStatus?.running ? 'bg-green-500' : 'bg-gray-400'}"
						></span>
						{serverStatus?.running ? 'Running' : 'Stopped'}
					</span>
					<div class="text-xs text-gray-600 dark:text-gray-300">
						{#if serverStatus?.bind}
							<span class="font-mono">{serverStatus.bind}</span>
						{/if}
						{#if serverStatus?.running}
							<span class="text-gray-400">· pid {serverStatus.pid}</span>
							{#if serverStatus.detached}
								<span
									class="ml-1 rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300"
									>daemon</span
								>
							{/if}
						{/if}
						<span class="text-gray-400"
							>· {serverStatus?.rule_count ?? rules.length} rule(s) loaded</span
						>
					</div>
				</div>
				<div class="flex items-center gap-2">
					{#if serverStatus?.running}
						<button
							class="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
							onclick={restartServer}
							disabled={serverBusy}
							title="Stop and start to apply edited rules"
						>
							{serverBusy ? '…' : 'Restart'}
						</button>
						<button
							class="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
							onclick={stopServer}
							disabled={serverBusy}
						>
							Stop
						</button>
					{:else}
						<button
							class="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-500 disabled:opacity-50"
							onclick={startServer}
							disabled={serverBusy}
						>
							{serverBusy ? 'Starting…' : 'Start server'}
						</button>
					{/if}
				</div>
			</div>

			<label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
				<input type="checkbox" bind:checked={keepAsDaemon} disabled={serverStatus?.running} />
				Keep running after the wizard closes (run as a daemon)
			</label>

			<!-- Bind address: each workstation needs a distinct port so multiple
			     wizards / servers on one machine don't collide. -->
			<div class="flex flex-wrap items-center gap-2 text-xs">
				<span class="text-gray-600 dark:text-gray-300">Bind address</span>
				<input
					type="text"
					bind:value={bindDraft}
					oninput={() => (editingBind = true)}
					disabled={serverStatus?.running}
					class="w-56 rounded-md border border-gray-300 px-2 py-1 font-mono text-xs disabled:opacity-60 dark:border-gray-600 dark:bg-gray-900"
				/>
				<button
					class="rounded border border-gray-300 px-2 py-1 hover:border-indigo-300 disabled:opacity-50 dark:border-gray-600"
					onclick={findFreePort}
					disabled={serverStatus?.running || serverBusy}
				>
					Find free port
				</button>
				{#if editingBind && !serverStatus?.running}
					<button
						class="rounded bg-indigo-600 px-2 py-1 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
						onclick={saveBind}
						disabled={serverBusy}
					>
						Save bind
					</button>
				{/if}
				{#if serverStatus?.running}
					<span class="text-gray-400">(stop the server to change)</span>
				{/if}
			</div>
			<p class="text-[11px] text-gray-500">
				Register this exact address in the client's <a
					class="text-indigo-600 hover:underline"
					href="/manage_remote_servers">Remote Servers</a
				> page to connect.
			</p>

			{#if serverError}
				<pre
					class="overflow-x-auto rounded-md bg-red-50 p-2 text-xs text-red-800 dark:bg-red-900/30 dark:text-red-300">{serverError}</pre>
			{/if}
		</section>
	{/if}

	{#if statusMessage}
		<div
			class="rounded-md p-3 text-sm {statusMessage.ok
				? 'bg-green-50 text-green-800 dark:bg-green-900/30 dark:text-green-300'
				: 'bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-300'}"
		>
			{statusMessage.text}
		</div>
	{/if}

	<!-- Existing rules -->
	<section class="space-y-2">
		<h2 class="text-lg font-medium">Rules</h2>
		{#if rules.length === 0}
			<div
				class="rounded-xl border border-gray-200 bg-white/70 p-4 text-sm text-gray-600 dark:border-white/10 dark:bg-gray-800/70 dark:text-gray-300"
			>
				No rules yet. Build one below.
			</div>
		{:else}
			{#each rules as rule, idx}
				<div
					class="rounded-lg border border-gray-200 bg-white/70 p-3 dark:border-white/10 dark:bg-gray-800/70"
				>
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<div class="font-medium">{rule.id}</div>
							{#if rule.message}
								<div class="text-xs text-gray-500">{rule.message}</div>
							{/if}
							<div class="mt-1 text-xs">
								<span class="font-semibold text-amber-700 dark:text-amber-400">when</span>
								{summarizeCondition(rule.when)}
							</div>
							<div class="text-xs">
								<span class="font-semibold text-red-700 dark:text-red-400">deny</span>
								{rule.deny.map(summarizeDeny).join(' ; ')}
							</div>
						</div>
						<button
							class="shrink-0 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
							title="Delete rule"
							onclick={() => deleteRule(idx)}
						>
							<TrashIcon size={18} />
						</button>
					</div>
				</div>
			{/each}
		{/if}
	</section>

	<!-- Rule builder -->
	<section
		class="space-y-4 rounded-xl border border-gray-200 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-800/70"
	>
		<h2 class="text-lg font-medium">New rule</h2>

		<div class="grid gap-3 sm:grid-cols-2">
			<div>
				<label class="mb-1 block text-xs text-gray-600 dark:text-gray-300" for="rule-id">Rule id</label>
				<input
					id="rule-id"
					type="text"
					bind:value={draftId}
					placeholder="cryo_amp_safety"
					class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				/>
			</div>
			<div>
				<label class="mb-1 block text-xs text-gray-600 dark:text-gray-300" for="rule-msg"
					>Message (shown when blocked)</label
				>
				<input
					id="rule-msg"
					type="text"
					bind:value={draftMessage}
					placeholder="Bias channel is energized; set it to 0 V first."
					class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
				/>
			</div>
		</div>

		<!-- When conditions -->
		<div class="space-y-2">
			<div class="flex items-center justify-between">
				<h3 class="text-sm font-semibold text-amber-700 dark:text-amber-400">
					When (all conditions hold)
				</h3>
				<button
					class="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline"
					onclick={addCondition}
				>
					<PlusIcon size={14} /> Add condition
				</button>
			</div>
			{#each draftConds as cond, idx}
				{@const inst = selectedCondInst(cond)}
				<div class="grid items-center gap-2 sm:grid-cols-[1fr_1fr_auto_1fr_auto]">
					<select
						bind:value={cond.instKey}
						class="rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
					>
						<option value="" disabled>Instrument…</option>
						{#each instruments.filter((i) => i.state_keys.length > 0) as i}
							<option value={i.attribute ?? i.path}>{instLabel(i)}</option>
						{/each}
					</select>
					<select
						bind:value={cond.key}
						disabled={!inst}
						class="rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
					>
						<option value="" disabled>State key…</option>
						{#each inst?.state_keys ?? [] as k}
							<option value={k}>{k}</option>
						{/each}
					</select>
					<select
						bind:value={cond.op}
						class="rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
					>
						<option value="equals">=</option>
						<option value="not_equals">≠</option>
						<option value="greater_than">&gt;</option>
						<option value="less_than">&lt;</option>
						<option value="in">in</option>
					</select>
					<input
						type="text"
						bind:value={cond.value}
						placeholder={cond.op === 'in' ? 'a, b, c' : 'value'}
						class="rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
					/>
					<button
						class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
						title="Remove condition"
						onclick={() => removeCondition(idx)}
					>
						<TrashIcon size={16} />
					</button>
				</div>
			{/each}
			{#if draftConds.length === 0}
				<p class="text-xs text-gray-500">No conditions yet.</p>
			{/if}
		</div>

		<!-- Deny clauses -->
		<div class="space-y-2">
			<div class="flex items-center justify-between">
				<h3 class="text-sm font-semibold text-red-700 dark:text-red-400">Deny these methods</h3>
				<button
					class="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline"
					onclick={addDeny}
				>
					<PlusIcon size={14} /> Add deny clause
				</button>
			</div>
			{#each draftDeny as deny, idx}
				{@const inst = selectedDenyInst(deny)}
				<div
					class="space-y-2 rounded-md border border-gray-200 p-2 dark:border-gray-600"
				>
					<div class="flex items-center gap-2">
						<select
							bind:value={deny.instKey}
							class="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
						>
							<option value="" disabled>Instrument…</option>
							{#each instruments.filter((i) => i.methods.length > 0) as i}
								<option value={i.attribute ?? i.path}>{instLabel(i)}</option>
							{/each}
						</select>
						<button
							class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
							title="Remove deny clause"
							onclick={() => removeDeny(idx)}
						>
							<TrashIcon size={16} />
						</button>
					</div>
					{#if inst}
						<div class="flex flex-wrap gap-2">
							{#each inst.methods as m}
								<label
									class="flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs dark:border-gray-600"
								>
									<input
										type="checkbox"
										checked={deny.methods.has(m)}
										onchange={() => toggleDenyMethod(idx, m)}
									/>
									{m}
								</label>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
			{#if draftDeny.length === 0}
				<p class="text-xs text-gray-500">No deny clauses yet.</p>
			{/if}
		</div>

		<button
			class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
			onclick={addRule}
		>
			Add rule
		</button>
	</section>

	<div class="flex items-center gap-3">
		<button
			class="rounded-md bg-green-600 px-5 py-2 text-sm font-medium text-white hover:bg-green-500 disabled:opacity-50"
			onclick={save}
			disabled={saving}
		>
			{saving ? 'Saving…' : 'Save permissions'}
		</button>
		<span class="text-xs text-gray-500"
			>Writes the <code>permissions:</code> block; the rest of server.yaml is preserved.</span
		>
	</div>
</section>
