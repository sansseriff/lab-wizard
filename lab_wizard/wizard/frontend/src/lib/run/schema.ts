/** Reading a params model's JSON schema (pydantic's), to render and check a form from it.
 *
 * The backend's model is the source of truth: it checks the whole project on
 * save. This is the part of that check worth doing as someone types — a number
 * that is not a number, a value under its minimum — so a mistake shows at the
 * field, before Save.
 */

export type JsonSchema = {
	type?: string;
	title?: string;
	description?: string;
	default?: unknown;
	const?: unknown;
	enum?: unknown[];
	properties?: Record<string, JsonSchema>;
	items?: JsonSchema;
	$ref?: string;
	allOf?: JsonSchema[];
	anyOf?: JsonSchema[];
	oneOf?: JsonSchema[];
	discriminator?: { propertyName: string; mapping?: Record<string, string> };
	minimum?: number;
	maximum?: number;
	exclusiveMinimum?: number;
	exclusiveMaximum?: number;
	$defs?: Record<string, JsonSchema>;
};

export type FieldKind = 'group' | 'choice' | 'boolean' | 'number' | 'integer' | 'text' | 'enum' | 'numbers' | 'other';

/** ``schema`` with any ``$ref`` (or pydantic's ``allOf: [$ref]`` wrapper) followed. */
export function resolve(schema: JsonSchema, defs: Record<string, JsonSchema>): JsonSchema {
	let s = schema;
	for (let i = 0; i < 10; i++) {
		const ref = s.$ref ?? (s.allOf?.length === 1 ? s.allOf[0].$ref : undefined);
		if (!ref) break;
		const target = defs[ref.split('/').pop() ?? ''] ?? {};
		const { $ref: _ref, allOf: _allOf, ...rest } = s;
		s = { ...target, ...rest };
	}
	return s;
}

/** The variants of a choice (a discriminated union), resolved, by their tag. */
export function variants(schema: JsonSchema, defs: Record<string, JsonSchema>): Record<string, JsonSchema> {
	const tag = schema.discriminator?.propertyName;
	const out: Record<string, JsonSchema> = {};
	if (!tag) return out;
	for (const option of schema.oneOf ?? schema.anyOf ?? []) {
		const variant = resolve(option, defs);
		const value = variant.properties?.[tag]?.const ?? variant.properties?.[tag]?.default;
		if (typeof value === 'string') out[value] = variant;
	}
	return out;
}

export function kindOf(schema: JsonSchema): FieldKind {
	if (schema.discriminator && (schema.oneOf || schema.anyOf)) return 'choice';
	if (schema.properties) return 'group';
	if (schema.enum) return 'enum';
	if (schema.type === 'boolean') return 'boolean';
	if (schema.type === 'integer') return 'integer';
	if (schema.type === 'number') return 'number';
	if (schema.type === 'string') return 'text';
	if (schema.type === 'array' && ['number', 'integer'].includes(schema.items?.type ?? '')) return 'numbers';
	return 'other';
}

/** A field's name for a person: its key, with underscores as spaces. */
export function label(key: string): string {
	return key.replace(/_/g, ' ');
}

/** A value for ``schema`` when none is given: its default, else an empty one of its kind. */
export function defaultFor(schema: JsonSchema, defs: Record<string, JsonSchema>): unknown {
	const s = resolve(schema, defs);
	if (s.default !== undefined) return structuredClone(s.default);
	if (s.const !== undefined) return s.const;
	switch (kindOf(s)) {
		case 'group':
			return Object.fromEntries(
				Object.entries(s.properties ?? {}).map(([k, v]) => [k, defaultFor(v, defs)])
			);
		case 'boolean':
			return false;
		case 'numbers':
			return [];
		case 'number':
		case 'integer':
			return 0;
		default:
			return '';
	}
}

/** What is wrong with ``value`` as a number for ``schema``, or ``null``. */
export function numberProblem(text: string, schema: JsonSchema, integer: boolean): string | null {
	if (text.trim() === '') return 'Needs a value.';
	const n = Number(text);
	if (!Number.isFinite(n)) return 'Not a number.';
	if (integer && !Number.isInteger(n)) return 'Must be a whole number.';
	if (schema.minimum !== undefined && n < schema.minimum) return `At least ${schema.minimum}.`;
	if (schema.maximum !== undefined && n > schema.maximum) return `At most ${schema.maximum}.`;
	if (schema.exclusiveMinimum !== undefined && n <= schema.exclusiveMinimum)
		return `More than ${schema.exclusiveMinimum}.`;
	if (schema.exclusiveMaximum !== undefined && n >= schema.exclusiveMaximum)
		return `Less than ${schema.exclusiveMaximum}.`;
	return null;
}

/** A list of numbers as typed, ``"0, 0.1, 0.2"``; ``null`` if any is not a number. */
export function parseNumbers(text: string): number[] | null {
	const parts = text.split(/[\s,]+/).filter(Boolean);
	const numbers = parts.map(Number);
	return numbers.every(Number.isFinite) ? numbers : null;
}

export const pathKey = (path: (string | number)[]): string => path.join('.');
