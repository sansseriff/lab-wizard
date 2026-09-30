/** The composer's state: one definition being edited, and what the backend says about it.
 *
 * Components get this object and a path, never a copy of a step, so every edit
 * lands in the one definition and every view of it agrees. After each edit the
 * definition is checked again (debounced) — the backend is the only judge of
 * whether it can be generated, so the composer never re-implements those rules;
 * it only places the problems it is told about next to the step they concern.
 */

import { api, unwrap } from '$lib/api';
import {
	type Catalog,
	type CheckResult,
	type Definition,
	type FieldSpec,
	type Path,
	type Problem,
	type Step,
	getAt,
	newStep,
	paramLeaves,
	pathKey,
	renameKey,
	renameParamRefs,
	renameRoleRefs,
	uniqueName,
	walkSteps
} from './model';

export type Origin = 'workspace' | 'builtin' | null;

export class ProcedureEditor {
	definition = $state<Definition>() as Definition;
	check = $state<CheckResult | null>(null);
	checking = $state(false);
	checkError = $state<string | null>(null);
	/** The saved text, to tell whether there is anything to save. */
	savedJson = $state('');
	/** Where the loaded procedure lived, and under which name. */
	origin = $state<Origin>(null);
	loadedName = $state<string | null>(null);
	/** Identity survives list reordering. Paths are derived from the current tree. */
	selectedStep = $state<Step | null>(null);
	selectedParameter = $state<string | null>(null);
	selectedRole = $state<string | null>(null);
	collapsedSteps = $state<Step[]>([]);
	revealVersion = $state(0);
	steps = $derived.by(() => walkSteps(this.definition, this.catalog));
	selectedPath = $derived(this.steps.find(({ step }) => step === this.selectedStep)?.path ?? null);
	checkedJson = $state<string | null>(null);
	checkCurrent = $derived.by(() => this.checkedJson === this.json);

	readonly catalog: Catalog;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#generation = 0;

	constructor(catalog: Catalog, definition: Definition, origin: Origin, loadedName: string | null) {
		this.catalog = catalog;
		this.definition = definition;
		this.origin = origin;
		this.loadedName = loadedName;
		this.savedJson = JSON.stringify(definition);
		this.selectedStep =
			this.definition.body?.type === 'sequence'
				? (this.definition.body.children?.[0] ?? null)
				: this.definition.body;
	}

	json = $derived(JSON.stringify(this.definition));
	dirty = $derived(this.json !== this.savedJson);

	markSaved(origin: Origin, definition: Definition = this.definition) {
		this.savedJson = JSON.stringify(definition);
		this.origin = origin;
		this.loadedName = definition.name;
	}

	// ------------------------- checking -------------------------

