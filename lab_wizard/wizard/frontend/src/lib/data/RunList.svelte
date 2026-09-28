<script lang="ts">
	/** The runs that match the filters, newest first.
	 *
	 * Click one to look at it; Cmd/Ctrl-click to add or remove a run, and
	 * Shift-click to take a range, to overlay several on one plot.
	 */
	import Pill from '$lib/components/Pill.svelte';
	import { localTime, type RunRow } from './model';

	let {
		runs,
		total,
		selected,
		loading = false,
		onselect,
		onmore
	}: {
		runs: RunRow[];
		total: number;
		selected: number[];
		loading?: boolean;
		onselect: (ids: number[]) => void;
		onmore: () => void;
	} = $props();

	let anchor: number | null = null;

	const tones: Record<string, 'ok' | 'warn' | 'crit' | 'accent' | 'neutral'> = {
		success: 'ok',
		running: 'accent',
		failed: 'crit',
		aborted: 'warn'
	};

	function pick(event: MouseEvent, id: number) {
		if (event.shiftKey && anchor !== null) {
			const ids = runs.map((r) => r.id);
			const [a, b] = [ids.indexOf(anchor), ids.indexOf(id)].sort((x, y) => x - y);
			if (a !== -1) {
				const range = ids.slice(a, b + 1);
				onselect([...selected.filter((s) => !range.includes(s)), ...range]);
				return;
			}
		}
		anchor = id;
		if (event.metaKey || event.ctrlKey) toggle(id);
		else onselect([id]);
	}

	function toggle(id: number) {
		anchor = id;
		onselect(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<div class="flex items-center gap-2 border-b border-line px-3 py-2 text-xs text-muted">
		<span><span class="font-semibold text-ink tabular-nums">{total}</span> {total === 1 ? 'run' : 'runs'}</span>
		{#if selected.length > 1}
			<span class="ml-auto">{selected.length} overlaid</span>
			<button class="text-accent hover:underline" onclick={() => onselect(selected.slice(0, 1))}>Just one</button>
		{/if}
	</div>
	<ul class="min-h-0 flex-1 overflow-y-auto" aria-label="Runs" aria-multiselectable="true" role="listbox">
		{#each runs as run (run.id)}
			{@const chosen = selected.includes(run.id)}
			<li
				role="option"
				aria-selected={chosen}
				class="flex cursor-pointer items-start gap-2 border-b border-line px-3 py-2 text-body {chosen
					? 'bg-accent-wash'
					: 'hover:bg-surface-2'}"
				onclick={(e) => pick(e, run.id)}
				onkeydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') {
						e.preventDefault();
						toggle(run.id);
					}
				}}
				tabindex="0"
			>
				<input
					type="checkbox"
					class="mt-0.5"
					checked={chosen}
					onclick={(e) => {
						e.stopPropagation();
						toggle(run.id);
					}}
					aria-label="Overlay run {run.id}"
				/>
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						<span class="truncate font-medium {chosen ? 'text-accent-strong' : ''}">{run.procedure}</span>
						<span class="ml-auto shrink-0"><Pill tone={tones[run.status] ?? 'neutral'} dot={run.status === 'running'}>{run.status}</Pill></span>
					</div>
					<div class="mt-0.5 flex flex-wrap gap-x-2 text-fine text-muted">
						<span class="tabular-nums">{localTime(run.started_at)}</span>
						<span>{run.device ?? 'no device'}</span>
						{#if run.operator}<span>{run.operator}</span>{/if}
						<span class="tabular-nums">{run.points} pts</span>
						<span class="mono opacity-70">#{run.id}</span>
					</div>
				</div>
			</li>
		{:else}
			<li class="px-3 py-6 text-xs text-muted">{loading ? 'Loading…' : 'No run matches these filters.'}</li>
		{/each}
		{#if runs.length < total}
			<li class="p-3 text-center">
				<button class="lw-btn lw-btn-sm" onclick={onmore} disabled={loading}>Show more ({total - runs.length} left)</button>
			</li>
		{/if}
	</ul>
</div>
