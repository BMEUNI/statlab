# Codex workflow setup and historical audit

Human reference; not startup reading. Execution/validation policy lives only in [AGENTS.md](../AGENTS.md).

## Current role policy — 2026-10-05

Main is the sole planner/reviewer. Main investigates existing code read-only, resolves every design
decision and writes a detailed implementation-ready stage spec before delegation. The spec names
exact files/symbols, signatures/types/defaults, existing helpers, per-file algorithms and branches,
ordering/state/concurrency/error behavior, concrete edge cases, test setup/assertions, commands and
stop conditions. Follow [the required plan checklist](../AGENTS.md#required-plan-before-delegation).
Main writes no application, test, generated or probe code, including corrections or restoration.
Workflow policy, agent settings, plans and acceptance receipts are the permitted main edits.

Only the `implementer` role may be spawned, with `fork_turns="none"`: `gpt-6-luna`, low reasoning,
one writer. The implementer transcribes the resolved recipe and runs its specified permitted checks.
It makes no design decisions or independent failure fixes. Gaps/failures return to main; main updates
the plan before the implementer changes behavior. Complexity and failed corrections do not authorize
Sol escalation. The project `implementer_strong` profile is removed. No nested subagents; the
implementer profile sets `[agents].enabled = false`. Main owns review and final acceptance.

Project [settings](../.codex/config.toml) retain Sol/high for main, set Luna/low worker defaults and
cap spawned threads at one. The [implementer profile](../.codex/agents/implementer.toml) also pins
Luna/low. Low reasoning is a model setting, not a guarantee of zero internal reasoning; the policy
removes independent design authority. Official [OpenAI Docs on subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describe custom role configuration layers and model/effort defaults. Do not assume a running session
has reloaded edited files; inspect its available role model/effort and start fresh if mismatched.

The audit below records earlier evidence. Its historical model choices are superseded by this policy;
accepted product receipts and engineering/validation requirements remain unchanged.

## Historical diagnosis — 2026-10-03

The planner/implementer split is reasonable. This session shows recovery overhead, large context,
review churn and expensive evidence preparation. It does not isolate GPT versus Claude performance,
and cannot establish that changing CLI alone makes equivalent work faster.

Reviewed supplied session export `codex-session-01a0fd72-6a3d-7532-b1af-f1ed031e5ad2.md`, its matching
local rollout and three child rollouts; root AGENTS, all 27 existing plan Markdown files by procedural
inventory/search and relevant sections; project/global Codex settings, agent definitions, hook config,
Makefile and pyproject validation settings. This is a workflow audit, not a fresh product-code audit.
Global settings and credentials were not changed. The optional `caveman learn` executable is absent;
no Cave Score, money savings or analyzer-derived claims are available.

### Measured evidence

| Observation | Evidence and interpretation |
|---|---|
| Final full coverage suite: 72.08 seconds | 2,189 passed, one designed skip. Test execution alone cannot explain multi-hour batch. Full gate already ran once; do not claim repeated full suites caused this session. |
| LLM first implementation/handoff: about 23 minutes | Spawn 06:04:44 UTC; handoff 06:27:58. Finished batch response 07:40:47: about 73 more minutes for review, corrections, proofs, acceptance and status. These intervals include coordination, not pure inference. |
| Two runtime failures | Parent turns ended with `failed to load workspace requirements`; recovery worker also failed. A replacement continued unfinished evidence. This is runtime evidence, not proof of slow model inference. |
| Long unobserved interval | Parent log has no events from Oct 2 17:51:55 to Oct 3 05:57:54 UTC (12h 05m 59s). Sleep, suspension, approval or runtime cause is not established. Four reported parent turn durations total 3.70h, unlike wall-clock span. Do not add overlapping child durations. |
| High reasoning throughout | Parent used `gpt-6.1-sol`/high; workers mostly Luna/high. Parent totals: 95,758 output tokens, including 72,029 reasoning tokens. Lower routine effort is a trial, not guaranteed equal quality. |
| Growing context | Parent final request: 274,759 input tokens; LLM worker final request: 229,300. Project context override 1,050,000; compaction threshold 850,000. First recovery worker used full-history fork; later workers already used none. |
| Reused input dominates | Parent cumulative input 24,892,439, of which 24,382,976 cached (~98%). These are repeated request inputs, not 24.9M unique tokens or a dollar bill. Cache reuse does not remove review/tool round trips. |
| Coordination overhead | Parent invoked `wait_agent` 36 times and `list_agents` 28 times, plus 20 messages and four followups. Some were necessary; counts alone do not measure wasted seconds. |
| Review reversed its own request | Export around lines 13,100–13,600: extra SDK response guards requested, then removed after checking SDK validation. Required reachable-failure evidence now precedes guard requests. |
| 24 required mutation proofs | 15 MCP + nine LLM. Guard-selector corrections, restoration receipts and expired replay artifacts required additional work. Preserve accepted B6 proof; future specs select high-risk mutations deliberately. |

Raw timing source: local `~/.codex/sessions/2026/10/02/rollout-2026-10-02T18-28-49-01a0fd72-6a3d-7532-b1af-f1ed031e5ad2.jsonl`
and child rollouts identified by parent thread ID. No comparable Kiro session was provided.

### Changes applied on 2026-10-03 (historical)

- Shortened root AGENTS and made full-history worker forks opt-in rather than default.
- Live state moved from 93,354 to 3,359 UTF-8 bytes. Previous continuation text retained verbatim
  after an archive header in [WORKFLOW_HISTORY.md](./WORKFLOW_HISTORY.md).
- Startup documents (AGENTS + live state + index) shrank from 147,301 to 34,839 bytes: 76.3% less.
  This is a file-size reduction, not a measured runtime/token-cost improvement.
- Moved verified dependency facts intact from index to [ENVIRONMENT.md](./ENVIRONMENT.md).
  Facts remain available without mandatory full-file startup reads.
- Consolidated review; dependent stage specs follow prerequisite handoffs. Fixed contradictory
  full-suite-per-milestone wording in PLAN; corrected stale 86-tool / 31-decision summaries.
- Preserved main/implementer separation, security, lineage, strict typing, 100% coverage and one
  final gate. Added durable receipt policy so temporary log expiry alone cannot restart acceptance.
- The audit originally set Sol/medium for main, Luna/medium for routine implementation and
  Sol/medium `implementer_strong` for complex work or failed corrections. This model/escalation
  policy is superseded by the 2026-10-05 role policy above.
- Removed forced context capacity; automatic compaction now starts at 120,000 tokens. This is a
  starting experiment, not an official optimal value. If summaries lose relevant state, improve
  concise handoffs or adjust based on measured batches; do not restore 850k automatically.
- Disabled project IDEA MCP (Java IDE), automatic Graphify/Cavecrew skills and Superpowers plugin.
  They add irrelevant tools or competing workflow directions here. PyCharm, RTK and other global
  tools remain available. No measured latency attribution to any disabled integration is claimed.

## Starting the next batch

Open a fresh CLI session from repository root, after current workers finish:

```sh
codex
```

Use `/status` to inspect current session and `/model` to select model/reasoning when needed. A resumed
thread or desktop selection can retain prior settings; edited files are not proof a running thread
changed. Start fresh for these defaults. Check `/agent` for the worker's actual selected model.

Suggested prompt:

> Continue B13 from plan/WHATS_NEXT.md. Main plans and reviews only; write no code. Read chunk33's
> dependencies and existing seams, then write its detailed resolved per-file recipe under root AGENTS.
> Delegate only to implementer with fork_turns="none", Luna/low. Return missing decisions/failures to
> main for explicit correction plans. After33's terminal handoff, specify34; keep stages sequential.
> Preserve accepted evidence and the current user stop on runtime tests. Follow current commit authority;
> never push.

All implementation, including complex engine/restart/cancellation boundaries, uses Luna/low.
Main does the complex reasoning before delegation. When a plan fails, main diagnoses the failure
read-only and supplies a smaller or clearer correction recipe. Never raise worker model/effort or
take over code writing. A CLI/session override must not silently bypass this project role policy.

## Optional CLI choices

- `/fast` trades higher usage for lower latency on supported models/accounts. Not enabled here.
  Current docs: included limits consumed at 2.5x Standard; purchased credits/PAYG at 2x. These are
  billing multipliers, not promised task speedups. See [Speed](https://learn.chatgpt.com/docs/agent-configuration/speed).
- If runtime repeatedly fails to load workspace requirements, finish/stop affected workers, run
  `codex doctor --summary`, then try a fresh `codex --no-daemon` session to isolate shared-daemon state.
  This is diagnosis, not a proven fix. Do not keep resubmitting product work into a broken runtime.
- Keep one implementer for dependent stages. Main performs investigation and reviews itself;
  additional independent implementers require explicit user authorization and non-overlapping ownership.
- Keep useful integrations; disable others per project. No need to uninstall global plugins or loosen
  approval/sandbox/network policy for speed. RTK reduces output volume; it does not accelerate inference.
- Current CLI 0.160.0 uses separate profile files (`~/.codex/<name>.config.toml`). Old `[profiles.name]`
  recipes are obsolete since 0.134.0. Project settings override profile settings; use `-c` for a deliberate
  one-off override here. See [Advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

## Measure next two comparable batches

Record start/end and interruptions separately; first focused green time; correction rounds; final
acceptance duration; parent/worker model+effort and final input size. Success means less elapsed
active work while all acceptance requirements pass, not fewer tests or a smaller token bill alone.
If Luna needs repeated corrections, inspect which plan details were missing and improve the next recipe.
No six-hour-to-X-minute promise follows from this one interrupted session.

## Historical validation and reversibility — 2026-10-03

Only workflow/planning/Codex files changed. No product gates rerun: B6 source/test/runtime-test config
remain untouched. Worker TOML/settings assertions exited 0. Main inspected config diff and doctor
receipt: `config.load` is OK (reported load 4 ms; active thread overrides not inspected).
`codex doctor --json` exited 1 due provider network reachability, with WebSocket/MCP/desktop/runtime
inventory/update warnings. It is not an all-green doctor result; current sandbox diagnostics cannot
explain the historical workspace-requirements failure. Full diagnostic:
`/private/tmp/tune-codex-config-doctor-final.json`.

Main verified archive/environment content preservation, all relative file links in changed/new
Markdown, one live Next heading and `git diff --check` (exit 0). Changes are uncommitted.

Tracked originals remain at `de2aa83`; original root AGENTS/index/live-state copies also at
`/private/tmp/codex-workflow-audit-20261003/`. Archive preserves previous live file durably. To undo,
review/revert only this task's exact files, preserving unrelated changes. For individual Codex choices,
edit `.codex/config.toml`; re-enable a named integration or remove its project override. Agent role
files independently control worker models/effort. Future sessions load updated files.

Official references checked 2026-10-03: [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic),
[Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), and
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
