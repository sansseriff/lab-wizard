import { $ } from "bun";
import path from "node:path";
import { existsSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { CHROME, checkFiles, formatFindings } from "./css-compat";


const current_directory = import.meta.dir; // https://bun.sh/docs/api/import-meta

const build_directory = path.join(current_directory, "build");

const output_directory = path.join(current_directory, "../backend/static");


console.log('\x1b[33m >>>>> Building SvelteKit frontend (adapter-static)... \x1b[0m');
await $`bun run build`;


// Fail on CSS that Qt5 WebEngine (Chrome 87) would silently drop; see css-compat.ts.
console.log(`\x1b[33m >>>>> Checking CSS against Chrome ${CHROME}... \x1b[0m`);
const css_files = [...new Bun.Glob("**/*.css").scanSync(build_directory)].map((name) => path.join(build_directory, name));
const { problems } = checkFiles(css_files);
if (problems.length) {
    throw new Error(
        `CSS that Chrome ${CHROME} can't use. Rewrite it in vite.config.ts, avoid it, ` +
        `or add it to ALLOWED in css-compat.ts:\n${formatFindings(problems)}`
    );
}

// Selectors in scripts (querySelector, closest, matches) escape that check, and
// Chrome 87 throws on these pseudo-classes rather than skipping them.
const new_selector = /:(is|where|has)\(/;
for (const name of new Bun.Glob("**/*.js").scanSync(build_directory)) {
    const match = readFileSync(path.join(build_directory, name), "utf8").match(new RegExp(`.{0,60}${new_selector.source}.{0,60}`));
    if (match) {
        throw new Error(`${name} uses a selector Chrome ${CHROME} can't parse:\n  ${match[0]}`);
    }
}

console.log('\x1b[33m >>>>> Moving compiled static site to /backend/static/ \x1b[0m');

// Ensure build output exists before copying
if (!existsSync(build_directory)) {
    throw new Error(`Build directory not found: ${build_directory}`);
}

// On Linux the wizard's window is Qt5 WebEngine (Chrome 87), which lacks
// Array.prototype.at, structuredClone and the like. @vitejs/plugin-legacy
// builds a polyfills chunk for them but only links it from a Vite index.html,
// which SvelteKit doesn't use, so link it from every prerendered page. It's a
// plain script, and placed first in <head> it runs before SvelteKit's inline
// boot script starts loading the app.
const immutable_directory = path.join(build_directory, "_app/immutable");
const polyfills = readdirSync(immutable_directory).find((name) => /^polyfills\..*\.js$/.test(name));
if (!polyfills) {
    throw new Error(`Polyfills chunk not found in ${immutable_directory}`);
}
// The chunk is meant to be a module, so its minified top-level vars would
// become globals in a plain script. One is named `qt`, which replaced the
// window.qt object Qt injects, so pywebview's bridge found no
// qt.webChannelTransport ("Cannot read property 'send' of undefined").
// A function scope keeps them local; core-js installs the polyfills on
// globalThis explicitly.
const polyfills_file = path.join(immutable_directory, polyfills);
writeFileSync(polyfills_file, `(function () {\n${readFileSync(polyfills_file, "utf8")}\n})();\n`);
console.log(`\x1b[33m >>>>> Linking ${polyfills} from every page... \x1b[0m`);
for (const page of new Bun.Glob("**/*.html").scanSync(build_directory)) {
    const file = path.join(build_directory, page);
    const html = readFileSync(file, "utf8");
    if (!html.includes("<head>")) {
        throw new Error(`No <head> in ${file}`);
    }
    writeFileSync(file, html.replace("<head>", `<head>\n\t\t<script src="/_app/immutable/${polyfills}"></script>`));
}

// Check if output directory exists and remove it first
if (existsSync(output_directory)) {
    console.log('\x1b[33m >>>>> Removing existing output directory... \x1b[0m');
    if (process.platform === "win32") {
        // rm -rf is not working in bun shell yet as of Bun 1.1.34
        execSync(`rmdir /S /Q ${output_directory}`, { stdio: 'inherit' });
    } else {
        await $`rm -rf ${output_directory}`;
    }
}

// Create output directory
// Do not pre-create output directory; we'll move/rename build -> static path

console.log('\x1b[33m >>>>> Moving build -> /backend/static (no nesting) ... \x1b[0m');
// Rename/move the entire build directory to the static path so files land at /static/*
// This avoids creating /static/build/*
renameSync(build_directory, output_directory);