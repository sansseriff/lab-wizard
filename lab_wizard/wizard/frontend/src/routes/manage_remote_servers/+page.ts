import { browser } from '$app/environment';
import { fetchWithConfig } from '../../api';

export type RemoteServer = { name: string; url: string };

export type RemoteAttribute = {
	attribute_name: string;
	behavior_abc: string | null;
	type_hint: string | null;
	path: string;
};

export type TestResult = {
	ok: boolean;
	attributes?: RemoteAttribute[];
	error?: string;
};

export type RemoteServersData = {
	servers: RemoteServer[];
};

export const load = async () => {
	if (!browser) {
		return { servers: [] } as RemoteServersData;
	}
	const data = await fetchWithConfig<RemoteServersData>('/api/remote-servers', 'GET');
	return { servers: data?.servers ?? [] };
};

export const prerender = true;
export const ssr = false;
