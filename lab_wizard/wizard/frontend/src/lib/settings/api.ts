/** Workspace settings (the /api/settings endpoints in backend/main.py), typed. */
import { fetchWithConfig } from '$lib/api';

/** Where this workspace keeps each kind of thing, resolved from lab-wizard.toml. */
export type WorkspacePaths = {
	root: string;
	manifest: string;
	config_dir: string;
	projects_dir: string;
	logs_dir: string;
	data_dir: string;
	database: string;
};

/** How a run of a project with file saving on is laid out (config/data.yaml). */
export type FileSettings = { root: string; path: string; plot_png: boolean };

/** Something wrong with a folder template: an error blocks saving, a warning does not. */
export type TemplateProblem = { level: 'error' | 'warning'; key: string; message: string };

export type TemplateCheck = {
	/** Where the latest run's folder would go (a made-up run before there is one). */
	example: string;
	problems: TemplateProblem[];
};

export type FileSettingsView = TemplateCheck & {
	files: FileSettings;
	/** The folder runs go under, resolved. */
	folder: string;
	/** Every key a template can use: the fixed ones, then those this lab has recorded. */
	keys: string[];
};

export const settingsApi = {
	workspace: () => fetchWithConfig<WorkspacePaths>('/api/settings/workspace', 'GET'),
	files: () => fetchWithConfig<FileSettingsView>('/api/settings/files', 'GET'),
	saveFiles: (files: FileSettings) => fetchWithConfig<FileSettingsView>('/api/settings/files', 'PUT', files),
	checkTemplate: (path: string) =>
		fetchWithConfig<TemplateCheck>('/api/settings/files/check', 'POST', { path })
};
