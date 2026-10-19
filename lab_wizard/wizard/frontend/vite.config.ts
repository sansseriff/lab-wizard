import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import legacy from '@vitejs/plugin-legacy';
import postcss, { type Plugin as PostcssPlugin } from 'postcss';
import postcssPresetEnv from 'postcss-preset-env';
import type { Plugin } from 'vite';

// On Linux the wizard's window is Qt5 WebEngine, which is Chrome 87. That
// browser drops cascade layers (@layer, Chrome 99), which hold all of
// Tailwind 4's utilities, and :is() and :where() (Chrome 88), which Tailwind
// uses for space-y, divide-y and group-hover and Svelte uses in scoped styles.
// Each takes whole rules with it. postcss-preset-env rewrites @layer and :is().
//
// Its layer polyfill works by raising specificity, sized from the most
// specific selector anywhere in the CSS, so it must see all of it at once: run
// per file, an unlayered component style can lose to a layered Tailwind reset.
// SvelteKit always splits CSS into one file per route, so the finished files
// are joined, processed once, and split back.
const OLD_BROWSERS = 'chrome >= 87';

// There is no polyfill for :where(), since its zero specificity has no
// equivalent in plain selectors. It becomes :is(), which postcss-preset-env
// then expands; the only difference is that the selector inside now counts
// toward specificity.
const whereToIs: PostcssPlugin = {
	postcssPlugin: 'where-to-is',
	Rule(rule) {
		if (rule.selector.includes(':where(')) rule.selector = rule.selector.replaceAll(':where(', ':is(');
	}
};

// Tailwind 4's translate-*, rotate-* and scale-* utilities set the individual
// transform properties (Chrome 104), which Chrome 87 ignores: a dialog centred
// with left-1/2 -translate-x-1/2 sat with its left edge mid-screen, its right
// half off the window. Nothing polyfills them, so each becomes a transform.
// That would clash on an element also given a transform, or more than one of
// these, but nothing in the app combines them.
const TRANSFORM_FUNCTIONS: Record<string, string> = { translate: 'translate', rotate: 'rotate', scale: 'scale' };

/** Splits a value on spaces outside parentheses: "var(--a) var(--b)" -> two parts. */
function splitTopLevel(value: string): string[] {
	const parts: string[] = [];
	let depth = 0;
	let current = '';
	for (const char of value.trim()) {
		if (char === '(') depth++;
		if (char === ')') depth--;
		if (/\s/.test(char) && depth === 0) {
			if (current) parts.push(current);
			current = '';
		} else {
			current += char;
		}
	}
	if (current) parts.push(current);
	return parts;
}

// Dynamic viewport units (dvh, Chrome 108) make the whole declaration invalid
// in Chrome 87, so pages sized with h-[calc(100dvh-...)] lost their height.
// dvh differs from vh only where browser toolbars come and go, which a
// desktop window doesn't have.
const DYNAMIC_VIEWPORT_UNIT = /(\d)dv(h|w|min|max)\b/g;

const oldBrowserRewrites: PostcssPlugin = {
	postcssPlugin: 'old-browser-rewrites',
	Declaration(decl) {
		decl.value = decl.value.replace(DYNAMIC_VIEWPORT_UNIT, '$1v$2');
		const fn = TRANSFORM_FUNCTIONS[decl.prop];
		if (!fn) return;
		const value = decl.value.trim();
		decl.prop = 'transform';
		decl.value = value === 'none' ? 'none' : `${fn}(${splitTopLevel(value).join(', ')})`;
	}
};

function oldBrowserCss(): Plugin {
	return {
		name: 'old-browser-css',
		apply: 'build',
		enforce: 'post',
		async generateBundle(_options, bundle) {
			const files = Object.values(bundle).filter(
				(file) => file.type === 'asset' && file.fileName.endsWith('.css')
			);
			if (files.length === 0) return;
			const marker = (i: number) => `/*old-browser-css:${i}*/`;
			const joined = files
				.map((file, i) => {
					const source = file.type === 'asset' ? file.source : '';
					return marker(i) + (typeof source === 'string' ? source : new TextDecoder().decode(source));
				})
				.join('\n');
			const processor = postcss([whereToIs, oldBrowserRewrites, postcssPresetEnv({ browsers: OLD_BROWSERS })]);
			const { css } = await processor.process(joined, { from: undefined });
			const parts = css.split(/\/\*old-browser-css:\d+\*\//);
			if (parts.length !== files.length + 1 || parts[0].trim() !== '') {
				this.error('old-browser-css: CSS chunk markers were lost while processing');
			}
			files.forEach((file, i) => {
				if (file.type === 'asset') file.source = parts[i + 1];
			});
		}
	};
}

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit(),
		legacy({
			targets: ['chrome >= 64', 'safari >= 12'],
			// The default modern target is Chrome 105, which would leave out
			// polyfills (Array.prototype.at, Chrome 92) and syntax lowering that
			// Qt5 WebEngine needs. build.ts links the polyfill chunk into each page.
			modernTargets: OLD_BROWSERS,
			modernPolyfills: true,
			renderLegacyChunks: false
		}),
		oldBrowserCss()
	],
	define: {
		'import.meta.env.SKIP_LOADING': JSON.stringify(process.env.SKIP_LOADING || 'false')
	},
});
