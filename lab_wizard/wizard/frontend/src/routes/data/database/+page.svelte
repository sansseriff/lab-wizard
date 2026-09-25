<script lang="ts">
	/** Where every run is recorded.
	 *
	 * The lab database needs no configuring: every run started from a project
	 * is recorded in the workspace's data/lab.db. Browsing it here is not built
	 * yet (plans/semantic_data_plan.md, Phase 7), and this page says so rather
	 * than pretending otherwise; until then runs are read from Python.
	 */
	import DatabaseIcon from 'phosphor-svelte/lib/Database';
</script>

<section class="space-y-4">
	<div>
		<h1 class="text-[22px] font-semibold tracking-tight">Database</h1>
		<p class="mt-1 max-w-[64ch] text-[13px] text-muted">
			Every run started from a project is recorded here: one row per point, tagged with every
			parameter in force, every step it executed, and what each instrument was configured with.
			The order of a procedure's loops does not change the rows, so the data can be sliced by
			parameters that were never the outer loop.
		</p>
	</div>

	<div class="rounded border border-line bg-surface px-4 py-4">
		<div class="flex items-center gap-2">
			<DatabaseIcon size={18} class="text-muted" />
			<p class="text-[13px] font-medium">
				<code>data/lab.db</code> in this workspace
			</p>
		</div>
		<p class="mt-2 max-w-[64ch] text-xs text-muted">
			Nothing to configure. A project outside any workspace records into its own
			<code>data/lab.db</code>. Name the device under test in the project's <code>run:</code> block
			before a run, so its runs can be found by device later.
		</p>
	</div>

	<div class="rounded border border-line bg-surface-2 px-3.5 py-3">
		<p class="text-[12.5px] font-medium">Browsing runs here is not built yet.</p>
		<p class="mt-1 max-w-[64ch] text-xs text-muted">
			Until it is, read runs from Python with <code>lab_wizard.lib.data</code>:
			<code>find(procedure="mcr_curve").points()</code> gives a table of every point. To also save
			each run as a folder of CSV files, add a <code>file_saver</code> on
			<a class="text-accent hover:underline" href="/data/savers">Savers</a>.
		</p>
	</div>
</section>
