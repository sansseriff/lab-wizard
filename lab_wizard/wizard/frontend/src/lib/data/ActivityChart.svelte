<script lang="ts">
	/** A run's steps over time: a lane per level of loop, and one of the innermost steps.
	 *
	 * Drawn on one canvas by pixel column (``binLane`` in activity.ts), so it
	 * costs the same for a run of 60 steps or 60,000. Two uses of one drawing:
	 *
	 * - The whole run (no ``closeUp``): dragging across zooms in, Fit (or a
	 *   double-click) zooms out, a click keeps a moment. While a run goes, what
	 *   it is expected to take yet is hatched past "now".
	 * - A close-up (``closeUp`` ms wide) centred on a moment. While a run goes
	 *   that moment is now, fixed in the middle, and the steps travel past it;
	 *   the steps expected next are drawn dashed.
	 *
	 * The travelling close-up is not redrawn every frame. Anything else the
	 * page does on its one thread (a plot redrawing, steps arriving) would
	 * delay such a frame, and the strip would stutter. Instead it is painted
	 * into canvases two close-ups wide and slid by a transform animation, which
	 * the browser runs off that thread at a constant speed on the wall clock.
	 * They are painted afresh when steps arrive (and every quarter close-up),
	 * the slide restarting where it was; the past half and the expected half
	 * are separate canvases, each clipped at the "now" line, which stays put.
	 *
	 * Steps carry no labels: hovering one says what it is, picked by lane (the
	 * pointer's height) and time (its position), again each frame as they move.
	 */
	import { onMount } from 'svelte';
	import { binLane, contextAt, kindColor, segmentAt, span, type Activity, type Expected } from './activity';

	let {
		activity,
		now,
		live = false,
		estimatedEnd = null,
		cursor = null,
		closeUp = null,
		center = null,
		expectedSteps = [],
		onhover,
		onpick
	}: {
		activity: Activity;
		now: number;
		live?: boolean;
		estimatedEnd?: number | null;
		/** A moment kept: a click here, or a plot point's. */
		cursor?: number | null;
		/** Show this many ms around ``center`` (or, live, around now) instead of the whole run. */
		closeUp?: number | null;
		center?: number | null;
		expectedSteps?: Expected[];
		onhover?: (at: number | null) => void;
		onpick?: (at: number) => void;
	} = $props();

	// The travelling close-up's "now" line is this far behind the clock. A step
	// reaches the page some tens of ms after it happens; drawn at the clock, a
	// new one would appear already past the line. A little behind, every step
	// has arrived by the time it reaches the line.
	const PLAYHEAD_DELAY_MS = 200;

	// How solid everything past the travelling close-up's line is: what is
	// about to cross it, recorded or expected alike, as one. Only steps that
	// have not begun are told apart, by a dashed outline; a box turns solid as
	// it crosses the line, and nowhere else.
	const UPCOMING = 0.3;

	const LOOP_H = 12;
	const STEPS_H = 18;
	const GAP = 3;
	const AXIS_H = 16;
	const TOP = 4;

	let host: HTMLDivElement;
	let canvas: HTMLCanvasElement | undefined = $state();
	let pastCanvas: HTMLCanvasElement | undefined = $state();
	let futureCanvas: HTMLCanvasElement | undefined = $state();
	let width = $state(0);
	let theme = $state(0);
	let view = $state<[number, number] | null>(null);
	let hover = $state<{ x: number; y: number } | null>(null);
	let selecting = $state<{ from: number; to: number } | null>(null);
	let travelling = $derived(live && closeUp !== null && center === null);
	// The wall clock, for what is under the pointer while the close-up travels.
	let frameNow = $state(Date.now());

	const lanes = $derived(activity.lanes);
	const tops = $derived.by(() => {
		let y = TOP;
		return lanes.map((lane) => {
			const top = y;
			y += (lane.kind === 'loop' ? LOOP_H : STEPS_H) + GAP;
			return top;
		});
	});
	const height = $derived(TOP + lanes.reduce((h, l) => h + (l.kind === 'loop' ? LOOP_H : STEPS_H) + GAP, 0) + AXIS_H);

	// The whole run, and while it goes the time it is expected to take. The end
	// moves only when the estimate passes it or falls well short of it, so the
	// axis rescales now and then rather than with every step.
	let stickyEnd = 0;
	const domain = $derived.by<[number, number]>(() => {
		const start = activity.start;
		const end = Math.max(activity.end, live ? now : activity.end, start + 1);
		if (!live || estimatedEnd === null || estimatedEnd <= end) {
			stickyEnd = 0;
			return [start, end];
		}
		if (estimatedEnd > stickyEnd || estimatedEnd - start < 0.8 * (stickyEnd - start)) {
			const total = estimatedEnd - start;
			const unit = 10 ** Math.floor(Math.log10(total / 20));
			const step = [1, 2, 5, 10].map((m) => m * unit).find((q) => q >= total / 20) ?? unit * 10;
			stickyEnd = start + Math.ceil((total * 1.05) / step) * step;
		}
		return [start, stickyEnd];
	});

	function viewAt(clock: number): [number, number] {
		if (closeUp === null) return view ?? domain;
		const middle = center ?? (travelling ? clock - PLAYHEAD_DELAY_MS : clock);
		return [middle - closeUp / 2, middle + closeUp / 2];
	}
	const shown = $derived(viewAt(travelling ? frameNow : now));

	onMount(() => {
		const observer = new ResizeObserver(() => (width = host.clientWidth));
		observer.observe(host);
		const media = matchMedia('(prefers-color-scheme: dark)');
		const retheme = () => theme++;
		media.addEventListener('change', retheme);
		return () => {
			observer.disconnect();
			media.removeEventListener('change', retheme);
		};
	});

	// A new run starts zoomed out; a running one keeps its zoom as it grows.
	const start = $derived(activity.start);
	$effect(() => {
		void start;
		view = null;
		stickyEnd = 0;
	});

	// Redrawn when anything it shows changes.
	let frame = 0;
	$effect(() => {
		void [activity, now, live, estimatedEnd, cursor, shown, hover, selecting, width, height, theme, expectedSteps, canvas];
		if (travelling || !canvas) return;
		cancelAnimationFrame(frame);
		frame = requestAnimationFrame(() => canvas && paint(canvas, width, ...viewAt(now), now, 'all'));
		return () => cancelAnimationFrame(frame);
	});

	// The travelling close-up: painted for a moment, then slid by the browser.
	$effect(() => {
		void [activity, expectedSteps, width, height, theme];
		const past = pastCanvas;
		const future = futureCanvas;
		const span = closeUp;
		if (!travelling || !past || !future || span === null || width <= 0) return;
		const wallAtZero = Date.now() - performance.now();
		// One device pixel's worth of time. Every painting starts on a multiple of
		// it, so a step's edge lands on the same pixel column whenever it is
		// painted again; starting anywhere else would round each edge afresh,
		// and repainting many times a second would make the edges shimmer.
		const msPerPixel = span / (width * (devicePixelRatio || 1));
		const paintAndSlide = () => {
			// The time of the frame being made, on the timeline the animation runs on.
			const t = (document.timeline.currentTime as number | null) ?? performance.now();
			// The time at the "now" line.
			const origin = wallAtZero + t - PLAYHEAD_DELAY_MS;
			// Two close-ups of time from half a close-up before now (room to slide a
			// whole close-up), starting on the pixel grid.
			const v0 = Math.floor((origin - span / 2) / msPerPixel) * msPerPixel;
			const v1 = v0 + 2 * span;
			paint(past, 2 * width, v0, v1, origin, 'past');
			paint(future, 2 * width, v0, v1, origin, 'future', wallAtZero + t);
			for (const c of [past, future]) {
				for (const old of c.getAnimations()) old.cancel();
				const slide = c.animate([{ transform: 'translateX(0px)' }, { transform: `translateX(${-width}px)` }], {
					duration: span,
					easing: 'linear',
					fill: 'forwards'
				});
				// Unmoved when the canvas's middle-of-the-view time was at the line: a hair before this frame.
				slide.startTime = v0 + span / 2 + PLAYHEAD_DELAY_MS - wallAtZero;
			}
		};
		paintAndSlide();
		const timer = setInterval(paintAndSlide, Math.min(1000, span / 4));
		return () => {
			clearInterval(timer);
			for (const c of [past, future]) for (const a of c.getAnimations()) a.cancel();
		};
	});

	// While the pointer is over a travelling close-up, what is under it changes as the steps go by.
	$effect(() => {
		if (!travelling || !hover) return;
		let id = 0;
		const tick = () => {
			frameNow = Date.now();
			id = requestAnimationFrame(tick);
		};
		id = requestAnimationFrame(tick);
		return () => cancelAnimationFrame(id);
	});

	let colors: Record<string, string> = {};
	$effect(() => {
		void theme;
		const style = getComputedStyle(host);
		colors = Object.fromEntries(['--accent', '--muted', '--line-2', '--crit', '--ink-2'].map((n) => [n, style.getPropertyValue(n).trim()]));
	});

	/** Draw ``v0``..``v1`` into ``target``, ``cssWidth`` wide: all of it, or for a travelling
	 * close-up the past (steps running now drawn on to the end, the "now" line
	 * clips them) or the future (only what is expected). */
	function paint(
		target: HTMLCanvasElement,
		cssWidth: number,
		v0: number,
		v1: number,
		clock: number,
		part: 'all' | 'past' | 'future',
		known: number = clock
	) {
		if (cssWidth <= 0) return;
		const dpr = devicePixelRatio || 1;
		const w = Math.round(cssWidth * dpr);
		const h = Math.round(height * dpr);
		if (target.width !== w) target.width = w;
		if (target.height !== h) target.height = h;
		const ctx = target.getContext('2d')!;
		ctx.setTransform(1, 0, 0, 1, 0, 0);
		ctx.clearRect(0, 0, w, h);
		const x = (t: number) => ((t - v0) / (v1 - v0)) * w;
		// How far a step still running is drawn: to now; in the past half of a
		// travelling close-up to the end, as the "now" line clips it; in the
		// future half to as far as anything is known (``known``, the clock, ahead
		// of the line by its delay).
		const runningTo = part === 'past' ? v1 : part === 'future' ? known : clock;
		// Past the line, what has been recorded but not reached it yet is faint;
		// crossing the line it turns solid, in the same place and shape.
		const fade = part === 'future' ? UPCOMING : 1;
		const { '--accent': accent, '--muted': muted, '--line-2': line, '--crit': crit, '--ink-2': ink } = colors;

		lanes.forEach((lane, i) => {
			const y = Math.round(tops[i] * dpr);
			const laneH = Math.round((lane.kind === 'loop' ? LOOP_H : STEPS_H) * dpr);
			const cats = lane.kind === 'loop' ? 2 : Math.max(1, activity.kinds.length);
			ctx.fillStyle = line;
			ctx.globalAlpha = 0.25;
			ctx.fillRect(0, y, w, laneH);
			ctx.globalAlpha = 1;
			const { cat, mixed, weight: raw } = binLane(lane.segments, v0, v1, w, cats, runningTo);
			// Averaged with neighbouring columns only over the whole run: a close-up is
			// painted on a fixed pixel grid, where each column's mix is already the
			// same from one painting to the next, and averaging over a range that
			// moves with every painting would change how a step looks each time.
			const weight = lane.kind === 'steps' && closeUp === null ? smooth(raw, cats, cat, lane.segments, v0, v1, w, runningTo) : raw;

			// A loop lane: iterations in alternating shades, one even shade where
			// they are too short to tell apart.
			// The steps lane: each column stacked by what ran in it, each kind as
			// high as its share of the column, so steps shorter than a pixel read
			// as a steady mix rather than whichever won the pixel; idle time is
			// left empty. Columns that look the same are drawn as one rectangle.
			const look = (c: number) => {
				if (cat[c] < 0) return '';
				if (lane.kind === 'loop') return mixed[c] ? 'm' : String(cat[c]);
				let key = '';
				for (let k = 0; k < cats; k++) key += `${Math.round(weight[c * cats + k] * laneH)},`;
				return key;
			};
			let runStart = 0;
			let runLook = look(0);
			for (let c = 1; c <= w; c++) {
				const next = c < w ? look(c) : null;
				if (next === runLook) continue;
				if (runLook) {
					if (lane.kind === 'loop') {
						ctx.fillStyle = accent;
						ctx.globalAlpha = (runLook === 'm' ? 0.45 : runLook === '0' ? 0.6 : 0.32) * fade;
						ctx.fillRect(runStart, y, c - runStart, laneH);
						ctx.globalAlpha = 1;
					} else {
						ctx.globalAlpha = fade;
						let stack = y + laneH;
						runLook.split(',').forEach((px, k) => {
							const hk = Number(px);
							if (!hk) return;
							stack -= hk;
							ctx.fillStyle = kindColor(k);
							ctx.fillRect(runStart, stack, c - runStart, hk);
						});
						ctx.globalAlpha = 1;
					}
				}
				runStart = c;
				runLook = next ?? '';
			}
		});

		const stepsTop = Math.round(tops[lanes.length - 1] * dpr);
		const stepsH = Math.round(STEPS_H * dpr);

		// What is expected, as faint as what is recorded past the line: a loop's
		// rounds in their alternating shades, so its stripes go on; steps, with a
		// dashed outline on those not yet begun and wide enough to read as a box.
		// The rest of a step or round running now has no outline, so it and what
		// is known of it read as one.
		ctx.lineWidth = dpr;
		ctx.setLineDash([3 * dpr, 2 * dpr]);
		const knownX = Math.round(x(part === 'future' ? known : clock));
		const loopLanes = lanes.length - 1;
		// Per lane, where the rest of what runs in it now ends: nothing else is expected before.
		const restX = new Map<number, number>();
		for (const e of part === 'past' ? [] : expectedSteps) {
			// A round of a loop deeper than the lanes shown is left out, as its lane is.
			if (e.depth !== undefined && e.depth >= loopLanes) continue;
			const lane = e.depth ?? loopLanes;
			// Placed by times that do not change from one painting to the next, so
			// they travel at the speed of everything else: a rest from where what is
			// known of it ends (the two are drawn alike, so the joint does not show),
			// the others after that rest. One expected before now (the run is late)
			// overlaps what is known, faintly.
			const after = restX.get(lane) ?? -Infinity;
			const a = e.continues ? Math.max(knownX, Math.round(x(e.t0))) : Math.max(Math.round(x(e.t0)), after, part === 'all' ? knownX : -Infinity);
			if (e.continues) restX.set(lane, Math.round(x(e.t1 ?? e.t0)));
			const b = Math.round(x(e.t1 ?? e.t0));
			if (b <= a || b < 0 || a > w) continue;
			if (e.depth !== undefined) {
				ctx.fillStyle = accent;
				ctx.globalAlpha = (e.cat === 0 ? 0.6 : 0.32) * UPCOMING;
				ctx.fillRect(a, Math.round(tops[lane] * dpr), b - a, Math.round(LOOP_H * dpr));
				ctx.globalAlpha = 1;
				continue;
			}
			const color = kindColor(Math.max(0, e.cat));
			ctx.fillStyle = color;
			ctx.globalAlpha = UPCOMING;
			ctx.fillRect(a, stepsTop, Math.max(1, b - a), stepsH);
			ctx.globalAlpha = 1;
			if (!e.continues && b - a >= 12 * dpr) {
				ctx.strokeStyle = color;
				ctx.strokeRect(a + 0.5 * dpr, stepsTop + 0.5 * dpr, b - a - dpr, stepsH - dpr);
			}
		}
		ctx.setLineDash([]);

		// Failures: a mark above the steps lane where each one happened (faded past the line, like the step).
		ctx.fillStyle = crit;
		ctx.globalAlpha = fade;
		for (const f of activity.failures) {
			const fx = x(activity.execs[f].t1 ?? activity.execs[f].t0);
			if (fx < 0 || fx > w) continue;
			ctx.fillRect(Math.round(fx) - dpr, stepsTop - 2 * dpr, 2 * dpr, stepsH + 2 * dpr);
		}
		ctx.globalAlpha = 1;

		const bottom = Math.round((height - AXIS_H) * dpr);
		// The whole run, while it goes: what is still to come, faintly, to the end of the axis.
		if (closeUp === null && live && estimatedEnd !== null && estimatedEnd > clock) {
			const a = Math.max(0, x(clock));
			ctx.save();
			ctx.beginPath();
			ctx.rect(a, TOP * dpr, w - a, bottom - TOP * dpr);
			ctx.clip();
			ctx.strokeStyle = muted;
			ctx.globalAlpha = 0.35;
			ctx.lineWidth = dpr;
			for (let d = a - bottom; d < w; d += 6 * dpr) {
				ctx.beginPath();
				ctx.moveTo(d, bottom);
				ctx.lineTo(d + bottom, 0);
				ctx.stroke();
			}
			ctx.restore();
		}

		const vertical = (t: number, color: string, dash: number[] = [], width = 1) => {
			const vx = Math.round(x(t)) + 0.5;
			if (vx < 0 || vx > w) return;
			ctx.strokeStyle = color;
			ctx.lineWidth = width * dpr;
			ctx.setLineDash(dash.map((d) => d * dpr));
			ctx.beginPath();
			ctx.moveTo(vx, 0);
			ctx.lineTo(vx, bottom);
			ctx.stroke();
			ctx.setLineDash([]);
		};
		if (part === 'all') {
			if (live) vertical(clock, accent, [], closeUp !== null ? 2 : 1);
			if (cursor !== null) vertical(cursor, ink);
			if (closeUp !== null && center !== null) vertical(center, ink);
			if (hover && closeUp === null) vertical(timeOf(hover.x, v0, v1), muted, [3, 3]);
		}
		if (selecting && part === 'all') {
			ctx.fillStyle = accent;
			ctx.globalAlpha = 0.15;
			const a = Math.min(selecting.from, selecting.to) * dpr;
			ctx.fillRect(a, 0, Math.abs(selecting.to - selecting.from) * dpr, bottom);
			ctx.globalAlpha = 1;
		}

		// The time axis, from the run's start.
		ctx.fillStyle = muted;
		ctx.font = `${10 * dpr}px ui-monospace, Menlo, monospace`;
		ctx.textBaseline = 'top';
		const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 3e4, 6e4, 12e4, 3e5, 6e5, 12e5, 18e5, 36e5, 72e5];
		const every = steps.find((s) => (s / (v1 - v0)) * cssWidth >= 80) ?? steps.at(-1)!;
		// From the tick before the left edge: its label reaches into view.
		const first = Math.max(0, (Math.ceil((v0 - activity.start) / every) - 1) * every);
		// Ticks closer than a second say as many decimals as tell them apart.
		const decimals = every < 1000 ? Math.max(1, Math.ceil(-Math.log10(every / 1000))) : 0;
		const tick = (t: number) => (t === 0 ? '0' : decimals ? `${(t / 1000).toFixed(decimals)} s` : span(t));
		for (let t = first; activity.start + t <= v1; t += every) {
			const tx = x(activity.start + t);
			ctx.fillRect(Math.round(tx), bottom, dpr, 3 * dpr);
			ctx.fillText(tick(t), Math.round(tx) + 3 * dpr, bottom + 3 * dpr);
		}
	}

	/** A steps lane's mix averaged over neighbouring columns where steps are denser than pixels.
	 *
	 * A loop whose round is about a column wide puts a different slice of a
	 * round in each column, so the mix jumps from column to column. Averaged
	 * over a window of a few steps' width, it shows the proportions they keep.
	 */
	function smooth(
		weight: Float32Array,
		cats: number,
		cat: Int16Array,
		segments: { t0: number; t1: number | null }[],
		v0: number,
		v1: number,
		w: number,
		clock: number
	) {
		// How many steps a column holds where anything ran (a running run fills
		// only the start of the chart). Below a quarter of a step per column, the
		// steps are wide enough to draw as they are.
		let used = 0;
		for (let c = 0; c < w; c++) if (cat[c] >= 0) used++;
		if (!used) return weight;
		let inView = 0;
		for (const seg of segments) if (seg.t0 < v1 && (seg.t1 ?? clock) > v0) inView++;
		const perColumn = inView / used;
		if (perColumn < 0.25) return weight;
		// Wide enough for a few steps either side: a couple of rounds of a short loop.
		const radius = Math.min(24, Math.ceil(3 / perColumn));
		const out = new Float32Array(weight.length);
		for (let k = 0; k < cats; k++) {
			let sum = 0;
			let n = 0;
			for (let c = -radius; c < w + radius; c++) {
				const add = c + radius;
				if (add < w) {
					sum += weight[add * cats + k];
					n++;
				}
				const drop = c - radius - 1;
				if (drop >= 0) {
					sum -= weight[drop * cats + k];
					n--;
				}
				if (c >= 0 && c < w) out[c * cats + k] = sum / n;
			}
		}
		return out;
	}

	/** A time in the run, to as many decimals as tell the ends of ``range`` apart. */
	function offset(t: number, [a, b]: [number, number]) {
		const range = b - a;
		if (range >= 10_000) return span(t - activity.start);
		const decimals = Math.max(1, Math.ceil(-Math.log10(range / 1000)) + 1);
		return `${((t - activity.start) / 1000).toFixed(decimals)} s`;
	}

	// ---------------- what is under the pointer ----------------

	function timeOf(px: number, v0: number, v1: number) {
		return v0 + (px / Math.max(1, width)) * (v1 - v0);
	}

	/** What the pointer is over, said in a line or two; while travelling, again as the steps move under it. */
	const hoverText = $derived.by(() => {
		if (!hover) return null;
		const [v0, v1] = shown;
		const clock = travelling ? frameNow : now;
		const at = timeOf(hover.x, v0, v1);
		// Past the line is what is expected, while travelling.
		const line = travelling ? clock - PLAYHEAD_DELAY_MS : clock;
		const lane = lanes.findIndex((l, i) => hover!.y >= tops[i] - GAP / 2 && hover!.y < tops[i] + (l.kind === 'loop' ? LOOP_H : STEPS_H) + GAP / 2);
		const where = contextAt(activity, at, clock);
		const path = where ? where.crumbs.map((c) => c.label).join(' › ') : 'before the first step';
		let what = '';
		if (lane >= 0) {
			const seg = segmentAt(lanes[lane].segments, at, clock);
			if (seg) {
				const e = activity.execs[seg.exec];
				const took = span((e.t1 ?? clock) - e.t0);
				if (lanes[lane].kind === 'steps') what = `${e.name} · ${took}${e.t1 === null ? ' so far' : ''}`;
				else {
					const level = where?.levels[lane];
					what = level ? `${level.label} ${level.index + 1}${level.total !== null ? ` of ${level.total}` : ''} · ${took}` : took;
				}
			} else if (lanes[lane].kind === 'steps') {
				const next = at > line ? expectedSteps.find((e) => e.t0 <= at && (e.t1 ?? e.t0) >= at) : undefined;
				if (next) what = `${next.name} · about ${span((next.t1 ?? next.t0) - next.t0)}, expected`;
			}
		}
		return { what, when: span(Math.max(0, at - activity.start)), path };
	});

	function onpointermove(e: PointerEvent) {
		const rect = host.getBoundingClientRect();
		const p = { x: Math.min(Math.max(e.clientX - rect.left, 0), rect.width), y: e.clientY - rect.top };
		hover = p;
		const [v0, v1] = shown;
		if (closeUp === null) onhover?.(timeOf(p.x, v0, v1));
		if (selecting) selecting = { ...selecting, to: p.x };
	}

	function onpointerleave() {
		hover = null;
		if (closeUp === null) onhover?.(null);
	}

	function onpointerdown(e: PointerEvent) {
		if (e.button !== 0 || closeUp !== null) return;
		host.setPointerCapture(e.pointerId);
		const x = e.clientX - host.getBoundingClientRect().left;
		selecting = { from: x, to: x };
	}

	function onpointerup() {
		if (!selecting) return;
		const { from, to } = selecting;
		selecting = null;
		const [v0, v1] = shown;
		if (Math.abs(to - from) > 4) {
			const a = timeOf(Math.min(from, to), v0, v1);
			const b = timeOf(Math.max(from, to), v0, v1);
			view = [a, Math.max(b, a + 1)];
		} else {
			onpick?.(timeOf(to, v0, v1));
		}
	}

	function onwheel(e: WheelEvent) {
		if (closeUp !== null) return;
		const [v0, v1] = shown;
		const range = v1 - v0;
		if (e.ctrlKey || e.metaKey) {
			// A pinch, or Cmd/Ctrl and the wheel: zoom about the pointer.
			e.preventDefault();
			const at = timeOf(e.clientX - host.getBoundingClientRect().left, v0, v1);
			const [d0, d1] = domain;
			const next = Math.min(d1 - d0, Math.max(1, range * Math.exp(e.deltaY * 0.01)));
			const a = at - ((at - v0) / range) * next;
			view = clampView([a, a + next]);
		} else if (view && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
			e.preventDefault();
			const shift = (e.deltaX / width) * range;
			view = clampView([v0 + shift, v1 + shift]);
		}
	}

	function clampView([a, b]: [number, number]): [number, number] | null {
		const [d0, d1] = domain;
		if (b - a >= d1 - d0) return null;
		if (a < d0) return [d0, d0 + (b - a)];
		if (b > d1) return [d1 - (b - a), d1];
		return [a, b];
	}
