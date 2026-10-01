import { expect, test } from 'bun:test';
import {
	activity,
	binLane,
	closeUpSpan,
	contextAt,
	readableLevels,
	expected,
	segmentAt,
	shapeOf,
	span,
	timeByKind,
	type Loops,
	type Segment
} from '../src/lib/data/activity';
import type { Step } from '../src/lib/data/model';

const T0 = Date.parse('2026-09-30T21:47:52Z');
const iso = (ms: number) => new Date(T0 + ms).toISOString();

/** A pcr_trigger_levels run, as it records: 3 bias points, each a settle and 2 trigger levels of a count.
 * ``stopAt`` cuts it off there, as a live run looks: steps going then have no end yet. */
function pcrRun(stopAt = Infinity): Step[] {
	const steps: Step[] = [];
	let t = 0;
	const open = (path: string, kind: string) => {
		const step: Step = { path, kind, started_at: iso(t), ended_at: null, status: null, error: null };
		if (t <= stopAt) steps.push(step);
		return step;
	};
	const close = (step: Step) => {
		if (t <= stopAt) Object.assign(step, { ended_at: iso(t), status: 'success' });
	};
	const leaf = (path: string, kind: string, ms: number) => {
		const step = open(path, kind);
		t += ms;
		close(step);
	};
	const guard = open('source_guard', 'source_guard');
	const bias = open('source_guard/sweep[0]', 'sweep');
	for (let i = 0; i < 3; i++) {
		const point = `source_guard/sweep[0]/sequence#${i}`;
		const body = open(point, 'sequence');
		leaf(`${point}/set_voltage[0]`, 'set_voltage', 1);
		leaf(`${point}/wait[1]`, 'wait', 50);
		const levels = open(`${point}/sweep[2]`, 'sweep');
		for (let j = 0; j < 2; j++) {
			const level = open(`${point}/sweep[2]/sequence#${j}`, 'sequence');
			leaf(`${point}/sweep[2]/sequence#${j}/set_threshold[0]`, 'set_threshold', 1);
			leaf(`${point}/sweep[2]/sequence#${j}/count[1]`, 'count', 100);
			close(level);
		}
		close(levels);
		close(body);
	}
	close(bias);
	close(guard);
	return steps;
}

const LOOPS: Loops = {
	'source_guard/sweep[0]': { kind: 'sweep', parameter: 'bias_voltage', total: 3 },
	'source_guard/sweep[0]/sequence#/sweep[2]': { kind: 'sweep', parameter: 'trigger_mV', total: 2 }
};

test('a path is the same for every run of a loop body once its iteration numbers are cut', () => {
	expect(shapeOf('source_guard/sweep[0]/sequence#45/sweep[2]/sequence#3/count[1]')).toBe(
		'source_guard/sweep[0]/sequence#/sweep[2]/sequence#/count[1]'
	);
});

test('a run is a lane per loop level and one of its innermost steps, whatever wraps them', () => {
	const a = activity(pcrRun(), LOOPS);
	expect(a.lanes.map((l) => [l.kind, l.label])).toEqual([
		['loop', 'bias_voltage'],
		['loop', 'trigger_mV'],
		['steps', 'steps']
	]);
	expect(a.lanes[0].segments).toHaveLength(3);
	expect(a.lanes[1].segments).toHaveLength(6);
	expect(a.kinds).toEqual(['set_voltage', 'wait', 'set_threshold', 'count']);
	expect(a.lanes[2].segments).toHaveLength(3 * 2 + 3 * 2 * 2);
	expect(a.running).toBe(false);
	// Without the definition, a loop is named for itself.
	expect(activity(pcrRun()).lanes[0].label).toBe('sweep');
});

test('where a finished run was at a moment', () => {
	const a = activity(pcrRun(), LOOPS);
	// The second bias point: set (1), wait (50), then the first level's threshold (1) and count.
	const pointStart = 1 + 50 + 2 * 101;
	const ctx = contextAt(a, T0 + pointStart + 1 + 50 + 1 + 10)!;
	expect(ctx.crumbs.map((c) => c.label)).toEqual(['source_guard', 'bias_voltage 2 of 3', 'trigger_mV 1 of 2', 'count']);
	expect(ctx.levels.map((l) => [l.index, l.total])).toEqual([
		[1, 3],
		[0, 2]
	]);
	expect(ctx.step).toEqual({ name: 'count', mean: 100 });
	expect(ctx.inStep).toBe(10);
	expect(ctx.remaining).toBeNull();
	expect(contextAt(a, T0 - 1)).toBeNull();
});

test('a running run expects what followed the same place last round, and says how long is left', () => {
	// Into the third bias point's first count.
	const stop = 2 * 253 + 1 + 50 + 1 + 20;
	const a = activity(pcrRun(stop), LOOPS);
	expect(a.running).toBe(true);
	const ctx = contextAt(a, T0 + stop, T0 + stop)!;
	expect(ctx.crumbs.map((c) => c.label)).toEqual(['source_guard', 'bias_voltage 3 of 3', 'trigger_mV 1 of 2', 'count']);
	// The second trigger level, as at the last bias point; this is the last, so nothing after it.
	const countStart = 2 * 253 + 1 + 50 + 1;
	expect(expected(a, 10_000).map((e) => [e.name, e.t0 - T0, e.t1! - T0])).toEqual([
		['set_threshold', countStart + 100, countStart + 101],
		['count', countStart + 101, countStart + 201]
	]);
	// Earlier in the run, the pattern since the same place last round repeats,
	// as far as asked, and stops where the last bias point does.
	const earlier = 253 + 1 + 50 + 1 + 20;
	const before = activity(pcrRun(earlier), LOOPS);
	expect(expected(before, 10_000).map((e) => e.name)).toEqual([
		...['set_threshold', 'count', 'set_voltage', 'wait', 'set_threshold', 'count'],
		...['set_threshold', 'count']
	]);
	expect(expected(before, 150).map((e) => e.name)).toEqual(['set_threshold', 'count']);
	// The last bias point: what is left of its mean 253 ms.
	expect(ctx.levels[0].remaining).toBeCloseTo(253 - (1 + 50 + 1 + 20), 5);
	expect(ctx.remaining).toBe(ctx.levels[0].remaining);
});

