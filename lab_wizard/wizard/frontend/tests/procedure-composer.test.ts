import { expect, test } from 'bun:test';
import { plugin } from 'bun';
import { compileModule } from 'svelte/compiler';
import { transpileModule, ScriptTarget, ModuleKind } from 'typescript';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import {
	blankDefinition,
	newPlot,
	whereValue,
	type Catalog,
	type FieldKind,
	type FieldSpec,
	type Step
} from '../src/lib/procedures/model';
import {
	cleanupSummary,
	stepContext,
	stepSummary,
	valueSummary
} from '../src/lib/procedures/presentation';

// Run the real rune-based editor with Svelte's client reactivity, without a DOM.
plugin({
	name: 'svelte-editor-tests',
	setup(build) {
		build.onLoad({ filter: /editor\.svelte\.ts$/ }, ({ path }) => ({
			contents: compileModule(
				transpileModule(
					readFileSync(path, 'utf8').replace(
						"'$lib/api'",
						JSON.stringify(resolve(dirname(path), '../api/index.ts'))
					),
					{ compilerOptions: { target: ScriptTarget.ESNext, module: ModuleKind.ESNext } }
				).outputText,
				{ filename: path, generate: 'client' }
			).js.code,
			loader: 'js'
		}));
	}
});
const { ProcedureEditor } = await import('../src/lib/procedures/editor.svelte');

const field = (kind: FieldKind, extra: Partial<FieldSpec> = {}): FieldSpec => ({
	kind,
	requires: [],
	required: true,
	optional: false,
	default: null,
	literal_type: null,
	column: null,
	...extra
});
const stepFields: Record<string, Record<string, FieldSpec>> = {
	sequence: { children: field('steps') },
	count: { gate_time: field('value') },
	wait: { seconds: field('value') },
	sweep: {
		parameter: field('literal', { column: 'records' }),
		values: field('values'),
		body: field('step')
	},
	with_parameter: {
		parameter: field('literal', { column: 'records' }),
		value: field('value'),
		body: field('step')
	},
	source_guard: { source: field('role'), body: field('step') },
	if: {
		condition: field('step'),
		then: field('step'),
		otherwise: field('step', { optional: true })
	}
};
const catalog: Catalog = {
	steps: Object.fromEntries(
		Object.entries(stepFields).map(([type, fields]) => [
			type,
			{ type, fields, group: 'flow', summary: '', doc: '', emits: [] }
		])
	),
	behaviors: {},
	fillers: {}
};
function editorFor(body: Step) {
	return new ProcedureEditor(catalog, { ...blankDefinition(), body }, null, null);
}

test('selection follows a moved step and wrapping/replacement keeps the inspector on the new container', () => {
	const editor = editorFor({
		type: 'sequence',
		children: [
			{ type: 'count', gate_time: 1 },
			{ type: 'wait', seconds: 2 }
		]
	});
	const original = editor.selectedStep;
	editor.moveStep(['body', 'children', 0], 1);
	expect(editor.selectedStep).toBe(original);
	expect(editor.selectedPath).toEqual(['body', 'children', 1]);
	editor.wrapStep(editor.selectedPath!, 'sweep');
	expect(editor.selectedStep?.type).toBe('sweep');
	expect(editor.selectedStep?.body.type).toBe('count');
	editor.unwrapStep(editor.selectedPath!);
	expect(editor.selectedStep?.type).toBe('count');
	editor.replaceStep(editor.selectedPath!, 'wait');
	expect(editor.selectedStep?.type).toBe('wait');
});

test('insertion selects the new step; removal selects an existing sibling, then the empty sequence', () => {
	const editor = editorFor({ type: 'sequence', children: [{ type: 'wait', seconds: 1 }] });
	editor.insertStep(['body', 'children'], 'count', null, 0);
	expect(editor.selectedPath).toEqual(['body', 'children', 0]);
	expect(editor.selectedStep?.type).toBe('count');
	editor.removeStep(editor.selectedPath!);
	expect(editor.selectedStep?.type).toBe('wait');
	editor.removeStep(editor.selectedPath!);
	expect(editor.selectedPath).toEqual(['body']);
	expect(editor.definition.body.children).toEqual([]);
});

test('required child slots become empty sequences while optional branches can be removed', () => {
	const editor = editorFor({
		type: 'if',
		condition: { type: 'wait', seconds: 1 },
		then: { type: 'count', gate_time: 1 },
		otherwise: { type: 'wait', seconds: 2 }
	});
	editor.removeStep(['body', 'then']);
	expect(editor.definition.body.then).toEqual({ type: 'sequence', children: [] });
	editor.removeStep(['body', 'otherwise']);
	expect(editor.definition.body.otherwise).toBeUndefined();
});

test('revealing a hidden step expands its ancestors and preserves unrelated collapsed groups', () => {
	const editor = editorFor({
		type: 'sequence',
		children: [
			{ type: 'sweep', parameter: 'a', body: { type: 'count', gate_time: 1 } },
			{ type: 'sweep', parameter: 'b', body: { type: 'wait', seconds: 1 } }
		]
	});
	editor.toggleStep(editor.definition.body.children[0]);
	editor.toggleStep(editor.definition.body.children[1]);
	editor.selectStep(['body', 'children', 0, 'body']);
	expect(editor.collapsedSteps).toEqual([editor.definition.body.children[1]]);
});

