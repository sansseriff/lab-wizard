/** A project's settings and runs, for the Run page (backend/routes/runs.py). */
import { api, unwrap, type Schemas } from '$lib/api';

export type RunDetails = Schemas['RunConfig'];
export type Outputs = Schemas['OutputsConfig-Output'];
export type ProjectSettings = Omit<Schemas['ProjectSettings'], 'params_schema'> & {
	params_schema: import('./schema').JsonSchema | null;
};
export type LaunchStatus = Schemas['LaunchStatus'];
export type { Problem } from '$lib/api';

const project = (name: string) => ({ params: { path: { name } } });

export const runApi = {
	settings: async (name: string) =>
		(await unwrap(api.GET('/api/projects/{name}/settings', project(name)))) as ProjectSettings,
	/** Save; a refusal throws an `ApiError` whose `problems` say where. */
	save: async (
		name: string,
		body: { yaml: string } | Partial<{ run: RunDetails; params: Record<string, unknown>; outputs: Outputs }>
	) => (await unwrap(api.PUT('/api/projects/{name}/settings', { ...project(name), body }))) as ProjectSettings,
	status: (name: string) => unwrap(api.GET('/api/projects/{name}/launch', project(name))),
	launch: (name: string) => unwrap(api.POST('/api/projects/{name}/launch', project(name))),
	stop: (name: string) => unwrap(api.POST('/api/projects/{name}/stop', project(name)))
};
