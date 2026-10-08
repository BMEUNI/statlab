---
status: completed
dependencies: [48]
completion_date: 2026-10-08
implementation_notes: "later experiment designs titled Search wave N with a lead sentence; npm 90/90"
---

# Chunk 49 — the chat announces a search wave

User report 2026-10-08 (run `r_ca4d7d9637afd45e`): after the first four candidates were
evaluated, new models "randomly" started training. This is the D3/D26 search wave: the
engine re-enters `experiment_design`, which completes again with a larger
`candidate_set.candidates` (4 then 7). The chat showed it as a plain "Experiment design"
step whose lead sentence was the agent's "I'm confirming the same seven models...", so the
reason (in the rest of `decision.user_summary`) was hidden under More.

Decision: frontend only. Within one run's event feed, an `experiment_design` completion whose
candidate count is larger than the previous `experiment_design` completion's count is search
wave N (N starts at 1 and increments on each such increase). Rewinds fork a new run, so
repeated completions inside one run are waves. The step gets the title `Search wave N` and a
deterministic lead sentence before the agent's own summary. Non-goals: no backend change, no
timeline/progress change.

## Stage 49a

Owned: `frontend/src/run/model.ts`, `frontend/src/chat/RunChat.tsx`,
`frontend/tests/run-model.test.ts`. Preserve other edits.

1. `model.ts`, `FeedItem` step variant: add optional `title?: string`.
2. `model.ts`: new private function above `foldEvents`:
   `function candidateCount(summary: JsonObject): number | null` returns
   `summary.candidate_set.candidates.length` when `candidate_set` is a JSON object and
   `candidates` is an array; else `null`.
3. `foldEvents`: add locals `let designCount: number | null = null;` and `let wave = 1;`
   beside `completedSeq`. In the `node.completed`/`node.skipped`/`node.failed` branch, where
   the step item is built (the `if (candidate === null || ...)` block), compute
   `const summary = isJsonObject(result.summary) ? result.summary : {};` and reuse it for
   `narrate`. Then, only when `event.type === 'node.completed' && p.node === 'experiment_design'`:
   `const count = candidateCount(summary);` If `designCount !== null && count !== null &&
   count > designCount`: `wave += 1`, and push the step with
   `title: `Search wave ${wave}`` and
   `text: `After comparing the ${designCount} models so far, I'm trying ${count - designCount} more. ${narrated}``
   where `narrated` is the existing `narrate(...)` result. In every other case push the step
   exactly as now (no `title` key at all). After pushing, when `count !== null`, set
   `designCount = count`. All other event handling unchanged.
4. `RunChat.tsx` step case: `title: item.title ?? branchTitle(item.node, item.candidateId),`.
5. Test `foldEvents titles later experiment designs as search waves` in
   `run-model.test.ts` (use the existing `event` helper and reset `nextSeq = 0`). Events:
   - `node.completed` `{ node: 'experiment_design', execution_id: 'e1', result: { status: 'completed', summary: { decision: { user_summary: 'Picked four.' }, candidate_set: { candidates: [{}, {}, {}, {}] } } } }`
   - same with `execution_id: 'e2'`, `user_summary: 'Pushing boosting further.'`, 7 candidates
   - same with `execution_id: 'e3'`, `user_summary: 'Same seven.'`, 7 candidates
   Take `feed.items.filter((item) => item.kind === 'step')` and deep-equal against three
   step items: seq 1 `{ seq: 1, ts: 't1', kind: 'step', node: 'experiment_design', candidateId: null, status: 'completed', text: 'Picked four.' }`;
   seq 2 the same shape with `title: 'Search wave 2'` and
   `text: "After comparing the 4 models so far, I'm trying 3 more. Pushing boosting further."`;
   seq 3 with `text: 'Same seven.'` and no title.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: review, offline browser look if a wave is reachable, commit.

## Acceptance receipt (2026-10-08)

Implementer and main: `npm test` 90/90 pass, `npm run typecheck` 0, `npm run build` 0. Diff
matches the recipe. Real run `r_ca4d7d9637afd45e` events (seq 11: 4 candidates, seq 55: 7)
meet the wave rule. No backend change, so `make check` evidence from chunk 48 stands.
