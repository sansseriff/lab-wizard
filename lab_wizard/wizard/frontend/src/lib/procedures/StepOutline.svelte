<script lang="ts">
	import CaretDownIcon from 'phosphor-svelte/lib/CaretDown';
	import CaretRightIcon from 'phosphor-svelte/lib/CaretRight';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import Self from './StepOutline.svelte';
	import StepPicker from './StepPicker.svelte';
	import type { ProcedureEditor } from './editor.svelte';
	import { pathKey, type Path } from './model';
	import { cleanupSummary, humanize, stepContext, stepSummary, stepTitle } from './presentation';
	import RowContextMenu from '$lib/components/menu/RowContextMenu.svelte';
	import type { MenuAction } from '$lib/components/menu/items';

	let { editor, path }: { editor: ProcedureEditor; path: Path } = $props();
	const step = $derived(editor.step(path));
	const spec = $derived(editor.catalog.steps[step?.type]);
	const key = $derived(pathKey(path));
	const selected = $derived(editor.selectedStep === step);
	const children = $derived(
		Object.entries(spec?.fields ?? {}).filter(([, f]) => f.kind === 'step' || f.kind === 'steps')
	);
	const problems = $derived(
		(editor.check?.problems ?? []).filter((p) => path.every((part, i) => p.path[i] === part))
	);
	// An unnamed sequence is just its ordered children. Named or invalid sequences
	// keep a row so their label and diagnostics remain reachable.
	const flat = $derived(
		step?.type === 'sequence' && !step.name && !editor.placedProblems.byStep.get(key)?.length
	);
	const cleanup = $derived(step ? cleanupSummary(step, editor.definition) : null);
	const collapsed = $derived(editor.collapsedSteps.includes(step));
	let picker = $state<{ target: Path; index?: number; title: string } | null>(null);

	// Right-click: the inspector's structural actions, on the row itself.
	// Built only when the menu opens, since every row has one.
	const menu = $derived.by((): MenuAction[] => {
		const context = stepContext(editor.definition, editor.catalog, path);
		const inList = context.place === 'list';
		const removes = context.optional || inList;
		return [
			...(inList
				? [
						{ label: 'Move up', disabled: context.index === 0, onselect: () => editor.moveStep(path, -1) },
						{
							label: 'Move down',
							disabled: context.index === context.count - 1,
							onselect: () => editor.moveStep(path, 1)
						}
					]
				: []),
			...(context.place === 'root'
				? []
				: [{ label: removes ? 'Remove' : 'Empty this slot', danger: true, onselect: () => editor.removeStep(path) }])
		];
	});
</script>

