/** Workspace settings (backend/routes/settings.py), typed from the backend. */
import { api, unwrap, type Schemas } from '$lib/api';

export type WorkspacePaths = Schemas['WorkspacePaths'];
export type FileSettings = Schemas['FileSettings-Output'];
export type TemplateProblem = Schemas['TemplateProblem'];
export type TemplateCheck = Schemas['TemplateCheck'];
export type FileSettingsView = Schemas['FileSettingsView'];

export const settingsApi = {
	workspace: () => unwrap(api.GET('/api/settings/workspace')),
	files: () => unwrap(api.GET('/api/settings/files')),
	saveFiles: (files: FileSettings) => unwrap(api.PUT('/api/settings/files', { body: files })),
	checkTemplate: (path: string) => unwrap(api.POST('/api/settings/files/check', { body: { path } }))
};
