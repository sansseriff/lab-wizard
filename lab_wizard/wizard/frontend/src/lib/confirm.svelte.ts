/** `await ask({...})` in place of `window.confirm()`.
 *
 * The browser's confirm box looks different in every browser, cannot say which
 * button is the dangerous one, and blocks the page. This shows the app's own
 * ConfirmDialog (mounted once, in the root layout) and resolves true or false.
 */
import { beforeNavigate, goto } from '$app/navigation';

export type Question = {
	title: string;
	description?: string;
	confirmLabel?: string;
	tone?: 'primary' | 'danger';
};

type Pending = Question & { resolve: (answer: boolean) => void };

export const pending = $state<{ current: Pending | null }>({ current: null });

export function ask(question: Question): Promise<boolean> {
	// A second question replaces the first, which counts as cancelled.
	pending.current?.resolve(false);
	return new Promise((resolve) => {
		pending.current = { ...question, resolve };
	});
}

export function answer(yes: boolean) {
	const current = pending.current;
	pending.current = null;
	current?.resolve(yes);
}

/** Ask before leaving a page with unsaved work. Call during component setup.
 *
 * `beforeNavigate` has to decide synchronously, so the navigation is cancelled,
 * the question asked, and on a yes the same navigation is replayed. Closing the
 * tab or leaving the app cannot wait for a dialog; cancelling those makes the
 * browser show its own "leave site?" prompt, which is the best on offer.
 */
export function guardNavigation(isDirty: () => boolean, question: Question) {
	let replaying = false;
	beforeNavigate((navigation) => {
		if (replaying) {
			replaying = false;
			return;
		}
		if (!isDirty()) return;
		navigation.cancel();
		if (navigation.type === 'leave' || !navigation.to) return;
		const { url } = navigation.to;
		const delta = navigation.type === 'popstate' ? navigation.delta : undefined;
		ask(question).then(async (yes) => {
			if (!yes) return;
			replaying = true;
			if (delta) history.go(delta);
			else await goto(url);
		});
	});
}