	scheduleCheck(delayMs = 300) {
		if (this.#timer) clearTimeout(this.#timer);
		// Invalidate in-flight answers immediately, including the debounce window.
		this.#generation++;
		this.checking = true;
		this.#timer = setTimeout(() => this.runCheck(), delayMs);
	}

	async runCheck() {
		if (this.#timer) clearTimeout(this.#timer);
		const generation = ++this.#generation;
		const json = this.json;
		this.checking = true;
		try {
			const result = await unwrap<CheckResult>(
				api.POST('/api/procedures/check', { body: { definition: $state.snapshot(this.definition) } })
			);
			// An answer about an older edit would mark problems that are already fixed.
			if (generation !== this.#generation) return;
			this.check = result;
			this.checkedJson = json;
			this.checkError = null;
		} catch (e) {
			if (generation !== this.#generation) return;
			this.checkError = e instanceof Error ? e.message : String(e);
		} finally {
			if (generation === this.#generation) this.checking = false;
		}
	}

	/** Problems grouped under the deepest step (or role) their path falls inside. */
	placedProblems = $derived.by(() => this.#placeProblems());

	#placeProblems(): {
		byStep: Map<string, Problem[]>;
		byRole: Map<string, Problem[]>;
		general: Problem[];
	} {
		const byStep = new Map<string, Problem[]>();
		const byRole = new Map<string, Problem[]>();
		const general: Problem[] = [];
		const stepKeys = walkSteps(this.definition, this.catalog)
			.map(({ path }) => path)
			.sort((a, b) => b.length - a.length);
		for (const problem of this.check?.problems ?? []) {
			if (problem.path[0] === 'roles' && problem.path.length > 1) {
				const role = String(problem.path[1]);
				byRole.set(role, [...(byRole.get(role) ?? []), problem]);
				continue;
			}
			const owner = stepKeys.find(
				(p) => p.length <= problem.path.length && p.every((part, i) => part === problem.path[i])
			);
			if (!owner) {
				general.push(problem);
				continue;
			}
			const key = pathKey(owner);
			byStep.set(key, [...(byStep.get(key) ?? []), problem]);
		}
		return { byStep, byRole, general };
	}

	// ------------------------- tree edits -------------------------

	selectStep(path: Path) {
		this.selectedStep = this.step(path) ?? null;
		const ancestors = this.steps
			.filter((entry) => entry.path.every((part, i) => path[i] === part))
			.map(({ step }) => step);
		this.collapsedSteps = this.collapsedSteps.filter((step) => !ancestors.includes(step));
		this.revealVersion++;
	}

	toggleStep(step: Step) {
		this.collapsedSteps = this.collapsedSteps.includes(step)
			? this.collapsedSteps.filter((item) => item !== step)
			: [...this.collapsedSteps, step];
	}

	dispose() {
		if (this.#timer) clearTimeout(this.#timer);
		this.#generation++;
	}

	step(path: Path): Step {
		return getAt(this.definition, path);
	}

	setAt(path: Path, value: unknown) {
		const parent = getAt(this.definition, path.slice(0, -1));
		const last = path[path.length - 1];
		const selected = parent[last] === this.selectedStep;
		if (value === undefined) delete parent[last];
		else parent[last] = value;
		if (selected) this.selectStep(path);
	}

	removeStep(path: Path) {
		const parent = getAt(this.definition, path.slice(0, -1));
		const last = path[path.length - 1];
		const owner = this.steps
			.filter(
				(entry) =>
					entry.path.length < path.length && entry.path.every((part, i) => path[i] === part)
			)
			.at(-1);
		if (Array.isArray(parent)) parent.splice(Number(last), 1);
		else if (path.length === 1) this.definition.body = { type: 'sequence', children: [] };
		else {
			const field = this.catalog.steps[owner?.step.type ?? '']?.fields[String(last)];
			if (field?.optional) delete parent[last];
			else parent[last] = { type: 'sequence', children: [] };
		}
		this.selectedStep = Array.isArray(parent)
			? (parent[Math.min(Number(last), parent.length - 1)] ?? owner?.step ?? null)
			: (owner?.step ?? this.definition.body);
		this.revealVersion++;
	}

	moveStep(path: Path, delta: number) {
		const list = getAt(this.definition, path.slice(0, -1));
		const from = Number(path[path.length - 1]);
		const to = from + delta;
		if (!Array.isArray(list) || to < 0 || to >= list.length) return;
		const [item] = list.splice(from, 1);
		list.splice(to, 0, item);
	}

	/** Build a step, declaring any role it needs that the procedure has no fit for. */
	build(type: string, preferredRole: string | null = null): Step {
		return newStep(this.catalog, type, this.definition, (field, spec) =>
			this.roleForField(field, spec, preferredRole)
		);
	}

	insertStep(target: Path, type: string, preferredRole: string | null = null, index?: number) {
		const step = this.build(type, preferredRole);
		const container = getAt(this.definition, target);
		if (Array.isArray(container)) {
			const at = index ?? container.length;
			container.splice(at, 0, step);
			this.selectStep([...target, at]);
		} else {
			this.setAt(target, step);
			this.selectStep(target);
		}
	}

	replaceStep(path: Path, type: string, preferredRole: string | null = null) {
		this.setAt(path, this.build(type, preferredRole));
		this.selectStep(path);
	}

	/** Put a new step of `type` where `path` is, with the old step inside its first slot. */
	wrapStep(path: Path, type: string) {
		const old = $state.snapshot(this.step(path));
		const wrapper = this.build(type);
		const spec = this.catalog.steps[type];
		const slot = Object.entries(spec.fields).find(
			([, f]) => f.kind === 'step' || f.kind === 'steps'
		);
		if (!slot) return;
		const [name, field] = slot;
		wrapper[name] = field.kind === 'steps' ? [old] : old;
		this.setAt(path, wrapper);
		this.selectStep(path);
	}

	/** Lift the only child of a container step into its place. */
	unwrapStep(path: Path) {
		const step = this.step(path);
		const spec = this.catalog.steps[step.type];
		const children: Step[] = [];
		for (const [name, field] of Object.entries(spec?.fields ?? {})) {
			if (field.kind === 'step' && step[name]) children.push(step[name]);
			if (field.kind === 'steps') children.push(...(step[name] ?? []));
		}
		if (children.length !== 1) return;
		this.setAt(path, $state.snapshot(children[0]));
		this.selectStep(path);
	}

	// ------------------------- roles -------------------------

	roleForField(field: string, spec: FieldSpec, preferred: string | null): string | null {
		const fits = (role: string) => {
			const behavior = this.definition.roles[role]?.behavior;
			if (!spec.requires.length) return true;
			const satisfies = this.catalog.behaviors[behavior]?.satisfies ?? [];
			return spec.requires.some((r) => satisfies.includes(r));
		};
		if (preferred && preferred in this.definition.roles && fits(preferred)) return preferred;
		const fitting = Object.keys(this.definition.roles).filter(fits);
		if (fitting.length) return fitting[0];
		if (!spec.requires.length) return null;
		return this.addRole(field, spec.requires[0]);
	}

	addRole(base: string, behavior: string): string {
		const name = uniqueName(base, Object.keys(this.definition.roles));
		this.definition.roles[name] = { behavior };
		return name;
	}

	renameRole(from: string, to: string) {
		if (!to || from === to || to in this.definition.roles) return false;
		renameKey(this.definition.roles, from, to);
		renameRoleRefs(this.definition.body, from, to);
		return true;
	}

	removeRole(name: string) {
		delete this.definition.roles[name];
	}

	roleUses(name: string): number {
		let uses = 0;
		const visit = (node: any) => {
			if (!node || typeof node !== 'object') return;
			if (Array.isArray(node)) return node.forEach(visit);
			if (Object.keys(node).length === 1 && node.role === name) uses++;
			Object.values(node).forEach(visit);
		};
		visit(this.definition.body);
		return uses;
	}

	// ------------------------- params -------------------------

	/** Declare a param at a dotted path, creating groups along the way. */
	addParam(
		dotted: string,
		decl: { type: string; default?: unknown; unit?: string | null }
	): string {
		const taken = new Set(paramLeaves(this.definition.params).map((p) => p.name));
		let name = dotted;
		for (let n = 2; taken.has(name); n++) name = `${dotted}_${n}`;
		const parts = name.split('.');
		let group: any = this.definition.params;
		for (const part of parts.slice(0, -1)) {
			if (!group[part] || typeof group[part].type === 'string') group[part] = {};
			group = group[part];
		}
		group[parts[parts.length - 1]] = { ...decl };
		return name;
	}

	renameParam(groupPath: string[], from: string, to: string) {
		const group = getAt(this.definition.params, groupPath);
		if (!to || from === to || to in group) return false;
		renameKey(group, from, to);
		const prefix = groupPath.length ? `${groupPath.join('.')}.` : '';
		renameParamRefs(this.definition.body, `${prefix}${from}`, `${prefix}${to}`);
		return true;
	}

	paramUses(dotted: string): number {
		let uses = 0;
		const visit = (node: any) => {
			if (!node || typeof node !== 'object') return;
			if (Array.isArray(node)) return node.forEach(visit);
			if (
				Object.keys(node).length === 1 &&
				typeof node.param === 'string' &&
				(node.param === dotted || node.param.startsWith(`${dotted}.`))
			)
				uses++;
			Object.values(node).forEach(visit);
		};
		visit(this.definition.body);
		return uses;
	}
}
