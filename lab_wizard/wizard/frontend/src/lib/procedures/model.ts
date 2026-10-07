/** The procedure definition as the composer edits it, and pure helpers over it.
 *
 * The shape is exactly the YAML under `config/procedures` (see
 * `lib/procedures/definition.py`), so what the composer holds is what is saved
 * — there is no second, UI-only model to keep in step with it. Everything the
 * composer knows about which fields a step has comes from the step catalog the
 * backend derives from the `*StepParams` schemas; nothing here names a step
 * type except the empty sequence a new slot starts as.
 */

export type Path = (string | number)[];

export type FieldKind = 'step' | 'steps' | 'role' | 'value' | 'values' | 'value_map' | 'literal';

export type FieldSpec = {
	kind: FieldKind;
	/** For a role field: behaviors that may fill it. Empty means any. */
	requires: string[];
	required: boolean;
	/** A step slot that may be left empty. */
	optional: boolean;
	default: unknown;
	literal_type: 'bool' | 'int' | 'float' | 'str' | 'any' | null;
	/** `records`: names a data column this step writes; `reads`: one it reads. */
	column: 'records' | 'reads' | null;
};

export type StepSpec = {
	type: string;
	group: 'flow' | 'instruments';
	summary: string;
	doc: string;
	emits: string[];
	fields: Record<string, FieldSpec>;
};

export type BehaviorSpec = {
	name: string;
	summary: string;
	satisfies: string[];
	bindable: boolean;
};

export type Catalog = {
	steps: Record<string, StepSpec>;
	behaviors: Record<string, BehaviorSpec>;
	/** This workspace's instruments able to fill each behavior. */
	fillers: Record<string, string[]>;
};

export type ParamType = 'float' | 'int' | 'bool' | 'str' | 'sweep';

export type ParamDecl = {
	type: ParamType;
	default?: unknown;
	description?: string;
	unit?: string | null;
};

export type ParamGroup = { [name: string]: ParamDecl | ParamGroup };

export type Step = { type: string; name?: string | null; [field: string]: any };

export type RoleDecl = { behavior: string; description?: string };

/** A per-column condition: a value, or one chosen per run. Ranges and lists are YAML-only. */
export type WhereCondition =
	| string
	| number
	| boolean
	| { per_run: 'min' | 'max' | 'first' | 'last' }
	| { in: unknown[] }
	| { range: [number, number] };

/** One of a procedure's plots: what a run of it is usually looked at as. */
export type PlotDecl = {
	name?: string | null;
	x: string;
	y: string[];
	y2?: string[];
	where?: Record<string, WhereCondition>;
	derived?: Record<string, string>;
	series?: string | null;
	label?: string | null;
	connect?: 'seq' | 'x' | 'none';
	kind?: 'line' | 'scatter' | 'histogram' | 'waterfall';
	log_x?: boolean;
	log_y?: boolean;
	/** What part of the axis to show: [low, high], a null end fits the data. */
	x_range?: AxisRange | null;
	y_range?: AxisRange | null;
};

export type AxisRange = [number | null, number | null];

export type Definition = {
	schema_version?: number;
	name: string;
	description: string;
	roles: Record<string, RoleDecl>;
	params: ParamGroup;
	body: Step;
	plots?: PlotDecl[];
	derived?: Record<string, string>;
	/** Facts about the setup its derived columns read with setup("name"), each bound by a measurement. */
	needs?: Record<string, { unit?: string | null; description?: string }>;
};

export type Problem = { path: Path; message: string };

export type CheckResult = {
	ok: boolean;
	problems: Problem[];
	/** Things that generate and run, but probably not as intended. */
	warnings?: Problem[];
	records: string[];
	python: string | null;
};

export const PER_RUN = ['min', 'max', 'first', 'last'] as const;

/** A typed `where` value: numbers and booleans as themselves, anything else as text. */
export function whereValue(text: string): string | number | boolean {
	const trimmed = text.trim();
	if (trimmed === 'true' || trimmed === 'false') return trimmed === 'true';
	if (trimmed !== '' && Number.isFinite(Number(trimmed))) return Number(trimmed);
	return text;
}

/** A default plot for a procedure: its first recorded column against its first swept one. */
export function newPlot(
	records: string[],
	swept: string[],
	taken: (string | null | undefined)[]
): PlotDecl {
	const x = swept[0] ?? records[0] ?? '';
	const y = records.find((c) => c !== x) ?? '';
	return {
		name: uniqueName(
			'plot',
			taken.filter((n): n is string => !!n)
		),
		x,
		y: y ? [y] : []
	};
}

export const PARAM_TYPES: ParamType[] = ['float', 'int', 'bool', 'str', 'sweep'];

export function blankDefinition(): Definition {
	return {
		name: 'new_procedure',
		description: '',
		roles: {},
		params: {},
		body: { type: 'sequence', children: [] }
	};
}

export function pathKey(path: Path): string {
	return path.join('.');
}

export function isParamDecl(entry: ParamDecl | ParamGroup | undefined): entry is ParamDecl {
	return !!entry && typeof (entry as ParamDecl).type === 'string';
}

export function isRef(
	value: unknown,
	key: 'param' | 'swept' | 'role'
): value is Record<string, string> {
	return !!value && typeof value === 'object' && !Array.isArray(value) && key in (value as object);
}

// --------------------------- reading ---------------------------

export function getAt(root: any, path: Path): any {
	let node = root;
	for (const part of path) {
		if (node == null) return undefined;
		node = node[part];
	}
	return node;
}

