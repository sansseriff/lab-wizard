/** The Data page's model: what the lab database API returns, and the pure
 * functions the page is built from (filters, plot specs, the timeline).
 *
 * See plans/semantic_data_plan.md §8 (filters) and §11 (plot specs), and
 * lab_wizard/wizard/backend/data_api.py for the API itself.
 */
import type { PlotDecl } from '$lib/procedures/model';

export type FacetValue = { value: string; runs: number };

/** One filter key: ``device.wafer`` with its values and how many runs each leaves. */
export type Facet = {
	key: string;
	group: string;
	values: FacetValue[];
	numeric: boolean;
	range?: [number, number];
	/** A metadata quantity's unit ({value, unit} in the run: block). */
	unit?: string | null;
};

/** Chosen values per facet key: any of a list, or a numeric range. */
export type Filter = string[] | { range: [number, number] };
export type Filters = Record<string, Filter>;

export type RunRow = {
	id: number;
	procedure: string;
	status: string;
	started_at: string;
	ended_at: string | null;
	device: string | null;
	operator: string | null;
	notes: string | null;
	project: string | null;
	points: number;
};

export type PlotSpec = PlotDecl & { runs: number[] };

export type RunDetail = {
	run: RunRow & { metadata: Record<string, unknown> };
	params: Record<string, unknown>;
	instruments: Record<string, { class?: string; type?: string; attribute_name?: string; params?: unknown }>;
	columns: Record<string, { unit?: string | null; bins?: unknown }>;
	derived: Record<string, string>;
	plots: PlotSpec[];
	definition_source: 'procedure' | 'recorded' | null;
};

/** One drawable line: the values, and the point (run, seq) each came from. */
export type Series = {
	label: string;
	axis: 'y' | 'y2';
	y_name: string;
	x: (number | string)[];
	y: number[];
	z: (number | null)[];
	run_id: number[];
	seq: number[];
};

export type Step = {
	/** Present on live steps, which arrive more than once: started, then ended. */
	id?: number;
	path: string;
	kind: string;
	started_at: string;
	ended_at: string | null;
	status: string | null;
	error: string | null;
};

export type Point = { seq: number; t: string; steps: string[]; values: Record<string, unknown> };

export type Device = {
	name: string;
	properties: Record<string, string | number | boolean | null>;
	notes: string | null;
	runs: number;
};

// --------------------------- filters ---------------------------

/** Add or remove one value of a key; a key with nothing chosen is dropped. */
export function toggleValue(filters: Filters, key: string, value: string): Filters {
	const current = filters[key];
	const chosen = Array.isArray(current) ? current : [];
	const next = chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value];
	const { [key]: _dropped, ...rest } = filters;
	return next.length ? { ...rest, [key]: next } : rest;
}

/** Choose a numeric range for a key, or clear it with ``null``. */
export function setRange(filters: Filters, key: string, range: [number, number] | null): Filters {
	const { [key]: _dropped, ...rest } = filters;
	return range ? { ...rest, [key]: { range } } : rest;
}

export function isChosen(filters: Filters, key: string, value: string): boolean {
	const current = filters[key];
	return Array.isArray(current) && current.includes(value);
}

/** What a chosen filter reads as in the list of active ones. */
export function describeFilter(key: string, filter: Filter): string {
	if (Array.isArray(filter)) return `${key} = ${filter.join(' or ')}`;
	return `${filter.range[0]} ≤ ${key} ≤ ${filter.range[1]}`;
}

/** A facet key as shown under its section: ``device.wafer`` under Device properties is ``wafer``. */
export function facetLabel(key: string): string {
	const dot = key.indexOf('.');
	return dot === -1 ? key : key.slice(dot + 1);
}

