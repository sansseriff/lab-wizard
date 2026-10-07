/** SI prefixes on a known unit: `100 kΩ` is `100000 Ω`.
 *
 * The same rules as `lib/data/units.py`, so the page can say whether a field
 * can fill a need while it is being typed. The backend decides: a binding it
 * cannot convert is refused when the measurement is created or run.
 */

const ALIASES: Record<string, string> = {
	ohm: 'Ω', ohms: 'Ω', Ohm: 'Ω', Ohms: 'Ω', Ω: 'Ω', 'Ω': 'Ω',
	volt: 'V', volts: 'V',
	amp: 'A', amps: 'A',
	watt: 'W', watts: 'W',
	sec: 's'
};
const BASES = new Set(['Ω', 'V', 'A', 'W', 'Hz', 's', 'K', 'F', 'H', 'm', 'g', 'S', 'J', 'C', 'T', 'Pa']);
const PREFIXES: Record<string, number> = {
	T: 1e12, G: 1e9, M: 1e6, k: 1e3,
	m: 1e-3, u: 1e-6, µ: 1e-6, μ: 1e-6, n: 1e-9, p: 1e-12, f: 1e-15
};

function canonical(unit: string): string {
	const u = unit.trim();
	if (u in ALIASES) return ALIASES[u];
	if (u.length > 1 && u[0] in PREFIXES && u.slice(1) in ALIASES) return u[0] + ALIASES[u.slice(1)];
	return u;
}

/** `[factor, base]`: `kΩ` is `[1000, 'Ω']`. An unknown unit is its own base. */
export function toBase(unit: string): [number, string] {
	const u = canonical(unit);
	if (BASES.has(u)) return [1, u];
	if (u.length > 1 && u[0] in PREFIXES && BASES.has(u.slice(1))) return [PREFIXES[u[0]], u.slice(1)];
	return [1, u];
}

export function baseUnit(unit: string | null | undefined): string | null {
	return unit ? toBase(unit)[1] : null;
}

/** Whether a value in `unit` can be read in `target`. No unit on either side always can. */
export function compatible(unit: string | null | undefined, target: string | null | undefined): boolean {
	return !unit || !target || baseUnit(unit) === baseUnit(target);
}
