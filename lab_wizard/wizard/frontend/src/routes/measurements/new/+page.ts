import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { api, errorMessage, unwrap } from '$lib/api';

/** One thing a measurement can be created from. See api_measurement_choices.
 *
 * `procedure` is a definition — this workspace's own, or built into lab_wizard;
 * `custom` is a Python file in this workspace's measurements folder. To the
 * person creating a measurement both are roles to bind and params to set.
 */
export type MeasurementChoice = {
	name: string;
	kind: 'procedure' | 'custom';
	origin: 'builtin' | 'workspace' | null;
	description: string;
	roles?: Record<string, string>;
	records?: string[];
	presets: string[];
	error?: string;
};

export const load: PageLoad = async () => {
	if (!browser) {
		return { choices: [] as MeasurementChoice[], error: null as string | null };
	}
	try {
		const data = await unwrap<{ choices: MeasurementChoice[] }>(api.GET('/api/measurement-choices'));
		return { choices: data?.choices ?? [], error: null };
	} catch (e) {
		return { choices: [] as MeasurementChoice[], error: errorMessage(e) };
	}
};

export const prerender = true;
export const ssr = false;
