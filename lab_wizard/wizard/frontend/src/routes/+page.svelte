<script lang="ts">
	// Home tasks grouped by the three workstation roles: Run, Host, Consume.
	type Task = { href: string; label: string; description: string };
	type Group = { title: string; blurb: string; tasks: Task[] };

	const groups: Group[] = [
		{
			title: 'Build & run measurements',
			blurb: 'Set up a measurement and the resources it uses.',
			tasks: [
				{
					href: '/get_measurements',
					label: 'Create Measurement',
					description: 'Start a guided measurement setup.'
				},
				{
					href: '/create_custom_resource',
					label: 'Create Custom Resource',
					description: 'Define a reusable instrument or component.'
				},
				{
					href: '/manage_savers',
					label: 'Manage Savers',
					description: 'Configure database / file savers used by measurements.'
				},
				{
					href: '/manage_plotters',
					label: 'Manage Plotters',
					description: 'Configure local or web plotters used by measurements.'
				}
			]
		},
		{
			title: 'This workstation (host)',
			blurb: "The hardware this machine drives and the server that exposes it.",
			tasks: [
				{
					href: '/manage_instruments',
					label: 'Manage Instruments',
					description: 'Add, reset, or remove instrument configs for this workstation.'
				},
				{
					href: '/manage_permissions',
					label: 'Server & Permissions',
					description: 'Start/stop the instrument server and author its safety rules.'
				}
			]
		},
		{
			title: 'Remote',
			blurb: 'Other machines whose instruments your measurements can use.',
			tasks: [
				{
					href: '/manage_remote_servers',
					label: 'Remote Servers',
					description: 'Register remote lab_wizard servers to use in measurements.'
				}
			]
		}
	];
</script>

<section class="mx-auto w-full max-w-3xl space-y-8 py-6">
	<h1 class="text-center text-3xl font-semibold tracking-tight sm:text-4xl">Lab Wizard</h1>

	{#each groups as group}
		<div class="space-y-3">
			<div>
				<h2 class="text-sm font-semibold tracking-wide text-gray-500 uppercase dark:text-gray-400">
					{group.title}
				</h2>
				<p class="text-xs text-gray-500 dark:text-gray-400">{group.blurb}</p>
			</div>
			<div class="grid gap-3 sm:gap-4">
				{#each group.tasks as t}
					<a
						href={t.href}
						class="group rounded-xl border border-gray-200 bg-white/70 p-4 text-left shadow-sm backdrop-blur transition hover:border-indigo-300 hover:shadow-md supports-[backdrop-filter]:bg-white/60 sm:p-5 dark:border-white/10 dark:bg-gray-800/70"
					>
						<div class="flex items-center justify-between">
							<div>
								<h3 class="text-lg font-medium text-gray-900 sm:text-xl dark:text-gray-50">
									{t.label}
								</h3>
								<p class="mt-1 text-xs text-gray-600 sm:text-sm dark:text-gray-300">
									{t.description}
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
	{/each}
</section>
