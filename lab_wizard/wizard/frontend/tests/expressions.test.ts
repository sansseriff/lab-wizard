import { expect, test } from 'bun:test';
import { matching, parse, serialize, splitTopLevel, type Candidate } from '../src/lib/expressions/refs';

const COLUMNS = ['bias_voltage', 'sense_voltage', 'counts', 'phase'];

test('references become chips, everything else stays text, and it round-trips', () => {
	const text = '(bias_voltage - sense_voltage) / setup("bias_resistance") * param(\'readout.gate_s\')';
	const segments = parse(text, COLUMNS);
	expect(segments.filter((s) => 'ref' in s)).toEqual([
		{ ref: { kind: 'column', name: 'bias_voltage' } },
		{ ref: { kind: 'column', name: 'sense_voltage' } },
		{ ref: { kind: 'setup', name: 'bias_resistance' } },
		{ ref: { kind: 'param', name: 'readout.gate_s' } }
	]);
	// The quotes are normalised; the meaning is not.
	expect(serialize(segments)).toBe(
		'(bias_voltage - sense_voltage) / setup("bias_resistance") * param("readout.gate_s")'
	);
});

test('a function, a string, an unknown name and part of a name are not chips', () => {
	const segments = parse('mean(counts, phase == "counts") + countsx + nope', COLUMNS);
	expect(segments.filter((s) => 'ref' in s).map((s) => ('ref' in s ? s.ref.name : ''))).toEqual(['counts', 'phase']);
	expect(serialize(segments)).toBe('mean(counts, phase == "counts") + countsx + nope');
});

test('a list of expressions splits only at its top-level commas', () => {
	expect(splitTopLevel('counts, mean(counts, phase == "a,b"), x')).toEqual(['counts', 'mean(counts, phase == "a,b")', 'x']);
	expect(splitTopLevel(' ')).toEqual([]);
});

test('matches that start with what was typed come first, including a param path’s last part', () => {
	const candidates: Candidate[] = [
		{ kind: 'column', name: 'sense_voltage' },
		{ kind: 'setup', name: 'bias_resistance' },
		{ kind: 'param', name: 'bias.settle_s' },
		{ kind: 'column', name: 'bias_voltage' },
		{ kind: 'param', name: 'readout.gate_time_s' }
	];
	expect(matching(candidates, 'bias').map((c) => c.name)).toEqual(['bias_resistance', 'bias.settle_s', 'bias_voltage']);
	expect(matching(candidates, 'gate').map((c) => c.name)).toEqual(['readout.gate_time_s']);
	expect(matching(candidates, 'volt').map((c) => c.name)).toEqual(['sense_voltage', 'bias_voltage']);
});
