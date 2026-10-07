import { browser } from '$app/environment';
import { api, errorMessage, unwrap } from '$lib/api';
import { setupsApi, type Setup } from '$lib/setups/api';
import type { NeedDecl } from '$lib/setups/model';

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
    measurementKind: 'procedure' as 'procedure' | 'custom',
    presets: [] as string[],
    /** What the procedure's derived columns read from its setup. */
    needs: {} as Record<string, NeedDecl>,
    setups: [] as Setup[],
    requirements: [] as any[],
    sources: [] as Source[],
    ownServer: null as { name: string; url: string; pid: number } | null,
    /** Why the measurement could not be loaded, shown instead of an empty page. */
    loadError: null as string | null
};

export const load = async ({ url }: any) => {
    if (!browser) return EMPTY;

    const name = url.searchParams.get('name');
    if (!name) return EMPTY;
    const kind: 'procedure' | 'custom' = url.searchParams.get('kind') === 'custom' ? 'custom' : 'procedure';

    let requirements: any[];
    try {
        requirements = await unwrap<any[]>(
            api.GET('/api/get-resources/{name}', { params: { path: { name }, query: { kind } } })
        );
    } catch (e) {
        const message = errorMessage(e);
        return { ...EMPTY, measurementName: name, measurementKind: kind, loadError: message };
    }
    const sourceData = await unwrap<{
        sources: Source[];
        own_server: { name: string; url: string; pid: number } | null;
    }>(api.GET('/api/instrument-sources'));
    const choices = await unwrap<{
        choices: { name: string; kind: string; presets: string[]; needs?: Record<string, NeedDecl> }[];
    }>(api.GET('/api/measurement-choices'));
    const choice = choices?.choices?.find((c) => c.name === name && c.kind === kind);
    const setups = await setupsApi.list().catch(() => [] as Setup[]);

    return {
        measurementName: name,
        measurementKind: kind,
        presets: choice?.presets ?? [],
        needs: choice?.needs ?? {},
        setups,
        requirements,
        sources: sourceData?.sources ?? [],
        ownServer: sourceData?.own_server ?? null,
        loadError: null
    };
};

export const prerender = true;
export const ssr = false;