/** Facets grouped into the sidebar's sections, keeping the server's order. */
export function groupFacets(facets: Facet[], search = ''): { group: string; facets: Facet[] }[] {
	const needle = search.trim().toLowerCase();
	const out: { group: string; facets: Facet[] }[] = [];
	for (const facet of facets) {
		if (
			needle &&
			!facet.key.toLowerCase().includes(needle) &&
			!facet.values.some((v) => v.value.toLowerCase().includes(needle))
		)
			continue;
		const last = out.at(-1);
		if (last && last.group === facet.group) last.facets.push(facet);
		else out.push({ group: facet.group, facets: [facet] });
	}
	return out;
}

// --------------------------- plots ---------------------------

/** A run's plot, drawn for the selected runs: several runs overlay one line each. */
export function specForRuns(plot: PlotSpec | PlotDecl, runs: number[]): PlotSpec {
	// A plain copy: the plot may be page state, which structuredClone refuses.
	const spec: PlotSpec = JSON.parse(JSON.stringify({ ...plot, runs }));
	if (runs.length > 1 && !spec.series) spec.series = 'run';
	return spec;
}

/** An axis title: its columns, with their units when every one has the same. */
export function axisLabel(names: string[], units: Record<string, string | null | undefined>): string {
	if (!names.length) return '';
	const found = [...new Set(names.map((n) => units[n] ?? null))];
	const unit = found.length === 1 ? found[0] : null;
	return unit ? `${names.join(', ')} (${unit})` : names.join(', ');
}

// --------------------------- the timeline ---------------------------

export type TimelineRow = Step & {
	depth: number;
	name: string;
	/** Where the bar starts and how wide it is, as fractions of the run's span. */
	left: number;
	width: number;
};

/** Steps laid out as bars on the run's time axis, indented by nesting. */
export function timeline(steps: Step[], now: number = Date.now()): TimelineRow[] {
	if (!steps.length) return [];
	const start = (s: Step) => Date.parse(s.started_at);
	const end = (s: Step) => (s.ended_at ? Date.parse(s.ended_at) : now);
	const t0 = Math.min(...steps.map(start));
	const t1 = Math.max(...steps.map(end));
	const span = Math.max(t1 - t0, 1);
	return steps.map((step) => {
		const segments = step.path.split('/');
		return {
			...step,
			depth: segments.length - 1,
			name: segments.at(-1) ?? step.path,
			left: (start(step) - t0) / span,
			width: Math.max((end(step) - start(step)) / span, 0.002)
		};
	});
}

/** The steps a point came from and every step they ran inside. */
export function stepsOfPoint(point: Point | null): Set<string> {
	const out = new Set<string>();
	for (const path of point?.steps ?? []) {
		const segments = path.split('/');
		for (let i = 1; i <= segments.length; i++) out.add(segments.slice(0, i).join('/'));
	}
	return out;
}

// --------------------------- formatting ---------------------------

