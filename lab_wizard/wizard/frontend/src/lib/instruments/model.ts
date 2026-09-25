import type { TreeItem, InstrumentMeta } from '$lib/types/instruments';

export type NodePath = { type: string; key: string }[];
export type ParamSchema = {
	$ref?: string;
	$defs?: Record<string, ParamSchema>;
	properties?: Record<string, ParamSchema>;
	additionalProperties?: ParamSchema | boolean;
	anyOf?: ParamSchema[];
	type?: string;
	title?: string;
	description?: string;
	default?: any;
	enum?: any[];
	const?: any;
	minimum?: number;
	maximum?: number;
};
export type ParamUpdate = {
	path: NodePath;
	fields: Record<string, any>;
	expected_fields: Record<string, any>;
};
export type ParamResult = { fields: Record<string, any>; yaml: string };
export const pathKey = (path: NodePath) => JSON.stringify(path);
export function nodeAt(tree: TreeItem[], path: NodePath): TreeItem | null {
	let nodes = tree;
	let found: TreeItem | undefined;
	for (const part of path) {
		found = nodes.find((node) => node.key === part.key && node.type === part.type);
		if (!found) return null;
		nodes = Object.values(found.children ?? {});
	}
	return found ?? null;
}
export function nodeTitle(node: TreeItem): string {
	return node.fields.attribute_name || node.type;
}
/** Children in display order: those with a `slot` first, by slot number, then the
 *  rest in saved order. Keys are hashes, so the slot is the only meaningful order. */
export function sortedChildren(node: TreeItem): TreeItem[] {
	const slotOf = (child: TreeItem) => {
		const slot = child.fields.slot;
		return slot == null || slot === '' ? null : String(slot);
	};
	return Object.values(node.children ?? {})
		.map((child, index) => ({ child, index, slot: slotOf(child) }))
		.sort((a, b) => {
			if (a.slot == null || b.slot == null)
				return a.slot == null && b.slot == null ? a.index - b.index : a.slot == null ? 1 : -1;
			return a.slot.localeCompare(b.slot, undefined, { numeric: true }) || a.index - b.index;
		})
		.map(({ child }) => child);
}
export function nodeAddress(node: TreeItem): string {
	const f = node.fields;
	if (f.port != null) return String(f.port);
	if (f.ip_address != null) return `${f.ip_address}:${f.ip_port}`;
	if (f.gpib_address != null) return `GPIB ${f.gpib_address}`;
	if (f.slot != null) return `Slot ${f.slot}`;
	return node.key;
}
export function matchesTree(node: TreeItem, query: string): boolean {
	const q = query.trim().toLowerCase();
	return (
		!q ||
		`${nodeTitle(node)} ${node.type} ${nodeAddress(node)} ${node.key}`.toLowerCase().includes(q) ||
		Object.values(node.children ?? {}).some((child) => matchesTree(child, q))
	);
}
export function childTypes(
	metadata: Record<string, InstrumentMeta>,
	parent: TreeItem
): InstrumentMeta[] {
	return Object.values(metadata).filter((m) => m.parent_type === parent.type);
}
export function existingParentChain(path: NodePath) {
	return [...path]
		.reverse()
		.map((part) => ({ ...part, action: 'use_existing' as const, resolved: true }));
}
/** Only siblings under the selected ancestor can fill the next chain step.
 * A slot/address hash is unique within its parent, not across the entire tree. */
export function parentCandidates(tree: TreeItem[], type: string, ancestors: NodePath): TreeItem[] {
	const nodes = ancestors.length ? Object.values(nodeAt(tree, ancestors)?.children ?? {}) : tree;
	return nodes.filter((node) => node.type === type);
}
export function resolveSchema(schema: ParamSchema, root: ParamSchema): ParamSchema {
	if (schema.$ref?.startsWith('#/$defs/'))
		return { ...root.$defs?.[schema.$ref.slice(8)], ...schema, $ref: undefined };
	return schema;
}
