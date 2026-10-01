/** The lab database API (backend/routes/data.py), over the typed client. */
import { api, unwrap } from '$lib/api';
import type { AxisRange } from '$lib/procedures/model';
import type { Device, Facet, Filters, LineShape, Loops, PlotSpec, Point, RunDetail, RunRow, Series, Step } from './model';

/** Filters as the backend takes them: one JSON query parameter, absent when empty. */
const filtersQuery = (filters: Filters) =>
	Object.keys(filters).length ? { filters: JSON.stringify(filters) } : {};

export const dataApi = {
	facets: (filters: Filters) =>
		unwrap<{ runs: number; facets: Facet[] }>(api.GET('/api/data/facets', { params: { query: filtersQuery(filters) } })),
	runs: (filters: Filters, page = 1, pageSize = 100) =>
		unwrap<{ total: number; page: number; page_size: number; runs: RunRow[] }>(
			api.GET('/api/data/runs', { params: { query: { ...filtersQuery(filters), page, page_size: pageSize } } })
		),
	run: (id: number) => unwrap<RunDetail>(api.GET('/api/data/runs/{run_id}', { params: { path: { run_id: id } } })),
	steps: (id: number) =>
		unwrap<{ steps: Step[]; loops: Loops }>(api.GET('/api/data/runs/{run_id}/steps', { params: { path: { run_id: id } } })),
	point: (id: number, seq: number) =>
		unwrap<Point>(api.GET('/api/data/runs/{run_id}/points/{seq}', { params: { path: { run_id: id, seq } } })),
	plot: (spec: PlotSpec) =>
		unwrap<{ series: Series[]; units: Record<string, string | null>; shape: LineShape }>(
			api.POST('/api/data/plot', { body: { spec } })
		),
	notebook: (spec: PlotSpec) => unwrap<{ source: string }>(api.POST('/api/data/plot/notebook', { body: { spec } })),
	savePlot: (procedure: string, plot: PlotSpec, replace: string | null = null) =>
		unwrap<{ plots: PlotSpec[]; origin: string }>(
			api.POST('/api/procedures/{name}/plots', { params: { path: { name: procedure } }, body: { plot, replace } })
		),
	/** Keep what part of a run's plot to show, wherever it is drawn; two nulls forget it. */
	saveView: (runId: number, plot: string, x_range: AxisRange | null, y_range: AxisRange | null) =>
		unwrap<{ plot: string; x_range: AxisRange | null; y_range: AxisRange | null }>(
			api.PUT('/api/data/runs/{run_id}/views/{plot}', {
				params: { path: { run_id: runId, plot } },
				body: { x_range, y_range }
			})
		),
	devices: () => unwrap<{ devices: Device[] }>(api.GET('/api/data/devices')),
	saveDevice: (name: string, properties: Device['properties'], notes: string | null) =>
		unwrap<Device>(api.PUT('/api/data/devices/{name}', { params: { path: { name } }, body: { properties, notes } })),
	/** The run's export URL; the browser downloads what it returns. */
	exportUrl: (id: number) => `/api/data/runs/${id}/export`
};