/** A timestamp in the lab's own time, to the minute. */
export function localTime(iso: string | null): string {
	if (!iso) return '—';
	const d = new Date(iso);
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function duration(from: string, to: string | null, now: number = Date.now()): string {
	const seconds = Math.max(0, ((to ? Date.parse(to) : now) - Date.parse(from)) / 1000);
	if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
	if (seconds < 60) return `${seconds.toFixed(1)} s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
	return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}

/** Nested params as ``path: value`` rows, the way filters name them. */
/** A value with a unit, as run metadata writes one: ``{value: 0.8, unit: K}``. */
export function isQuantity(value: unknown): value is { value: unknown; unit: string } {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
	const keys = Object.keys(value).sort();
	return keys.length === 2 && keys[0] === 'unit' && keys[1] === 'value' && typeof (value as { unit: unknown }).unit === 'string';
}

export function flatten(value: unknown, prefix = ''): [string, unknown][] {
	if (isQuantity(value)) return [[prefix, value]];
	if (value && typeof value === 'object' && !Array.isArray(value)) {
		const entries = Object.entries(value as Record<string, unknown>);
		if (!entries.length) return prefix ? [[prefix, '{}']] : [];
		return entries.flatMap(([key, inner]) => flatten(inner, prefix ? `${prefix}.${key}` : key));
	}
	return [[prefix, value]];
}

/** A value as one line of text. */
export function show(value: unknown): string {
	if (value === null || value === undefined) return '—';
	if (isQuantity(value)) return value.unit ? `${show(value.value)} ${value.unit}` : show(value.value);
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

const SPEC_DEFAULTS: Record<string, unknown> = {
	name: null,
	runs: [],
	y2: [],
	where: {},
	derived: {},
	series: 'run',
	label: null,
	connect: 'seq',
	kind: 'line',
	log_x: false,
	log_y: false
};

/** Whether two specs draw the same plot: a field left out is its default. */
export function sameSpec(a: PlotSpec | PlotDecl | null, b: PlotSpec | PlotDecl | null): boolean {
	if (!a || !b) return a === b;
	const canonical = (spec: object) => {
		const full: Record<string, unknown> = { ...SPEC_DEFAULTS };
		for (const [key, value] of Object.entries(spec)) if (value !== undefined) full[key] = value;
		return JSON.stringify(Object.keys(full).sort().map((key) => [key, full[key]]));
	};
	return canonical(a) === canonical(b);
}

// --------------------------- saying what a plot is ---------------------------

/** How a plot's rows fell into lines (``line_shape`` in lib/data/plot.py). */
export type LineShape = {
	lines: number;
	/** Fewest and most points on one line. */
	points: [number, number];
};

function names(list: string[]): string {
	return list.length <= 1 ? (list[0] ?? '') : `${list.slice(0, -1).join(', ')} and ${list.at(-1)}`;
}

/** What a plot draws, in words: "6 scans of count_rate against bias_voltage, one for
 * each value of trigger_mV, 20 points each". Without ``shape`` (nothing drawn yet,
 * as in the composer) it says the same without the counts. */
export function describePlot(plot: PlotDecl, shape?: LineShape | null): string {
	const what = `${names([...plot.y, ...(plot.y2 ?? [])]) || '…'} against ${plot.x || '…'}`;
	const each =
		plot.series === undefined || plot.series === 'run'
			? 'run'
			: plot.series
				? `value of ${plot.series}`
				: null;
	if (!shape) return each ? `One scan of ${what} for each ${each}.` : `One scan of ${what}, through every point.`;
	if (!shape.lines) return `Nothing to draw: no point has ${what.replace(' against ', ' and ')}.`;
	const [fewest, most] = shape.points;
	const points = fewest === most ? `${most}` : `${fewest}–${most}`;
	if (shape.lines === 1) return `1 scan of ${what}, ${points} points.`;
	return `${shape.lines} scans of ${what}, one for each ${each ?? 'line'}, ${points} points each.`;
}

/** The other swept parameters a plot leaves inside one line, read from the procedure itself.
 *
 * Each run has a row for every combination of its swept parameters. With x
 * one of them, a line holding several values of another visits each x once per
 * value of it and doubles back, unless that parameter is what the lines are
 * split by, or a condition fixes it. ``swept`` are the parameters the
 * procedure's sweeps and repeats bind. It is said in the composer, where the
 * plot is chosen before anything is measured; on a drawn plot the zigzag
 * speaks for itself.
 */
export function sweptWithin(plot: PlotDecl, swept: string[]): string[] {
	if (!swept.includes(plot.x)) return [];
	const fixed = new Set(Object.keys(plot.where ?? {}));
	return swept.filter((p) => p !== plot.x && p !== plot.series && !fixed.has(p));
}

/** ``sweptWithin`` in words: short, the fix sits beside it. */
export function sweptWithinWarning(plot: PlotDecl, swept: string[]): string | null {
	const others = sweptWithin(plot, swept);
	if (!others.length) return null;
	return `${names(others)} ${others.length > 1 ? 'are' : 'is'} also swept, so this line will zigzag.`;
}
