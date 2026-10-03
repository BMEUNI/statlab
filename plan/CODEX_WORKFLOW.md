# Codex workflow audit and setup — 2026-10-03

Human reference; not startup reading. Execution/validation policy lives only in [AGENTS.md](../AGENTS.md).

## Diagnosis

The planner/implementer split is reasonable. This session shows recovery overhead, large context,
review churn and expensive evidence preparation. It does not isolate GPT versus Claude performance,
and cannot establish that changing CLI alone makes equivalent work faster.

Reviewed supplied [session export](../codex-session-01a0fd72-6a3d-7532-b1af-f1ed031e5ad2.md), its matching
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

### Changes applied

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
- Project uses Sol/medium for main, Luna/medium for routine implementer, and Sol/medium
  `implementer_strong` for complex work or failed corrections. Worker escalation is explicit.
- Removed forced context capacity; automatic compaction now starts at 120,000 tokens. This is a
  starting experiment, not an official optimal value. If summaries lose relevant state, improve
  concise handoffs or adjust based on measured batches; do not restore 850k automatically.
- Disabled project IDEA MCP (Java IDE), automatic Graphify/Cavecrew skills and Superpowers plugin.
  They add irrelevant tools or competing workflow directions here. PyCharm, RTK and other global
  tools remain available. No measured latency attribution to any disabled integration is claimed.

### Starting the next batch

Open a fresh CLI session from repository root, after current workers finish:

```sh
codex
```

Use `/status` to inspect current session and `/model` to select model/reasoning when needed. A resumed
thread or desktop selection can retain prior settings; edited files are not proof a running thread
changed. Start fresh for these defaults. Check `/agent` for the worker's actual selected model.

Suggested prompt:

> Continue B7 from plan/WHATS_NEXT.md. Follow root AGENTS. Specify chunk 22, then delegate implementation
> to implementer_strong with fork_turns="none" and bounded inputs. Reuse accepted B6 evidence. Complete
> dependency-ready B7 stages, consolidated review and one final acceptance gate. No commit or push.

Routine narrow task: Luna/medium worker. Complex engine, restart, cancellation or audit boundary:
Sol/medium worker from the start. Reserve high reasoning for a named design or review uncertainty:

```sh
codex -m gpt-6.1-sol -c 'model_reasoning_effort="high"'
```

The CLI override outranks project defaults. Avoid setting all workers to a weak model just because
per-token price is lower; correction time matters. Conversely, high/max reasoning everywhere is not
required to preserve quality. Compare outcomes on similarly scoped tasks.

### Optional CLI choices

- `/fast` trades higher usage for lower latency on supported models/accounts. Not enabled here.
  Current docs: included limits consumed at 2.5x Standard; purchased credits/PAYG at 2x. These are
  billing multipliers, not promised task speedups. See [Speed](https://learn.chatgpt.com/docs/agent-configuration/speed).
- If runtime repeatedly fails to load workspace requirements, finish/stop affected workers, run
  `codex doctor --summary`, then try a fresh `codex --no-daemon` session to isolate shared-daemon state.
  This is diagnosis, not a proven fix. Do not keep resubmitting product work into a broken runtime.
- Keep one writer for dependent stages. More concurrency does not shorten a serial dependency chain.
  Use separate agents for independent read-only audits only when useful and explicitly authorized.
- Keep useful integrations; disable others per project. No need to uninstall global plugins or loosen
  approval/sandbox/network policy for speed. RTK reduces output volume; it does not accelerate inference.
- Current CLI 0.160.0 uses separate profile files (`~/.codex/<name>.config.toml`). Old `[profiles.name]`
  recipes are obsolete since 0.134.0. Project settings override profile settings; use `-c` for a deliberate
  one-off override here. See [Advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

### Measure next two comparable batches

Record start/end and interruptions separately; first focused green time; correction rounds; final
acceptance duration; parent/worker model+effort and final input size. Success means less elapsed
active work while all acceptance requirements pass, not fewer tests or a smaller token bill alone.
If Luna needs repeated corrections, compare the next comparable task with Sol as implementer.
No six-hour-to-X-minute promise follows from this one interrupted session.

## Validation and reversibility

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
