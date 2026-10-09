---
status: completed
dependencies: [53]
completion_date: 2026-10-09
implementation_notes: "step details drop per-call and per-attempt cost; npm 91/91"
---

# Chunk 54 — no per-step cost in the step details

User request 2026-10-09: the step details show a Cost column per agent call and "Agent cost
for this attempt"; unnecessary, the run total in the header is enough. The only per-step cost
display is `frontend/src/run/NodeInspector.tsx`; the header total (`RunHeader.tsx:83`) stays.

## Stage 54a

Owned: `frontend/src/run/NodeInspector.tsx`. Preserve other edits.

1. Empty-state text (line ~22): "Select a step to see what the agent decided, which tools it
   ran and what it cost." becomes "Select a step to see what the agent decided and which tools
   it ran."
2. Agent calls table: remove the `<th scope="col">Cost</th>` header and the
   `<td>{formatUsd(call.costUsd)}</td>` cell. Other columns unchanged.
3. Remove the `<p>Agent cost for this attempt: ...</p>` line.
4. Remove `formatUsd` from the `./model.ts` import (it stays exported and used by
   `RunHeader.tsx`).

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

## Acceptance receipt (2026-10-09)

Implementer and main: npm test 91/91, typecheck 0, build 0. Diff matches steps 1-4. Frontend
only, so `make check` evidence from chunk 53 stands.
