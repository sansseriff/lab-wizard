# Plans

Design and implementation plans, consolidated here from the repo root. Each
plan carries a status block at the top; this table is the summary.

| Plan | Status | What remains |
|---|---|---|
| [server_plan.md](server_plan.md) | **Mostly complete** | Phase 9 (run claims, parallel dispatch) built. Left: push notifications (6.3), forced `attribute` refs (8.6), per-attribute source selection in the picker, merged-tree edit UI |
| [database_plan.md](database_plan.md) | **Mostly complete** | `query.py` pandas helpers, the `measurements_full` view, Alembic migrations |
| [procedure_plan.md](procedure_plan.md) | **In progress** | Phases 0-5, 6.1-6.5 and 7 built (5.6 included). Next: 6.6 (port `AgilentN7764A`) |
| [semantic_data_plan.md](semantic_data_plan.md) | **Proposed** | How a run records what it *means* — coordinates vs readings, one row per point, the run type and device gaps. Decisions needed before building |
| [setup_plan.md](setup_plan.md) | **Proposed** | A dated record of the apparatus (resistors, lasers, shunts, …) that every run links to; replaces the Run page's free-form metadata. Six open questions in §9 |
| [guard_plan.md](guard_plan.md) | **Proposed** | Long-lived holds by a client that defines "safe": a lapsed guard freezes instead of resetting, guards are kept across restarts, unknown state denies. Eight phases; open questions in §6 |
| [runner_plan.md](runner_plan.md) | **First version built** | Cases A–D work, with the lab database as the live bus (see its As built block). Left: step progress in the live view, tabs in the plot window |

## Relationship to `docs/roadmap.md`

[`docs/roadmap.md`](../docs/roadmap.md) is the *published* inventory of what is
scaffolded but unfinished, and it is written against the source rather than
against these plans. Where the two disagree, check the code. The roadmap is
stale in places (graceful shutdown and client reconnect are built) but not all:
its "single-threaded poll loop" item is still true for the socket path — see
`server_plan.md` Phase 9.0.

## Convention

A plan is a **design record**, not a task list: it states the problem, the
concepts, and the phases, and it keeps the rationale for decisions that are
otherwise invisible in the resulting code. Mark phases with ✅ / ⬜ as they land,
and keep the "Remaining" or "Open questions" section at the bottom honest.
