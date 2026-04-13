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

export const load = async () => {
    if (!browser) {
        return { tree: [] as TreeItem[], metadata: {} as Record<string, InstrumentMeta> };
    }
    const manageData = await fetchWithConfig<ManageData>('/api/manage-instruments', 'GET');
    return {
        tree: manageData?.tree ?? [],
        metadata: manageData?.metadata ?? {}
    };
};

export const prerender = true;
export const ssr = false;
