/** Lists every CSS feature in the built site that Qt5 WebEngine can't use.
 *
 * On Linux the wizard's window is Qt5 WebEngine, which is Chrome 87, and
 * Chrome drops what it doesn't understand without a word: a whole rule for an
 * unknown selector, a declaration for an unknown property or value. Tailwind 4
 * and Svelte target much newer browsers, so each gap only showed up as some
 * page looking wrong. This walks the finished CSS and looks each property,
 * value keyword, function, unit, selector and at-rule up in MDN's
 * browser-compat-data, the table behind MDN's compatibility charts.
 *
 * build.ts runs it on every build and fails on anything not in ALLOWED. Run it
 * by hand with: bun run css-compat.ts [directory]
 */
import bcd from '@mdn/browser-compat-data' with { type: 'json' };
import postcss, { type AtRule, type Node } from 'postcss';
import { readFileSync } from 'node:fs';
import path from 'node:path';

// Matches OLD_BROWSERS in vite.config.ts.
export const CHROME = 87;

/** Features found unsupported that are fine to ship, with why. Keys are the
 *  feature names the report prints. */
const ALLOWED: Record<string, string> = {
	'::file-selector-button': "Tailwind's reset for file inputs; the setup editor's file picker keeps Chrome's own button style.",
	'accent-color': 'Checkboxes and radios stay Chrome blue instead of the app accent.',
	'lh unit': "Tailwind's reset for date inputs' min-height; the app has none.",
	'scrollbar-width':
		"bits-ui hides the scrollbar in Select and Combobox lists. Select also uses ::-webkit-scrollbar, so only Combobox lists show one."
};

type Compat = { __compat?: { support: { chrome?: unknown } } } & { [key: string]: Compat };

/** The Chrome version a feature arrived in, or Infinity if it hasn't.
 *  Prefixed and flagged entries don't count: the built CSS uses the plain names. */
function chromeVersion(feature: Compat | undefined): number | undefined {
	const support = feature?.__compat?.support.chrome;
	if (support === undefined) return undefined;
	const entries = (Array.isArray(support) ? support : [support]) as Record<string, unknown>[];
	// The earliest counts even if partial: outline worked long before it
	// followed border-radius (Chrome 94).
	const versions = entries
		.filter((e) => !e.prefix && !e.alternative_name && !e.flags && typeof e.version_added === 'string')
		.map((e) => Number((e.version_added as string).replace('≤', '')))
		.filter(Number.isFinite);
	return versions.length ? Math.min(...versions) : Infinity;
}

const css = bcd.css as unknown as Record<'properties' | 'selectors' | 'at-rules' | 'types', Record<string, Compat>>;

/** Every function name under css.types (color-mix, oklch, linear-gradient, ...),
 *  however deep MDN files it. */
const functions = new Map<string, Compat>();
(function index(node: Record<string, Compat>) {
	for (const [name, child] of Object.entries(node)) {
		if (name === '__compat') continue;
		if (child.__compat && !functions.has(name)) functions.set(name, child);
		index(child);
	}
})(css.types);

const lengths = css.types.length;
const UNITS: Record<string, Compat> = {
	...Object.fromEntries(['lh', 'rlh', 'ic', 'ric', 'cap', 'rcap', 'rch', 'rex', 'vb', 'vi'].map((u) => [u, lengths[u]])),
	...Object.fromEntries(['dvh', 'dvw', 'dvb', 'dvi', 'dvmin', 'dvmax'].map((u) => [u, lengths.viewport_percentage_units_dynamic])),
	...Object.fromEntries(['lvh', 'lvw', 'lvb', 'lvi', 'lvmin', 'lvmax'].map((u) => [u, lengths.viewport_percentage_units_large])),
	...Object.fromEntries(['svh', 'svw', 'svb', 'svi', 'svmin', 'svmax'].map((u) => [u, lengths.viewport_percentage_units_small])),
	...Object.fromEntries(['cqw', 'cqh', 'cqi', 'cqb', 'cqmin', 'cqmax'].map((u) => [u, lengths.container_query_length_units]))
};

export type Finding = { feature: string; chrome: number; example: string; count: number };

/** Drops strings, escapes and attribute selectors, so only real syntax is left
 *  to scan: `.hover\:x` is a class name, not a :x pseudo-class. */
function stripNoise(text: string): string {
	return text
		.replace(/\\./g, '_')
		.replace(/"[^"]*"|'[^']*'/g, '""')
		.replace(/url\([^)]*\)/g, 'url()');
}

