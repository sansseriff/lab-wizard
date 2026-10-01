/* Resize lab: the Data page's layout with each suspect behind a switch.
 * Every setting is in the query string and a change reloads the page, so each
 * scenario starts clean and its URL can be opened in any browser. */
(() => {
	const DEFAULTS = { plot: 'bokeh-webgl', points: '2000', plots: '1', hidpi: '1', sides: 'light', mode: 'live', cv: '0', write: 'var' };
	const params = new URLSearchParams(location.search);
	const cfg = Object.fromEntries(Object.entries(DEFAULTS).map(([k, v]) => [k, params.get(k) ?? v]));

	// ---- the controls ----
	const form = document.getElementById('controls');
	for (const [k, v] of Object.entries(cfg)) {
		const el = form.elements[k];
		if (el.type === 'checkbox') el.checked = v === '1';
		else el.value = v;
	}
	form.addEventListener('change', () => {
		const next = new URLSearchParams();
		for (const k of Object.keys(DEFAULTS)) {
			const el = form.elements[k];
			next.set(k, el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value);
		}
		location.search = next.toString();
	});
	if (cfg.cv === '1') document.documentElement.classList.add('cv');
	const label = `plot=${cfg.plot}${cfg.plot === 'none' ? '' : ` pts=${cfg.points} plots=${cfg.plots} hidpi=${cfg.hidpi}`} sides=${cfg.sides} mode=${cfg.mode}${cfg.cv === '1' ? ' cv' : ''}${cfg.write === 'direct' ? ' write=direct' : ''}`;
	document.getElementById('scenario').textContent = label;
	document.title = `Resize lab: ${label}`;
	window.resizeHud.scenario = () => label;

	// ---- side panels ----
	const heavy = cfg.sides === 'heavy';
	const words = ['bias_sweep', 'iv_curve', 'count_rate', 'pcr', 'dark_counts', 'tc_scan', 'jitter'];
	const pick = (i, a) => a[i % a.length];
	document.getElementById('filters').innerHTML = Array.from(
		{ length: heavy ? 40 : 5 },
		(_, f) =>
			`<div class="facet"><b>${pick(f, ['procedure', 'device', 'operator', 'wafer', 'run.temperature'])} ${f}</b>${Array.from(
				{ length: heavy ? 12 : 4 },
				(_, v) => `<label><input type="checkbox" /><span>${pick(v + f, words)}_${v}</span><span>${(v * 7 + f) % 50}</span></label>`
			).join('')}</div>`
	).join('');
	document.getElementById('runs').innerHTML = Array.from(
		{ length: heavy ? 1500 : 40 },
		(_, i) =>
			`<div class="row"><span>#${1500 - i}</span><span>${pick(i, words)} on DUT-${i % 13}, ${pick(i, ['notes about the fibre', 'cooldown 3', 'after realign', ''])}</span><span>2026-09-${String((i % 28) + 1).padStart(2, '0')}</span><span>${(i * 37) % 900} pts</span></div>`
	).join('');
	// As Timeline.svelte draws a run's steps: a name column that is a share of the width, and a bar.
	if (cfg.sides === 'timeline') document.getElementById('bottom').innerHTML = Array.from(
		{ length: 2000 },
		(_, i) => `<div class="tl" title="step ${i}"><span style="padding-left:${(i % 3) * 12}px">${pick(i, ['sweep', 'set_bias', 'read_counts'])}[${i}]</span><span class="bar"><i style="left:${(i / 2000) * 100}%;width:${0.05 + (i % 7) * 0.01}%"></i></span></div>`
	).join('');
	else document.getElementById('bottom').innerHTML = Array.from(
		{ length: heavy ? 300 : 30 },
		(_, i) => `<div class="row"><span>${i}</span><span>set bias ${(i * 0.1).toFixed(1)} µA → read counts</span><span>${(i * 1.7).toFixed(1)} s</span><span>ok</span></div>`
	).join('');

	// ---- plots ----
	const plotsEl = document.getElementById('plots');
	const hosts = Array.from({ length: cfg.plot === 'none' ? 0 : +cfg.plots }, () => {
		const wrap = document.createElement('div');
		wrap.className = 'plot-wrap';
		const host = document.createElement('div');
		host.className = 'plot-host';
		wrap.appendChild(host);
		plotsEl.appendChild(wrap);
		return host;
	});
	if (!hosts.length) plotsEl.innerHTML = '<p style="margin:auto;color:var(--muted)">No plot in this scenario.</p>';

	const n = +cfg.points;
	const xs = Float64Array.from({ length: n }, (_, i) => i / n * 10);
	const ys = Float64Array.from(xs, (x) => Math.sin(x * 3) * Math.exp(-x / 6) + (Math.random() - 0.5) * 0.08);

	// Settles once every plot has drawn, so a sweep measures resizing, not the first draw.
	let ready = Promise.resolve();
	if (cfg.plot === 'canvas2d') hosts.forEach(canvasPlot);
	else if (cfg.plot.startsWith('bokeh')) ready = loadBokeh().then((B) => Promise.all(hosts.map((h) => bokehPlot(B, h))));

	function canvasPlot(host) {
		const canvas = document.createElement('canvas');
		canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
		host.appendChild(canvas);
		const ctx = canvas.getContext('2d');
		new ResizeObserver(() => {
			const dpr = cfg.hidpi === '1' ? devicePixelRatio : 1;
			const w = host.clientWidth, h = host.clientHeight;
			canvas.width = Math.round(w * dpr);
			canvas.height = Math.round(h * dpr);
			ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
			ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim() || '#ffffff';
			ctx.fillRect(0, 0, w, h);
			ctx.strokeStyle = '#4f41ef';
			ctx.beginPath();
			for (let i = 0; i < n; i++) {
				const px = 40 + (xs[i] / 10) * (w - 50), py = h / 2 - ys[i] * (h / 2 - 20);
				i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
			}
			ctx.stroke();
		}).observe(host);
	}

	function loadBokeh() {
		const script = (src) =>
			new Promise((ok, fail) => {
				const s = document.createElement('script');
				s.src = src;
				s.async = false;
				s.onload = ok;
				s.onerror = () => fail(new Error(`Could not load ${src}`));
				document.head.appendChild(s);
			});
		return ['bokeh/bokeh.min.js', 'bokeh/bokeh-gl.min.js', 'bokeh/bokeh-api.min.js']
			.reduce((p, src) => p.then(() => script(src)), Promise.resolve())
			.then(() => window.Bokeh);
	}

	function bokehPlot(B, host) {
		// As BokehPlot.svelte builds it.
		const surface = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim() || '#ffffff';
		const fig = B.Plotting.figure({
			sizing_mode: 'stretch_both',
			output_backend: cfg.plot === 'bokeh-webgl' ? 'webgl' : 'canvas',
			hidpi: cfg.hidpi === '1',
			tools: 'pan,box_zoom,wheel_zoom,reset,save,tap',
			x_axis_label: 'bias (µA)',
			y_axis_label: 'count rate (Hz)',
			background_fill_color: surface,
			border_fill_color: surface
		});
		fig.toolbar.logo = null;
		const source = new B.ColumnDataSource({ data: { x: Array.from(xs), y: Array.from(ys) } });
		fig.line({ x: { field: 'x' }, y: { field: 'y' }, source, line_color: '#4f41ef', line_width: 1.5, legend_label: 'run #412' });
		fig.scatter({ x: { field: 'x' }, y: { field: 'y' }, source, size: 4, fill_color: '#4f41ef', line_color: null });
		return B.Plotting.show(fig, host);
	}

	// ---- splitters ----
	const viewer = document.getElementById('viewer');
	const detail = document.getElementById('detail');
	const sizes = { '--filters': 240, '--runs': 420, '--bottom': 220 };
	// `var` sets inherited custom properties on the grid, as the app's style:--x does;
	// `direct` sets the grid's columns and the bottom panel's height themselves.
	const bottomEl = document.getElementById('bottom');
	function applySize(name, px) {
		if (cfg.write === 'var') return viewer.style.setProperty(name, `${px}px`);
		if (name === '--bottom') bottomEl.style.height = `${px}px`;
		else viewer.style.gridTemplateColumns = `${sizes['--filters']}px 0 ${sizes['--runs']}px 0 minmax(0, 1fr)`;
	}
	for (const [k, v] of Object.entries(sizes)) applySize(k, v);

	function limits(name) {
		if (name === '--bottom') return [80, detail.clientHeight - 200];
		const other = name === '--filters' ? sizes['--runs'] : sizes['--filters'];
		return [name === '--filters' ? 120 : 160, viewer.clientWidth - other - 300];
	}

	// Measures the page's style + layout for a size write: the first write of a
	// frame forces it right away (it would happen later in the frame anyway).
	let measuredThisFrame = false;
	function write(name, px) {
		sizes[name] = px;
		applySize(name, px);
		if (measuredThisFrame) return;
		measuredThisFrame = true;
		const t0 = performance.now();
		void viewer.offsetWidth;
		window.resizeHud.metric('layout', performance.now() - t0);
		requestAnimationFrame(() => (measuredThisFrame = false));
	}

	/** One drag, whether from a pointer or the sweep. */
	function startDrag(split) {
		const name = split.dataset.var;
		const reverse = split.hasAttribute('data-reverse');
		const start = sizes[name];
		const [min, max] = limits(name);
		const grab = split.querySelector('.grab');
		let target = start;
		let frameQueued = false;
		split.classList.add('dragging');

		if (cfg.mode === 'freeze') {
			for (const h of hosts) {
				const r = h.getBoundingClientRect();
				Object.assign(h.style, { width: `${r.width}px`, height: `${r.height}px`, right: 'auto', bottom: 'auto' });
			}
		}

		return {
			move(delta) {
				target = Math.round(Math.min(Math.max(start + (reverse ? -delta : delta), min), Math.max(min, max)));
				if (cfg.mode === 'release') {
					const offset = (target - start) * (reverse ? -1 : 1);
					grab.style.translate = name === '--bottom' ? `0 ${offset}px` : `${offset}px 0`;
				} else if (cfg.mode === 'raf') {
					if (frameQueued) return;
					frameQueued = true;
					requestAnimationFrame(() => {
						frameQueued = false;
						write(name, target);
					});
				} else {
					write(name, target);
				}
			},
			end() {
				split.classList.remove('dragging');
				grab.style.translate = '';
				write(name, target);
				if (cfg.mode === 'freeze') for (const h of hosts) Object.assign(h.style, { width: '', height: '', right: '', bottom: '' });
			}
		};
	}

	for (const split of document.querySelectorAll('.split')) {
		split.addEventListener('pointerdown', (e) => {
			if (e.button !== 0) return;
			e.preventDefault();
			split.setPointerCapture(e.pointerId);
			const vertical = split.dataset.var === '--bottom';
			const from = vertical ? e.clientY : e.clientX;
			const drag = startDrag(split);
			const move = (ev) => drag.move((vertical ? ev.clientY : ev.clientX) - from);
			const end = () => {
				drag.end();
				split.removeEventListener('pointermove', move);
				split.removeEventListener('lostpointercapture', end);
			};
			split.addEventListener('pointermove', move);
			split.addEventListener('lostpointercapture', end);
		});
	}

	// ---- auto sweep: the run-list divider, ±180 px, 3 s ----
	function sweep() {
		const split = document.querySelector('[data-var="--runs"]');
		const drag = startDrag(split);
		window.resizeHud.begin(`${label} [sweep]`);
		const t0 = performance.now();
		const step = (t) => {
			const p = (t - t0) / 3000;
			if (p >= 1) {
				drag.end();
				// Let the release's layout land before closing the measurement.
				requestAnimationFrame(() => requestAnimationFrame(() => window.resizeHud.end()));
				return;
			}
			drag.move(Math.sin(p * Math.PI * 4) * 180);
			requestAnimationFrame(step);
		};
		requestAnimationFrame(step);
	}
	document.getElementById('sweep').onclick = sweep;
	if (params.get('sweep') === '1') ready.then(() => setTimeout(sweep, 800));
})();
