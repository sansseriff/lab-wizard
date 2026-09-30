<!--
  A draggable divider between two panels of a grid, for the panel before it
  (or, with `reverse`, after it) to be resized. It sits in its own grid cell,
  which may be zero wide or a gutter: the grab area is centred on the cell and wider than
  it, so a panel's own border can stay the visible line.

  `size` is pixels and is kept in localStorage under `storageKey`, so a layout
  that was dragged stays where it was left. Until it is dragged it follows
  `initial`, which a page can derive from the window to keep a proportion.
  Double-click goes back to that; the arrow keys nudge it.
-->
<script lang="ts">
	import { onMount } from 'svelte';

	let {
		size = $bindable(),
		initial,
		min = 160,
		max = Infinity,
		orientation = 'vertical',
		reverse = false,
		storageKey,
		label
	}: {
		size: number;
		/** The size until the user sets one, and what double-click goes back to. */
		initial: number;
		min?: number;
		max?: number;
		/** `vertical` is a vertical line, dragged left and right. */
		orientation?: 'vertical' | 'horizontal';
		/** The panel being sized is after the handle, not before it. */
		reverse?: boolean;
		storageKey: string;
		label: string;
	} = $props();

	const horizontal = $derived(orientation === 'horizontal');
	let dragging = $state(false);
	// Set once the user has chosen a size, here or in an earlier session.
	let chosen = $state(false);

	$effect(() => {
		if (!chosen && initial > 0) size = clamp(initial);
	});

	const clamp = (value: number) => Math.round(Math.min(Math.max(value, min), Math.max(min, max)));

	function save() {
		chosen = true;
		try {
			localStorage.setItem(storageKey, String(size));
		} catch {
			// storage can be unavailable; the layout just is not remembered
		}
	}

	onMount(() => {
		try {
			const stored = Number(localStorage.getItem(storageKey));
			if (Number.isFinite(stored) && stored > 0) {
				chosen = true;
				size = clamp(stored);
			}
		} catch {
			// as above
		}
	});

	function onpointerdown(event: PointerEvent) {
		if (event.button !== 0) return;
		event.preventDefault();
		const target = event.currentTarget as HTMLElement;
		target.setPointerCapture(event.pointerId);
		const from = horizontal ? event.clientY : event.clientX;
		const start = size;
		dragging = true;

		const move = (e: PointerEvent) => {
			const delta = (horizontal ? e.clientY : e.clientX) - from;
			size = clamp(start + (reverse ? -delta : delta));
		};
		const end = () => {
			dragging = false;
			target.removeEventListener('pointermove', move);
			target.removeEventListener('pointerup', end);
			target.removeEventListener('pointercancel', end);
			save();
		};
		target.addEventListener('pointermove', move);
		target.addEventListener('pointerup', end);
		target.addEventListener('pointercancel', end);
	}

	function onkeydown(event: KeyboardEvent) {
		const keys = horizontal ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight'];
		const at = keys.indexOf(event.key);
		if (at === -1) return;
		event.preventDefault();
		const grows = (at === 1) !== reverse;
		size = clamp(size + (grows ? 16 : -16));
		save();
	}

	function reset() {
		chosen = false;
		try {
			localStorage.removeItem(storageKey);
		} catch {
			// as above
		}
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
	role="separator"
	aria-orientation={horizontal ? 'horizontal' : 'vertical'}
	aria-label={label}
	aria-valuenow={size}
	aria-valuemin={min}
	tabindex="0"
	class="group relative z-10 touch-none select-none {horizontal ? 'h-0 w-full' : 'h-full w-full'}"
	{onpointerdown}
	{onkeydown}
	ondblclick={reset}
>
	<div
		class="absolute {horizontal
			? 'inset-x-0 top-1/2 h-2 -translate-y-1/2 cursor-row-resize'
			: 'inset-y-0 left-1/2 w-2 -translate-x-1/2 cursor-col-resize'}"
	>
		<div
			class="absolute transition-colors group-hover:bg-accent group-focus-visible:bg-accent {dragging
				? 'bg-accent'
				: ''} {horizontal ? 'inset-x-0 top-1/2 h-0.5 -translate-y-1/2' : 'inset-y-0 left-1/2 w-0.5 -translate-x-1/2'}"
		></div>
	</div>
</div>