function inSupports(node: Node): boolean {
	for (let p = node.parent; p; p = p.parent) {
		if (p.type === 'atrule' && (p as AtRule).name === 'supports') return true;
	}
	return false;
}

export function checkCss(source: string): Finding[] {
	const found = new Map<string, Finding>();
	const report = (feature: string, compat: Compat | undefined, example: string) => {
		const chrome = chromeVersion(compat);
		if (chrome === undefined || chrome <= CHROME) return;
		const entry = found.get(feature);
		if (entry) entry.count++;
		else found.set(feature, { feature, chrome, example: example.slice(0, 160), count: 1 });
	};

	const checkValue = (value: string, example: string, property?: Compat) => {
		const text = stripNoise(value);
		for (const [, name] of text.matchAll(/([a-z-]+)\(/g)) {
			if (name !== 'var' && name !== 'url') report(`${name}()`, functions.get(name), example);
		}
		for (const [, unit] of text.matchAll(/\d(?:\.\d+)?([a-z]+)\b/g)) {
			report(`${unit} unit`, UNITS[unit], example);
		}
		if (property) {
			for (const [, word] of text.matchAll(/(?<![\w(-])([a-z][a-z-]*)(?![\w(-])/g)) {
				if (word in property && word !== '__compat') report(`value ${word}`, property[word], example);
			}
		}
	};

	postcss.parse(source).walk((node) => {
		if (inSupports(node)) return;
		if (node.type === 'decl') {
			const example = `${node.parent?.type === 'rule' ? node.parent.selector : ''} { ${node.prop}: ${node.value} }`;
			if (node.prop.startsWith('--')) return checkValue(node.value, example);
			const property = css.properties[node.prop];
			report(node.prop, property, example);
			// Keywords in a property Chrome lacks are moot, and a font list
			// falls through names it doesn't know to the next.
			const known = (chromeVersion(property) ?? 0) <= CHROME && node.prop !== 'font-family';
			checkValue(node.value, example, known ? property : undefined);
		} else if (node.type === 'rule') {
			const selector = stripNoise(node.selector);
			for (const [, colons, name] of selector.matchAll(/(::?)([a-z-]+)/g)) {
				report(`${colons}${name}`, css.selectors[name], node.selector);
			}
			// :not(a, b) and :not(a b) are Chrome 88; plain :not(a) is ancient.
			for (const [, inner] of selector.matchAll(/:not\(([^()]*)\)/g)) {
				if (/[,\s>+~]/.test(inner.trim())) report(':not() with a selector list', css.selectors.not.selector_list, node.selector);
			}
		} else if (node.type === 'atrule') {
			const example = `@${node.name} ${node.params}`;
			const rule = css['at-rules'][node.name];
			report(`@${node.name}`, rule, example);
			if (node.name === 'media' && /[<>]=?/.test(node.params)) {
				report('@media range syntax', rule?.range_syntax, example);
			}
			if (node.name === 'media') checkValue(node.params, example);
		}
	});
	return [...found.values()];
}

export function checkFiles(files: string[]): { problems: Finding[]; allowed: Finding[] } {
	const all = new Map<string, Finding>();
	for (const file of files) {
		for (const finding of checkCss(readFileSync(file, 'utf8'))) {
			const entry = all.get(finding.feature);
			if (entry) entry.count += finding.count;
			else all.set(finding.feature, finding);
		}
	}
	const findings = [...all.values()].sort((a, b) => a.feature.localeCompare(b.feature));
	return {
		problems: findings.filter((f) => !(f.feature in ALLOWED)),
		allowed: findings.filter((f) => f.feature in ALLOWED)
	};
}

export function formatFindings(findings: Finding[]): string {
	return findings
		.map((f) => `  ${f.feature} (Chrome ${f.chrome === Infinity ? 'none' : f.chrome}, ${f.count}x)\n      e.g. ${f.example}`)
		.join('\n');
}

if (import.meta.main) {
	const directory = process.argv[2] ?? path.join(import.meta.dir, '../backend/static');
	const files = [...new Bun.Glob('**/*.css').scanSync(directory)].map((f) => path.join(directory, f));
	const { problems, allowed } = checkFiles(files);
	if (allowed.length) console.log(`Allowed (see ALLOWED in css-compat.ts):\n${formatFindings(allowed)}`);
	if (problems.length) {
		console.log(`CSS that Chrome ${CHROME} can't use:\n${formatFindings(problems)}`);
		process.exit(1);
	}
	console.log(`No CSS features newer than Chrome ${CHROME} in ${files.length} files.`);
}
