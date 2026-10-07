# Handoff — 2026-10-07

Offline runtime validation of B9–B14 is COMPLETE and accepted. Durable evidence:
[receipts/B9-B14-offline.md](./receipts/B9-B14-offline.md) (final `make check` exit 0, 3006 passed,
1 designed skip, 100% line/branch, strict statics). Stage history:
[offline_runtime_validation.md](./offline_runtime_validation.md).

Next, only with explicit user authorization: the paid online runtime run. Before it:
- start the API from the repo root (run creation copies `./uv.lock`);
- record the git commit (the manifest does not);
- capture server stderr: crashed nodes/runs now log `Node <name> executor failed with <Type>` /
  `Run <id> execution failed with <Type>` plus frame locations (never messages);
- per-run evidence: `<data_dir>/<slug>/runs/<run_id>/` (`events.jsonl`, `state.json`,
  `report/report.md`, `pipeline.py`, `manifest.json`) and SQLite tables `node_executions`
  (summary/error), `llm_calls` (model/tokens/cost/duration), `tool_calls`, `gates`, `metrics`,
  `models`.

`graphify update .` was not run by main this session (auto-mode classifier denied it); run it
manually if the code graph is needed.
