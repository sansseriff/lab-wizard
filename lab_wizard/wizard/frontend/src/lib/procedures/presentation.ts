/** Readable views of a definition. These helpers never change execution structure. */
import {
	boundNames,
	getAt,
	isRef,
	paramLeaves,
	walkSteps,
	type Catalog,
	type Definition,
	type Path,
	type Step
} from './model';

export function humanize(value: string): string {
	const words = value.replace(/_/g, ' ');
	return words.charAt(0).toUpperCase() + words.slice(1);
}

export function formatValue(value: unknown): string {
	if (value === undefined || value === null) return 'Not set';
	if (typeof value === 'boolean') return value ? 'Yes' : 'No';
	if (Array.isArray(value)) return value.length ? value.map(formatValue).join(', ') : 'Empty list';
	if (typeof value === 'object') {
		const sweep = value as Record<string, unknown>;
		if (sweep.mode === 'linear') return `${sweep.start} → ${sweep.stop}, step ${sweep.step}`;
		if (sweep.mode === 'explicit') return formatValue(sweep.values);
		if (sweep.mode === 'waypoints')
			return `${(sweep.points as number[]).join(' → ')}, step ${sweep.step}`;
		return JSON.stringify(value);
	}
	return String(value);
}

export function valueSummary(value: unknown, definition: Definition, unit = ''): string {
	if (isRef(value, 'role')) return value.role || 'Choose a role';
	if (isRef(value, 'swept')) return `Current ${value.swept || 'sweep value'}`;
	if (isRef(value, 'param')) {
		const param = paramLeaves(definition.params).find((p) => p.name === value.param);
		if (!param) return `Missing parameter: ${value.param || '(unnamed)'}`;
		return `${formatValue(param.decl.default)}${param.decl.unit || unit ? ` ${param.decl.unit || unit}` : ''} · default`;
	}
	return `${formatValue(value)}${unit && value != null ? ` ${unit}` : ''}`;
}

export function stepTitle(step: Step): string {
	if (step.name) return step.name;
	if (
		step.type === 'with_parameter' &&
		step.parameter === 'phase' &&
		typeof step.value === 'string'
	)
		return humanize(step.value);
	const titles: Record<string, string> = {
		count: 'Count photons',
		set_threshold: 'Set counter threshold',
		source_guard: 'Source guard',
		safe_guard: 'Safe-state guard',
		with_parameter: 'Label recorded rows',
		with_settings: 'Temporary settings',
		read_voltage: 'Read device voltage',
		if: 'If / else',
		selector: 'First successful step',
		return_to_zero_and_off: 'Reset source',
		value_above: 'Check value above',
		value_below: 'Check value below'
	};
	if (step.type === 'sweep') return `Sweep ${step.parameter || 'values'}`;
	return titles[step.type] ?? humanize(step.type);
}

export function stepSummary(step: Step, definition: Definition, catalog: Catalog): string {
	const value = (key: string, unit = '') =>
		valueSummary(step[key] ?? catalog.steps[step.type]?.fields[key]?.default, definition, unit);
	const fields: Record<string, [string, string]> = {
		set_voltage: ['voltage', 'V'],
		set_threshold: ['threshold_mV', 'mV'],
		set_attenuation: ['attenuation_db', 'dB'],
		wait: ['seconds', 's'],
		count: ['gate_time', 's'],
		sweep: ['values', '']
	};
	if (fields[step.type]) return value(...fields[step.type]);
	if (step.type === 'with_parameter') return `${step.parameter || 'Label'} = ${value('value')}`;
	if (step.type === 'repeat') return `${value('count')} repetitions`;
	if (step.type === 'retry') return `Up to ${value('max_attempts')} attempts`;
	if (step.type === 'read_voltage') return `${value('sense')} → ${step.field ?? 'voltage'}`;
	if (step.type === 'sequence' || step.type === 'selector')
		return `${step.children?.length ?? 0} steps`;
	if (step.type === 'value_above' || step.type === 'value_below')
		return `${step.field || 'Value'} ${step.type === 'value_above' ? '>' : '<'} ${value('threshold')}`;
	const roles = Object.entries(catalog.steps[step.type]?.fields ?? {})
		.filter(([, f]) => f.kind === 'role')
		.map(([name]) => value(name));
	return roles.join(' · ') || catalog.steps[step.type]?.summary.replaceAll('``', '') || 'Unknown step type';
}

/** Preserve conditional cleanup in the summary; a parameter default isn't a guarantee. */
export function cleanupSummary(step: Step, definition: Definition): string | null {
	if (step.type === 'safe_guard')
		return `On exit · ${valueSummary(step.instrument, definition)} → declared safe state`;
	if (step.type === 'with_settings')
		return `On exit · restore ${valueSummary(step.instrument, definition)} settings`;
	if (step.type !== 'source_guard') return null;
	const actions = [
		['return_to_zero', 'return to 0 V'],
		['turn_off_at_end', 'turn off']
	].flatMap(([field, label]) => {
		const v = step[field] ?? true;
		return v === false ? [] : [v === true ? label : `${label} if ${valueSummary(v, definition)}`];
	});
	return `On exit · ${actions.length ? `${valueSummary(step.source, definition)}: ${actions.join(', then ')}` : 'no source cleanup enabled'}`;
}

export function stepContext(definition: Definition, catalog: Catalog, path: Path) {
	const ancestors = walkSteps(definition, catalog).filter(
		(entry) => entry.path.length < path.length && entry.path.every((part, i) => path[i] === part)
	);
	const bindings = new Map<string, string>();
	for (const { step } of ancestors) {
		for (const name of boundNames(step, catalog.steps[step.type])) {
			bindings.set(
				name,
				step.type === 'with_parameter'
					? valueSummary(step.value, definition)
					: 'current sweep value'
			);
		}
	}
	const parent = getAt(definition, path.slice(0, -1));
	const owner = ancestors.at(-1)?.step;
	const field =
		typeof path.at(-1) === 'string'
			? catalog.steps[owner?.type ?? '']?.fields[String(path.at(-1))]
			: undefined;
	return {
		scope: [...bindings.keys()],
		bindings: [...bindings.entries()],
		breadcrumbs: ancestors.filter(({ step }) => step.type !== 'sequence' || step.name),
		place: (Array.isArray(parent) ? 'list' : path.length === 1 ? 'root' : 'slot') as
			| 'list'
			| 'root'
			| 'slot',
		index: Array.isArray(parent) ? Number(path.at(-1)) : 0,
		count: Array.isArray(parent) ? parent.length : 1,
		optional: !!field?.optional
	};
}
