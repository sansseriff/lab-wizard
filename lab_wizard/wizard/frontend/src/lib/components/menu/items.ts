/** One entry in a row's actions menu. The same list feeds the "⋯" button and
 *  the right-click menu, so the two can never disagree. */
export type MenuAction = {
	label: string;
	/** Run on choosing; or give `href` to navigate instead. */
	onselect?: () => void;
	href?: string;
	/** Shown in the danger colour, after a divider. */
	danger?: boolean;
	disabled?: boolean;
};
