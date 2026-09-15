# Plans

Design and implementation plans, consolidated here from the repo root. Each
plan carries a status block at the top; this table is the summary.

| Plan | Status | What remains |
|---|---|---|
| [server_plan.md](server_plan.md) | **Mostly complete; Phase 9 proposed** | Phase 9 run-scoped claims (with threaded dispatch and lease acquisition for local runs), push notifications (6.3), forced `attribute` refs (8.6), per-attribute source selection in the picker, merged-tree edit UI |
| [database_plan.md](database_plan.md) | **Mostly complete** | `query.py` pandas helpers, the `measurements_full` view, Alembic migrations |
| [procedure_plan.md](procedure_plan.md) | **Proposed** | All of it. Phase 0 (param-category cleanup) is independently valuable; Phase 6 adds the missing `Attenuator` ABC (two implementers waiting) and builds `mcr_curve` |
| [runner_plan.md](runner_plan.md) | **Not started** | Everything past listing projects — no launch endpoint, no run view, and both plotters are still placeholders |

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
