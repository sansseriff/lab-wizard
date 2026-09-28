/** A project's settings and runs, for the Run page (backend/routes/runs.py). */
import { fetchWithConfig } from '$lib/api';

export type RunDetails = {
	device: string | null;
	operator: string | null;
	notes: string | null;
	metadata: Record<string, unknown>;
};

export type Outputs = { files: boolean; live_plot: 'none' | 'window' | 'web'; plot: string };

export type ProjectSettings = {
	name: string;
	path: string;
	measurement: string;
	kind: 'procedure' | 'custom';
	setup_file: string | null;
	yaml: string;
	run: RunDetails;
	params: Record<string, unknown>;
	outputs: Outputs;
	params_schema: import('./schema').JsonSchema | null;
};

/** Where a problem is, as the backend's model saw it: ``["measurement", "params", "settle_s"]``. */
export type Problem = { path: (string | number)[]; message: string };

export type LaunchStatus = {
	state: 'idle' | 'starting' | 'running' | 'ended';
	launch_id?: string;
	pid?: number;
	started_at?: string;
	run_id?: number | null;
	exit_code?: number | null;
	log?: string;
	log_file?: string;
};

const base = (name: string) => `/api/projects/${encodeURIComponent(name)}`;

export const runApi = {
	settings: (name: string) => fetchWithConfig<ProjectSettings>(`${base(name)}/settings`, 'GET'),
	/** Save; a refusal comes back as its problems, not as a thrown error. */
	async save(
		name: string,
		body: { yaml: string } | Partial<{ run: RunDetails; params: Record<string, unknown>; outputs: Outputs }>
	): Promise<{ ok: true; settings: ProjectSettings } | { ok: false; message: string; problems: Problem[] }> {
		const response = await fetch(`${base(name)}/settings`, {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
		const data = await response.json().catch(() => ({}));
		if (response.ok) return { ok: true, settings: data as ProjectSettings };
		const detail = data?.detail;
		return {
			ok: false,
			message: typeof detail === 'string' ? detail : (detail?.message ?? `HTTP ${response.status}`),
			problems: detail?.problems ?? []
		};
	},
	status: (name: string) => fetchWithConfig<LaunchStatus>(`${base(name)}/launch`, 'GET'),
	launch: (name: string) => fetchWithConfig<LaunchStatus>(`${base(name)}/launch`, 'POST'),
	stop: (name: string) => fetchWithConfig<LaunchStatus>(`${base(name)}/stop`, 'POST')
};
