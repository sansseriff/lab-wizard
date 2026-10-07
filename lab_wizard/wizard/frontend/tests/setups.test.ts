import { expect, test } from 'bun:test';
import { bindProblem, leaves, sameNameBindings, withField } from '../src/lib/setups/model';
import { baseUnit, compatible, toBase } from '../src/lib/setups/units';

const FIELDS = {
	cryostat: 'BlueFors1',
	channel2: { bias_resistor: { value: 10, unit: 'kΩ' } },
	bias_resistance: { value: 100, unit: 'kohm' },
	qcl_power: { value: 12, unit: 'mW' },
	wiring: { image: '3f9a.jpg' }
};

test('a prefixed unit has the base unit the backend gives it', () => {
	expect(toBase('kΩ')).toEqual([1000, 'Ω']);
	expect(baseUnit('kohm')).toBe('Ω');
	expect(baseUnit('dBm')).toBe('dBm');
	expect(compatible('MΩ', 'ohm')).toBe(true);
	expect(compatible('mW', 'ohm')).toBe(false);
	expect(compatible(null, 'ohm')).toBe(true);
});

test("a setup's leaves are its fields, groups opened, pictures included", () => {
	const all = leaves(FIELDS);
	expect(all.map((l) => l.path)).toEqual(['cryostat', 'channel2.bias_resistor', 'bias_resistance', 'qcl_power', 'wiring']);
	expect(all[1].shown).toBe('10 kΩ');
	expect(all[4].shown).toBe('picture');
});

test('a field fills a need only as a number in its unit', () => {
	const byPath = new Map(leaves(FIELDS).map((l) => [l.path, l]));
	const need = { unit: 'ohm' };
	expect(bindProblem(byPath.get('channel2.bias_resistor'), need)).toBeNull();
	expect(bindProblem(byPath.get('qcl_power'), need)).toBe('in mW, not ohm');
	expect(bindProblem(byPath.get('cryostat'), need)).toBe('not a number');
	expect(bindProblem(undefined, need)).toBe('the setup has no such field');
});

test('a need is bound to a field of its own name when that field fits', () => {
	expect(sameNameBindings({ bias_resistance: { unit: 'ohm' }, power: { unit: 'W' } }, FIELDS)).toEqual({
		bias_resistance: 'bias_resistance'
	});
});

test('a field is added at a dotted path, groups made as needed', () => {
	const next = withField(FIELDS, 'channel3.bias_resistor', { value: 1, unit: 'MΩ' });
	expect(next.channel3).toEqual({ bias_resistor: { value: 1, unit: 'MΩ' } });
	expect(withField(FIELDS, 'channel2.balun', 'BAL-0006').channel2).toEqual({
		bias_resistor: { value: 10, unit: 'kΩ' },
		balun: 'BAL-0006'
	});
});
