/** A run's steps, read two ways: the whole run at a glance, and where it was at one moment.
 *
 * A procedure repeats itself: a sweep of 386 bias points with a sweep of 4
 * trigger levels inside is 6,000 step executions of a handful of distinct
 * steps. Listing them is unreadable and, at that count, slow to draw; so both
 * views here are built from the structure instead.
 *
 * The bird's-eye view (:func:`activity`) is a few lanes over time. Each level
 * of loop nesting is one lane, its iterations in alternating shades; the last
 * lane is the innermost steps, coloured by what they are. Steps that only
 * wrap others (a sequence, a source guard) get no lane, so a procedure nested
 * ten deep with two loops is still three lanes. Lanes are drawn by pixel
 * column (:func:`binLane`), so steps shorter than a pixel add up to a band
 * rather than a row of hairlines, whatever their number.
 *
 * The context (:func:`contextAt`) is the path at one moment, said the way a
 * person would ("bias_voltage 46 of 386 › trigger_mV 3 of 4 › count"), each
 * loop's progress, and the steps just before and after. Loop totals come from
 * the run's definition (``loops`` in ``run_detail``); where none is known, a
 * finished run counts what it did and a running one says how far it has got.
 */
import type { Loops, Step } from './model';

export type { Loop, Loops } from './model';

