import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import legacy from '@vitejs/plugin-legacy';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit(),
		legacy({
			targets: ['chrome >= 64', 'safari >= 12'],
			modernPolyfills: true,
			renderLegacyChunks: false
		})
	],
	define: {
		'import.meta.env.SKIP_LOADING': JSON.stringify(process.env.SKIP_LOADING || 'false')
	},
});