{#if step}
	<div class="outline-node" id="step-{key}">
		{#if !flat}
			<RowContextMenu items={menu} disabled={path.length === 1}>
				<div class="outline-item">
					{#if children.length}
						<button
							class="outline-toggle"
							aria-label="{collapsed ? 'Expand' : 'Collapse'} {stepTitle(step)}"
							aria-expanded={!collapsed}
							onclick={() => editor.toggleStep(step)}
						>
							{#if collapsed}<CaretRightIcon size={14} />{:else}<CaretDownIcon size={14} />{/if}
						</button>
					{:else}<span class="outline-spacer"></span>{/if}
					<button
						class="outline-row"
						class:selected
						aria-pressed={selected}
						onclick={() => editor.selectStep(path)}
					>
						<span class="outline-title"
							>{stepTitle(step)}{#if !spec}<span class="text-crit"> · unknown type</span>{/if}</span
						>
						<span class="outline-summary">{stepSummary(step, editor.definition, editor.catalog)}</span
						>
						{#if problems.length}<span class="outline-problems"
								>{problems.length} problem{problems.length === 1 ? '' : 's'}</span
							>{/if}
					</button>
				</div>
			</RowContextMenu>
		{/if}
		{#if flat || !collapsed}
			<div class:scoped={!flat && children.length > 0}>
				{#each children as [name, field] (name)}
					{#if children.length > 1}<div class="branch-label">
							{humanize(name)}{field.optional && !step[name] ? ' · optional' : ''}
						</div>{/if}
					{#if field.kind === 'steps'}
						{#each step[name] ?? [] as child, i (child)}
							<Self {editor} path={[...path, name, i]} />
							{#if editor.selectedStep === child}
								<button
									class="outline-add insertion"
									onclick={() =>
										(picker = {
											target: [...path, name],
											index: i + 1,
											title: `Insert after ${stepTitle(child)}`
										})}><PlusIcon size={12} /> Insert step here</button
								>
							{/if}
						{/each}
					{:else if step[name]}<Self {editor} path={[...path, name]} />{/if}
					{#if field.kind === 'steps' || !step[name]}
						<div class="outline-add-row">
							<button
								class="outline-add"
								onclick={() =>
									(picker = {
										target: [...path, name],
										title: `Add to ${stepTitle(step)}${children.length > 1 ? ` · ${humanize(name)}` : ''}`
									})}
							>
								<PlusIcon size={12} />
								{field.kind === 'steps' && !step[name]?.length
									? 'Add first step'
									: children.length > 1
										? `Add ${humanize(name).toLowerCase()} step`
										: 'Add step'}
							</button>
							{#if flat}<button
									class="sequence-settings"
									class:active={selected}
									aria-pressed={selected}
									onclick={() => editor.selectStep(path)}
									aria-label="Edit sequence at {key}">Sequence settings</button
								>{/if}
						</div>
					{/if}
				{/each}
			</div>
		{/if}
		{#if cleanup}<div class="outline-cleanup">{cleanup}</div>{/if}
	</div>
{:else}
	<button class="outline-add" onclick={() => (picker = { target: path, title: 'Add first step' })}
		><PlusIcon size={12} /> Add first step</button
	>
{/if}

{#if picker}
	<StepPicker
		{editor}
		title={picker.title}
		onclose={() => (picker = null)}
		onpick={(type, role) => {
			if (picker) editor.insertStep(picker.target, type, role, picker.index);
			picker = null;
		}}
	/>
{/if}

<style>
	.outline-node {
		min-width: 0;
		scroll-margin-top: 64px;
	}
	.outline-item {
		display: flex;
		align-items: start;
		gap: 2px;
	}
	.outline-toggle,
	.outline-spacer {
		width: 24px;
		flex: 0 0 24px;
	}
	.outline-toggle {
		display: flex;
		justify-content: center;
		padding: 11px 0;
		color: var(--muted);
		border-radius: 4px;
	}
	.outline-toggle:hover {
		background: var(--surface-2);
	}
	.outline-row {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 12px;
		min-width: 0;
		padding: 9px 10px;
		text-align: left;
		border-radius: 4px;
		font-size: var(--text-body);
		line-height: 1.45;
	}
	.outline-row:hover {
		background: var(--surface-2);
	}
	.outline-row.selected {
		background: var(--accent-wash);
		color: var(--accent-strong);
	}
	.outline-title {
		overflow-wrap: anywhere;
	}
	.outline-summary {
		margin-left: auto;
		color: var(--muted);
		font-size: var(--text-xs);
		overflow-wrap: anywhere;
	}
	.outline-problems {
		color: var(--crit);
		font-size: var(--text-xs);
	}
	.scoped {
		margin-left: 12px;
		padding-left: 8px;
		border-left: 1px solid var(--line-2);
	}
	.branch-label {
		padding: 8px 10px 3px;
		color: var(--ink-2);
		font-weight: 600;
		font-size: var(--text-xs);
	}
	.outline-add-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 12px;
		padding-left: 24px;
		margin: 3px 0 5px;
	}
	.outline-add {
		display: flex;
		align-items: center;
		gap: 5px;
		padding: 5px 8px;
		border-radius: 4px;
		color: var(--accent);
		font-size: var(--text-xs);
	}
	.outline-add:hover {
		background: var(--accent-wash);
	}
	.insertion {
		margin-left: 24px;
	}
	.sequence-settings {
		font-size: var(--text-fine);
		color: var(--muted);
		padding: 5px 0;
	}
	.sequence-settings:hover,
	.sequence-settings.active {
		color: var(--accent);
		text-decoration: underline;
	}
	.outline-cleanup {
		padding: 7px 10px 10px 34px;
		font-size: var(--text-xs);
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}
	@media (max-width: 600px) {
		.scoped {
			margin-left: 6px;
			padding-left: 3px;
		}
		.outline-summary {
			flex-basis: 100%;
			margin-left: 0;
		}
	}
	@media (pointer: coarse) {
		button {
			min-height: 44px;
		}
		.outline-toggle {
			flex-basis: 32px;
		}
	}
</style>