/** Every step in the tree with its path, parents before children. */
export function walkSteps(definition: Definition, catalog: Catalog): { path: Path; step: Step }[] {
	const out: { path: Path; step: Step }[] = [];
	const visit = (step: Step | null | undefined, path: Path) => {
		if (!step || typeof step !== 'object') return;
		out.push({ path, step });
		const spec = catalog.steps[step.type];
		if (!spec) return;
		for (const [name, field] of Object.entries(spec.fields)) {
			if (field.kind === 'step') visit(step[name], [...path, name]);
			if (field.kind === 'steps' && Array.isArray(step[name])) {
				step[name].forEach((child: Step, i: number) => visit(child, [...path, name, i]));
			}
		}
	};
	visit(definition.body, ['body']);
	return out;
}

/** Dotted names of every param leaf, with its declaration. */
export function paramLeaves(group: ParamGroup, prefix = ''): { name: string; decl: ParamDecl }[] {
	const out: { name: string; decl: ParamDecl }[] = [];
	for (const [name, entry] of Object.entries(group ?? {})) {
		if (isParamDecl(entry)) out.push({ name: `${prefix}${name}`, decl: entry });
		else out.push(...paramLeaves(entry, `${prefix}${name}.`));
	}
	return out;
}

/** Groups and leaves in display order, with paths for the parameter inspector. */
export function paramNodes(
	group: ParamGroup,
	path: string[] = []
): { path: string[]; entry: ParamDecl | ParamGroup }[] {
	return Object.entries(group ?? {}).flatMap(([name, entry]) => {
		const current = [...path, name];
		return [{ path: current, entry }, ...(isParamDecl(entry) ? [] : paramNodes(entry, current))];
	});
}

export function validIdentifier(name: string): boolean {
	return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name);
}

/** Can a role of `behavior` fill a field requiring one of `requires`? */
export function roleFits(
	catalog: Catalog,
	behavior: string | undefined,
	requires: string[]
): boolean {
	if (!requires.length) return true;
	const satisfies = (behavior && catalog.behaviors[behavior]?.satisfies) || [];
	return requires.some((r) => satisfies.includes(r));
}

/** Names a step binds for the steps inside it — a sweep's parameter. */
export function boundNames(step: Step, spec: StepSpec | undefined): string[] {
	if (!spec) return [];
	const hasChildren = Object.values(spec.fields).some(
		(f) => f.kind === 'step' || f.kind === 'steps'
	);
	if (!hasChildren) return [];
	return Object.entries(spec.fields)
		.filter(([name, f]) => f.column === 'records' && typeof step[name] === 'string' && step[name])
		.map(([name]) => step[name]);
}

// --------------------------- building ---------------------------

export function uniqueName(base: string, taken: Iterable<string>): string {
	const used = new Set(taken);
	const clean = base.replace(/[^0-9a-zA-Z_]/g, '_').replace(/^(\d)/, '_$1') || 'name';
	if (!used.has(clean)) return clean;
	for (let n = 2; ; n++) if (!used.has(`${clean}_${n}`)) return `${clean}_${n}`;
}

/** A new step of `type`: defaults filled, child slots empty sequences, and
 * role fields bound where `roleFor` says which role to use. */
export function newStep(
	catalog: Catalog,
	type: string,
	definition: Definition,
	roleFor: (field: string, spec: FieldSpec) => string | null = () => null
): Step {
	const spec = catalog.steps[type];
	const step: Step = { type };
	const sweepParam = paramLeaves(definition.params).find((p) => p.decl.type === 'sweep');
	for (const [name, field] of Object.entries(spec.fields)) {
		switch (field.kind) {
			case 'step':
				if (!field.optional) step[name] = { type: 'sequence', children: [] };
				break;
			case 'steps':
				step[name] = [];
				break;
			case 'role': {
				const role = roleFor(name, field);
				if (role) step[name] = { role };
				break;
			}
			case 'values':
				step[name] = sweepParam ? { param: sweepParam.name } : [];
				break;
			case 'value_map':
				step[name] = {};
				break;
			default:
				if (field.default !== null && field.default !== undefined) {
					step[name] = structuredClone(field.default);
				} else if (field.kind === 'literal' && field.literal_type === 'str') {
					step[name] = '';
				}
		}
	}
	return step;
}

/** Every `{role: from}` in the tree becomes `{role: to}`. */
export function renameRoleRefs(node: any, from: string, to: string): void {
	if (!node || typeof node !== 'object') return;
	if (Array.isArray(node)) {
		node.forEach((n) => renameRoleRefs(n, from, to));
		return;
	}
	if (Object.keys(node).length === 1 && node.role === from) node.role = to;
	for (const value of Object.values(node)) renameRoleRefs(value, from, to);
}

/** Every `{param: from…}` becomes `{param: to…}` — a group rename moves its leaves. */
export function renameParamRefs(node: any, from: string, to: string): void {
	if (!node || typeof node !== 'object') return;
	if (Array.isArray(node)) {
		node.forEach((n) => renameParamRefs(n, from, to));
		return;
	}
	if (Object.keys(node).length === 1 && typeof node.param === 'string') {
		if (node.param === from) node.param = to;
		else if (node.param.startsWith(`${from}.`)) node.param = to + node.param.slice(from.length);
	}
	for (const value of Object.values(node)) renameParamRefs(value, from, to);
}

/** Rename a key in place, keeping its position — YAML order is the user's order. */
export function renameKey<T>(record: Record<string, T>, from: string, to: string): void {
	if (from === to || !(from in record)) return;
	const entries = Object.entries(record);
	for (const key of Object.keys(record)) delete record[key];
	for (const [key, value] of entries) record[key === from ? to : key] = value;
}

/** A literal typed into a text box, as the value it most plausibly is. */
export function parseLiteral(text: string): unknown {
	const trimmed = text.trim();
	if (trimmed === 'true') return true;
	if (trimmed === 'false') return false;
	if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return Number(trimmed);
	return text;
}
