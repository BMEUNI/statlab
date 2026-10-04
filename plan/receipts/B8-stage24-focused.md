# B8 chunk24 focused handoff

Candidate base: `1e9fe6a03367b4f7ff47792e515e9de70139590a`. Working-tree source/test candidate, uncommitted.
Tracked owned-file diff SHA256: `ea7e3b596491a447069bdaafa58065ddc95fe53ccb5e4c90fccff1e50e67799b`.
File hashes below include the three new files excluded from git diff.
Main's new `plan/24_candidate_fanout.md` preserved, not changed by worker.

## Implemented

Canonical frozen branch tuples, B7 singular JSON migration/read view, independent candidate
progress, domain-supplied traversal, overlap prerequisite snapshot, serialized latest-state
merge/checkpoint/event/prune, sibling failure/gate isolation, typed new-wave proposals and
retune hooks with CandidateSet reuse and caps. Candidate SQL wave/status persisted.
ToolBoundary delegates unchanged B6 dispatch; pause/cancel drains admitted calls and stops
later calls/internal repair retries. FitBudget charges discovered heavy audited durations,
rehydrates SQL totals, refuses later admissions and records honest in-flight overshoot.
Restart repairs every branch's committed/halted boundaries, candidate status, shared overlap
and events; only unfinished work receives fresh attempts.

Unchanged: B6 client/server/contracts, migrations/configuration, checkpointer source,
persistence models. Accepted B6/B7 evidence reused; no baseline, coverage/global gate,
mutation quota, commit or push.

## Focused evidence

Final pytest command (900-second subprocess timeout, no coverage):

```sh
.venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' .venv/bin/python -m pytest -q --no-cov -o addopts='--strict-markers --strict-config' tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_graph_checkpoint.py tests/test_graph_engine.py tests/test_graph_engine_tools.py tests/test_restart_reconciliation.py tests/test_repository.py::test_candidate_wave_identity_and_status
```

Exit 0: **120 passed in 21.44s**. Full output `/private/tmp/b8-stage24-focused9.log`.
Local MCP socket fixture required approved escalated execution.

Scoped commands:

```sh
.venv/bin/python -m mypy backend/statlab/graph backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
.venv/bin/python -m ruff check backend/statlab/graph backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
.venv/bin/python -m ruff format --check backend/statlab/graph backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
git diff --check
```

All exit 0. Mypy: 12 files, no issues. Ruff: all checks passed. Format: 12 already formatted.
Full outputs: `/private/tmp/b8-stage24-mypy9.log`, `/private/tmp/b8-stage24-ruff9.log`,
`/private/tmp/b8-stage24-format9.log`. Diff check emitted no output.

Earlier correction evidence: focused7 exit 1, 117 passed/3 failed (repair fixture unknown
spine node `alpha`, unchanged B6 lineage correctly refused it); fixture changed to `intake`.
Targeted `make test-fast TESTS='tests/test_candidate_fanout_tools.py::test_internal_repair_admission_guard'`
exit 0 (`/private/tmp/b8-stage24-repair8.log`). Final focused9 supersedes it.

## Requirements proved

- Events/barriers show c_02 reaches its next node while c_01 and overlap are blocked;
  checkpoint preserves sibling results and shared tail waits for join.
- Failed candidate isolates siblings; awaiting candidate produces dormant join;
  all failed candidates fail run. Two waves/four candidates: split and overlap once,
  design twice, prepare four times, train/evaluate eight times, shared comparison once.
- Initial cap/topology/baseline failures have no side effects; cross-wave duplicate/cap
  guards refuse new candidate insertions. Retune cap yields append-only attempts.
- Natural asyncio cancellation retains identity. Recovery replays only unfinished c_01 fit
  plus overlap/tail, fails two interrupted attempts with process-terminated evidence.
  Checkpoint-ahead repair restores all five shared/candidate outcomes and terminal events,
  candidate SQL status, then second reconciliation changes nothing.
- Explicit pause/cancel drains one entered tool call, refuses second; internal one-repair
  retry also stopped by pause, cancel or exhausted budget. Interrupted attempts remain
  unpruned/running until ordinary restart reconciliation.
- Two fits admitted together overshoot one-second budget: total audit >=2200 ms,
  overshoot >=1200 ms; third fit never dispatched, halted boundary persisted;
  fresh budget helper reads exactly the same audit and refuses admission.
