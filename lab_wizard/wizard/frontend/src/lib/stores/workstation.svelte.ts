/** Workstation-wide facts the persistent chrome shows on every page.
 *
 * Server state and hardware ownership are read by the sidebar's status strip,
 * by Overview, and by any page whose behaviour depends on which process will
 * actually touch hardware — Manage Instruments' discovery notice, for one. They
 * are fetched once here rather than per page so the answer cannot differ
 * between two things visible at the same time.
 *
 * Deliberately *not* a `+layout.ts` load: the state changes while the user
 * watches (they press Start), so it has to be refreshable without a navigation.
 */
import { fetchWithConfig } from '$lib/api';

export type ServerStatus = {
	has_config: boolean;
	running: boolean;
	detached?: boolean;
	bind?: string | null;
	pid?: number | null;
	rule_count?: number;
};

export type HardwareOwner = {
	owner: 'wizard' | 'server';
	url?: string | null;
};

/** The three states a workstation's server can be in.
 *
 * "Not configured" is not a degenerate case of "stopped" — there is no
 * server.yaml to start, and the fix is Configure, not Start. Collapsing the two
 * is what makes a stopped server look broken when it is merely absent.
 */
export type ServerPhase = 'unconfigured' | 'stopped' | 'running';

class Workstation {
	server = $state<ServerStatus | null>(null);
	owner = $state<HardwareOwner | null>(null);
	workspaceDir = $state<string>('');
	loading = $state(false);
	error = $state<string | null>(null);

	/** Trailing path segment of the workspace, which is what people call it. */
	get workspaceName(): string {
		const parts = this.workspaceDir.replace(/[/\\]$/, '').split(/[/\\]/);
		return parts[parts.length - 1] || 'workspace';
	}

	get phase(): ServerPhase {
		if (!this.server?.has_config) return 'unconfigured';
		return this.server.running ? 'running' : 'stopped';
	}

	/** Whether the permission gate can observe instrument calls at all.
	 *
	 * Only calls routed through a server are seen by it. With no server running
	 * the wizard opens hardware itself — which works, but means every rule on
	 * the Permissions page is inert. Worth saying out loud wherever it matters.
	 */
	get gateActive(): boolean {
		return this.owner?.owner === 'server';
	}

	async refresh(): Promise<void> {
		this.loading = true;
		try {
			const [server, owner, health] = await Promise.all([
				fetchWithConfig<ServerStatus>('/api/server/status', 'GET'),
				fetchWithConfig<HardwareOwner>('/api/hardware-owner', 'GET'),
				fetchWithConfig<{ workspace: string }>('/api/health', 'GET')
			]);
			this.server = server;
			this.owner = owner;
			this.workspaceDir = health.workspace ?? '';
			this.error = null;
		} catch (e) {
			// A wizard backend that cannot answer is worth surfacing, but it must
			// not blank the chrome — the last known state is still the best guess.
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.loading = false;
		}
	}
}

export const workstation = new Workstation();
