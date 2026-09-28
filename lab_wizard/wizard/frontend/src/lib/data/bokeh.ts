/** BokehJS, loaded on demand.
 *
 * BokehJS is a drawing library, not a server: the page hands it series and it
 * draws them on a WebGL canvas (the tag_gui pattern; plans/runner_plan.md §5).
 * Its prebuilt bundles are pinned through npm and emitted by Vite as files of
 * this app, so nothing is fetched from the internet (the wizard runs on lab
 * computers that may have none) and only a page that draws pays the 1.5 MB.
 * They define the global `Bokeh`, so they load as ordinary scripts, in order.
 */
import bokehUrl from '@bokeh/bokehjs/build/js/bokeh.min.js?url';
import glUrl from '@bokeh/bokehjs/build/js/bokeh-gl.min.js?url';
import apiUrl from '@bokeh/bokehjs/build/js/bokeh-api.min.js?url';

export type BokehGlobal = any;

let loading: Promise<BokehGlobal> | null = null;

function script(src: string): Promise<void> {
	return new Promise((resolve, reject) => {
		const tag = document.createElement('script');
		tag.src = src;
		tag.async = false;
		tag.onload = () => resolve();
		tag.onerror = () => reject(new Error(`Could not load ${src}`));
		document.head.appendChild(tag);
	});
}

export function loadBokeh(): Promise<BokehGlobal> {
	loading ??= (async () => {
		for (const src of [bokehUrl, glUrl, apiUrl]) await script(src);
		return (window as unknown as { Bokeh: BokehGlobal }).Bokeh;
	})();
	return loading;
}