/** A path with each iteration number cut to ``#``: the same for every run of a loop's body. */
export function shapeOf(path: string): string {
	return path.replace(/#\d+(?=\/|$)/g, '#');
}

/** A path segment as a person names it: ``sweep[2]`` and ``sequence#45`` are ``sweep`` and ``sequence``. */
function nameOf(segment: string): string {
	return segment.replace(/(\[\d+\]|#\d+)$/, '');
}

export type Exec = {
	path: string;
	name: string;
	shape: string;
	t0: number;
	/** Null while the step is still running. */
	t1: number | null;
	status: string | null;
	error: string | null;
	/** Index of the step this ran inside, or -1. */
	parent: number;
	/** Which run of its loop's body this is, or null if it is not one. */
	iteration: number | null;
	leaf: boolean;
};

export type Segment = { t0: number; t1: number | null; cat: number; exec: number };

export type Lane = {
	kind: 'loop' | 'steps';
	label: string;
	title: string;
	segments: Segment[];
};

/** One level of loop nesting: every round of every loop that deep. */
export type Depth = {
	/** The loops this deep, by swept parameter or name. */
	label: string;
	/** Every round at this depth, by index into ``execs``, in the order they started. */
	iterations: number[];
	/** How long a round takes on average, from those finished. */
	round: number | null;
	finished: number;
	/** Rounds in one pass of the loop: from the definition, else the most one pass has gone. */
	total: number | null;
};

export type Activity = {
	execs: Exec[];
	depths: Depth[];
	/** Indices of the innermost steps, in the order they started. */
	leaves: number[];
	lanes: Lane[];
	/** The innermost steps' names; a steps-lane segment's ``cat`` indexes this. */
	kinds: string[];
	failures: number[];
	loops: Loops;
	start: number;
	/** The last time anything is known to have happened; a running step goes on past it. */
	end: number;
	running: boolean;
	/** Mean duration of each kind of step (a loop body is one round), by shape, from those finished. */
	meanByShape: Map<string, number>;
	/** How many iterations each loop execution went round, by its index. */
	rounds: Map<number, number>;
};

/** Loop lanes beyond this many are left out; the steps lane still shows what ran. */
export const MAX_LOOP_LANES = 3;

const FAILED = new Set(['failed', 'aborted', 'interrupted']);

/** How many loops a round is inside, from 0: the number of iterations on its path, less its own. */
function depthOf(path: string): number {
	return (path.match(/#\d+(?=\/|$)/g)?.length ?? 1) - 1;
}

export function activity(steps: Step[], loops: Loops = {}): Activity {
	const execs: Exec[] = steps
		.map((s) => {
			const segments = s.path.split('/');
			const last = segments.at(-1) ?? s.path;
			const hash = /#(\d+)$/.exec(last);
			return {
				path: s.path,
				name: nameOf(last),
				shape: shapeOf(s.path),
				t0: Date.parse(s.started_at),
				t1: s.ended_at ? Date.parse(s.ended_at) : null,
				status: s.status,
				error: s.error,
				parent: -1,
				iteration: hash ? Number(hash[1]) : null,
				leaf: true
			};
		})
		.sort((a, b) => a.t0 - b.t0);

	const index = new Map(execs.map((e, i) => [e.path, i]));
	// A leaf is a step whose kind never has anything inside it, so a loop body
	// that has just started, and has no children yet, is not taken for one.
	const parents = new Set<string>();
	const rounds = new Map<number, number>();
	for (const e of execs) {
		const cut = e.path.lastIndexOf('/');
		if (cut < 0) continue;
		e.parent = index.get(e.path.slice(0, cut)) ?? -1;
		if (e.parent >= 0) parents.add(execs[e.parent].shape);
		if (e.iteration !== null && e.parent >= 0) {
			rounds.set(e.parent, Math.max(rounds.get(e.parent) ?? 0, e.iteration + 1));
		}
	}

	const kinds: string[] = [];
	const kindIndex = new Map<string, number>();
	const leaves: number[] = [];
	const failures: number[] = [];
	const byDepth: { iterations: number[]; labels: Map<string, string>; sum: number; finished: number; total: number | null }[] = [];
	const totals = new Map<string, { sum: number; n: number }>();
	let start = Infinity;
	let end = -Infinity;
	let running = false;

	execs.forEach((e, i) => {
		e.leaf = !parents.has(e.shape);
		start = Math.min(start, e.t0);
		end = Math.max(end, e.t1 ?? e.t0);
		if (e.t1 === null) running = true;
		if (e.status && FAILED.has(e.status)) failures.push(i);

		if (e.leaf) {
			let cat = kindIndex.get(e.name);
			if (cat === undefined) {
				cat = kinds.push(e.name) - 1;
				kindIndex.set(e.name, cat);
			}
			leaves.push(i);
		}

		if (e.t1 !== null) {
			const mean = totals.get(e.shape) ?? { sum: 0, n: 0 };
			mean.sum += e.t1 - e.t0;
			mean.n += 1;
			totals.set(e.shape, mean);
		}
		if (e.iteration !== null && e.parent >= 0) {
			const loop = execs[e.parent];
			const info = loops[loop.shape];
			const depth = (byDepth[depthOf(e.path)] ??= { iterations: [], labels: new Map(), sum: 0, finished: 0, total: null });
			depth.iterations.push(i);
			depth.labels.set(loop.shape, info?.parameter ?? loop.name);
			depth.total ??= info?.total ?? null;
			if (e.t1 !== null) {
				depth.sum += e.t1 - e.t0;
				depth.finished += 1;
			}
		}
	});

	const depths: Depth[] = [];
	const lanes: Lane[] = [];
	for (const d of byDepth) {
		if (!d) continue;
		const label = [...new Set(d.labels.values())].join(', ');
		// A pass of a loop of unknown length: the most rounds one pass went.
		let most = 0;
		for (const i of d.iterations) most = Math.max(most, execs[i].iteration! + 1);
		depths.push({
			label,
			iterations: d.iterations,
			round: d.finished ? d.sum / d.finished : null,
			finished: d.finished,
			total: d.total ?? (most || null)
		});
		if (lanes.length >= MAX_LOOP_LANES) continue;
		lanes.push({
			kind: 'loop',
			label,
			title: [...d.labels.keys()].join('\n'),
			segments: d.iterations.map((i) => ({ t0: execs[i].t0, t1: execs[i].t1, cat: execs[i].iteration! % 2, exec: i }))
		});
	}
	lanes.push({
		kind: 'steps',
		label: 'steps',
		title: 'The innermost steps, by what they are',
		segments: leaves.map((i) => ({ t0: execs[i].t0, t1: execs[i].t1, cat: kindIndex.get(execs[i].name)!, exec: i }))
	});

	const meanByShape = new Map([...totals].map(([shape, { sum, n }]) => [shape, sum / n]));
	return {
		execs,
		depths,
		leaves,
		lanes,
		kinds,
		failures,
		loops,
		start: Number.isFinite(start) ? start : 0,
		end: Number.isFinite(end) ? end : 0,
		running,
		meanByShape,
		rounds
	};
}

// --------------------------- drawing a lane ---------------------------

/** Per pixel column, the category that took most of it and how much of it was covered (0 to 1).
 *
 * ``weight`` is each category's share of each column (``column * categories +
 * category``), for drawing a column as the mix it was.
 *
 * A lane's segments run one after another, so each column is visited once per
 * segment that overlaps it and the cost is the segments in view plus the
 * columns, however many steps there are. ``cat`` is -1 where nothing ran.
 */
export function binLane(
	segments: Segment[],
	v0: number,
	v1: number,
	columns: number,
	categories: number,
	now: number
): { cat: Int16Array; cover: Float32Array; mixed: Uint8Array; weight: Float32Array } {
	const weight = new Float32Array(columns * categories);
	const cat = new Int16Array(columns).fill(-1);
	const cover = new Float32Array(columns);
	const mixed = new Uint8Array(columns);
	if (v1 <= v0 || columns <= 0) return { cat, cover, mixed, weight };
	const scale = columns / (v1 - v0);

	// The first segment that ends inside the view: they are in order and do not overlap.
	let lo = 0;
	let hi = segments.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if ((segments[mid].t1 ?? now) < v0) lo = mid + 1;
		else hi = mid;
	}
	for (let s = lo; s < segments.length; s++) {
		const seg = segments[s];
		if (seg.t0 > v1) break;
		const a = Math.max(seg.t0, v0);
		const b = Math.min(seg.t1 ?? now, v1);
		if (b <= a) continue;
		const x0 = (a - v0) * scale;
		const x1 = (b - v0) * scale;
		const last = Math.min(columns - 1, Math.ceil(x1) - 1);
		for (let c = Math.max(0, Math.floor(x0)); c <= last; c++) {
			weight[c * categories + seg.cat] += Math.min(x1, c + 1) - Math.max(x0, c);
		}
	}
	for (let c = 0; c < columns; c++) {
		let best = -1;
		let bestWeight = 0;
		let total = 0;
		for (let k = 0; k < categories; k++) {
			const w = weight[c * categories + k];
			total += w;
			if (w > bestWeight) {
				best = k;
				bestWeight = w;
			}
		}
		cat[c] = best;
		cover[c] = Math.min(1, total);
		mixed[c] = total > 0 && bestWeight / total < 0.75 ? 1 : 0;
	}
	return { cat, cover, mixed, weight };
}

// --------------------------- one moment ---------------------------

/** A part of the path; ``depth`` is set on a round of a loop, which level of loop it is. */
export type Crumb = { label: string; title: string; depth?: number };

/** One level of loop, at a moment: the round it was in then, or the last it had been in. */
export type Level = {
	/** The loop: its swept parameter, or its name. */
	label: string;
	/** Which round, from 0; -1 before the first. */
	index: number;
	/** How many rounds it goes: from the definition, or counted once it has finished. */
	total: number | null;
	/** Whether the run was inside this level then, rather than between its rounds. */
	active: boolean;
	/** Time left in this loop, if it can be told. */
	remaining: number | null;
};

export type Context = {
	at: number;
	crumbs: Crumb[];
	levels: Level[];
	/** The innermost step at the moment, and how long that kind of step takes on average. */
	step: { name: string; mean: number | null };
	/** How long the step at the moment had been running. */
	inStep: number | null;
	/** Estimated time until the run ends, while it runs. */
	remaining: number | null;
};

/** Of steps in the order they started, the place of the last to start by ``at``, or -1. */
function latestBy(order: number[], execs: Exec[], at: number): number {
	let lo = 0;
	let hi = order.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (execs[order[mid]].t0 <= at) lo = mid + 1;
		else hi = mid;
	}
	return lo - 1;
}

/** The innermost step running at ``at``, or the last to start before it: its place in ``leaves``. */
function leafAt(a: Activity, at: number): number {
	return latestBy(a.leaves, a.execs, at);
}

/** Where the run was at ``at``: the innermost step then and every step it was inside. */
export function contextAt(a: Activity, at: number, now: number = Date.now()): Context | null {
	const { execs, leaves } = a;
	const position = leafAt(a, at);
	if (position < 0) return null;
	const leaf = execs[leaves[position]];
	// The run's latest step, while it runs; between two steps too, so the
	// estimate does not come and go with them.
	const live = a.running && position === leaves.length - 1;

	const chain: Exec[] = [];
	for (let e: Exec | undefined = leaf; e; e = e.parent >= 0 ? execs[e.parent] : undefined) chain.unshift(e);

	// A round: which loop, which round, how many, and (while the run goes) the time left in it.
	const level = (e: Exec, active: boolean): Level => {
		const loop = execs[e.parent];
		const info = a.loops[loop.shape];
		const total = info?.total ?? (loop.t1 !== null ? (a.rounds.get(e.parent) ?? null) : null);
		const mean = a.meanByShape.get(e.shape) ?? null;
		// The rounds after this one, and what is left of this one if it has not ended.
		const remaining =
			live && active && total !== null && mean !== null
				? Math.max(0, total - e.iteration! - 1) * mean + (e.t1 === null ? Math.max(0, mean - (now - e.t0)) : 0)
				: null;
		return { label: info?.parameter ?? loop.name, index: e.iteration!, total, active, remaining };
	};

	const crumbs: Crumb[] = [];
	const onPath = new Map<number, Exec>();
	for (const e of chain) {
		if (e.iteration !== null && e.parent >= 0) {
			const depth = depthOf(e.path);
			onPath.set(depth, e);
			const { label, total } = level(e, true);
			crumbs.push({ label: `${label} ${e.iteration + 1}${total !== null ? ` of ${total}` : ''}`, title: e.path, depth });
		} else if (e === leaf || (e.name !== 'sequence' && !a.loops[e.shape])) {
			crumbs.push({ label: e.name, title: e.path });
		}
	}

	// Every level of loop the run has, whether or not it was inside it then:
	// one it was between rounds of says the last round it had been in.
	const levels: Level[] = a.depths.map((depth, d) => {
		const current = onPath.get(d);
		if (current) return level(current, true);
		const last = latestBy(depth.iterations, execs, at);
		if (last < 0) return { label: depth.label, index: -1, total: depth.total, active: false, remaining: null };
		return level(execs[depth.iterations[last]], false);
	});

	return {
		at,
		crumbs,
		levels,
		step: { name: leaf.name, mean: a.meanByShape.get(leaf.shape) ?? null },
		inStep: (leaf.t1 ?? now) >= at ? at - leaf.t0 : null,
		remaining: live ? (levels.find((l) => l.remaining !== null)?.remaining ?? null) : null
	};
}

// --------------------------- what can be read ---------------------------

/** Steps quicker than this change too fast to read: a step's name and time, or a loop's round. */
export const READABLE_MS = 500;
/** A loop whose whole pass takes this long is worth a progress bar, however quick its rounds. */
export const WATCHABLE_MS = 5000;

/** Which levels of loop are worth a row of their own: true or false, or null until it can be told.
 *
 * A level is worth one if its rounds are slow enough to read, or a whole pass
 * of it is long enough to watch go by (1,121 bias points of 160 ms each). One
 * whose rounds flash by (a few trigger levels of a millisecond each) is left
 * to the close-up. A running run is judged once three rounds have finished.
 */
export function readableLevels(a: Activity): (boolean | null)[] {
	return a.depths.map((d) => {
		if (d.round === null || (a.running && d.finished < 3)) return null;
		return d.round >= READABLE_MS || (d.total ?? 0) * d.round >= WATCHABLE_MS;
	});
}

// --------------------------- close up ---------------------------

/** A step expected to run, or with ``depth`` a round of the loop that deep;
 * ``continues`` marks the rest of one running now. ``cat`` is a step's kind,
 * or a round's place in its loop's alternation of shades. */
export type Expected = Segment & { name: string; continues?: boolean; depth?: number };

/** The innermost steps a running run is expected to take next, placed in time, up to ``horizon`` ms ahead.
 *
 * What comes next is what followed the same place in the outermost loop's
 * last round (trigger level 1 of the previous bias point), or, in its first
 * round, what followed this step the last time it ran; each keeps its offset
 * from that step, so the gaps between steps are kept too. That stretch, from
 * the same place to here, is one period of the run, and repeats until the
 * horizon, but not past the outermost loop's last round.
 *
 * ``anchor`` is the step to expect after, by its place in ``leaves``: the latest
 * by default. Asked again from the same step with a longer horizon, it gives
 * the same steps in the same places, and more after them.
 */
export function expected(a: Activity, horizon: number, anchor: number = a.leaves.length - 1): Expected[] {
	const { execs, leaves } = a;
	if (!a.running || anchor < 0 || anchor >= leaves.length) return [];
	const position = anchor;
	const leaf = execs[leaves[position]];
	const outer = (path: string) => path.replace(/#\d+(?=\/|$)/, '#');
	const place = outer(leaf.path);
	let samePlace = -1;
	let sameStep = -1;
	for (let p = position - 1, looked = 0; p >= 0 && looked < 20000; p--, looked++) {
		const e = execs[leaves[p]];
		if (outer(e.path) === place) {
			samePlace = p;
			break;
		}
		if (sameStep < 0 && e.shape === leaf.shape) sameStep = p;
	}
	const from = samePlace >= 0 ? samePlace : sameStep;
	if (from < 0) return [];

	const outermost = contextAt(a, leaf.t0, leaf.t0)?.levels[0];
	// Whole periods there is room for: each one ends at this place a round later.
	const fullPeriods = outermost && outermost.total !== null ? outermost.total - 1 - outermost.index : Infinity;
	const round = (path: string) => /^.*?#\d+(?=\/|$)/.exec(path)?.[0];
	const template = execs[leaves[from]];
	const period = leaf.t0 - template.t0;
	const out: Expected[] = [];
	if (period <= 0) return out;
	// Where the predicted steps stop: the end of the last round, or the horizon.
	let stopAt = leaf.t0 + horizon;
	steps: for (let k = 0; k <= fullPeriods; k++) {
		for (let q = from + 1; q <= position; q++) {
			// The period ends with the step running now, which has not finished:
			// it is taken as it went the round before, a period on.
			const source = q === position ? from : q;
			const shift = (q === position ? k + 1 : k) * period;
			const e = execs[leaves[source]];
			const t0 = leaf.t0 + (e.t0 - template.t0) + shift;
			// The last period stops where the last round does.
			if (k === fullPeriods && round(execs[leaves[q]].path) !== round(template.path)) {
				stopAt = t0;
				break steps;
			}
			if (t0 - leaf.t0 > horizon) break steps;
			const t1 = leaf.t0 + ((e.t1 ?? e.t0) - template.t0) + shift;
			out.push({ t0, t1, cat: a.kinds.indexOf(e.name), exec: -1, name: e.name });
		}
	}

	// The rounds of each loop in the same stretch, repeated the same way: a
	// round of the outermost loop (one per period) counts on, one of a loop
	// inside it starts over each period as that loop does. Only with a period
	// that is a round of the outermost loop, which is known from its first.
	if (samePlace < 0) return out.sort((x, y) => x.t0 - y.t0);
	for (const [d, depth] of a.depths.entries()) {
		if (d >= MAX_LOOP_LANES) break;
		for (const i of depth.iterations) {
			const r = execs[i];
			if (r.t0 <= template.t0 || r.t0 > leaf.t0) continue;
			const length = r.t1 !== null ? r.t1 - r.t0 : a.meanByShape.get(r.shape);
			if (length === undefined) continue;
			for (let k = 0; k <= fullPeriods; k++) {
				const t0 = r.t0 + (k + 1) * period;
				if (t0 >= stopAt || t0 - leaf.t0 > horizon) break;
				const index = r.iteration! + (d === 0 ? k + 1 : 0);
				out.push({ t0, t1: t0 + length, cat: index % 2, exec: -1, name: depth.label, depth: d });
			}
		}
	}
	// In time order, a loop's rounds before the steps they hold.
	return out.sort((x, y) => x.t0 - y.t0 || (x.depth ?? Infinity) - (y.depth ?? Infinity));
}

/** How much of the run a close-up shows. One width for every run, so a
 * travelling close-up always goes by at the same speed: a step crosses it in
 * four seconds, long steps as long bars and quick ones as slivers. */
export const CLOSE_UP_MS = 4000;

/** The segment of a lane running at ``at``, if any. */
export function segmentAt(segments: Segment[], at: number, now: number): Segment | null {
	let lo = 0;
	let hi = segments.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (segments[mid].t0 <= at) lo = mid + 1;
		else hi = mid;
	}
	const seg = segments[lo - 1];
	return seg && (seg.t1 ?? now) >= at ? seg : null;
}

// --------------------------- saying it ---------------------------

// Distinguishable in both themes, as BokehPlot's lines are.
const PALETTE = ['#17a398', '#e0a100', '#4f41ef', '#e4572e', '#a23b72', '#3b8ea5', '#6c9a3b', '#c1666b', '#7d6bb0', '#2e86ab'];

/** The colour of a kind of innermost step, by its index in ``Activity.kinds``. */
export function kindColor(kind: number): string {
	return PALETTE[kind % PALETTE.length];
}

/** A length of time, as short as reads well: ``0.4 ms``, ``54 ms``, ``2.3 s``, ``1 min 10 s``. */
export function span(ms: number): string {
	if (ms < 0.1) return '<0.1 ms';
	if (ms < 1) return `${ms.toFixed(1)} ms`;
	if (ms < 1000) return `${Math.round(ms)} ms`;
	const seconds = ms / 1000;
	if (seconds < 60) return `${seconds.toFixed(1)} s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
	return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}

/** Share of the run's time each kind of innermost step took, most first. */
export function timeByKind(a: Activity, now: number = Date.now()): { kind: number; share: number }[] {
	const sums = new Float64Array(a.kinds.length);
	let total = 0;
	for (const seg of a.lanes.at(-1)?.segments ?? []) {
		const d = (seg.t1 ?? now) - seg.t0;
		sums[seg.cat] += d;
		total += d;
	}
	return [...sums]
		.map((sum, kind) => ({ kind, share: total > 0 ? sum / total : 0 }))
		.sort((x, y) => y.share - x.share);
}
