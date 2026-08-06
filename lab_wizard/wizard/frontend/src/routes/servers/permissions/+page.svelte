<script lang="ts">
	import { fetchWithConfig } from '$lib/api';
	import { workstation } from '$lib/stores/workstation.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Callout from '$lib/components/Callout.svelte';
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

	// Lifecycle lives on Servers ▸ This workspace. Only the *consequence* of the
	// server's state is needed here: a rule that is saved but not loaded is not
	// in force, and that has to be visible while editing.
	const serverStatus: ServerStatus | null = $derived(data.serverStatus ?? null);
	const enforcing = $derived(serverStatus?.running ?? false);
	/** Rules on disk that the running server has not read yet. */
	const staleRules = $derived(
		enforcing && (serverStatus?.rule_count ?? 0) !== rules.length
	);

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
			// The chrome's rule count comes from the same status call, so refresh
			// it here rather than leaving the topbar reporting a stale number.
			await workstation.refresh();
			statusMessage = {
				text: enforcing
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
	<PageHeader title="Permissions">
		{#snippet actions()}
			<button class="lw-btn lw-btn-primary" onclick={save} disabled={saving}>
				{saving ? 'Saving…' : 'Save permissions'}
			</button>
		{/snippet}
		A rule blocks methods (<strong class="text-crit">deny</strong>) while a state condition holds
		(<strong class="text-warn">when</strong>). Saved to
		<code>config/server/server.yaml</code> — the rest of that file is preserved.
	</PageHeader>

	<!-- Rules are read at start, so an edit is not in force until a restart. That
	     is easy to forget mid-edit, which is exactly when it matters. -->
	{#if !enforcing}
		<Callout tone="warn" title="No rule here is currently enforced. ">
			The permission gate only sees calls made through a running instrument server, and this
			workspace's is not running. Edits still save.
			<a class="underline" href="/servers">Start it on This workspace →</a>
		</Callout>
	{:else if staleRules}
		<Callout tone="warn" title="Saved, but not loaded. ">
			The running server read {serverStatus?.rule_count ?? 0} rule(s) at start and there are now
			{rules.length} on disk. Restart it to apply the difference.
			<a class="underline" href="/servers">Restart on This workspace →</a>
		</Callout>
	{/if}

	{#if statusMessage}
		<Callout tone={statusMessage.ok ? 'ok' : 'crit'}>{statusMessage.text}</Callout>
	{/if}

	<Panel title="Rules" description="{rules.length} defined" flush>
		{#if rules.length === 0}
			<p class="px-3.5 py-6 text-center text-xs text-muted">
				No rules yet. Every method is allowed. Build one below.
			</p>
		{:else}
			<ul>
				{#each rules as rule, idx (rule.id + idx)}
					<li class="flex items-start justify-between gap-3 border-b border-line px-3.5 py-3 last:border-b-0">
						<div class="min-w-0 space-y-1">
							<div class="mono text-[13px] font-semibold">{rule.id}</div>
							{#if rule.message}
								<p class="text-[12px] text-muted">“{rule.message}”</p>
							{/if}
							<div class="text-[12px]">
								<span class="mr-1.5 font-semibold uppercase tracking-wide text-warn">when</span>
								<span class="mono">{summarizeCondition(rule.when)}</span>
							</div>
							<div class="text-[12px]">
								<span class="mr-1.5 font-semibold uppercase tracking-wide text-crit">deny</span>
								<span class="mono">{rule.deny.map(summarizeDeny).join(' ; ')}</span>
							</div>
						</div>
						<button
							class="lw-btn lw-btn-sm shrink-0"
							title="Delete this rule"
							onclick={() => deleteRule(idx)}
						>
							<TrashIcon size={13} />
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</Panel>

	<!-- Rule builder. The when/deny split is the rule's actual grammar, so it is
	     the structure of the form too rather than one flat list of fields. -->
	<Panel title="New safety rule">
		<div class="grid gap-3 sm:grid-cols-2">
			<div>
				<label class="lw-label" for="rule-id">Rule id</label>
				<input
					id="rule-id"
					type="text"
					bind:value={draftId}
					placeholder="cryo_amp_safety"
					class="lw-input mono"
				/>
			</div>
			<div>
				<label class="lw-label" for="rule-msg">Message shown when blocked</label>
				<input
					id="rule-msg"
					type="text"
					bind:value={draftMessage}
					placeholder="Bias channel is energized; set it to 0 V first."
					class="lw-input"
				/>
			</div>
		</div>

		<!-- When -->
		<div class="mt-4 space-y-2 border-t border-line pt-3">
			<div class="flex items-center justify-between gap-3">
				<h3 class="text-[11px] font-semibold uppercase tracking-[0.09em] text-warn">
					When — all conditions hold
				</h3>
				<button class="lw-btn lw-btn-sm" onclick={addCondition}>
					<PlusIcon size={12} /> Add condition
				</button>
			</div>

			{#if draftConds.length === 0}
				<p class="text-xs text-muted">
					No conditions yet. A rule with no <em>when</em> would deny unconditionally.
				</p>
			{/if}

			{#each draftConds as cond, idx (idx)}
				{@const inst = selectedCondInst(cond)}
				<div class="grid items-center gap-2 sm:grid-cols-[1.4fr_1fr_auto_1fr_auto]">
					<select bind:value={cond.instKey} class="lw-select">
						<option value="" disabled>Instrument…</option>
						{#each instruments.filter((i) => i.state_keys.length > 0) as i (i.path)}
							<option value={i.attribute ?? i.path}>{instLabel(i)}</option>
						{/each}
					</select>
					<select bind:value={cond.key} disabled={!inst} class="lw-select">
						<option value="" disabled>State key…</option>
						{#each inst?.state_keys ?? [] as k (k)}
							<option value={k}>{k}</option>
						{/each}
					</select>
					<select bind:value={cond.op} class="lw-select w-auto">
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
						class="lw-input mono"
					/>
					<button
						class="lw-btn lw-btn-sm"
						title="Remove condition"
						onclick={() => removeCondition(idx)}
					>
						<TrashIcon size={13} />
					</button>
				</div>
			{/each}
		</div>

		<!-- Deny -->
		<div class="mt-4 space-y-2 border-t border-line pt-3">
			<div class="flex items-center justify-between gap-3">
				<h3 class="text-[11px] font-semibold uppercase tracking-[0.09em] text-crit">
					Deny — these methods
				</h3>
				<button class="lw-btn lw-btn-sm" onclick={addDeny}>
					<PlusIcon size={12} /> Add deny clause
				</button>
			</div>

			{#if draftDeny.length === 0}
				<p class="text-xs text-muted">No deny clauses yet.</p>
			{/if}

			{#each draftDeny as deny, idx (idx)}
				{@const inst = selectedDenyInst(deny)}
				<div class="space-y-2 rounded border border-line bg-surface-2 p-2.5">
					<div class="flex items-center gap-2">
						<select bind:value={deny.instKey} class="lw-select flex-1">
							<option value="" disabled>Instrument…</option>
							{#each instruments.filter((i) => i.methods.length > 0) as i (i.path)}
								<option value={i.attribute ?? i.path}>{instLabel(i)}</option>
							{/each}
						</select>
						<button
							class="lw-btn lw-btn-sm shrink-0"
							title="Remove deny clause"
							onclick={() => removeDeny(idx)}
						>
							<TrashIcon size={13} />
						</button>
					</div>
					{#if inst}
						<div class="flex flex-wrap gap-1.5">
							{#each inst.methods as m (m)}
								<label
									class="flex cursor-pointer items-center gap-1.5 rounded border px-2 py-1 text-[11.5px] transition-colors {deny.methods.has(
										m
									)
										? 'border-crit/40 bg-crit-wash text-crit'
										: 'border-line bg-surface hover:border-muted'}"
								>
									<input
										type="checkbox"
										checked={deny.methods.has(m)}
										onchange={() => toggleDenyMethod(idx, m)}
									/>
									<span class="mono">{m}</span>
								</label>
							{/each}
						</div>
					{:else}
						<p class="text-[11px] text-muted">Pick an instrument to see the methods it exposes.</p>
					{/if}
				</div>
			{/each}
		</div>

		<div class="mt-4 flex items-center gap-3 border-t border-line pt-3">
			<button class="lw-btn" onclick={addRule}>Add rule</button>
			<span class="text-[11px] text-muted">
				Added rules are held here until you press <strong>Save permissions</strong>.
			</span>
		</div>
	</Panel>
</section>
