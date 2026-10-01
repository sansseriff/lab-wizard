/* Resize HUD: measures frame rate and per-frame work while a divider is dragged.
 *
 * Standalone, so it works on the lab pages and injected into the real wizard
 * (paste into the console, or load it with the bookmarklet on index.html).
 *
 * While a pointer is down on a [role=separator] (or between begin()/end()), it
 * records, once per animation frame:
 *   frame   - time between rAF callbacks; 16.7 ms is 60 fps.
 *   layout  - style + layout forced at the start of the frame (the page's cost
 *             of taking the new size). Only the lab knows when a size was
 *             written, so it reports this through window.resizeHud.metric().
 *   observers - time spent in ResizeObserver callbacks. Bokeh resizes and
 *             repaints its canvases inside one. Only observers created after
 *             this script loaded are counted.
 *   render  - from the rAF callback to the next task: style, layout, observers,
 *             paint, all the main-thread work of producing the frame.
 */
(() => {
	if (window.resizeHud) return;

	const metrics = {};
	let active = false;
	let label = '';
	let frames = [];
	let renders = [];
	let moves = 0;
	let started = 0;
	let last = 0;
	let observerTime = 0;
	const results = [];

	// ---- ResizeObserver timing ----
	const NativeRO = window.ResizeObserver;
	window.ResizeObserver = class extends NativeRO {
		constructor(callback) {
			super((entries, observer) => {
				const t0 = performance.now();
				try {
					callback(entries, observer);
				} finally {
					observerTime += performance.now() - t0;
				}
			});
		}
	};

	// ---- per-frame sampling ----
	const channel = new MessageChannel();
	let renderStart = 0;
	channel.port1.onmessage = () => {
		if (renderStart) renders.push(performance.now() - renderStart);
		renderStart = 0;
	};

	function frame(t) {
		if (!active) return;
		if (last) frames.push(t - last);
		last = t;
		if (observerTime) {
			(metrics.observers ??= []).push(observerTime);
			observerTime = 0;
		}
		renderStart = performance.now();
		channel.port2.postMessage(0);
		requestAnimationFrame(frame);
	}

	function begin(name) {
		if (active) return;
		active = true;
		label = name || document.title;
		frames = [];
		renders = [];
		moves = 0;
		last = 0;
		observerTime = 0;
		for (const k of Object.keys(metrics)) delete metrics[k];
		started = performance.now();
		requestAnimationFrame(frame);
		draw(true);
	}

	function end() {
		if (!active) return;
		active = false;
		const seconds = (performance.now() - started) / 1000;
		if (frames.length < 3) return draw(false);
		const row = {
			label,
			seconds: +seconds.toFixed(1),
			fps: +(1000 / avg(frames)).toFixed(0),
			frame_p50: +pct(frames, 50).toFixed(1),
			frame_p95: +pct(frames, 95).toFixed(1),
			frame_max: +Math.max(...frames).toFixed(1),
			render: +avg(renders).toFixed(1),
			moves_per_frame: +(moves / frames.length).toFixed(2)
		};
		for (const [k, v] of Object.entries(metrics)) row[k] = +avg(v).toFixed(1);
		results.push(row);
		draw(false);
	}

	function metric(name, ms) {
		if (active) (metrics[name] ??= []).push(ms);
	}

	const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
	function pct(a, p) {
		const s = [...a].sort((x, y) => x - y);
		return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] ?? 0;
	}

	// ---- automatic: any separator drag ----
	addEventListener(
		'pointerdown',
		(e) => {
			if (e.target.closest?.('[role=separator]')) begin(window.resizeHud.scenario?.() ?? document.title);
		},
		true
	);
	addEventListener('pointermove', () => active && moves++, true);
	for (const type of ['pointerup', 'pointercancel']) addEventListener(type, () => setTimeout(end), true);

	// ---- the panel ----
	const panel = document.createElement('div');
	panel.style.cssText =
		'position:fixed;right:12px;bottom:12px;z-index:2147483647;max-width:min(760px,calc(100vw - 24px));' +
		'max-height:45vh;overflow:auto;font:11px/1.45 ui-monospace,Menlo,monospace;color:#e8e8ef;' +
		'background:rgba(20,20,28,.92);border:1px solid #444;border-radius:8px;padding:8px 10px;box-shadow:0 4px 18px rgba(0,0,0,.35)';
	let liveTimer = 0;

	function draw(live) {
		clearInterval(liveTimer);
		if (live) {
			liveTimer = setInterval(() => {
				const recent = frames.slice(-30);
				panel.querySelector('[data-live]').textContent = recent.length
					? `● ${(1000 / avg(recent)).toFixed(0)} fps   frame ${avg(recent).toFixed(1)} ms   render ${avg(renders.slice(-30)).toFixed(1)} ms`
					: '● measuring…';
			}, 150);
		}
		const cols = results.length ? Object.keys(Object.assign({}, ...results)) : [];
		panel.innerHTML = `
			<div style="display:flex;gap:8px;align-items:center;margin-bottom:4px">
				<b>Resize HUD</b><span style="color:#999">${navigator.userAgent.match(/(Chrome|Firefox|Version)\/[\d.]+( Safari)?/)?.[0] ?? ''}</span>
				<span style="margin-left:auto"></span>
				<button data-copy>Copy results</button><button data-clear>Clear</button><button data-hide>Hide</button>
			</div>
			<div data-live style="color:#7fd17f">${live ? '● measuring…' : 'Drag a divider to measure.'}</div>
			${
				results.length
					? `<table style="border-collapse:collapse;margin-top:4px">
						<tr>${cols.map((c) => `<th style="text-align:left;padding:1px 6px;color:#aaa;font-weight:500">${c}</th>`).join('')}</tr>
						${results
							.map(
								(r) =>
									`<tr>${cols.map((c) => `<td style="padding:1px 6px;${c === 'fps' ? fpsColor(r.fps) : ''}">${r[c] ?? ''}</td>`).join('')}</tr>`
							)
							.join('')}
					</table>`
					: ''
			}`;
		panel.querySelector('[data-copy]').onclick = copy;
		panel.querySelector('[data-clear]').onclick = () => {
			results.length = 0;
			draw(false);
		};
		panel.querySelector('[data-hide]').onclick = () => panel.remove();
	}

	const fpsColor = (fps) => `color:${fps >= 50 ? '#7fd17f' : fps >= 30 ? '#e0c060' : '#ff7b7b'};font-weight:600`;

	function copy() {
		const cols = Object.keys(Object.assign({}, ...results));
		const text = [
			`${navigator.userAgent}  dpr=${devicePixelRatio}  window=${innerWidth}x${innerHeight}`,
			'',
			`| ${cols.join(' | ')} |`,
			`|${cols.map(() => '---').join('|')}|`,
			...results.map((r) => `| ${cols.map((c) => r[c] ?? '').join(' | ')} |`)
		].join('\n');
		const done = () => (panel.querySelector('[data-copy]').textContent = 'Copied');
		navigator.clipboard?.writeText(text).then(done, () => prompt('Copy:', text));
		console.log(text);
	}

	const mount = () => {
		document.body.appendChild(panel);
		draw(false);
	};
	document.body ? mount() : addEventListener('DOMContentLoaded', mount);

	window.resizeHud = { begin, end, metric, results, scenario: null };
})();
