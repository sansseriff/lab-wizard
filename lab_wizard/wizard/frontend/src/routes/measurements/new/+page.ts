import type { PageLoad } from './$types';
import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';

type MeasurementInfo = {
    name: string;
    description: string;
    measurement_dir: string;
};

export const load: PageLoad = async ({ fetch }) => {
    if (!browser) {
        return { measurements: [] as MeasurementInfo[] };
    }
    const data = await fetchWithConfig('/api/get-measurements', 'GET');

    // Backend returns an object keyed by measurement name; turn into array
    const measurements: MeasurementInfo[] = Object.values(data ?? {});
    return { measurements };
};

export const prerender = true;
export const ssr = false;
