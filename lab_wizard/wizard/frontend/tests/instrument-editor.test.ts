import { expect, test } from 'bun:test';
import {
	childTypes,
	existingParentChain,
	matchesTree,
	nodeAt,
	parentCandidates,
	resolveSchema,
	sortedChildren
} from '../src/lib/instruments/model';
import type { TreeItem, InstrumentMeta } from '../src/lib/types/instruments';
const leaf = (): TreeItem => ({
	type: 'module',
	key: 'same-slot',
	fields: { slot: '1', attribute_name: 'voltage' },
	children: {}
});

test('adding fake928 advances from root choices to fake900 only in the chosen rack', () => {
	const racks: TreeItem[] = ['gpib-a', 'gpib-b'].map((key) => ({
		type: 'fakegpib', key, fields: {}, children: {
			'same-gpib-address': { type: 'fake900', key: 'same-gpib-address', fields: {}, children: {} }
		}
	}));
	expect(parentCandidates(racks, 'fakegpib', []).map((node) => node.key)).toEqual(['gpib-a', 'gpib-b']);
	for (const rack of racks) {
		const candidates = parentCandidates(racks, 'fake900', [{ type: rack.type, key: rack.key }]);
		expect(candidates).toHaveLength(1);
		expect(candidates[0]).toBe(rack.children['same-gpib-address']);
		expect(candidates[0].type).toBe('fake900');
		expect(candidates.some((node) => node.key === rack.key)).toBe(false);
	}
	expect(parentCandidates(racks, 'fake900', [{ type: 'fakegpib', key: '' }])).toEqual([]);
	expect(parentCandidates(racks, 'fake900', [{ type: 'fakegpib', key: 'removed' }])).toEqual([]);
});
const tree: TreeItem[] = ['rack-a', 'rack-b'].map((key) => ({
	type: 'rack',
	key,
	fields: { port: key },
	children: { 'same-slot': leaf() }
}));

test('selection and prefilled ancestors retain the entire path across repeated child hashes', () => {
	const path = [
		{ type: 'rack', key: 'rack-b' },
		{ type: 'module', key: 'same-slot' }
	];
	expect(nodeAt(tree, path)).toBe(tree[1].children['same-slot']);
	expect(nodeAt(tree, [{ type: 'wrong', key: 'rack-b' }])).toBeNull();
	expect(existingParentChain(path)).toEqual([
		{ type: 'module', key: 'same-slot', action: 'use_existing', resolved: true },
		{ type: 'rack', key: 'rack-b', action: 'use_existing', resolved: true }
	]);
});

test('search retains ancestors of matching instruments and matches physical addresses', () => {
	expect(matchesTree(tree[0], 'VOLTAGE')).toBe(true);
	expect(matchesTree(tree[0], 'slot 1')).toBe(true);
	expect(matchesTree(tree[0], 'rack-b')).toBe(false);
	expect(matchesTree(tree[1], 'rack-b')).toBe(true);
});

test('parent picker offers only direct children and schemas resolve channel definitions', () => {
	const metadata = {
		child: { type: 'child', parent_type: 'rack' },
		unrelated: { type: 'unrelated', parent_type: 'other-rack' },
		root: { type: 'root', parent_type: null }
	} as unknown as Record<string, InstrumentMeta>;
	expect(childTypes(metadata, tree[0]).map((m) => m.type)).toEqual(['child']);
	expect(
		resolveSchema(
			{ $ref: '#/$defs/Channel' },
			{ $defs: { Channel: { type: 'object', properties: { gain: { type: 'number' } } } } }
		).properties?.gain.type
	).toBe('number');
});

test('children sort numerically by slot, with unslotted children after in saved order', () => {
	const child = (key: string, slot?: string | number): TreeItem => ({
		type: 'module',
		key,
		fields: slot == null ? {} : { slot },
		children: {}
	});
	const parent: TreeItem = {
		type: 'rack',
		key: 'rack',
		fields: {},
		children: Object.fromEntries(
			[child('a', '10'), child('b'), child('c', 2), child('d', '1'), child('e')].map((c) => [
				c.key,
				c
			])
		)
	};
	expect(sortedChildren(parent).map((c) => c.key)).toEqual(['d', 'c', 'a', 'b', 'e']);
});
