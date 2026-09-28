import { expect, test } from 'bun:test';
import {
	axisLabel,
	describePlot,
	sweptWithin,
	sweptWithinWarning,
	describeFilter,
	facetLabel,
	flatten,
	groupFacets,
	sameSpec,
	setRange,
	specForRuns,
	stepsOfPoint,
	timeline,
	toggleValue,
	type Facet,
	type PlotSpec,
	type Step
} from '../src/lib/data/model';

test('choosing and unchoosing filter values', () => {
	let filters = toggleValue({}, 'procedure', 'mcr_curve');
	filters = toggleValue(filters, 'procedure', 'iv_curve');
	expect(filters).toEqual({ procedure: ['mcr_curve', 'iv_curve'] });
	filters = toggleValue(filters, 'procedure', 'mcr_curve');
	filters = toggleValue(filters, 'procedure', 'iv_curve');
	expect(filters).toEqual({}); // a key with nothing chosen is gone, not empty

	const ranged = setRange({ device: ['A7'] }, 'param.gate_time_s', [0.1, 1]);
	expect(ranged).toEqual({ device: ['A7'], 'param.gate_time_s': { range: [0.1, 1] } });
	expect(setRange(ranged, 'param.gate_time_s', null)).toEqual({ device: ['A7'] });
	expect(describeFilter('device', ['A7', 'B2'])).toBe('device = A7 or B2');
	expect(describeFilter('x', { range: [1, 2] })).toBe('1 ≤ x ≤ 2');
});

test('facets are grouped into their sections and searched by key or value', () => {
	const facet = (key: string, group: string, values: string[]): Facet => ({
		key,
		group,
		numeric: false,
		values: values.map((value) => ({ value, runs: 1 }))
	});
	const facets = [
		facet('procedure', 'Procedure', ['mcr_curve']),
		facet('device.wafer', 'Device properties', ['W12']),
		facet('device.width_nm', 'Device properties', ['80'])
	];
	expect(groupFacets(facets).map((g) => [g.group, g.facets.length])).toEqual([
		['Procedure', 1],
		['Device properties', 2]
	]);
	expect(groupFacets(facets, 'W12').map((g) => g.facets.map((f) => f.key))).toEqual([['device.wafer']]);
	expect(facetLabel('device.wafer')).toBe('wafer');
	expect(facetLabel('param.readout.gate_time_s')).toBe('readout.gate_time_s');
	expect(facetLabel('procedure')).toBe('procedure');
});

test('several runs overlay one line each; one run keeps its plot as it was', () => {
	const plot: PlotSpec = { name: 'MCR', x: 'a', y: ['b'], series: null, runs: [] };
	expect(specForRuns(plot, [3]).series).toBe(null);
	expect(specForRuns(plot, [3, 4])).toMatchObject({ runs: [3, 4], series: 'run' });
	expect(specForRuns({ ...plot, series: 'phase' }, [3, 4]).series).toBe('phase');
	expect(plot.runs).toEqual([]); // a copy, not the procedure's plot edited
});

test('a spec is unchanged when only its defaults are spelled differently', () => {
	const plot: PlotSpec = { x: 'a', y: ['b'], runs: [1], kind: 'line', y2: [], log_y: false };
	expect(sameSpec(plot, { x: 'a', y: ['b'], runs: [1] })).toBe(true);
	expect(sameSpec(plot, { ...plot, kind: undefined })).toBe(true);
	expect(sameSpec(plot, { ...plot, log_y: true })).toBe(false);
	expect(sameSpec(plot, { ...plot, where: { phase: 'signal' } })).toBe(false);
});

test('an axis title carries the unit when its columns share one', () => {
	expect(axisLabel(['bias'], { bias: 'V' })).toBe('bias (V)');
	expect(axisLabel(['a', 'b'], { a: 'Hz', b: 'Hz' })).toBe('a, b (Hz)');
	expect(axisLabel(['a', 'b'], { a: 'Hz', b: 'V' })).toBe('a, b');
	expect(axisLabel(['count_rate / 1000'], {})).toBe('count_rate / 1000');
});

test('the timeline lays steps out on the run time axis, indented by nesting', () => {
	const step = (path: string, start: number, end: number | null, status = 'success'): Step => ({
		path,
		kind: path.split('/').at(-1)!,
		started_at: new Date(start * 1000).toISOString(),
		ended_at: end === null ? null : new Date(end * 1000).toISOString(),
		status: end === null ? null : status,
		error: null
	});
	const rows = timeline([step('sequence', 0, 10), step('sequence/sweep[0]', 2, 10), step('sequence/sweep[0]/count#1', 5, null)], 10_000);
	expect(rows.map((r) => [r.name, r.depth, r.left])).toEqual([
		['sequence', 0, 0],
		['sweep[0]', 1, 0.2],
		['count#1', 2, 0.5]
	]);
	expect(rows[0].width).toBe(1);
	expect(rows[2].width).toBe(0.5); // still running: up to now
});

test('a point highlights its own steps and every step they ran inside', () => {
	const lit = stepsOfPoint({ seq: 1, t: '', values: {}, steps: ['sequence/sweep[0]/count#1'] });
	expect([...lit]).toEqual(['sequence', 'sequence/sweep[0]', 'sequence/sweep[0]/count#1']);
	expect(stepsOfPoint(null).size).toBe(0);
});

test('nested params flatten to the paths filters use', () => {
	expect(flatten({ readout: { gate_time_s: 1, threshold_mV: -50 }, bias: 0.2 })).toEqual([
		['readout.gate_time_s', 1],
		['readout.threshold_mV', -50],
		['bias', 0.2]
	]);
	expect(flatten({})).toEqual([]);
});

test('a plot says what it draws, counts included once it is drawn', () => {
	const plot = { x: 'bias_voltage', y: ['count_rate'], series: 'trigger_mV' };
	expect(describePlot(plot)).toBe('One scan of count_rate against bias_voltage for each value of trigger_mV.');
	expect(describePlot(plot, { lines: 6, points: [20, 20], })).toBe(
		'6 scans of count_rate against bias_voltage, one for each value of trigger_mV, 20 points each.'
	);
	expect(describePlot({ ...plot, series: undefined }, { lines: 2, points: [18, 20], })).toBe(
		'2 scans of count_rate against bias_voltage, one for each run, 18–20 points each.'
	);
	expect(describePlot({ ...plot, series: null })).toBe('One scan of count_rate against bias_voltage, through every point.');
});

test('before any run, the procedure itself says when a line would double back', () => {
	const swept = ['bias_voltage', 'trigger_mV'];
	const plot = { x: 'bias_voltage', y: ['count_rate'], series: 'run' };
	expect(sweptWithin(plot, swept)).toEqual(['trigger_mV']);
	expect(sweptWithinWarning(plot, swept)).toBe('trigger_mV is also swept, so this line will zigzag.');
	// Split by it, or fix it with a condition, and each line is a clean scan.
	expect(sweptWithin({ ...plot, series: 'trigger_mV' }, swept)).toEqual([]);
	expect(sweptWithin({ x: 'trigger_mV', y: ['count_rate'], where: { bias_voltage: { per_run: 'max' } } }, swept)).toEqual([]);
	// x measured rather than swept: the rows cannot repeat it.
	expect(sweptWithin({ x: 'sense_voltage', y: ['current'] }, ['bias_voltage'])).toEqual([]);
	// A repeat varies between rows as much as a sweep does.
	expect(sweptWithin({ x: 'bias_voltage', y: ['counts'] }, ['bias_voltage', 'repeat'])).toEqual(['repeat']);
});