- Three concurrent runs/nine candidate tasks retain run-qualified IDs and complete
  without SQLite lock errors.
- Real entered B6 MCP client trains logistic/rf/hist three candidates on byte-identical
  train/valid/test refs and contents. Early test fitting is refused before session dispatch.
  One shared inference fit runs while all three candidates are active. Three validation
  scores feed comparison/ranking; select_champion precedes one test score, second refused;
  test metric model_id equals runs.champion_model_id and test claim is durable.

## Next-stage hazards / remaining scope

No focused failures remaining. Main review and one final stable-batch acceptance remain.
Domain executor must use RunState.branches/NodeInvocation.candidate and supply TraversalPlan,
planner/retune callbacks as needed; graph has no domain-specific node names.
Construct ToolBoundary with the entered/discovered B6 client and the loop's control/budget
before run/recovery so heavy-tool accounting can rehydrate correctly. Explicit API controls,
resume overrides/gate resolution and production domain wiring remain their later stages.
Budget is admission: active estimator/server threads are not preempted.

## Exact file SHA256 manifest

```text
6bf57deff4b9236cc484752652dbb7c3c7d2c6d5699586a940955580c102e4bc  backend/statlab/graph/__init__.py
436b8b0276ed4d8ea708798b3a13934d283e291b208fe12272669da3cd5596f7  backend/statlab/graph/candidates.py
582fe6a8824a1b8777aa80ea149971c0c838513db65e476c19298de47587d0e5  backend/statlab/graph/engine.py
8e48c981518352296003df875e35f8f13b9b287285690997c9f04c8690dd8c3d  backend/statlab/graph/reconciliation.py
d8409bae1b00f4bea086ef5d9e72734db2ee9c4db8c78d7bb3945fcbdb68d726  backend/statlab/graph/state.py
03cf4b2c97bfdb21d2ef05f2dde834c6dcdf1aabdd9494503df4a790ccf2c935  backend/statlab/persistence/repository.py
cb775a7ea08b974184c7e09b9d742f17ef5c44bad4c682f81638d3e6a6425ceb  tests/test_candidate_fanout.py
fe57c62311dc0e3109f49b016f47eaf7a80fa6e6d52a1d8d1bed22bf5082d3e8  tests/test_candidate_fanout_tools.py
04ac04c82e72fad31835dcfd2fbe0a64839fb54ce134ab19737d8a996561695a  tests/test_graph_state.py
eaaccf54ab5047de2a780bee43317873c4bd59ac77ea37c8a8bc477f4bfefe29  tests/test_repository.py
0855ae0cc59ce1b2dd064eb7e992017e2901903b2379366a396907f28df62007  tests/test_restart_reconciliation.py
```


## Consolidated review corrections — 2026-10-04

Scope: engine.py, reconciliation.py, test_candidate_fanout.py,
test_restart_reconciliation.py only. Preserved stage26 and other dirty work.

1. Fan-out boundary dispatch updates SQL runs.current_node to the dispatched spec name
   under the existing coordinator lock. Display remains serialized last-started projection;
   independent branch checkpoint progress remains recovery authority.
2. Awaiting recovery includes the shared RunState.pending_result together with actual
   awaiting branch pending boundaries. Existing SQL gate filtering still republishes only
   unanswered pending gates belonging to those executions.

Public-loop regression blocks a later prefix node, candidate training node, and shared tail,
then reads SQL current_node while still running. Real fan-out produces valid awaiting shared
prefix, overlap, and tail checkpoints; an overlap case also awaits a candidate. Recovery
remains dormant and republishes both shared/candidate pending gates, excluding answered and
closed gates. Checkpointer round-trip validates these states without bypassing validators.

Regression-before-fix command:

```sh
make test-fast TESTS='tests/test_candidate_fanout.py::test_running_current_node_tracks_latest_dispatch tests/test_restart_reconciliation.py::test_fanout_shared_awaiting_republishes_unanswered_gates'
```

Genuine make exit 2 (pytest exit 1 shown by make Error 1): seven expected assertion failures,
three stale-current-node projections and four omitted-shared-gate projections. Full output:
`/private/tmp/b8-stage24-review-red.log`. No import failure or timeout.

Post-fix command:

```sh
make test-fast TESTS='tests/test_candidate_fanout.py tests/test_restart_reconciliation.py'
```

