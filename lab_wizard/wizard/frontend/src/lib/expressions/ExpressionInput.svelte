<script lang="ts">
	/** An expression, typed with suggestions, its references shown as chips.
	 *
	 * Start typing a name and a list of what it could be opens where the caret
	 * is: the run's columns, the procedure's params, its setup needs, and the
	 * functions. Pick one (click, or arrow keys and Enter or Tab) and it becomes
	 * a chip — an icon saying what kind of thing it is, and its name — so nobody
	 * types `param("readout.gate_time_s")` or `setup("bias_resistance")`. The
	 * value is still that text, as the backend reads it (`refs.ts`); a chip is
	 * removed with one Backspace, like a recipient in a mail app.
	 *
	 * The field is a `contenteditable`, so the chips sit in the text. Its DOM is
	 * the browser's while it is being typed in, and rebuilt from the text when
	 * it is not, so the text is always the one source of truth.
	 */
	import { mount, onMount, unmount, untrack, type Component } from 'svelte';
	import { Popover } from 'bits-ui';
	import ColumnsIcon from 'phosphor-svelte/lib/Columns';
	import FlaskIcon from 'phosphor-svelte/lib/Flask';
	import FunctionIcon from 'phosphor-svelte/lib/Function';
	import PlusIcon from 'phosphor-svelte/lib/Plus';
	import SlidersHorizontalIcon from 'phosphor-svelte/lib/SlidersHorizontal';
	import { matching, parse, refText, type Candidate, type Ref, type RefKind } from './refs';

	let {
		value,
		candidates,
		onchange,
		ondeclare,
		placeholder = '',
		invalid = false,
		class: klass = '',
		'aria-label': ariaLabel
	}: {
		value: string;
		candidates: Candidate[];
		/** The new text, when the field is left or Enter is pressed. */
		onchange: (text: string) => void;
		/** Declare a setup need of this name; offered when what is typed is none. */
		ondeclare?: (name: string) => void;
		placeholder?: string;
		invalid?: boolean;
		class?: string;
		'aria-label'?: string;
	} = $props();

	type Option = Candidate | { kind: 'declare'; name: string };

	const ICONS: Record<RefKind | 'function' | 'declare', Component<{ size?: number; weight?: 'bold' }>> = {
		column: ColumnsIcon,
		param: SlidersHorizontalIcon,
		setup: FlaskIcon,
		function: FunctionIcon,
		declare: PlusIcon
	};
	const KIND_LABEL = { column: 'column', param: 'param', setup: 'setup', function: 'function', declare: '' };

	// A chip's icon is plain markup inside the editable text, rendered once per kind.
	const iconMarkup = new Map<string, string>();
	function icon(kind: RefKind): string {
		let markup = iconMarkup.get(kind);
		if (markup === undefined) {
			const holder = document.createElement('span');
			const instance = mount(ICONS[kind], { target: holder, props: { size: 11, weight: 'bold' } });
			markup = holder.innerHTML;
			unmount(instance);
			iconMarkup.set(kind, markup);
		}
		return markup;
	}

	const uid = $props.id();
	const listId = `${uid}-suggestions`;
	let editor = $state<HTMLDivElement | null>(null);
	let focused = false;

	const columns = $derived(candidates.filter((c) => c.kind === 'column').map((c) => c.name));

	function chip(ref: Ref): HTMLElement {
		const el = document.createElement('span');
		el.contentEditable = 'false';
		el.className = `expr-chip expr-chip-${ref.kind}`;
		el.dataset.kind = ref.kind;
		el.dataset.name = ref.name;
		el.title = `${KIND_LABEL[ref.kind]} ${ref.name}`;
		el.innerHTML = `<span class="expr-chip-icon">${icon(ref.kind)}</span>`;
		el.append(document.createTextNode(ref.name));
		return el;
	}

	// Where a chip has no text beside it (first, last, or next to another chip)
	// the caret has nowhere to sit, and WebKit draws it outside the field. An
	// invisible character there gives it a place; it is never part of the value.
	const SPOT = '\u200b';

	function render(text: string) {
		if (!editor) return;
		const nodes: Node[] = [];
		for (const s of parse(text, columns)) {
			if ('ref' in s) {
				if (!(nodes.at(-1) instanceof Text)) nodes.push(document.createTextNode(SPOT));
				nodes.push(chip(s.ref));
			} else nodes.push(document.createTextNode(s.text));
		}
		if (nodes.length && !(nodes.at(-1) instanceof Text)) nodes.push(document.createTextNode(SPOT));
		editor.replaceChildren(...nodes);
	}

	/** The expression the field holds now. */
	function read(node: Node | null = editor): string {
		if (!node) return '';
		let out = '';
		node.childNodes.forEach((child) => {
			if (child.nodeType === Node.TEXT_NODE) out += (child.textContent ?? '').replace(/\u00a0/g, ' ').replaceAll(SPOT, '');
			else if (child instanceof HTMLElement && child.dataset.kind)
				out += refText({ kind: child.dataset.kind as RefKind, name: child.dataset.name ?? '' });
			else if (child instanceof HTMLElement && child.tagName !== 'BR') out += read(child);
		});
		return out;
	}

	onMount(() => render(untrack(() => value)));
	// A value changed from outside (another plot chosen, a rename) is shown,
	// unless it is being typed in, when the typing wins until it is left.
	$effect(() => {
		const text = value;
		void columns;
		untrack(() => {
			if (!focused) render(text);
		});
	});

	// ---- suggestions ----
	let open = $state(false);
	let typed = $state('');
	let active = $state(0);
	// Where what is being typed is: replaced by what is picked.
	let fragment: { node: Text; start: number; end: number } | null = null;
	let anchor = $state<{ getBoundingClientRect: () => DOMRect } | null>(null);

	const options = $derived.by((): Option[] => {
		const found: Option[] = matching(candidates, typed);
		const declarable =
			ondeclare && /^[A-Za-z_]\w*$/.test(typed) && !candidates.some((c) => c.kind === 'setup' && c.name === typed);
		return declarable ? [...found, { kind: 'declare', name: typed }] : found;
	});

	function close() {
		open = false;
		fragment = null;
	}

	/** Look at what is just before the caret, and suggest for it. */
	function suggest() {
		const selection = window.getSelection();
		const node = selection?.anchorNode;
		if (!selection?.isCollapsed || !editor || !node) return close();
		if (node === editor && !read().trim()) return showAll();
		if (!(node instanceof Text) || !editor.contains(node)) return close();
		const before = node.data.slice(0, selection.anchorOffset).replaceAll(SPOT, '');
		const word = /[A-Za-z_][\w.]*$/.exec(before);
		if (!word) return close();
		const start = selection.anchorOffset - word[0].length;
		fragment = { node, start, end: selection.anchorOffset };
		const range = document.createRange();
		range.setStart(node, start);
		range.setEnd(node, selection.anchorOffset);
		const rect = range.getBoundingClientRect();
		anchor = { getBoundingClientRect: () => rect };
		typed = word[0];
		active = 0;
		open = options.length > 0;
	}

	/** An empty field offers everything, under itself. */
	function showAll() {
		if (!editor) return;
		fragment = null;
		const rect = editor.getBoundingClientRect();
		anchor = { getBoundingClientRect: () => rect };
		typed = '';
		active = 0;
		open = options.length > 0;
	}

	function pick(option: Option) {
		if (!editor) return;
		if (option.kind === 'declare') ondeclare?.(option.name);
		const inserted =
			option.kind === 'function'
				? document.createTextNode(`${option.name}(`)
				: chip({ kind: option.kind === 'declare' ? 'setup' : option.kind, name: option.name });
		const range = document.createRange();
		if (fragment && editor.contains(fragment.node)) {
			range.setStart(fragment.node, fragment.start);
			range.setEnd(fragment.node, fragment.end);
		} else {
			range.selectNodeContents(editor);
			range.collapse(false);
		}
		range.deleteContents();
		const after = document.createTextNode(option.kind === 'function' ? '' : ' ');
		range.insertNode(after);
		range.insertNode(inserted);
		const caret = document.createRange();
		caret.setStart(after, after.length);
		caret.collapse(true);
		const selection = window.getSelection();
		selection?.removeAllRanges();
		selection?.addRange(caret);
		close();
	}

	function commit() {
		const text = read().trim();
		render(text);
		if (text !== value) onchange(text);
	}

	function onkeydown(event: KeyboardEvent) {
		if (open && options.length) {
			if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
				event.preventDefault();
				active = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
				return;
			}
			if (event.key === 'Enter' || event.key === 'Tab') {
				event.preventDefault();
				pick(options[active]);
				return;
			}
			if (event.key === 'Escape') {
				event.preventDefault();
				close();
				return;
			}
		}
		if (event.key === 'Enter') {
			// One line: Enter is done, as in any field.
			event.preventDefault();
			commit();
		}
		// Bold and italics mean nothing in an expression.
		if ((event.metaKey || event.ctrlKey) && ['b', 'i', 'u'].includes(event.key.toLowerCase())) event.preventDefault();
	}

	function onpaste(event: ClipboardEvent) {
		event.preventDefault();
		const text = (event.clipboardData?.getData('text/plain') ?? '').replace(/\s*\n\s*/g, ' ');
		document.execCommand('insertText', false, text);
	}
