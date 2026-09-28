<!-- A button that is only an icon. Its label is both the accessible name and
     the tooltip, so it can never be unlabelled for either audience. -->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import Tooltip from './Tooltip.svelte';

	let {
		label,
		onclick,
		disabled = false,
		small = false,
		ghost = false,
		class: className = '',
		children
	}: {
		label: string;
		onclick: () => void;
		disabled?: boolean;
		small?: boolean;
		/** No border or fill, for a button inside a row that is itself a box. */
		ghost?: boolean;
		class?: string;
		children: Snippet;
	} = $props();
</script>

<Tooltip text={label}>
	{#snippet child({ props })}
		<button
			{...props}
			type="button"
			class="lw-btn {small ? 'lw-btn-sm' : ''} {ghost ? 'lw-btn-ghost' : ''} px-2 {className}"
			aria-label={label}
			{disabled}
			onclick={(e) => {
				(props.onclick as ((e: MouseEvent) => void) | undefined)?.(e);
				onclick();
			}}>{@render children()}</button
		>
	{/snippet}
</Tooltip>