</script>

{#if closeUp === null}
	<div class="mb-1 flex min-h-6 items-center gap-2 pl-[7.5rem] text-fine text-muted">
		{#if view}
			<span class="tabular-nums">Showing {offset(shown[0], shown)} to {offset(shown[1], shown)}</span>
			<button class="lw-btn lw-btn-sm" onclick={() => (view = null)}>Fit the whole run</button>
		{:else}
			<span>Drag across the chart to zoom in.</span>
		{/if}
	</div>
{/if}
<div class="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-2">
	<div class="relative" style:height="{height}px">
		{#each lanes as lane, i (i)}
			<p
				class="absolute inset-x-0 truncate text-right text-fine {lane.kind === 'steps' ? 'text-ink-2' : 'text-muted'}"
				style:top="{tops[i]}px"
				style:line-height="{lane.kind === 'loop' ? LOOP_H : STEPS_H}px"
				title={lane.title}
			>
				{lane.label}
			</p>
		{/each}
	</div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="relative min-w-0 touch-none {closeUp === null ? 'cursor-crosshair' : 'cursor-default'}"
		bind:this={host}
		style:height="{height}px"
		data-chart={closeUp === null ? 'run' : 'close-up'}
		{onpointermove}
		{onpointerleave}
		{onpointerdown}
		{onpointerup}
		{onwheel}
		ondblclick={() => closeUp === null && (view = null)}
	>
		{#if travelling}
			<div class="absolute inset-y-0 left-0 w-1/2 overflow-hidden">
				<canvas bind:this={pastCanvas} class="absolute top-0 left-0 h-full will-change-transform" style:width="{2 * width}px"></canvas>
			</div>
			<div class="absolute inset-y-0 right-0 w-1/2 overflow-hidden">
				<canvas
					bind:this={futureCanvas}
					class="absolute top-0 h-full will-change-transform"
					style:left="{-width / 2}px"
					style:width="{2 * width}px"
				></canvas>
			</div>
			<div class="pointer-events-none absolute top-0 left-1/2 w-0.5 -translate-x-1/2 bg-accent" style:height="{height - AXIS_H}px"></div>
		{:else}
			<canvas bind:this={canvas} class="block size-full"></canvas>
		{/if}
		{#if hover && hoverText && !selecting}
			<div
				class="pointer-events-none absolute top-full z-10 mt-1 max-w-[28rem] rounded border border-line bg-surface px-2 py-1 text-fine shadow"
				style:left="{Math.min(hover.x, Math.max(0, width - 260))}px"
			>
				{#if hoverText.what}<p class="mono font-medium text-ink">{hoverText.what}</p>{/if}
				<p>
					<span class="tabular-nums text-muted">{hoverText.when}</span>
					<span class="mono text-ink-2">{hoverText.path}</span>
				</p>
			</div>
		{/if}
	</div>
</div>
