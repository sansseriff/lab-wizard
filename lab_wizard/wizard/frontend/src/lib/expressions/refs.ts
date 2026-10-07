/** The names an expression refers to, as chips rather than syntax.
 *
 * An expression is stored as the backend reads it (`lib/data/expressions.py`):
 * `(bias_voltage - sense_voltage) / setup("bias_resistance")`. Editing it, a
 * person sees `bias_voltage`, `sense_voltage` and `bias_resistance` as chips —
 * a column, a column, a setup need — and never types `setup("…")` or
 * `param("…")`. This module turns one form into the other.
 */

export type RefKind = 'column' | 'param' | 'setup';
export type Ref = { kind: RefKind; name: string };

/** What can be picked while typing: a reference, or a function to call. */
export type Candidate =
	| (Ref & { detail?: string })
	| { kind: 'function'; name: string; detail?: string };

export type Segment = { text: string } | { ref: Ref };

/** The expression text of a reference. */
export function refText(ref: Ref): string {
	if (ref.kind === 'column') return ref.name;
	return `${ref.kind}(${JSON.stringify(ref.name)})`;
}

const CALL = /(param|setup)\(\s*(["'])((?:(?!\2).)*)\2\s*\)/y;
const NAME = /[A-Za-z_][A-Za-z0-9_]*/y;

/** `text` cut into plain text and references. Only known columns become chips. */
export function parse(text: string, columns: Iterable<string>): Segment[] {
	const known = new Set(columns);
	const out: Segment[] = [];
	let plain = '';
	let i = 0;
	const flush = () => {
		if (plain) out.push({ text: plain });
		plain = '';
	};
	while (i < text.length) {
		const ch = text[i];
		if (ch === '"' || ch === "'") {
			// A string literal ("background") is never a reference.
			const end = text.indexOf(ch, i + 1);
			const stop = end < 0 ? text.length : end + 1;
			plain += text.slice(i, stop);
			i = stop;
			continue;
		}
		CALL.lastIndex = i;
		const call = CALL.exec(text);
		if (call && !/[A-Za-z0-9_.]/.test(text[i - 1] ?? '')) {
			flush();
			out.push({ ref: { kind: call[1] as RefKind, name: call[3] } });
			i = CALL.lastIndex;
			continue;
		}
		NAME.lastIndex = i;
		const name = /[A-Za-z0-9_.]/.test(text[i - 1] ?? '') ? null : NAME.exec(text);
		if (name) {
			const after = text.slice(NAME.lastIndex).trimStart();
			if (known.has(name[0]) && !after.startsWith('(')) {
				flush();
				out.push({ ref: { kind: 'column', name: name[0] } });
			} else plain += name[0];
			i = NAME.lastIndex;
			continue;
		}
		plain += ch;
		i++;
	}
	flush();
	return out;
}

export function serialize(segments: Segment[]): string {
	return segments.map((s) => ('ref' in s ? refText(s.ref) : s.text)).join('');
}

/** `text` split at its top-level commas: `mean(x, a == 1), y` is two expressions. */
export function splitTopLevel(text: string): string[] {
	const out: string[] = [];
	let depth = 0;
	let quote: string | null = null;
	let start = 0;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (quote) {
			if (ch === quote) quote = null;
		} else if (ch === '"' || ch === "'") quote = ch;
		else if (ch === '(') depth++;
		else if (ch === ')') depth = Math.max(0, depth - 1);
		else if (ch === ',' && depth === 0) {
			out.push(text.slice(start, i));
			start = i + 1;
		}
	}
	out.push(text.slice(start));
	return out.map((s) => s.trim()).filter(Boolean);
}

export const FUNCTIONS: Candidate[] = [
	...['mean', 'min', 'max', 'sum', 'count', 'first', 'last'].map((name) => ({
		kind: 'function' as const,
		name,
		detail: 'per run, optionally where a condition holds'
	})),
	...['abs', 'sqrt', 'exp', 'log', 'log10'].map((name) => ({ kind: 'function' as const, name }))
];

/** Candidates matching what is being typed, best first: names that start with it, then contain it. */
export function matching(candidates: Candidate[], typed: string, limit = 12): Candidate[] {
	const needle = typed.toLowerCase();
	if (!needle) return candidates.slice(0, limit);
	const starts: Candidate[] = [];
	const contains: Candidate[] = [];
	for (const c of candidates) {
		const name = c.name.toLowerCase();
		const leaf = name.split('.').pop() ?? name;
		if (name.startsWith(needle) || leaf.startsWith(needle)) starts.push(c);
		else if (name.includes(needle)) contains.push(c);
	}
	return [...starts, ...contains].slice(0, limit);
}

/** Everything an expression over some runs can refer to, in the order offered. */
export function expressionCandidates({
	columns = [],
	params = [],
	needs = {}
}: {
	columns?: string[];
	/** Numeric params, by dotted path, with their units. */
	params?: { name: string; unit?: string | null }[];
	needs?: Record<string, { unit?: string | null; description?: string }>;
}): Candidate[] {
	return [
		...Object.entries(needs).map(([name, need]) => ({
			kind: 'setup' as const,
			name,
			detail: need.unit ? `setup · ${need.unit}` : 'setup'
		})),
		...columns.map((name) => ({ kind: 'column' as const, name })),
		...params.map((p) => ({ kind: 'param' as const, name: p.name, detail: p.unit ? `param · ${p.unit}` : 'param' })),
		...FUNCTIONS
	];
}
