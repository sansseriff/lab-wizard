import { browser } from '$app/environment';
import { fetchWithConfig } from '../../api';

type TreeItem = {
    type: string;
    key: string;
    fields: Record<string, any>;
    children: Record<string, TreeItem>;
    num_channels?: number;
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

type AttributeEntry = {
    attribute_name: string;
    path: string;
    behavior_abc: string | null;
    type_hint: string | null;
};

/** One place instruments can come from. See backend/instrument_sources.py.
 *
 * `tree` is null for a remote-machine source: a tcp peer gets read + call and
 * never reconfiguration, so it is offered as a flat list of named leaves rather
 * than a hierarchy it could not act on.
 */
type Source = {
    name: string;
    kind: 'local' | 'machine' | 'remote';
    label: string;
    url: string | null;
    config_dir: string | null;
    tree: TreeItem[] | null;
    metadata: Record<string, InstrumentMeta>;
    attributes: AttributeEntry[];
    editable: boolean;
    reachable: boolean;
    error: string | null;
};

const EMPTY = {
    measurementName: null,
    requirements: [] as any[],
    sources: [] as Source[],
    ownServer: null as { name: string; url: string; pid: number } | null
};

export const load = async ({ url }: any) => {
    if (!browser) return EMPTY;

    const name = url.searchParams.get('name');
    if (!name) return EMPTY;

    let requirements = await fetchWithConfig(`/api/get-resources/${encodeURIComponent(name)}`, 'GET');
    requirements = Array.isArray(requirements) ? requirements : [];
    const sourceData = await fetchWithConfig<{
        sources: Source[];
        own_server: { name: string; url: string; pid: number } | null;
    }>('/api/instrument-sources', 'GET');

    return {
        measurementName: name,
        requirements,
        sources: sourceData?.sources ?? [],
        ownServer: sourceData?.own_server ?? null
    };
};

export const prerender = true;
export const ssr = false;