test('nested scopes retain phase labels, shadow matching labels, and only expose enclosing variables', () => {
	const definition = {
		...blankDefinition(),
		body: {
			type: 'with_parameter',
			parameter: 'phase',
			value: 'background',
			body: {
				type: 'with_parameter',
				parameter: 'phase',
				value: 'signal',
				body: {
					type: 'sweep',
					parameter: 'attenuation_db',
					values: [30, 0],
					body: { type: 'count', gate_time: 1 }
				}
			}
		}
	};
	const context = stepContext(definition, catalog, ['body', 'body', 'body', 'body']);
	expect(context.bindings).toEqual([
		['phase', 'signal'],
		['attenuation_db', 'current sweep value']
	]);
	expect(stepContext(definition, catalog, ['body', 'body', 'body']).scope).toEqual(['phase']);
});

test('summaries distinguish fixed values, defaults, missing parameters and current sweep values', () => {
	const definition = blankDefinition();
	definition.params = {
		gate: { type: 'float', default: 2, unit: 's' },
		sweep: { type: 'sweep', default: { mode: 'explicit', values: [30, 15, 0] }, unit: 'dB' }
	};
	expect(valueSummary({ param: 'gate' }, definition)).toBe('2 s · default');
	expect(valueSummary({ param: 'sweep' }, definition)).toBe('30, 15, 0 dB · default');
	expect(valueSummary({ param: 'gone' }, definition)).toContain('Missing parameter');
	expect(valueSummary({ swept: 'bias' }, definition)).toBe('Current bias');
	expect(stepSummary({ type: 'count', gate_time: 1 }, definition, catalog)).toBe('1 s');
});

test('source cleanup summaries respect disabled and parameterized cleanup options', () => {
	const definition = blankDefinition();
	definition.params = { cleanup: { type: 'bool', default: false } };
	const step = {
		type: 'source_guard',
		source: { role: 'bias' },
		return_to_zero: false,
		turn_off_at_end: false
	};
	expect(cleanupSummary(step, definition)).toContain('no source cleanup enabled');
	expect(cleanupSummary({ ...step, return_to_zero: { param: 'cleanup' } }, definition)).toContain(
		'return to 0 V if No · default'
	);
});

test('a stale validation response cannot mark the edited definition current', async () => {
	const editor = editorFor({ type: 'wait', seconds: 1 });
	const originalFetch = globalThis.fetch;
	let respond!: (response: Response) => void;
	let requested!: () => void;
	const inFlight = new Promise<void>((resolve) => (requested = resolve));
	globalThis.fetch = (() =>
		new Promise<Response>((resolve) => {
			respond = resolve;
			requested();
		})) as unknown as typeof fetch;
	try {
		const check = editor.runCheck();
		await inFlight; // the check is on its way; the edit below races it
		editor.setAt(['body', 'seconds'], 2);
		editor.scheduleCheck(60_000);
		respond(Response.json({ ok: true, problems: [], records: [], python: '' }));
		await check;
		expect(editor.checkCurrent).toBe(false);
		expect(editor.checking).toBe(true);
	} finally {
		editor.dispose();
		globalThis.fetch = originalFetch;
	}
});

test('saving an in-flight snapshot leaves later edits unsaved', () => {
	const editor = editorFor({ type: 'wait', seconds: 1 });
	const snapshot = JSON.parse(editor.json);
	editor.definition.description = 'Typed while saving';
	editor.definition.name = 'next_name';
	editor.markSaved('workspace', snapshot);
	expect(editor.dirty).toBe(true);
	expect(editor.loadedName).toBe(snapshot.name);
	expect(JSON.parse(editor.savedJson).description).toBe(snapshot.description);
	editor.markSaved('workspace');
	expect(editor.dirty).toBe(false);
});

test('nested parameter groups remain selectable and renaming updates nested workflow references', async () => {
	const { paramNodes, validIdentifier } = await import('../src/lib/procedures/model');
	const editor = editorFor({
		type: 'count',
		gate_time: { param: 'readout.gate_s' },
		counter: { role: 'counter' }
	});
	editor.definition.params = { readout: { gate_s: { type: 'float', default: 1 } }, empty: {} };
	editor.definition.roles = { counter: { behavior: 'Counter' }, spare: { behavior: 'Counter' } };
	expect(paramNodes(editor.definition.params).map((n) => n.path.join('.'))).toEqual([
		'readout',
		'readout.gate_s',
		'empty'
	]);
	expect(editor.renameParam([], 'readout', 'timing')).toBe(true);
	expect(editor.definition.body.gate_time).toEqual({ param: 'timing.gate_s' });
	expect(editor.renameParam(['timing'], 'gate_s', 'duration')).toBe(true);
	expect(editor.definition.body.gate_time).toEqual({ param: 'timing.duration' });
	expect(editor.renameRole('counter', 'detector')).toBe(true);
	expect(editor.definition.body.counter).toEqual({ role: 'detector' });
	expect(editor.renameRole('detector', 'spare')).toBe(false);
	expect(editor.definition.body.counter).toEqual({ role: 'detector' });
	expect(validIdentifier('gate_s')).toBe(true);
	expect(validIdentifier('gate.s')).toBe(false);
	expect(validIdentifier('3gate')).toBe(false);
});

test('a plot condition keeps numbers and booleans typed, and anything else as text', () => {
	expect(whereValue('-50')).toBe(-50);
	expect(whereValue('0.5')).toBe(0.5);
	expect(whereValue('true')).toBe(true);
	expect(whereValue('signal')).toBe('signal');
	expect(whereValue('')).toBe('');
});

test('a new plot puts the first recorded column against the first sweep, with a fresh name', () => {
	const plot = newPlot(['bias', 'counts', 'count_rate'], ['bias'], ['plot']);
	expect(plot).toEqual({ name: 'plot_2', x: 'bias', y: ['counts'] });
	expect(newPlot([], [], [])).toEqual({ name: 'plot', x: '', y: [] });
});
