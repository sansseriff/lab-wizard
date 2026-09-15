import { browser } from '$app/environment';
import { fetchWithConfig } from '$lib/api';

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
    // This workspace's own daemon, serving the same tree as `local`: choosing
    // it means using an instrument through the server instead of opening it.
    is_own_server?: boolean;
};

const EMPTY = {
    measurementName: null,
    measurementKind: 'measurement' as 'measurement' | 'procedure',
    presets: [] as string[],
    requirements: [] as any[],
    sources: [] as Source[],
    ownServer: null as { name: string; url: string; pid: number } | null
};

export const load = async ({ url }: any) => {
    if (!browser) return EMPTY;

    const name = url.searchParams.get('name');
    if (!name) return EMPTY;
    const kind: 'measurement' | 'procedure' =
        url.searchParams.get('kind') === 'procedure' ? 'procedure' : 'measurement';

    let requirements = await fetchWithConfig(
        `/api/get-resources/${encodeURIComponent(name)}?kind=${kind}`,
        'GET'
    );
    requirements = Array.isArray(requirements) ? requirements : [];
    const sourceData = await fetchWithConfig<{
        sources: Source[];
        own_server: { name: string; url: string; pid: number } | null;
    }>('/api/instrument-sources', 'GET');
    const choices = await fetchWithConfig<{
        choices: { name: string; kind: string; presets: string[] }[];
    }>('/api/measurement-choices', 'GET');
    const choice = choices?.choices?.find((c) => c.name === name && c.kind === kind);

    return {
        measurementName: name,
        measurementKind: kind,
        presets: choice?.presets ?? [],
        requirements,
        sources: sourceData?.sources ?? [],
        ownServer: sourceData?.own_server ?? null
    };
};

export const prerender = true;
export const ssr = false;
