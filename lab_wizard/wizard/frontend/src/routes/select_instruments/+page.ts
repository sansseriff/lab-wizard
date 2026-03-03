import { browser } from '$app/environment';
import { fetchWithConfig } from '../../api';

type TreeItem = {
    type: string;
    key: string;
    fields: Record<string, any>;
    children: Record<string, TreeItem>;
};

type InstrumentMeta = {
    type: string;
    class_name: string;
    module: string;
    is_top_level: boolean;
    is_child: boolean;
    parent_type: string | null;
    parent_chain: string[];
    child_types: string[];
    defaults: Record<string, any>;
    key_hint: string | null;
};

type ManageData = {
    tree: TreeItem[];
    metadata: Record<string, InstrumentMeta>;
};

export const load = async ({ fetch, url }: any) => {
    // Ensure this only runs in the browser so the state module is available
    if (!browser) {
        return {
            measurementName: null,
            instruments: [] as string[],
            tree: [] as TreeItem[],
            metadata: {} as Record<string, InstrumentMeta>
        };
    }

    const name = url.searchParams.get('name');
    if (!name) {
        return {
            measurementName: null,
            instruments: [] as string[],
            tree: [] as TreeItem[],
            metadata: {} as Record<string, InstrumentMeta>
        };
    }

    let instruments = await fetchWithConfig(`/api/get-instruments/${encodeURIComponent(name)}`, 'GET');
    instruments = Array.isArray(instruments) ? instruments : [];
    const manageData = await fetchWithConfig<ManageData>('/api/manage-instruments', 'GET');

    return {
        measurementName: name,
        instruments,
        tree: manageData?.tree ?? [],
        metadata: manageData?.metadata ?? {}
    };
};

export const prerender = true;
export const ssr = false;
