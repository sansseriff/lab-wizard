/** Setups and their pictures (backend/routes/setups.py). */
import { ApiError, api, unwrap, type Schemas } from '$lib/api';

export type Setup = Schemas['Setup'];

const named = (name: string) => ({ params: { path: { name } } });

export const setupsApi = {
	list: () => unwrap(api.GET('/api/setups')),
	get: (name: string) => unwrap(api.GET('/api/setups/{name}', named(name))),
	/** Create a setup, or replace its fields, device and notes. Past runs keep their copies. */
	save: (name: string, body: { fields: Record<string, unknown>; device: string | null; notes: string | null }) =>
		unwrap(api.PUT('/api/setups/{name}', { ...named(name), body })),
	remove: (name: string) => unwrap(api.DELETE('/api/setups/{name}', named(name))),
	/** Keep a picture; returns the name a field refers to it by. */
	async uploadImage(file: File): Promise<string> {
		const dot = file.name.lastIndexOf('.');
		const suffix = dot >= 0 ? file.name.slice(dot) : '.' + (file.type.split('/')[1] ?? 'png');
		const response = await fetch(`/api/setup-images?suffix=${encodeURIComponent(suffix)}`, {
			method: 'PUT',
			body: file
		});
		const body = await response.json().catch(() => ({}));
		if (!response.ok) throw new ApiError(response.status, body?.detail ?? `HTTP ${response.status}`);
		return body.image as string;
	},
	imageUrl: (image: string) => `/api/setup-images/${encodeURIComponent(image)}`
};
