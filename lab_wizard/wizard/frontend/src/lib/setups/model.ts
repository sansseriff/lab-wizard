/** A setup's fields, and the needs a procedure binds to them (plans/setup_plan.md). */
import { isQuantity } from '$lib/data/model';
import { compatible } from './units';

/** A procedure's need: `bias_resistance: {unit: ohm}`. */
export type NeedDecl = { unit?: string | null; description?: string };
/** `{need: field path}`: which field fills each need. */
export type Bindings = Record<string, string>;

export type Image = { image: string };

export function isImage(v: unknown): v is Image {
	return (
		v !== null &&
		typeof v === 'object' &&
		!Array.isArray(v) &&
		Object.keys(v).length === 1 &&
		typeof (v as Image).image === 'string'
	);
}

export function isImages(v: unknown): v is Image[] {
	return Array.isArray(v) && v.length > 0 && v.every(isImage);
}

/** One leaf of a setup: what a need can be bound to. */
export type Leaf = {
	path: string;
	value: unknown;
	/** The value as a person reads it: `10 kΩ`. */
	shown: string;
	unit: string | null;
	numeric: boolean;
};

export function leaves(fields: Record<string, unknown>, prefix = ''): Leaf[] {
	const out: Leaf[] = [];
	for (const [key, value] of Object.entries(fields)) {
		const path = prefix ? `${prefix}.${key}` : key;
		if (value !== null && typeof value === 'object' && !Array.isArray(value) && !isQuantity(value) && !isImage(value)) {
			out.push(...leaves(value as Record<string, unknown>, path));
			continue;
		}
		const raw = isQuantity(value) ? value.value : value;
		const unit = isQuantity(value) ? value.unit || null : null;
		out.push({
			path,
			value,
			shown: isImage(value) || isImages(value) ? 'picture' : `${String(raw ?? '')}${unit ? ` ${unit}` : ''}`,
			unit,
			numeric: typeof raw === 'number'
		});
	}
	return out;
}

/** Why `leaf` cannot fill `need`, or null if it can. */
export function bindProblem(leaf: Leaf | undefined, need: NeedDecl): string | null {
	if (!leaf) return 'the setup has no such field';
	if (!leaf.numeric) return 'not a number';
	if (!compatible(leaf.unit, need.unit)) return `in ${leaf.unit}, not ${need.unit}`;
	return null;
}

/** Each need bound to a field of the same name, when the setup has one that fits. */
export function sameNameBindings(needs: Record<string, NeedDecl>, fields: Record<string, unknown>): Bindings {
	const byName = new Map(leaves(fields).map((l) => [l.path, l]));
	const out: Bindings = {};
	for (const [name, need] of Object.entries(needs)) {
		if (!bindProblem(byName.get(name), need)) out[name] = name;
	}
	return out;
}

/** `fields` with `value` set at the dotted `path`, groups made as needed. */
export function withField(fields: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
	const [head, ...rest] = path.split('.');
	if (!rest.length) return { ...fields, [head]: value };
	const inner = fields[head];
	const group =
		inner !== null && typeof inner === 'object' && !Array.isArray(inner) && !isQuantity(inner) && !isImage(inner)
			? (inner as Record<string, unknown>)
			: {};
	return { ...fields, [head]: withField(group, rest.join('.'), value) };
}

