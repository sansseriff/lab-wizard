/** What a run is holding, for the pickers that let you choose instruments.
 *
 * A **claim** is a running measurement's exclusive hold on part of a server's
 * tree (`plans/server_plan.md` Phase 9). `/api/instrument-sources` reports the
 * claims each source's server holds, and tags each named leaf with who holds
 * it; what is left for the client is turning a tree position into the path a
 * claim names, which only the page drawing the tree knows.
 *
 * Shared by measurement creation and custom resources so both say the same
 * thing about the same hardware.
 */

import type { TreePathRef } from '$lib/components/TreeNode.svelte';

export type Claim = { holder: string; units: string[]; restoring: boolean };

/** The `inst://` path of a tree node — the same address a claim names.
 *
 * A node's path is its chain of keys from the root, which is exactly how the
 * server builds the paths it reports claims against.
 */
export function instPath(path: TreePathRef[]): string {
	return `inst://${path.map((p) => p.key).join('/')}`;
}

/** Whether a claim on `unit` covers `target` — the server's rule, in
 * `lib/server/claims.py`: a claim covers its whole subtree. */
export function claimCovers(unit: string, target: string): boolean {
	return unit === target || target.startsWith(`${unit}/`);
}

/** How this node is busy right now, if it is.
 *
 * A claim on something *inside* the node (one channel of a counter) is phrased
 * differently from one on the node itself: the sibling channels may still be
 * free, and on a driver whose channels are claimable that matters.
 */
export function busyLabel(claims: Claim[] | undefined, path: TreePathRef[]): string | null {
	const target = instPath(path);
	for (const claim of claims ?? []) {
		const holder = claim.restoring ? `${claim.holder} (being reset)` : claim.holder;
		if (claim.units.some((unit) => claimCovers(unit, target))) return `in use by ${holder}`;
		if (claim.units.some((unit) => claimCovers(target, unit))) return `part in use by ${holder}`;
	}
	return null;
}

/** How many units each source has claimed, for a count beside its name. */
export function busyCounts(sources: { name: string; claims?: Claim[] }[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const source of sources) {
		const units = (source.claims ?? []).reduce((n, c) => n + c.units.length, 0);
		if (units) counts.set(source.name, units);
	}
	return counts;
}