Exit 0: 61 successful test cases, including all seven new regressions. Uses the Makefile's
900-second Python subprocess timeout, no coverage. Configured double quiet mode suppresses
textual final summary; complete output contains 61 success markers and 100% completion.
Full output: `/private/tmp/b8-stage24-review-green.log`.

Scoped static commands:

```sh
.venv/bin/python -m mypy backend/statlab/graph/engine.py backend/statlab/graph/reconciliation.py tests/test_candidate_fanout.py tests/test_restart_reconciliation.py
.venv/bin/python -m ruff check backend/statlab/graph/engine.py backend/statlab/graph/reconciliation.py tests/test_candidate_fanout.py tests/test_restart_reconciliation.py
.venv/bin/python -m ruff format --check backend/statlab/graph/engine.py backend/statlab/graph/reconciliation.py tests/test_candidate_fanout.py tests/test_restart_reconciliation.py
git diff --check
```

All exit 0. Mypy: no issues in four source files; Ruff all checks passed; four files already
formatted; diff check silent. Full outputs: `/private/tmp/b8-stage24-review-mypy.log`,
`/private/tmp/b8-stage24-review-ruff.log`, `/private/tmp/b8-stage24-review-format.log`.
Original 120-case receipt remains reusable for unchanged properties. No global acceptance,
coverage, architecture changes, commit, push or remaining focused failure. Next-stage wiring
hazards above remain unchanged.

Current review-owned file hashes (supersede those four entries in the earlier manifest):

```text
10a9cd580e094526f298f00b67963dd281a8b06e0dc6e98356aef99c41514732  backend/statlab/graph/engine.py
cb3363a535ab9bd892748651c76f2cffc84dfd9e431a540ac531a78f7e615c06  backend/statlab/graph/reconciliation.py
4c738fec0167f02d21ed0ba9cabd2d20bd831b6c73cd80628b86ccf3977c91a4  tests/test_candidate_fanout.py
b15a2662508a06aa23bc0dc77a648c7083bba635f12d9e3cc2b684c27dd7cc9d  tests/test_restart_reconciliation.py
```


## Acceptance-path correction handoff — 2026-10-04

Candidate remains HEAD `1e9fe6a` plus dirty B8 edits; preserved stage26 work. This
correction changes graph/candidates.py, engine.py, state.py and the five test files
listed in the manifest below. Reconciliation/repository source hashes are retained
for the exercised boundaries; their source was unchanged in this correction.

Removed unreachable initialization/result/repair-callback guards using the actual
fan-out initialization and B6 repair contract. Removed the redundant pre-dispatch
lifecycle-flag branch: retained RunControl pause/cancel flags already stop admission.
No private-access tests, validator bypasses, casts, type ignores, coverage suppression,
configuration/dependency edits or worker coverage runs were introduced.

Reachable wave-2 correction: proposals are already checkpointed before repeated design
executes. A failed/awaiting repeated design must retain the branch barrier, avoiding
shared-prefix/overlap replay and replanning. RunState now permits only the declared
unfinished current-wave design at that barrier, with new-wave branches still pending
at their start and exactly the prior design history. Public-loop failed/awaiting tests
with/without overlap round-trip through Checkpointer, retain actual SQL ownership and
attempt 2, and reconcile without dispatch. Awaiting design republishes its actual gate.
Unrelated pending nodes, finished design, missing current-wave branches, started branches
and removed design declarations are refused through strict persisted-JSON validation.

Added proof for malformed traversal pairs/region/names/design/training order, persisted
branch IDs/history/wave/position/halt/join topology, real legacy null/missing-position
JSON, public declaration mismatch/missing callback nodes/no candidates, safe domain and
unexpected executor failures, valid skipped result on a non-skippable node, pre-dispatch
pause, B7 no-candidate fan-out, declined/paused retune, empty/paused planner, budget spent
by an in-flight sibling while retune awaits, budget spent while planner awaits, shared
heavy admission refusal, successful heavy/nonheavy internal repair, branch-only awaiting
gates with no shared pending boundary, and real closed/unmigrated SQL errors for budget
reads and candidate status writes. Existing drain/cancellation/lineage/split/champion and
wave/retune cap proofs remain in the focused group.

Final command (Makefile retains the 900-second Python subprocess timeout; no coverage):

