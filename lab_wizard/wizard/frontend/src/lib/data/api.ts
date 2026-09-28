/** The lab database API (lab_wizard/wizard/backend/data_api.py), typed. */
import { fetchWithConfig } from '$lib/api';
import type { Device, Facet, Filters, LineShape, PlotSpec, Point, RunDetail, RunRow, Series, Step } from './model';

function query(filters: Filters, extra: Record<string, string | number> = {}): string {
	const params = new URLSearchParams();
	if (Object.keys(filters).length) params.set('filters', JSON.stringify(filters));
	for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
	const text = params.toString();
	return text ? `?${text}` : '';
}

export const dataApi = {
	facets: (filters: Filters) =>
		fetchWithConfig<{ runs: number; facets: Facet[] }>(`/api/data/facets${query(filters)}`, 'GET'),
	runs: (filters: Filters, page = 1, pageSize = 100) =>
		fetchWithConfig<{ total: number; page: number; page_size: number; runs: RunRow[] }>(
			`/api/data/runs${query(filters, { page, page_size: pageSize })}`,
			'GET'
		),
	run: (id: number) => fetchWithConfig<RunDetail>(`/api/data/runs/${id}`, 'GET'),
	steps: (id: number) => fetchWithConfig<{ steps: Step[] }>(`/api/data/runs/${id}/steps`, 'GET'),
	point: (id: number, seq: number) => fetchWithConfig<Point>(`/api/data/runs/${id}/points/${seq}`, 'GET'),
	plot: (spec: PlotSpec) =>
		fetchWithConfig<{ series: Series[]; units: Record<string, string | null>; shape: LineShape }>(
			'/api/data/plot',
			'POST',
			{ spec }
		),
	notebook: (spec: PlotSpec) =>
		fetchWithConfig<{ source: string }>('/api/data/plot/notebook', 'POST', { spec }),
	savePlot: (procedure: string, plot: PlotSpec, replace: string | null = null) =>
		fetchWithConfig<{ plots: PlotSpec[]; origin: string }>(
			`/api/procedures/${encodeURIComponent(procedure)}/plots`,
			'POST',
			{ plot, replace }
		),
	devices: () => fetchWithConfig<{ devices: Device[] }>('/api/data/devices', 'GET'),
	saveDevice: (name: string, properties: Device['properties'], notes: string | null) =>
		fetchWithConfig<Device>(`/api/data/devices/${encodeURIComponent(name)}`, 'PUT', { properties, notes }),
	/** The run's export URL; the browser downloads what it returns. */
	exportUrl: (id: number) => `/api/data/runs/${id}/export`
};
