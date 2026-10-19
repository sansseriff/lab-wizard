import { $ } from "bun";
import path from "node:path";
import { existsSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";


const current_directory = import.meta.dir; // https://bun.sh/docs/api/import-meta

const build_directory = path.join(current_directory, "build");

const output_directory = path.join(current_directory, "../backend/static");


console.log('\x1b[33m >>>>> Building SvelteKit frontend (adapter-static)... \x1b[0m');
await $`bun run build`;


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