```sh
make test-fast TESTS="tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_graph_checkpoint.py tests/test_graph_engine.py tests/test_graph_engine_tools.py tests/test_restart_reconciliation.py tests/test_repository.py::test_candidate_wave_identity_and_status tests/test_repository.py::test_candidate_status_sql_failure -o addopts='--strict-markers --strict-config'"
```

Genuine make/pytest exit 0: **177 passed in 21.04s**. Full output:
`/private/tmp/b8-stage24-paths-final-focused.log`. The explicit addopts override removes
only configured quiet output, preserving strict markers/config so the textual summary
is available. Local MCP fixture ran with sandbox escalation after the initial focused
attempt's localhost bind denial.

Intermediate facts: `/private/tmp/b8-stage24-paths-focused1.log` make exit 2/pytest exit 1,
15 fixture/assertion failures (short CandidateSpec rationale and missing skipped reason)
plus one sandbox localhost-bind setup error; corrected without production changes.
`/private/tmp/b8-stage24-paths-focused2.log` make/pytest exit 0, 125 passed in 17.33s.
`/private/tmp/b8-stage24-paths-halts.log` exit 0, 4 passed in 1.14s after adding strict
corruption refusals. Initial mypy exit 1 was one fixed inferred fixed-length tuple
annotation (`/private/tmp/b8-stage24-paths-mypy1.log`); next scoped run exited 0.

Final statics scope is exactly the ten files in the manifest:

```sh
.venv/bin/python -m mypy backend/statlab/graph/candidates.py backend/statlab/graph/engine.py backend/statlab/graph/state.py backend/statlab/graph/reconciliation.py backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
.venv/bin/python -m ruff check backend/statlab/graph/candidates.py backend/statlab/graph/engine.py backend/statlab/graph/state.py backend/statlab/graph/reconciliation.py backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
.venv/bin/python -m ruff format --check backend/statlab/graph/candidates.py backend/statlab/graph/engine.py backend/statlab/graph/state.py backend/statlab/graph/reconciliation.py backend/statlab/persistence/repository.py tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_restart_reconciliation.py tests/test_repository.py
git diff --check
```

All genuine exits 0. Mypy no issues in 10 source files; Ruff all checks passed;
10 files already formatted; diff check silent. Full outputs:
`/private/tmp/b8-stage24-paths-final-mypy.log`,
`/private/tmp/b8-stage24-paths-final-ruff.log`,
`/private/tmp/b8-stage24-paths-final-format.log`.

No remaining focused failure. Main owns review and global acceptance; this receipt
makes no coverage claim. Existing next-stage hazards remain: domain callbacks must
use the explicit ToolBoundary seam, API resume/gate lifecycle wiring is later work,
and audited heavy budget is admission with honest in-flight overshoot, never thread
preemption. No commit or push.

Current SHA-256 manifest (supersedes matching prior entries):

```text
2a70821d37b38a10bdd01ee98607edd067ba9039e1ca7545127131a97b66f44d  backend/statlab/graph/candidates.py
9dd8512fc065e6b69ce510ae605a84e23292d418bb6d244e841bd3a23ac47823  backend/statlab/graph/engine.py
eae7ab6ecc8d1688eca1fd6c0ed647ac9e1558c2627948548f0a3e3694a70b5a  backend/statlab/graph/state.py
cb3363a535ab9bd892748651c76f2cffc84dfd9e431a540ac531a78f7e615c06  backend/statlab/graph/reconciliation.py
8f12ec870ff01dde7721dae6a327a591ab579ddef25b3ae47bfbcd8927e86648  backend/statlab/persistence/repository.py
6e91b1d1e58b01abf7edbe84f1c5d6625d6315ca390105586b482e130c33dfc9  tests/test_candidate_fanout.py
3e3dd8aeb7986f9f2d959c798a1a2953283378ace10a308fbe4b53cbe9f01ad7  tests/test_candidate_fanout_tools.py
31a616bd42024ac7ed88e7b70fa612cc7d4bafee3071974dbe92a4d2c6fdca75  tests/test_graph_state.py
fde219c21daabceb2b9e3dd4d572209f30d7f324fbae5dfc8c47d7e1c9008e5c  tests/test_restart_reconciliation.py
4dafc502bbf28c0cc6e5423dba7826021632a1e49ff14bc96a0dc4a2f0fec5ee  tests/test_repository.py
```
