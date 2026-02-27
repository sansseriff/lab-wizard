<script lang="ts">
	// center content with large CTA buttons
	const buttons = [
		{
			href: '/get_measurements',
			label: 'Create Measurement',
			description: 'Start a guided measurement setup.'
		},
		{
			href: '/manage_instruments',
			label: 'Manage Instruments',
			description: 'Add, reset, or remove instrument configs for this workstation.'
		},
		{
			href: '/create_custom_resource',
			label: 'Create Custom Resource',
			description: 'Define a reusable instrument or component.'
		}
	];
</script>

<section class="flex min-h-[60vh] items-center justify-center">
	<div class="w-full max-w-2xl text-center">
		<h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">Lab Wizard</h1>
		<p class="mt-2 text-sm text-gray-600 dark:text-gray-300">Choose what you want to do.</p>

		<div class="mt-6 grid gap-3 sm:gap-4">
			{#each buttons as b}
				<a
					href={b.href}
					class="group rounded-xl border border-gray-200 bg-white/70 p-4 text-left shadow-sm backdrop-blur transition hover:border-indigo-300 hover:shadow-md supports-[backdrop-filter]:bg-white/60 sm:p-5 dark:border-white/10 dark:bg-gray-800/70"
				>
					<div class="flex items-center justify-between">
						<div>
							<h2 class="text-lg font-medium text-gray-900 sm:text-xl dark:text-gray-50">
								{b.label}
							</h2>
							<p class="mt-1 text-xs text-gray-600 sm:text-sm dark:text-gray-300">
								{b.description}
							</p>
						</div>
						<span
							class="ml-4 inline-flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white group-hover:bg-indigo-500"
							>→</span
						>
					</div>
				</a>
			{/each}
		</div>
	</div>
</section>