</script>

<div
	bind:this={editor}
	class="lw-input mono expr-input {invalid ? 'border-crit' : ''} {klass}"
	contenteditable="true"
	role="combobox"
	tabindex="0"
	aria-label={ariaLabel}
	aria-autocomplete="list"
	aria-haspopup="listbox"
	aria-controls={listId}
	aria-expanded={open}
	data-placeholder={placeholder}
	spellcheck="false"
	oninput={suggest}
	onfocus={() => {
		focused = true;
		if (!read().trim()) showAll();
	}}
	onblur={() => {
		focused = false;
		close();
		commit();
	}}
	{onkeydown}
	{onpaste}
></div>

<Popover.Root bind:open>
	<Popover.Portal>
		<Popover.Content
			customAnchor={anchor}
			side="bottom"
			align="start"
			sideOffset={4}
			collisionPadding={8}
			trapFocus={false}
			onOpenAutoFocus={(e) => e.preventDefault()}
			onCloseAutoFocus={(e) => e.preventDefault()}
			onInteractOutside={(e) => {
				if (editor && e.target instanceof Node && editor.contains(e.target)) e.preventDefault();
			}}
			class="lw-select-content z-[70] w-80 p-1"
		>
			<ul id={listId} role="listbox" aria-label="Suggestions">
				{#each options as option, i (option.kind + option.name)}
					{@const Icon = ICONS[option.kind]}
					<li role="option" aria-selected={i === active}>
						<button
							type="button"
							class="lw-select-item w-full text-left {i === active ? 'bg-accent-wash' : ''}"
							onmousedown={(e) => e.preventDefault()}
							onmouseenter={() => (active = i)}
							onclick={() => pick(option)}
						>
							<span class="expr-kind expr-kind-{option.kind}"><Icon size={12} weight="bold" /></span>
							{#if option.kind === 'declare'}
								<span class="min-w-0 flex-1 truncate">New setup need <span class="mono">{option.name}</span></span>
							{:else}
								<span class="mono min-w-0 flex-1 truncate">{option.name}{option.kind === 'function' ? '()' : ''}</span>
								<span class="shrink-0 text-fine text-muted">{option.detail ?? KIND_LABEL[option.kind]}</span>
							{/if}
						</button>
					</li>
				{/each}
			</ul>
		</Popover.Content>
	</Popover.Portal>
</Popover.Root>

<style>
	/* One line is a control's height, like any field; a long expression grows. */
	.expr-input {
		height: auto;
		min-height: var(--control-h);
		padding-block: 0.3125rem;
		line-height: 1.25rem;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		cursor: text;
	}
	.expr-input:empty::before {
		content: attr(data-placeholder);
		color: var(--muted);
		pointer-events: none;
	}
	.expr-input :global(.expr-chip) {
		display: inline-flex;
		align-items: center;
		gap: 0.2rem;
		margin: 0 0.05rem;
		padding: 0 0.3rem;
		border-radius: 0.25rem;
		line-height: 1.125rem;
		vertical-align: top;
		margin-top: 0.0625rem;
		user-select: all;
	}
	.expr-input :global(.expr-chip-icon) {
		display: inline-flex;
		opacity: 0.8;
	}
	.expr-input :global(.expr-chip-column),
	.expr-kind-column {
		background: var(--accent-wash);
		color: var(--accent-strong);
	}
	.expr-input :global(.expr-chip-param),
	.expr-kind-param {
		background: var(--warn-wash);
		color: var(--warn);
	}
	.expr-input :global(.expr-chip-setup),
	.expr-kind-setup,
	.expr-kind-declare {
		background: var(--ok-wash);
		color: var(--ok);
	}
	.expr-kind {
		display: inline-grid;
		place-items: center;
		width: 1.25rem;
		height: 1.25rem;
		flex-shrink: 0;
		border-radius: 0.25rem;
	}
	.expr-kind-function {
		background: var(--surface-2);
		color: var(--ink-2);
	}
</style>
