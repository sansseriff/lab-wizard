<script lang="ts">
	/** Choose a step type — grouped by the role it would act on.
	 *
	 * Plan 4.3: pick a role, see the operations its behavior offers. Instrument
	 * steps are listed under every declared role they can act on, so choosing
	 * "count" under `counter` binds it to `counter`. A step no declared role can
	 * fill is still offered, under the behavior it needs; picking it declares
	 * that role. Flow steps need no role and sit in their own group.
	 */
	import Modal from '$lib/components/Modal.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { type StepSpec, roleFits } from './model';

	let {
		editor,
		title,
		/** Only steps that hold other steps — for wrapping. */
		containersOnly = false,
		onpick,
		onclose
	}: {
		editor: ProcedureEditor;
		title: string;
		containersOnly?: boolean;
		onpick: (type: string, role: string | null) => void;
		onclose: () => void;
	} = $props();

	let query = $state('');

	const catalog = $derived(editor.catalog);

	function isContainer(spec: StepSpec) {
		return Object.values(spec.fields).some((f) => f.kind === 'step' || f.kind === 'steps');
	}

	function roleFields(spec: StepSpec) {
		return Object.values(spec.fields).filter((f) => f.kind === 'role');
	}

	const visible = $derived(
		Object.values(catalog.steps)
			.filter((s) => !containersOnly || isContainer(s))
			.filter((s) => {
				const q = query.trim().toLowerCase();
				return !q || s.type.includes(q) || s.summary.toLowerCase().includes(q);
			})
			.sort((a, b) => a.type.localeCompare(b.type))
	);

	const byRole = $derived(
		Object.entries(editor.definition.roles).map(([role, decl]) => ({
			role,
			behavior: decl.behavior,
			steps: visible.filter((s) => {
				const fields = roleFields(s);
				return fields.length > 0 && fields.some((f) => f.requires.length && roleFits(catalog, decl.behavior, f.requires));
			})
		}))
	);

	const flow = $derived(visible.filter((s) => roleFields(s).length === 0));

	/** Instrument steps that need a behavior no declared role has. */
	const needingRole = $derived.by(() => {
		const groups = new Map<string, StepSpec[]>();
		const declared = Object.values(editor.definition.roles).map((r) => r.behavior);
		for (const s of visible) {
			const fields = roleFields(s).filter((f) => f.requires.length);
			if (!fields.length) continue;
			if (fields.some((f) => declared.some((b) => roleFits(catalog, b, f.requires)))) continue;
			const behavior = fields[0].requires[0];
			groups.set(behavior, [...(groups.get(behavior) ?? []), s]);
		}
		return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
	});

	/** Role steps that fit any role (with_settings) belong with every role; list them once. */
	const anyRole = $derived(
		visible.filter((s) => {
			const fields = roleFields(s);
			return fields.length > 0 && fields.every((f) => !f.requires.length);
		})
	);
</script>

{#snippet stepButton(spec: StepSpec, role: string | null)}
	<button
		class="flex w-full flex-col items-start rounded px-2.5 py-1.5 text-left hover:bg-accent-wash"
		onclick={() => onpick(spec.type, role)}
	>
		<span class="mono text-[12.5px] font-semibold">{spec.type}</span>
		<span class="text-[11.5px] text-muted">{spec.summary}</span>
	</button>
{/snippet}

<Modal {title} {onclose} width="max-w-3xl">
	<input class="lw-input mb-3" bind:value={query} placeholder="Search steps" aria-label="Search steps" />

	<div class="grid gap-4 sm:grid-cols-2">
		{#each byRole as group (group.role)}
			{#if group.steps.length}
				<section>
					<h3 class="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
						<span class="mono normal-case tracking-normal text-ink">{group.role}</span> · {group.behavior}
					</h3>
					{#each group.steps as spec (spec.type)}
						{@render stepButton(spec, group.role)}
					{/each}
				</section>
			{/if}
		{/each}

		{#if flow.length}
			<section>
				<h3 class="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
					Structure and flow
				</h3>
				{#each flow as spec (spec.type)}
					{@render stepButton(spec, null)}
				{/each}
			</section>
		{/if}

		{#if anyRole.length && Object.keys(editor.definition.roles).length}
			<section>
				<h3 class="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Any role</h3>
				{#each anyRole as spec (spec.type)}
					{@render stepButton(spec, null)}
				{/each}
			</section>
		{/if}

		{#each needingRole as [behavior, specs] (behavior)}
			<section>
				<h3 class="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
					Adds a {behavior} role
				</h3>
				{#each specs as spec (spec.type)}
					{@render stepButton(spec, null)}
				{/each}
			</section>
		{/each}
	</div>

	{#if !visible.length}
		<p class="text-sm text-muted">No step matches “{query}”.</p>
	{/if}
</Modal>
