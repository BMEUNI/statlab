---
status: completed
dependencies: [44]
completion_date: 2026-10-08
implementation_notes: "applied gates skip the schema check at boot; make check 3126 passed, 100%"
---

# Chunk 45 — boot must not fail on old applied gates

User report 2026-10-08: `make dev` fails at startup:
`backend/statlab/graph/gates.py:117 _validate → EngineError("gate decision schema changed")`,
called from `GateService.reconcile` (gates.py:~525) during `RestartReconciler.reconcile_projects`.
`statlab.db` holds one completed run (`r_261239930187a0a7`) whose 9 gates are all `applied`.
`RestartReconciler` (reconciliation.py:~79) still reconciles terminal runs that have applied
gates, and `reconcile` validates every gate's stored `decision_schema` against the current
model. Chunk 42 added `Decision.user_summary`, so every gate stored before it now mismatches and
the whole API refuses to start.

Decision: the schema equality check guards answering or preparing a gate with a stale form. An
`applied` gate is never answered again, so `reconcile` skips the check for applied gates only.
Pending, answered and prepared gates keep it (a stale form there must still be refused).
Non-goals: no migration of stored questions; no change to which runs are reconciled.

## Stage 45a

Owned: `backend/statlab/graph/gates.py`, `tests/test_control_gates.py`.

1. `_validate(self, gate: GateRow, *, current_schema: bool = True) -> GateQuestion`: the
   `decision_schema` comparison runs only when `current_schema` is true; everything else
   unchanged. Every existing caller keeps the default.
2. `reconcile`: `question = self._validate(gate, current_schema=gate.status != "applied")`.
3. Test `test_reconcile_skips_schema_check_for_applied_gates(engine_world)`:
   `lifecycle, service, _seen = await shared_gate(engine_world)`; `gate =
   list_gates(conn, lifecycle.initial.run_id)[0]`;
   `applied = transition_gate(conn, gate, status="applied",
   answer_json=GateAnswer(gate_id=gate.id, action="approve").model_dump_json(),
   question={**gate.question(), "decision_schema": {}})`;
   `await service.reconcile(await lifecycle.state())` does not raise, and the replayed events
   contain a `gate.closed` event whose payload `gate_id == gate.id`. Then
   `transition_gate(conn, applied, status="pending", answer_json=None,
   question={**gate.question(), "decision_schema": {}})` and assert
   `service.reconcile(await lifecycle.state())` raises `EngineError` matching
   `"gate decision schema changed"`.

Checks: `make test-fast TESTS="tests/test_control_gates.py tests/test_control_rewind.py tests/test_api_runs.py"`;
Ruff check, format --check and mypy on the two owned files.

Main then runs `make check`, starts the API against the real `statlab.db` (read-only boot
check) and commits.

## Acceptance receipt (2026-10-08)

Implementer: focused pytest (3 files) exit 0, Ruff/format/mypy exit 0. Main `make check` exit 0:
3126 passed, 1 designed skip, coverage 100 % (12,043 statements, 3,472 branches), mypy and Ruff
clean (log `check45.log`, temporary). Real-database boot is confirmed by the user's `make dev`.
