<script lang="ts">
	/** Draws series (as `to_series` gives them) with BokehJS on a WebGL canvas.
	 *
	 * The one plot component: the Data page draws recorded runs with it, and a
	 * live view will draw a run in progress with it (plans/runner_plan.md §5),
	 * so a plot looks the same either way. It rebuilds the figure only when the
	 * plot's shape changes (axes, kind, which lines); new values for the same
	 * lines replace the data in place, so a zoom survives a running run's
	 * refresh. Clicking a point reports which run and point it is.
	 *
	 * ``xRange``/``yRange`` fix what part of an axis shows (a null end fits the
	 * data); :func:`shownRanges` reads back what the user has zoomed to.
	 */
	import { onMount } from 'svelte';
	import { loadBokeh, type BokehGlobal } from '$lib/data/bokeh';
	import type { Series } from '$lib/data/model';
	import type { AxisRange } from '$lib/procedures/model';

	let {
		series,
		xLabel = '',
		yLabel = '',
		y2Label = '',
		kind = 'line',
		connect = 'seq',
		logX = false,
		logY = false,
		xRange = null,
		yRange = null,
		onpoint,
		webgl = true
	}: {
		series: Series[];
		xLabel?: string;
		yLabel?: string;
		y2Label?: string;
		kind?: 'line' | 'scatter' | 'histogram' | 'waterfall';
		connect?: 'seq' | 'x' | 'none';
		logX?: boolean;
		logY?: boolean;
		xRange?: AxisRange | null;
		yRange?: AxisRange | null;
		onpoint?: (runId: number, seq: number) => void;
		/** Draw with WebGL (fast for many points) or a plain canvas. The live view uses
		 * a plain canvas: WebGL shares the GPU with the page's own animation (the
		 * timeline's close-up), which then misses frames, most of all in Safari. */
		webgl?: boolean;
	} = $props();

	// Distinguishable in both themes; the brand indigo first.
	const PALETTE = [
		'#4f41ef', '#e4572e', '#17a398', '#e0a100', '#a23b72',
		'#3b8ea5', '#6c9a3b', '#c1666b', '#7d6bb0', '#2e86ab'
	];

	let host: HTMLDivElement;
	let Bokeh: BokehGlobal = $state(null);
	let failure = $state('');
	let theme = $state(0);

	let view: { remove(): void } | null = null;
	let figure: BokehGlobal = null;
	let sources: BokehGlobal[] = [];
	let shape = '';

	onMount(() => {
		loadBokeh()
			.then((b) => (Bokeh = b))
			.catch((e: Error) => (failure = e.message));
		const media = matchMedia('(prefers-color-scheme: dark)');
		const retheme = () => theme++;
		media.addEventListener('change', retheme);
		return () => {
			media.removeEventListener('change', retheme);
			clear();
		};
	});

	function data(s: Series) {
		return { x: s.x, y: s.y, z: s.z, run_id: s.run_id, seq: s.seq };
	}

	$effect(() => {
		if (!Bokeh || !host) return;
		const next = JSON.stringify([
			kind, connect, logX, logY, xRange, yRange, xLabel, yLabel, y2Label, theme, webgl,
			series.map((s) => [s.label, s.axis, s.y_name])
		]);
		if (next === shape && sources.length === series.length) {
			series.forEach((s, i) => (sources[i].data = data(s)));
			return;
		}
		shape = next;
		build();
	});

	/** What part of each axis is on screen now, after any pan or zoom. */
	export function shownRanges(): { x: AxisRange; y: AxisRange } | null {
		if (!figure) return null;
		const ends = (range: BokehGlobal): AxisRange => [range.start, range.end];
		return { x: ends(figure.x_range), y: ends(figure.y_range) };
	}

	/** A range whose set ends stay put; a null end follows the data. */
	function range(B: BokehGlobal, bounds: AxisRange | null) {
		const [start, end] = bounds ?? [null, null];
		return new B.DataRange1d({ ...(start !== null && { start }), ...(end !== null && { end }) });
	}

	function clear() {
		view?.remove();
		view = null;
		figure = null;
		sources = [];
		if (host) host.innerHTML = '';
	}

	function token(name: string): string {
		return getComputedStyle(host).getPropertyValue(name).trim();
	}

	async function build() {
		clear();
		const B = Bokeh;
		const [ink, muted, line, surface] = ['--ink-2', '--muted', '--line', '--surface'].map(token);
		const fig = B.Plotting.figure({
			sizing_mode: 'stretch_both',
			output_backend: webgl ? 'webgl' : 'canvas',
			tools: 'pan,box_zoom,wheel_zoom,reset,save,tap',
			x_range: range(B, xRange),
			y_range: range(B, yRange),
			x_axis_type: logX ? 'log' : 'linear',
			y_axis_type: logY ? 'log' : 'linear',
			x_axis_label: xLabel,
			y_axis_label: yLabel,
			background_fill_color: surface,
			border_fill_color: surface,
			outline_line_color: line
		});
		fig.toolbar.logo = null;

		const twin = series.some((s) => s.axis === 'y2');
		const byAxis: Record<string, BokehGlobal[]> = { default: [], y2: [] };
		if (twin) {
			fig.extra_y_ranges = { y2: new B.DataRange1d() };
			fig.add_layout(new B.LinearAxis({ y_range_name: 'y2', axis_label: y2Label }), 'right');
		}

		const zs = series.flatMap((s) => s.z).filter((z): z is number => z !== null);
		const mapper =
			kind === 'waterfall'
				? new B.LinearColorMapper({
						palette: B.Palettes.Viridis256,
						low: zs.length ? Math.min(...zs) : 0,
						high: zs.length ? Math.max(...zs) : 1
					})
				: null;

		// A legend names lines apart; one line needs none.
		const legend = series.length > 1;
		series.forEach((s, i) => {
			const source = new B.ColumnDataSource({ data: data(s) });
			sources.push(source);
			const range = s.axis === 'y2' ? 'y2' : 'default';
			const color = PALETTE[i % PALETTE.length];
			const common: Record<string, unknown> = { source, y_range_name: range };
			if (legend) common.legend_label = s.label || s.y_name;
			const x = { field: 'x' };
			const y = { field: 'y' };

			if (mapper) {
				byAxis[range].push(
					fig.scatter(x, y, {
						...common,
						marker: 'square',
						size: 7,
						fill_color: { field: 'z', transform: mapper },
						line_color: null
					})
				);
				return;
			}
			if (kind === 'histogram') {
				byAxis[range].push(fig.step(x, y, { ...common, mode: 'center', color, line_width: 1.6 }));
			} else if (kind === 'line' && connect !== 'none') {
				byAxis[range].push(fig.line(x, y, { ...common, color, line_width: 1.6 }));
			}
			const lone = kind === 'scatter' || connect === 'none';
			byAxis[range].push(
				fig.scatter(x, y, { ...common, color, size: lone ? 6 : kind === 'histogram' ? 3 : 4 })
			);
			source.selected.change.connect(() => {
				const index = source.selected.indices[0];
				if (index !== undefined) onpoint?.(source.data.run_id[index], source.data.seq[index]);
			});
		});

		// Each y axis scales to its own lines only.
		if (twin) {
			fig.y_range.renderers = byAxis.default;
			fig.extra_y_ranges.y2.renderers = byAxis.y2;
		}
		if (mapper) fig.add_layout(new B.ColorBar({ color_mapper: mapper, background_fill_color: surface }), 'right');

		for (const axis of [...fig.xaxis, ...fig.yaxis]) {
			axis.axis_label_text_color = ink;
			axis.major_label_text_color = muted;
			axis.axis_line_color = line;
			axis.major_tick_line_color = line;
			axis.minor_tick_line_color = line;
			axis.axis_label_text_font_style = 'normal';
		}
		for (const grid of [...fig.xgrid, ...fig.ygrid]) grid.grid_line_color = line;
		if (legend) {
			const box = fig.legend;
			box.background_fill_color = surface;
			box.border_line_color = line;
			box.label_text_color = ink;
			box.click_policy = 'hide';
			box.location = 'top_left';
		}

		figure = fig;
		view = await B.Plotting.show(fig, host);
	}
</script>

<div class="relative h-full min-h-[280px] w-full">
	<div bind:this={host} class="absolute inset-0"></div>
	{#if failure}
		<p class="absolute inset-0 grid place-items-center text-xs text-crit">{failure}</p>
	{:else if !Bokeh}
		<p class="absolute inset-0 grid place-items-center text-xs text-muted">Loading the plot…</p>
	{/if}
</div>