test('every level of loop is there at every moment, the ones not running at their last round', () => {
	const a = activity(pcrRun(), LOOPS);
	expect(a.depths.map((d) => [d.label, d.total, d.finished, d.round])).toEqual([
		['bias_voltage', 3, 3, 253],
		['trigger_mV', 2, 6, 101]
	]);
	// The second bias point's settle: no trigger level runs, the last was the first point's second.
	const ctx = contextAt(a, T0 + 253 + 1 + 10)!;
	expect(ctx.levels.map((l) => [l.label, l.index, l.total, l.active])).toEqual([
		['bias_voltage', 1, 3, true],
		['trigger_mV', 1, 2, false]
	]);
	expect(ctx.crumbs.map((c) => [c.label, c.depth])).toEqual([
		['source_guard', undefined],
		['bias_voltage 2 of 3', 0],
		['wait', undefined]
	]);
	// Before any trigger level, it is there too, not yet begun.
	expect(contextAt(a, T0 + 10)!.levels[1]).toEqual({ label: 'trigger_mV', index: -1, total: 2, active: false, remaining: null });
});

test('a level of loop is worth a row if its rounds can be read or its whole pass watched', () => {
	// 253 ms bias points, 3 of them: neither; 101 ms trigger levels: neither.
	expect(readableLevels(activity(pcrRun(), LOOPS))).toEqual([false, false]);
	// With 1,000 bias points the pass is long enough to watch.
	expect(readableLevels(activity(pcrRun(), { ...LOOPS, 'source_guard/sweep[0]': { kind: 'sweep', parameter: 'bias_voltage', total: 1000 } }))).toEqual([true, false]);
	// A running run is not judged on fewer than three finished rounds.
	expect(readableLevels(activity(pcrRun(500), LOOPS))).toEqual([null, false]);
});

test('the estimate holds between two rounds, when the latest step has ended', () => {
	// Just after the first bias point ended: its last count is over, nothing of the next has begun.
	const stop = 253;
	const steps = pcrRun()
		.filter((s) => Date.parse(s.started_at) < T0 + stop)
		.map((s) => (s.ended_at && Date.parse(s.ended_at) > T0 + stop ? { ...s, ended_at: null, status: null } : s));
	const a = activity(steps, LOOPS);
	expect(a.running).toBe(true);
	const ctx = contextAt(a, T0 + stop + 5, T0 + stop + 5)!;
	expect(ctx.remaining).toBeCloseTo(2 * 253, 5);
});

test('a close-up shows a few rounds of the innermost loop, and a lane is picked by time', () => {
	const a = activity(pcrRun(), LOOPS);
	// A trigger level takes 101 ms, long enough to see: four of them, rounded up,
	// for the whole run, whatever step it is at.
	expect(closeUpSpan(a)).toEqual({ span: 500, settled: true });
	// Trigger levels of a millisecond would not show: it is sized by the bias points instead.
	const quickLevels = activity(pcrRun(), LOOPS);
	quickLevels.depths[1].round = 1;
	expect(closeUpSpan(quickLevels).span).toBe(2000);
	const lane = a.lanes.at(-1)!.segments;
	expect(a.execs[segmentAt(lane, T0 + 10, T0)!.exec].name).toBe('wait');
	expect(segmentAt(lane, T0 + 10_000, T0)).toBeNull();
});

test('a lane is drawn by pixel column: short steps add up, the longest in a column wins', () => {
	const segs: Segment[] = [];
	// 100 steps of 1 ms alternating two kinds, then one of 50 ms of a third.
	for (let i = 0; i < 100; i++) segs.push({ t0: i, t1: i + 1, cat: i % 2, exec: i });
	segs.push({ t0: 100, t1: 150, cat: 2, exec: 100 });
	const { cat, cover, mixed } = binLane(segs, 0, 150, 3, 3, 0);
	expect([...cover]).toEqual([1, 1, 1]);
	expect(mixed[0]).toBe(1); // two kinds, half each
	expect(cat[2]).toBe(2);
	expect(mixed[2]).toBe(0);
	// A column nothing ran in is empty.
	expect(binLane(segs, 200, 300, 4, 3, 0).cat[0]).toBe(-1);
});

test('time is said as briefly as reads well, and the kinds by their share of the run', () => {
	expect(span(0.4)).toBe('0.4 ms');
	expect(span(0.02)).toBe('<0.1 ms');
	expect(span(54)).toBe('54 ms');
	expect(span(2300)).toBe('2.3 s');
	expect(span(70_000)).toBe('1 min 10 s');
	const a = activity(pcrRun(), LOOPS);
	expect(a.kinds[timeByKind(a)[0].kind]).toBe('count');
});
