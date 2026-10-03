# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**22 of 41 chunks complete, plus revisions 09r and 20r. B6 accepted 2026-10-03; committed
`de2aa83` (`feat(b6): add guarded MCP and typed LLM clients`).** Phases A/B and M7b complete.

Accepted B6: 2189 passed, one designed skip; 100% line/branch coverage; full mypy and Ruff clean.
Coverage run: 72.08 s. Acceptance candidate was parent `901e840` plus recorded changes, later
committed as `de2aa83`. Do not mistake acceptance-parent SHA for current implementation HEAD.
[Durable acceptance summary](./21_mcp_client.md#8-final-b6-acceptance-2026-10-03) records results,
commands, exits, aggregate input digest and required mutation/replay evidence. Docs-only changes
preserve acceptance; no startup baseline or repeat of B6 proofs.

## Next: batch B7 (chunks 22 engine core + 23 restart reconciliation)

Draft bounded chunk 22 from PLAN's declared spine, checkpointer/event-bus requirements and available
B6 client/repository seams. Stage 22 owns shared-node traversal and prune-on-success (D24).
After its focused terminal handoff and dependency review, specify/implement stage 23 boot/status
scan and node-boundary restart reconciliation. B7 closes M8a. Detailed specs do not exist yet.

Use one writer, focused handoffs, consolidated batch review and one stable final gate per AGENTS.
For engine/restart work, consider `implementer_strong` given async/state-machine boundaries.

## B6 seams and hazards for B7

- Reuse `create_node_execution`, MCP `CallContext`, durable test-score claim and DB call-log APIs.
  Production dispatch enforces both allow-lists, trusted injection and ownership/lineage guards.
- Share one LLM client to share its semaphore. Exactly one output repair; timeout 60; no hidden
  retries. Cancellation retains its identity; returned usage is recorded before content validation.
- Ordinary SDK chat fields are validated by SDK. Usage extras still need boundary checks.
- Routing/keys/prompts belong to 26; recovery/backoff to 31. No concrete engine, migration or new
  dependency was introduced by B6. Unknown pricing records zero with warning; Flash costs are
  informational peak upper estimates.

## Deferred responsibilities

| Responsibility | Owner |
|---|---|
| Candidate IDs (`c_01`, sequential) | 22 |
| Estimator validation, family cap D28, `max_candidates` | 27b |
| Remaining table repositories | First real caller's chunk |
| Provider-name environment check, within `llm/` | 26 |
| Cancel running node at tool-call boundary (D36c) | 31 |
| Modelling heuristics/pitfalls | Human; 26 scaffolds TODO headings only (D12) |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow, conventions and completed batches](./WORKFLOW_HISTORY.md): old counts and
  exceptions are historical; root AGENTS wins. Full previous continuation file retained there.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
