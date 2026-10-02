# LLM-Driven Statistical Modelling Workbench — Plan

> Living document. Compiled into a hand-off prompt for a coding agent in §16.
> Status: **COMPLETE v1.8** — 36 decisions locked, all 21 questions resolved.
> v1.8 (2026-10-01) is a full review after chunks 01–09: test-set integrity extended to lineage
> and forks (D32), honest reporting of a comparison (D33), leak-safe fitting rules (D34),
> confirmatory vs exploratory inference (D35), lifecycle and ownership fixes (D36). Changes to
> code already written are listed in `REVISIONS_IMPLEMENTED.md`.

**Project name: `modelling-agent`.** This is the canonical name for the `pyproject.toml`
package, the repository root in illustrative layouts throughout this document, and any
future distribution artifact. Do not use `onlab` or any other placeholder — it appears
nowhere in the codebase from chunk 01 onward.

---

## 1. What we are building

A locally-run web application that is **a generic interface over MCP tool phases**. Its first
domain is tabular statistical modelling: a user hands over one CSV and, if they have one, a JSON
data contract describing it, and an LLM agent (DeepSeek by default, any provider) **designs and
executes a comparison of multiple approaches** — several models *and* several methodologies
(encodings, feature strategies, preprocessing variants) — then ranks them and picks a champion.

The user talks to an agent through a website. The agent does not answer with advice; it
runs the work in the background through real tools and comes back with real numbers,
real plots and real model files.

Three things make it more than a chat wrapper:

1. **A run is a comparison, not a path** (§4.2). Nodes 1–6 build a shared foundation, design a
   reasoned candidate shortlist and fit the inferential model; nodes 7–11 run once per candidate;
   node 12 ranks them. The split is shared and the test set is locked until a champion is chosen,
   so the comparison stays statistically honest.
2. **The pipeline is a declared graph with explicit state** (§4.3), so it can be paused, forked,
   retried and resumed — and a new domain (time series, clustering, survival) is a new `NodeSpec`
   list plus tools, touching no existing code.
3. **All computation happens in 86 small typed MCP tools**, so results are deterministic and
   reproducible, and the LLM never sees or invents the data.

### 1.1 Non-goals (v1)

- No multi-user tenancy, no cloud deployment, no login — single user, loopback only (D14).
- No deep learning, no NLP, no image data. **v1 ships one domain, `tabular_supervised`** — the
  domain seam (D27) exists and is tested, but time series / clustering / survival are later
  additions, not v1 scope.
- No arbitrary code execution by the agent (deferred, see D2).
- No vector database. Memory is small and structured (see §7).
- No distributed compute and no out-of-core processing. Single machine, in-memory pandas,
  200 MB ceiling, never sampled (D15).

---

## 2. Locked decisions (provisional — flip any on request)

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Custom graph engine** (~300 LOC) owns orchestration. **No agent framework** — see D18, which supersedes this row's original DSPy/LiteLLM wiring. | The 7 control nodes *are* a bespoke router; bolting them onto LangGraph's `interrupt()` model is more work than writing the loop. |
| **D2** | **MCP tools only.** No agent-authored code execution in v1. The "agent writes code" deliverable is a generated standalone `pipeline.py` assembled from the logged tool calls. | Parameterised tools are the only version that can be unit-tested. `execute_python` sandbox is a v2 feature behind a config flag. |
| **D3** | **Fixed 13-node spine, dynamic traversal** (11 originally; `experiment_design` added by D28, `inferential_analysis` by D31). The agent may skip a skippable node with a logged reason, may retune within a candidate, and may propose a second candidate wave. Graph *topology* never changes at runtime. | Gives the "agent plans it" behaviour while keeping rewind-to-node-N tractable. |
| **D4** | **SQLite for metadata + filesystem for blobs.** | `sqlite3` is stdlib; the UI needs real queries ("runs sorted by score"). Blobs (parquet, joblib, png) belong on disk. |
| **D5** | **Data contract is a Pydantic schema, serialised as JSON, and *optional*.** Exactly one CSV and at most one JSON config per run. When the config is absent or partial, the missing fields are drafted from the dataset profile with reasoning and confirmed by the user **before the run starts** (see D8); the accepted result is written back as a complete contract JSON. Uploaded and elicited contracts are the same validated object. | Resolves **Q2**. An optional config is the realistic path — users rarely have one prepared. Making elicitation emit a *file* means the next run reuses the answers instead of re-asking. |
| **D6** | **Task scope: binary + multiclass classification and regression, with a pluggable model registry** (logreg / linear+ridge / random forest / hist gradient boosting). Adding more models = one dict entry. | Champion selection needs a real contest. Registry keeps §12 open. |
| **D7** | **Dual track: prediction *and* classical inference.** `statsmodels` is a first-class dependency. Every run with an inference goal fits exactly one inferential model (node 6 is skipped, with a logged reason, when node 2 records none) (OLS / GLM / Logit) alongside the predictive candidates and reports coefficient tables, standard errors, p-values, confidence intervals, VIF and residual assumption tests. **Prediction still decides the champion; the inferential fit is the explanatory companion, not a competitor.** Fitted **once per run** in its own SHARED node 6 (D31), never per candidate. | Resolves **Q1 = b**. A "statistical modelling" deliverable is expected to produce a p-value, not only an AUC. Cost is contained: one dependency, one tool module, a branch in nodes 7/8/9. |
| **D8** | **Contract elicitation happens in the setup wizard, before the run exists.** Upload CSV → backend profiles → agent proposes a complete contract with reasoning → user edits and confirms → choose mode → run starts. A run therefore *always* begins with a complete, validated contract, and auto mode is genuinely non-interactive. **Minimum input rule: `target.column` must be set at run creation** (from the config, or one click in the wizard); every other field may be agent-inferred. | Resolves **Q2b = a**. Inferring `scaling` or `cv_folds` wrongly costs a slightly worse model; inferring the *target* wrongly costs a garbage run that still reports a healthy-looking metric. That one field earns its mandatory click. Guided mode can still revisit any contract field later through the change/rewind handler. |
| **D9** | **Persistent SSE stream down, REST up.** One long-lived `EventSource` per run carries all telemetry; user messages, gate answers and lifecycle commands go up as ordinary validated `POST`s. Every event has a monotonic `seq`, mirrored to `events.jsonl`, so `?since=<seq>` replays a run's full history on reconnect or in a newly opened tab. **Agent prose streams token-by-token; structured decisions do not** — a gate proposal appears complete and validated, never assembling field by field. | Resolves **Q4 = a**. The traffic is deeply asymmetric: the server emits every tool call, metric and plot while the client sends a handful of commands. SSE is built for that shape and gives reconnection, heartbeats and replay from the platform instead of hand-written WebSocket plumbing. A run can last twenty minutes; reconnecting into the middle of one without losing the transcript is a hard requirement, and `seq` + `events.jsonl` is what delivers it. |
| **D10** | **Every non-skipped node gates in guided mode** *(amended by D29: shared nodes gate; per-candidate nodes gate only on exceptions)*, but each gate carries an applicable default and an **"approve all remaining"** action that collapses the rest of the run into auto mode. Gates also accept **free-text instructions**, which the agent interprets into a revised proposal and re-gates. **Gates block indefinitely** — no auto-approve timeout — and survive a server restart, so a gate opened yesterday can be answered today. | Resolves **Q3 = c + iii**. "Approve all remaining" gives the escape hatch without inventing a third mode, and is precisely what a user wants after a rewind ("redo the split, then just run the rest"). Auto-approve-on-timeout is rejected because it is the one setting that can silently produce a report claiming human review that never happened. Free-text answers are what make this a conversation rather than a form. |
| **D11** | **Rewind forks.** Rewinding to node N creates a new run with `forked_from_run_id` set, state copied up to N and artifacts **hard-linked** (same inode — immutable, so sharing is safe and disk cost is near zero). **A forked run keeps the parent's artifact refs** (refs are globally unique; the registry is keyed by ref), so the child gets hard-linked files and no new registry rows, and lineage facts such as `derived_from_test` are inherited rather than copied. Listing a run's artifacts follows `forked_from_run_id`; deleting a run must refuse while a fork references its artifacts (review 2026-10-01). The original run stays frozen and complete. **One run = exactly one reproducible path**, by construction. The UI follows the user to the fork so it feels continuous, and the run list renders lineage (`r_07 ← forked from r_03 at node 4`). | Resolves **Q9 = b**. Node 13 claims reproducibility; a run rewound three times in place cannot honestly make that claim, because its record would describe a history its `pipeline.py` never took. Forking also converts rewind from a destructive edit into the run-comparison feature (§12.5) for free. Accepted cost: the run list becomes a tree. |
| **D12** | **One hand-written skill file, `skills/modelling.md`**, with a shared preamble and one `## <node_name>` section per staging node, each having a fixed set of required headings the loader validates at startup. **The `Tools` allow-list is enforced in `mcp_client` against the active node**, not merely stated in the prompt. Authored by the user (not agent-generated). **Build order: the coding agent scaffolds the file with all required headings and `TODO` placeholder heuristics; the real domain content is written by hand afterwards, once the nodes exist.** | Resolves **Q8 = a + i**. The value of a skill file is consistency *across* nodes, which is visible in one document and invisible across eleven. If the agent writes its own instructions the file stops being a control surface and becomes decoration — this is the part of the system that is the author's contribution rather than DeepSeek's. Code-level tool enforcement is ~10 lines and turns a prompt suggestion into a guarantee. |
| **D13** | **The MCP server is a standalone HTTP service** — its own process, its own port, implementing the MCP **Streamable HTTP** transport (the current spec transport; plain HTTP+SSE is deprecated). Any MCP client can connect to it, not just this backend. The backend reaches it via `MCP_SERVER_URL`. **Bound to `127.0.0.1` by default and gated by a bearer token**, because this service reads and writes the filesystem and executes model fitting — it must not be openly reachable. Artifact paths are config-driven (`STATLAB_DATA_DIR`), shared with the backend, never relative to CWD. | Resolves **Q12 = b**. A genuinely independent, network-addressable MCP server is an explicit project goal, and it means the tool layer is reusable and demonstrable outside this app. Accepted cost: a third process to run and a health check. |
| **D14** | **Single-user, loopback-only, no login.** The web app binds to `127.0.0.1` with no auth; the MCP server binds to `127.0.0.1` behind a bearer token from `.env`. No `user_id` / `owner_id` columns are added — a nullable column is a one-line migration if multi-user ever becomes real, and adding it now would be speculative. Remote demos go through an SSH tunnel, never by binding `0.0.0.0`. | Resolves **Q5 = a + i**. Multi-user is not a login screen, it is an authorization model threaded through every project and run query — a different project. Loopback-only makes the absence of auth a deliberate, defensible choice instead of an oversight, and the MCP token removes the one genuinely sharp edge: a service that writes files and burns CPU on request should not be callable by any local process that stumbles onto the port. |
| **D15** | **Small-data only, and honest about it.** Everything is in-memory pandas; **nothing is ever sampled.** Guards instead of sampling: upload rejected above **200 MB** with a clear message, node 1 warns above **200k rows** that fitting will be slow, `max_fit_seconds` defaults to **300**, and distribution plots cap at **50 categories** per chart. | Resolves **Q6 = a**. Sampling is a correctness footgun: the moment some numbers come from a sample and others do not, every downstream statistic needs provenance tracking to stay honest, and the D7 inference track cannot report p-values from a silently sampled frame. Refusing a file we cannot handle properly is less code *and* more honest than half-handling it. Upgrade path stays narrow — v2 adds a chunked reader plus a `sampled: true` flag in every tool summary — precisely because v1 never pretends. |
| **D16** | **Hybrid routing, cost displayed but never enforced.** `deepseek-flash` is the default; `deepseek-v4-pro` for the genuinely reasoning-shaped nodes (problem definition, **experiment design**, inferential analysis, interpretation & diagnostics, champion selection — the D28/D31 nodes postdate this decision and were added in review). All routing lives in **one dict in `llm/routing.py`** with a single `model_for(node)` accessor and a documented precedence chain, so changing any node's model is a one-line edit or an env var — no code restructuring. Token and cost totals are logged to `llm_calls` and streamed live to the UI; **no hard budget cap**. | Resolves **Q7 = b + i**. The nodes differ qualitatively: "propose an imputation strategy from this missingness table" is extraction-shaped work Flash does well, while "given five models' metrics, calibration and diagnostics, which ships and what are the caveats" is what Pro exists for — and it is the output a human reads most closely. A full run is cents either way, so a token cap protects nothing while adding a failure mode that aborts a nearly-finished run; the real runaway risk is *compute*, already bounded by `max_retune_loops` and `max_fit_seconds`. |
| **D17** | **Unbounded parallel runs.** Each run is its own asyncio task; any number may execute at once, across or within projects. Three guards make that safe: (1) a **global fit semaphore** in the MCP server sized to `os.cpu_count()` (default) — runs proceed in parallel through LLM, IO and profiling work and serialise *only* at CPU-bound fits, with `n_jobs=1` per estimator so nothing oversubscribes; (2) **SQLite in WAL mode** with `busy_timeout=5000`, so concurrent readers never block and writers queue instead of erroring; (3) every `node_executions` row records **`concurrency_at_start`**, so a slow timing can be attributed to contention rather than misread as a regression. **Rewind still halts its parent** (marks it `superseded`) — parallelism is for runs the user deliberately starts, not for branches they just abandoned. | Resolves **Q13 = c**. Parallel runs are genuinely useful (kick off an auto run, explore a guided one). The naive risk is not parallelism itself but N simultaneous `GridSearchCV` calls thrashing the cores and making every logged `duration_ms` meaningless; the semaphore removes that while preserving the concurrency the user asked for. WAL is one line and is the difference between "concurrent" and "intermittent `database is locked`". |
| **D18** | **Provider-agnostic LLM layer: LiteLLM + Pydantic. No agent framework.** Supersedes D1's DSPy wiring and resolves **Q15**'s library question. Every LLM interaction is a Pydantic output model whose `model_json_schema()` is handed to the provider's structured-output facility; LiteLLM normalises the call, the schema support, the retries and the cost maths across providers. **DeepSeek is the default, not a dependency** — swapping to Claude or GPT is a model-string change plus an API key, with no code edit. DSPy is dropped: its payoff is prompt *optimisers*, which are out of scope, and it provides no memory (memory is §7's SQLite tables — a misconception worth naming). LangChain stays dropped. | Resolves **Q15 = no mandate**. Without optimisers, DSPy is a verbose way to do what Pydantic + a JSON schema already does, and it obscures the actual prompt — bad for a project that must be explained. LiteLLM is the one dependency that pays for itself here: multi-provider routing, `supports_response_schema()` capability detection, and `completion_cost()` per-provider pricing are all things we would otherwise hand-roll and maintain. |
| **D19** | **Report is markdown + PNG files.** `report/report.md` referencing `report/plots/*.png`, rendered in the UI. No PDF toolchain, no embedded base64. HTML export from the same markdown is a later two-line addition if a single shareable file is ever wanted. | Resolves **Q10 = a**. Keeps the report a *source artifact* rather than a rendered output: `report.md`, `pipeline.py` and `manifest.json` are all text, all diffable, all inspectable with a text editor, and the plots are files the tools already produce. A PDF renderer (weasyprint/pandoc/LaTeX) is exactly the dependency that breaks on a new Python release and blocks a build for reasons unrelated to this project. |
| **D20** | **Plain processes + a Makefile. No Docker.** Targets: `dev` (starts backend, MCP server and Vite together, with a trap so one Ctrl-C kills all three), `test`, `lint`, `mcp`, `db-reset`. `make dev` **fails fast with a clear message** if `DEEPSEEK_API_KEY` or `STATLAB_MCP_TOKEN` is absent from `.env`, rather than starting and dying on the first LLM call. | Resolves **Q11 = a**. This is a codebase under active development whose tools fit models and write files, so fast restarts and a working debugger matter more than reproducible images. D14 already scoped it loopback-only and single-user — exactly the case where containerisation buys nothing while costing rebuilds, debugger attachment setup, and macOS bind-mount ownership problems on the `projects/` tree. ~20 lines of Makefile give the same interface compose would have. |
| **D21** | **English throughout** — UI chrome and agent prose alike. No `language` field, no i18n scaffolding. | Resolves **Q14 = a + i**. Every term the UI displays (`roc_auc`, `stratified`, p-value, VIF, heteroscedasticity) is English regardless, so a partially translated interface would read worse than an English one; and statistical prose is where models are most reliable in English. Hungarian free-text in the contract's `notes` still works — mixed-language *input* is fine, it is mixed-language output that gets clumsy. |
| **D22** | **Small single-purpose tools, MCP server built first.** ~86 tools, each doing one thing: stateless, immutable artifacts, `fit`/`apply` split for anything learned from data, compute separated from render, one tool per split strategy. Registered by a `@tool` decorator that owns validation, envelope, timing and semaphore. Milestones **M1–M7 complete and test the entire server before any agent code exists**; a 33-tool walking skeleton (§6.4) covering both tasks with three estimators each runs end to end at M5. | Requested granularity, with two consequences worth naming. First, `fit`/`apply` separation makes **train-only fitting auditable** — a leak would require a visible `fit_imputer` call on the test set, recorded in the tool log. Second, tools-before-agent means that when the agent later makes a poor choice, a bad result is unambiguously a bad *decision* rather than a broken tool; without this order every failure is ambiguous. Estimator and inferential-model coverage is set by D25. |
| **D23** | **Every tool has its own signature; the agent discovers it dynamically.** No shared parameter bag, no generic `params: dict`, no mutually-exclusive optionals. Each tool declares a Pydantic model → JSON Schema → served through MCP `tools/list`, so parameter *names, types, defaults, bounds, enums and descriptions* differ per tool and the agent reads them at runtime rather than from a hardcoded prompt list. Field descriptions and enums are **mandatory**, because the schema is the agent's only documentation. Resolves **Q16 = a**: differing parameter sets are a feature, not a problem. | Requested. It is also the reason per-strategy tools are correct: `split_temporal` needs `time_column`, `split_grouped` needs `group_column`, and a single `split_data(strategy=...)` would have to accept both as optionals — precisely the shape that lets a model pass temporal parameters to a stratified split and get silently wrong behaviour. With per-tool schemas, that call cannot be constructed. Validation then happens *before* dispatch, and a schema violation is repairable feedback rather than a runtime error. |
| **D24** | **Prune within-node intermediates on node success; keep node-boundary artifacts.** When a node completes successfully, the frames produced *inside* it are deleted and the frame handed to the next node is kept. **Never prune on a failed node** — those intermediates are exactly what diagnosis needs. `STATLAB_KEEP_INTERMEDIATES=true` disables pruning entirely for debugging a phase. | Resolves **Q17 = b**. Rewind operates at *node* granularity (D11 forks at a node boundary), so within-node intermediates have no role once the node succeeds — they are debugging aids, not state. Granular tools would otherwise write 20–30 frames per run and several GB from a 200 MB input, paying disk permanently for something useful only in the minutes after a failure. Pruning on success only means the trade costs nothing exactly when it would have hurt. |
| **D25** | **Walking skeleton covers both tasks with three estimators each.** Classification: `logistic_regression`, `random_forest_classifier`, `hist_gb_classifier`. Regression: `linear_regression`, `random_forest_regressor`, `hist_gb_regressor`. Inferential: `logit` (binary) and `ols` (continuous). Two split strategies — `split_stratified` for classification, `split_random` for regression. 33 tools at M5. **Chunk 20r note:** `ridge` was added as a second interpretable linear regressor and `mnlogit` as the multiclass inferential model (`fit_mnlogit`); `split_temporal` and `split_grouped` were deferred out of v1 (D-c) — they are ~10 lines each when needed, and node 4 escalates a temporal/grouped request as `data` rather than silently falling back to a random split. | Resolves **Q18**. Three contrasting estimators per task (linear / bagged / boosted) make `compare_models` and champion selection genuinely exercised rather than trivially satisfied, and test the scaling vs no-scaling distinction in D7's two-matrix design from the start. Covering both tasks in the skeleton forces every metric, plot and diagnostic tool to branch on task type from day one — more work at M5, but cheaper than retrofitting regression into evaluation, interpretation and reporting after they were written single-task. `split_temporal` and `split_grouped` are deferred because they matter only for time-ordered or repeated-entity data and are ~10 lines each when needed. |
| **D26** | **A run is a comparison, not a path.** Nodes 1–6 run once (shared foundation, candidate design, inferential fit); nodes 7–11 run **once per candidate**; node 12 ranks all candidates and picks a champion. A candidate is a fully specified approach — preprocessing + features + estimator + params — so "methodologies" are compared, not just models. `varies_from_baseline` records the single thing each candidate changed. Bounded by `max_candidates` (8) and `max_total_fit_seconds`, with up to `max_search_waves` (2) iterative waves. **Three honesty rules enforced in code: the split is shared across all candidates; the `test` artifact is access-controlled and unreadable until node 12 with a champion chosen; the champion is scored on test exactly once.** | The project's purpose is comparison, so a single-path pipeline would miss the point. The three rules are the substance: with N candidates ranked on a set that is also used to score them, a comparison app produces confident nonsense — repeatedly peeking at test is precisely how model selection becomes overfitting to test. Locking the artifact rather than instructing the agent is what makes it a guarantee. Notably **the MCP server needs no changes**: granular stateless tools (D22) are simply called N times with different params, so the candidate abstraction lives entirely in the orchestrator. |
| **D27** | **Domains are pluggable; the node spine is declared, not hardcoded.** A `Domain` bundles a contract model, a skill file, a tool-phase list and an ordered `NodeSpec` list (each marked `SHARED` or `PER_CANDIDATE`). The engine, control layer, gates, forking, API, SSE, UI and report renderer are all domain-agnostic. Adding time series, clustering or survival analysis means writing tools + a registry + a spine, touching **no existing code**. Enforced by a test that no node name appears outside `domains/`. **v1 ships exactly one domain** (`tabular_supervised`). | Requested: the app is an interface to MCP tool phases, not a tabular-ML program. The seam costs little now — a `NodeSpec` list instead of an `if/elif` chain — and is nearly impossible to retrofit later, because by then node names will be scattered through the engine, the UI and the report. Writing a *second* domain now is the part that is deliberately deferred: it would be designed against guesses about an interface nobody has used yet. |
| **D28** | **Node 5 `experiment_design`: a reasoned shortlist, not a grid.** The spine becomes **13 nodes** (experiment design at 5, inferential analysis at 6 per D31). The agent reads the profile, problem spec, available estimators and methodology options, and project memory, then proposes **3–5 candidates**, each carrying a rationale that cites a specific EDA finding. Hard requirements: a **mandatory cheap interpretable baseline**; every candidate must differ from every other in at least one recorded dimension (dedupe by spec hash); no more than two candidates from the same model family without an explicit justification. Highest-value gate in the run. Resolves **Q19 = a**. | This is where the LLM earns its place. AutoML brute-forces the cross-product — 10 models × 4 encodings — and buys nothing but compute. The value here is a *shortlist justified by what the data actually showed*: "positive rate is 3%, so try class weighting and prefer PR-AUC" is a reasoned choice a grid cannot make. The mandatory baseline exists because without a reference point there is no way to tell whether the complex candidates earned their complexity. Approving a candidate set before anything trains is also where a user's judgement matters most, which is why it deserves its own node and gate rather than being buried inside another node's decision. |
| **D29** | **Candidates execute in parallel; guided mode gates the shortlist, then only exceptions.** All candidates advance concurrently, serialising only at CPU-bound fits via D17's existing semaphore. In guided mode the **node-5 shortlist is the gate**; nodes 7–11 then run ungated *except* on exceptions — a candidate failing, a material tool warning, or a metric far outside the expected range. `approve_all_remaining` (D10) still lets the user opt into per-node gating for a specific candidate. **Amends D10**: "every node gates" was written when a run was one path; with 5 candidates × 5 per-candidate nodes it would mean 25+ stops. **Per-candidate wall-clock timings are not comparable** under parallel execution (candidates compete for semaphore permits); the report states this rather than implying fit costs can be ranked. | Resolves **Q20 = b + i, with iii**. The parallel machinery already exists, so this is faster for free. On gating: a gate nobody reads is worse than no gate — 25 sequential prompts trains a user to click approve reflexively and the audit trail becomes fiction. The shortlist is the decision that actually matters, and exception-gating preserves oversight exactly where something went wrong. Being explicit about timing incomparability matters because a comparison-focused report inviting a fit-cost ranking from contended timings would be quietly wrong. |
| **D31** | **The inferential fit is per-run, in its own SHARED node 6 `inferential_analysis`.** Spine becomes **13 nodes**. Node 6 builds its own canonical inference matrix — median/mode imputation, dummy coding with explicit reference levels, no scaling, no selection beyond collinearity removal — fits **one** model on train+valid, and produces the coefficient table, VIF, residual and influence diagnostics. It depends only on nodes 1–4, so the engine may run it concurrently with the candidate fan-out. Node 12 reports exactly **one** coefficient table. Resolves **Q21 = a**. | The audit found D7 ("one inferential fit") contradicting its placement in a PER_CANDIDATE node. The fix is not bookkeeping: an inferential model answers a question about *the data* ("does plan tier affect churn?"), not about a candidate's encoding choice, so it has no business being multiplied by candidates. The rejected option — N coefficient tables, one per candidate — is worse than untidy, it is misleading: a coefficient's magnitude and significance depend on what else is in the model, so tables from different feature sets are not comparable and presenting N of them invites exactly the wrong reading. One canonical specification, stated once, with its assumptions tested. |
| **D30** | **Specification hygiene, from an external audit of v1.5.** (a) §4 carries a **normative spine table** that supersedes all other numbering. (b) The test-set lock has a named owner: `mcp_client` + a `locked` registry flag, **not** the stateless server, and the guarantee is scoped honestly to this application. (c) `NodeSpec` / `NodeResult` / `ArtifactRef` / `CandidateSpec` and the success/**error** envelope with a 4-way `error_class` taxonomy are frozen in **M0.5**, before the three milestones that consume them. (d) Every milestone's done-criterion includes `make test` fully green, not just its own demo. (e) All tolerances are numbers (§14.1) and concurrency tests inject permits instead of discovering them (§14.2). (f) M7→M7a/M7b and M8→M8a/M8b. (g) Restart reconciliation specified (§5.1). | The audit found two hard contradictions (a `split_data` tool the catalogue forbids, node renumbering not propagated to §3/§10), one core guarantee written as an outcome with no mechanism, and demo-only gates that let a milestone pass while breaking a sibling — evidenced by M5's own DoD depending on an M7 tool. Those are defects, not preferences, and a document claiming COMPLETE while containing them is worse than one that admits it is a draft. The deeper lesson taken: **an outcome is not a design.** "Rejected unless a champion is chosen" and "reproduces within tolerance" both read like specifications and neither told an implementer what to build. |
| **D32** | **Test-set integrity covers lineage and the whole project, not one artifact in one run** (review 2026-10-01). (a) **Lineage lock:** every artifact produced by a call that received a test-derived input is itself test-derived (`artifacts.derived_from_test`, set by `mcp_client` at registration). (b) **`fit_*` tools never receive a test-derived artifact, in any node.** Before node 12 no tool receives one, except `verify_split` in node 4, which reads `test` for its structural checks and produces no artifact. In node 12, once a champion is chosen, only transform-only tools — those registered `transform_only=True`: every `apply_*`, `drop_columns`, `predict`, `predict_proba` and the row-wise feature tools (chunk 17 D-a) — may receive them — the champion's already-fitted pipeline applied to `test` — plus **exactly one `score_*` call**, guarded by `runs.test_scored_at`; after that call, `reporting`-phase tools may read test-derived tables to write the report, and `select_champion` never receives one (chunk 19 D-d). (c) **Cross-run exposure is disclosed:** node 4 records `runs.test_fingerprint`: a sha256 content digest of the test frame (its column names, then `pd.util.hash_pandas_object(test.sort_index(), index=True)`), computed by the split tool and returned in its summary for the backend to store. A content digest rather than (dataset sha256, seed, positions): runs do not reference a dataset row, so the split tool cannot know the dataset's identity, and the digest is equal across runs exactly when the same rows of the same frame land in test (chunk 10 D-c). A fork hard-links the same test set (D11), and a re-split with the same seed recreates it, so a fork that reaches node 12 scores it again. Not forbidden (that would make rewind useless) but **counted**: node 12 reports how many earlier runs in the project scored the same fingerprint, and if any did, the report and UI state that the estimate is no longer unbiased. | The original rule keyed on one artifact id. That missed three ways to peek: transforming test in node 7 and scoring the transformed copy in node 10; the impossibility of scoring at all if only one `score_*` call may touch test (the frame must be imputed, encoded and predicted first); and forking after a disappointing test score until one looks better. Each turns "scored exactly once" into a per-artifact, per-run truth that is globally false. |
| **D33** | **Honest reporting of a comparison** (review 2026-10-01). (a) The champion's validation score is **selection-biased** — the maximum over N candidates, W waves and any retune loops (winner's curse). The report labels it a *selection score* and states N and W; only the test score is a performance estimate. (b) Every reported score carries a **95 % bootstrap CI** (1000 resamples, percentile, seeded from `constraints.random_seed`). Node 12 must say when the champion's validation CI overlaps the runner-up's: "not distinguishable on this validation set". (c) **Small-split guard:** node 4 escalates (`data`) instead of splitting when valid or test would hold fewer than 30 rows, or, for classification, fewer than 5 rows of any class. (d) **Default primary metric** for classification is `pr_auc` when the minority share is below 0.1, matching `summarize_target`'s imbalance warning, and `roc_auc` otherwise; overridable, choice logged. (e) Node 12's trade-offs weigh accuracy against interpretability and model complexity, **never fit time** (D29 makes timings incomparable). | D15 targets small data, which is exactly where one 20 % validation split is noisy and where reporting the winner's validation score as if it were unbiased misleads most. CIs make the noise visible without changing the shared-split model. **Rejected for v1:** ranking candidates by k-fold CV over train+valid. It is more statistically efficient on tiny data, but changes every tool's inputs, the shared-split guarantee and the test-lock reasoning; CIs plus the small-split guard buy most of the honesty for none of that. The plan's own c_02 example argues for PR-AUC at 3 % positives while defaulting to ROC-AUC; (d) removes that contradiction. |
| **D34** | **Leak-safe fitting rules** (review 2026-10-01). (a) **Tuning** (`tune_estimator`) uses `cv_folds`-fold CV *within train only*. `valid` ranks candidates and drives retune decisions; it is never inside a fit. (b) **Calibration** (`calibrate`) is fitted inside `train_estimator` by `CalibratedClassifierCV(cv=cv_folds)` on train, never on valid or test. (c) **Target encoding**: `fit_target_encoder` returns the encoder *and* the train frame encoded out-of-fold (cross-fitting, as sklearn's `TargetEncoder.fit_transform` does); `apply_target_encoder` is for valid/test only. (d) **Pre-split row drops** are limited to target-independent structural ones (exact duplicates via `drop_duplicate_rows`, rows above `drop_rows_above` missing share via `drop_high_missing_rows`), done once in node 4 and recorded. After the split, no tool drops a row: every candidate is scored on the same valid and test rows (D26). Rows with a missing target are dropped by the split tools themselves and counted in the split report: they can be neither fitted nor scored, and dropping them reads no target value. Everything learned from data is fit on train through `fit_*`/`apply_*`. (e) **Seeds**: every stochastic tool takes an explicit `seed`, which `mcp_client` fills from `constraints.random_seed` exactly as it fills `project`/`run`. `split.seed` governs only the partition. `n_jobs=1` is forced inside tools (D17). | §4 said the predictive matrix is "fitted on train (tuned on valid)" while the contract specifies `cv_folds`: two selection mechanisms for one decision, which two modules would implement differently. Calibration and target encoding each have one leak-free way to fit and several plausible-looking leaky ones; naming the fold removes the choice. Duplicates must go before the split, or one row lands in train and its copy in test — the leak `verify_split` check 2 exists to catch. Without an explicit seed the `pipeline.py` reproducibility claim (§14.1) has nothing to reproduce. |
| **D35** | **Inference is confirmatory only where pre-specified** (review 2026-10-01). Node 2 fixes the inference goals and their predictors *before* EDA (node 2 precedes node 3), and those are the confirmatory tests. Node 6 may add control covariates and transformations after seeing EDA; their coefficients are reported as **exploratory**, with p-values marked "not pre-specified". With more than one confirmatory test, p-values are **Holm-adjusted** (`statsmodels.stats.multitest.multipletests`), raw and adjusted both reported, each goal's α applied to the adjusted value. `count` targets use a **Poisson GLM**, not OLS (outside the walking skeleton). | A p-value is valid only for a specification chosen before looking at the outcome's relationships. Node 6 choosing its formula after EDA is specification search, and several goals each tested at α inflate the family-wise error rate. Both are standard ways an honest-looking coefficient table becomes misleading, and both are cheap to prevent: one label, one correction call. |
| **D36** | **Lifecycle and ownership corrections** (review 2026-10-01). (a) The canonical mode token is **`autonomous`** (code, DB, API); "Auto" is only a UI label. (b) **`superseded`** is a run status: the state of a parent halted by a rewind fork (D17), distinct from `cancelled` (user stopped it) and `completed`. (c) **Pause and cancel take effect at tool-call boundaries.** A tool body runs in a worker thread, and Python cannot interrupt a running thread, so a cancel waits for the current call to return; long tools enforce their own budget (`tune_estimator` stops at `max_fit_seconds` and returns best-so-far). (d) **Project memory lives only in the `memory` table**; §10.1's `memory.json` is dropped. `remember` is a backend action of the agent loop, not an MCP tool — the MCP server has no database (D26). | (a) The plan said "auto" in 13 places while the implemented `RunMode` and the DB CHECK say `autonomous`; an implementer following the prose would write values the database rejects. (b) D17 and §5 both say "marks it superseded", but no status existed to mark. (c) Was unspecified, and the obvious design (timeouts on the decorator) cannot work on threads. (d) Two stores for one fact drift, and no chunk owned either. |

### 2.1 Environment flag — Task 0

Project currently declares `requires-python = ">=3.14"` and `dependencies = []`.

**Task 0 deliverable is a committed, pinned, co-installable set** — exact `==` versions for
`litellm`, `mcp`, `pydantic`, `scikit-learn`, `statsmodels`, `pandas`, `pyarrow`, `fastapi`,
`uvicorn`, `pytest` — that install *together* and import on the chosen interpreter. Not a range,
not a resolver hope: the resolved versions go into `pyproject.toml` along with the final
`requires-python`.

Order of attempts:

1. Python 3.14. `statsmodels` is the canary — it ships compiled extensions and lags new
   interpreters longest. `litellm` is the second risk, for the opposite reason: it moves fast.
2. If any wheel is missing or the set cannot co-resolve, **fall back to 3.12** and pin there.
   3.12 is the floor; do not go lower.
3. Either way, **re-verify the §7.1 DeepSeek model names and pricing against the official docs**
   before pinning, since that lineup changed twice during 2026 and the figures here are dated
   2026-09-09.

Record which interpreter won and why in the commit message. A later milestone failing on a
dependency resolved differently than recorded is the failure this task exists to prevent.

Not used, deliberately: **DSPy** (its payoff is prompt optimisers, out of scope; it provides no
memory) and **LangChain/LangGraph** (the control nodes are a bespoke router). See D18.

---

## 3. Architecture

```
┌──────────────────────────── Browser ────────────────────────────┐
│  React + TypeScript + Vite                                      │
│  ┌───────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Project   │  │ Setup wizard │  │ Run        │  │ Report   │  │
│  │ picker    │  │ (data+config)│  │ workspace  │  │ viewer   │  │
│  └───────────┘  └──────────────┘  └────────────┘  └──────────┘  │
└──────── REST (commands) ──────────────── SSE (event stream) ────┘
                    │                            ▲
┌───────────────────▼────────────────────────────┴────────────────┐
│  FastAPI (uvicorn)                                              │
│                                                                 │
│   ┌──────────────────── Orchestrator ────────────────────────┐  │
│   │  RunLoop (asyncio task per run)                          │  │
│   │    ├─ inbound control queue  ◄── user messages           │  │
│   │    ├─ Control layer: 7 control nodes (router)            │  │
│   │    ├─ Staging spine: 13 nodes (§4): 1-6 shared,          │  │
│   │    │    7-11 per-candidate, 12-13 shared                 │  │
│   │    └─ Checkpointer: state.json after every node          │  │
│   └──────────┬───────────────────────────┬───────────────────┘  │
│              │                           │                      │
│      ┌───────▼────────┐         ┌────────▼─────────┐            │
│      │ Decision       │         │ MCP client       │            │
│      │ modules        │         │                  │            │
│      │ (Pydantic      │         └────────┬─────────┘            │
│      │  schemas)      │                  │                      │
│      └───────┬────────┘                  │                      │
│              │                           │                      │
│      ┌───────▼────────┐                  │                      │
│      │ LiteLLM        │                  │                      │
│      │ any provider:  │                  │                      │
│      │ DeepSeek (dflt)│                  │                      │
│      │ Claude · GPT…  │                  │                      │
│      └────────────────┘                  │                      │
└──────────────────────────────────────────┼──────────────────────┘
                                           │  MCP Streamable HTTP
                                           │  127.0.0.1:8765 + bearer token
┌──────────────────────────────────────────▼──────────────────────┐
│  MCP Server — "statlab-tools"   (separate process, own port)     │
│  pandas · scikit-learn · statsmodels · matplotlib · joblib       │
│  Tools grouped by phase. Operates on artifact IDs, never on      │
│  data passed through the LLM context. Shares STATLAB_DATA_DIR    │
│  with the backend. Usable by any MCP client.                     │
└─────────────────────────────┬───────────────────────────────────┘
                              │
        ┌─────────────────────▼─────────────────────┐
        │  Storage                                  │
        │  statlab.db (SQLite)  +  projects/ tree   │
        └───────────────────────────────────────────┘
```

### 3.1 The load-bearing invariant

**Data never enters the LLM context.** Tools accept and return *artifact IDs* plus small
JSON summaries. A dataframe lives on disk as parquet; the agent reasons over
`{"rows": 4521, "cols": 17, "missing": {"age": 0.03}, ...}`. This keeps token cost flat
regardless of dataset size, prevents hallucinated values, and is what makes runs cheap.

### 3.2 Repository layout

```
modelling-agent/
├── PLAN.md
├── pyproject.toml
├── backend/
│   └── statlab/
│       ├── main.py              # FastAPI app, routers, SSE
│       ├── config.py            # settings, env, paths
│       ├── db.py                # sqlite3 schema + migrations
│       ├── contract.py          # Pydantic DataContract (D5)
│       ├── storage.py           # project/run folders, artifact registry
│       ├── events.py            # event bus, SSE fan-out
│       ├── memory.py            # session + project memory (§7)
│       ├── graph/
│       │   ├── engine.py        # RunLoop, checkpointing, fork, candidate fan-out
│       │   ├── state.py         # RunState, CandidateState
│       │   ├── router.py        # control-node dispatch
│       │   ├── candidates.py    # candidate spec, budget, test-set lock (D26)
│       │   ├── staging/         # node implementations (domain-agnostic engine)
│       │   └── control/         # 7 control node implementations
│       ├── domains/
│       │   ├── base.py          # Domain, NodeSpec, SHARED / PER_CANDIDATE (D27)
│       │   └── tabular_supervised.py   # the only v1 domain
│       ├── llm/
│       │   ├── client.py        # LiteLLM wrapper: structured output, repair retry, cost
│       │   ├── routing.py       # Role → model string, precedence chain (§7.3)
│       │   ├── schemas.py       # Pydantic output models, one per decision
│       │   └── skills.py        # skill-file loader (§9)
│       ├── mcp_client.py        # tool discovery + invocation + logging
│       └── report.py            # markdown/HTML report + pipeline.py codegen
├── mcp_server/
│   └── statlab_tools/
│       ├── __main__.py          # `python -m statlab_tools` → serves on :8765
│       ├── server.py            # MCP Streamable HTTP app, tool registration, auth
│       ├── settings.py          # STATLAB_DATA_DIR, host/port, bearer token
│       ├── artifacts.py         # load/save parquet, joblib, png
│       ├── registry.py          # predictive model registry (D6)
│       ├── inference.py         # statsmodels inference registry (D7)
│       └── tools/               # one module per phase (§6)
├── skills/
│   └── modelling.md             # the skill file (§9)
├── frontend/
│   ├── package.json
│   └── src/
│       ├── api/                 # REST client + SSE hook
│       ├── pages/               # Projects, Setup, Run, Report
│       └── components/          # GraphView, Chat, GatePrompt, Artifacts
├── tests/
└── projects/                    # runtime data (gitignored)
```

---

### 3.3 Concurrency (D17)

Any number of runs execute simultaneously, and within each run candidates advance in parallel
(D29). What is shared, and how it is protected:

| Shared resource | Contention risk | Guard |
|---|---|---|
| CPU cores | N × `GridSearchCV` thrashing | global fit semaphore in the MCP server, `os.cpu_count()` permits, `n_jobs=1` per estimator |
| `statlab.db` | `database is locked` | WAL journal mode + `busy_timeout=5000`; short transactions, no long-held write locks |
| Run folders | none — every path is `run_id`-scoped | artifact IDs are namespaced per run by construction |
| RAM | N × up to ~600 MB DataFrames | `STATLAB_MAX_CONCURRENT_RUNS` (default `0` = unbounded, per D17); raise a clear error rather than swap if set |
| Timing fidelity | `duration_ms` meaningless under load | `concurrency_at_start` recorded per node execution |
| DeepSeek API | `429` at 2,500 concurrent (Flash) | not reachable at this scale; `429` is a `transient` error for the recovery handler regardless |

The semaphore lives in the MCP server rather than the orchestrator deliberately: it is the
process that actually burns the CPU, and putting the limit there means an *external* MCP client
(D13) is bounded by the same rule instead of bypassing it.

---

## 4. Staging nodes (the spine)

> **Normative spine — supersedes every other numbering in this document.**
>
> | # | Node | Kind |
> |---|---|---|
> | 1 | `intake` | SHARED |
> | 2 | `problem_definition` | SHARED |
> | 3 | `data_profiling` | SHARED |
> | 4 | `data_splitting` | SHARED |
> | 5 | `experiment_design` | SHARED |
> | 6 | `inferential_analysis` | SHARED |
> | 7 | `preprocessing` | PER_CANDIDATE |
> | 8 | `feature_engineering` | PER_CANDIDATE |
> | 9 | `model_training` | PER_CANDIDATE |
> | 10 | `model_evaluation` | PER_CANDIDATE |
> | 11 | `model_interpretation` | PER_CANDIDATE |
> | 12 | `champion_selection` | SHARED |
> | 13 | `artifact_persistence` | SHARED |
>
> Node 6 depends only on nodes 1-4, so the engine MAY execute it concurrently with the
> candidate fan-out; it is ordered here, not sequenced.
> Search loop: node 10 → node 5 (new candidate wave, `max_search_waves`).
> Retune loop: node 10 → node 9 (within one candidate, `max_retune_loops`).
> If any prose in this document disagrees with this table, the table wins.

Each node: consumes `RunState`, calls a typed decision module to *decide*, calls MCP tools to *act*,
writes artifacts, returns a `NodeResult`. In guided mode a HITL gate opens after the
decision and before the action (so the user edits parameters, not results).

| # | Node | Kind | LLM decides | Produces |
|---|---|---|---|---|
| 1 | **Intake, configuration & data contract** | shared | re-validates the contract against the actual CSV, flags mismatches and suspected leaks, proposes fixes (elicitation already happened pre-run, D8) | `raw` artifact, contract snapshot, intake report |
| 2 | **Problem definition & scoping** | shared | task type, primary predictive metric, secondary metrics, success threshold, **inference goals** (which relationships must be explained and at what α), constraints, risks | `problem_spec.json` |
| 3 | **Data profiling (EDA)** | shared | which profiles matter, what the numbers mean, hypotheses worth testing | profile JSON, plots, EDA narrative |
| 4 | **Data splitting** | shared | strategy (random/stratified/temporal/grouped), ratios, seed — **once, shared by every candidate** (D26). A strategy with no v1 tool (temporal, grouped) escalates as `data`, **never falls back to random**: a random split of time-ordered data lets the model see the future. Refuses splits that are too small (D33c). Records `test_fingerprint` (D32c) | `train`/`valid`/`test` artifacts, split report |
| 5 | **Experiment design** | shared | **the candidate shortlist**: which 3–5 approaches are worth trying and why, each justified by an EDA finding (D28) | `candidates.json`, comparison plan |
| 6 | **Inferential analysis** | shared | **the inferential specification** (formula, family, link, reference levels) and which findings are defensible; fitted **once per run** on a canonical matrix, independent of any candidate (D31) | inference matrix, one fit, coefficient table with p-values/CIs, VIF, residual & influence diagnostics |
| 7 | **Data preprocessing & cleaning** | per-candidate | imputation per column (`drop` drops the column, never rows), outlier policy, constant / high-missing column drops. Row drops and dtype casts are pre-split, in node 4 (D34d) | fitted transformers, cleaned artifacts |
| 8 | **Feature engineering** | per-candidate | encoding strategy, derived features, selection method | predictive feature matrix, feature manifest |
| 9 | **Model selection & training** | per-candidate | param grid and budget for this candidate's estimator | model artifact, fit logs |
| 10 | **Model evaluation & validation** | per-candidate | which metrics/curves per task, stability checks — **validation only, test is locked** (D26) | metrics, curve data + plots |
| 11 | **Model interpretation & diagnostics** | per-candidate | what to interpret, leakage suspicion, error patterns (predictive only — inferential diagnostics live in node 6) | importances, PDPs, error analysis |
| 12 | **Champion selection & reporting** | shared | **cross-candidate trade-off reasoning**, final recommendation, caveats, which node-6 inferential findings to state | comparison table, champion pointer, **the single test-set score**, report.md + plots |
| 13 | **Artifact persistence & reproducibility** | shared | what must be preserved, environment capture | `pipeline.py`, manifest, lockfile, run hash |

Tools per node are declared in the skill file's `Tools` section and enforced in code (D12);
the full catalogue is §6.3.

**Search loop (D3, D26):** node 10 may route back to node 5 to propose a **second wave** of
candidates informed by the first wave's comparison, capped at `max_search_waves` (default 2).
Within a candidate, node 10 may route back to node 9 to retune, capped at `max_retune_loops`
(default 2). Every iteration is a new `node_executions` row — history is append-only.

**Skips (D3):** any node may return `status="skipped"` with a mandatory reason. Node 8 is the
common case (nothing worth engineering for that candidate); node 6 is skipped only when node 2
recorded no inference goals. Nodes 1, 4, 5, 9, 10, 12, 13 are **non-skippable**.

**Two feature matrices (consequence of D7).** Prediction and inference want incompatible
preprocessing, so they are built in different places: the **predictive** matrix per candidate in
nodes 7–8, the **inferential** matrix once in node 6 (D31):

| | Predictive matrix | Inferential matrix |
|---|---|---|
| Scaling | as the model needs | none — coefficients must stay in original units |
| Categoricals | one-hot / target encoding | dummy coding with an explicit reference level |
| Feature selection | aggressive, metric-driven | only theory-driven drops + collinearity removal |
| Missing values | imputed | imputed, with a missingness indicator where informative |
| Fitted on | `train` (tuned by CV within `train`; ranked on `valid` — D34a) | `train` + `valid` (no held-out set needed for inference) |
| Built in | nodes 7–8, once per candidate | node 6, once per run (D31) |
| How many | one per candidate | exactly one per run |
| Judged by | held-out metric on `valid`, then `test` once if champion | assumption tests, AIC/BIC, coefficient stability |

The inferential matrix is derived from the same cleaning decisions, so the two stay
comparable; the divergence is deliberate and recorded in the feature manifest. Skipping the
inferential track is allowed only when node 2 recorded no inference goals.

---

### 4.1 The data contract

One JSON file per dataset, validated by a Pydantic model. **Every field is optional except
`target.column`, which must be set before a run can start (D8).** What the user does not
supply is drafted from the dataset profile and confirmed in the setup wizard. What the user
*does* supply is authoritative and never silently overridden — the agent may object in
writing, but it obeys.

```jsonc
{
  "version": 1,
  "dataset": {
    "delimiter": ",", "encoding": "utf-8", "decimal": ".",
    "na_values": ["", "NA", "N/A", "-999"], "header_row": 0
  },
  "target": {
    "column": "churned",
    "type": "binary",              // binary | multiclass | continuous | count
    "positive_class": 1,
    "class_order": null            // for ordinal multiclass
  },
  "features": {
    "include": null,               // null = everything not excluded
    "exclude": ["notes_free_text"],
    "id_columns": ["customer_id"], // never features, kept for traceability
    "roles": {                     // numeric | categorical | ordinal | datetime | boolean | text
      "age": "numeric", "plan": "categorical", "signup_date": "datetime"
    },
    "ordinal_levels": { "satisfaction": ["low", "medium", "high"] }
  },
  "objective": {
    "task": "classification",
    "primary_metric": "roc_auc",
    "secondary_metrics": ["f1", "precision", "recall", "brier"],
    "success_threshold": 0.80,
    "inference_goals": [           // drives the D7 track
      { "question": "Does plan tier affect churn?",
        "predictors": ["plan"], "alpha": 0.05 }
    ]
  },
  "split": {
    "strategy": "stratified",      // random | stratified | temporal | grouped
    "test_size": 0.2, "valid_size": 0.2,
    "time_column": null, "group_column": null, "seed": 42
  },
  "preprocessing": {
    "missing": { "default": "median", "per_column": { "plan": "most_frequent" },
                 "drop_rows_above": 0.6 },
    "outliers": "clip_iqr",        // none | clip_iqr | winsorize | flag_only
    "scaling": "standard",         // applies to the predictive matrix only
    "categorical_encoding": "onehot",
    "max_cardinality": 30,         // above this → target/frequency encoding
    "drop_constant": true, "drop_duplicates": true
  },
  "modelling": {
    "candidates": ["logistic_regression", "random_forest_classifier",
                   "hist_gb_classifier"],
    "inferential_model": "logit",
    "tuning": { "method": "grid", "cv_folds": 5, "max_fit_seconds": 300 },
    "class_weight": "balanced", "calibrate": true
  },
  "constraints": {
    "max_runtime_seconds": 1800, "max_retune_loops": 2,
    "max_candidates": 8, "max_search_waves": 2, "max_total_fit_seconds": 1200,
    "random_seed": 42, "interpretability_required": false
  },
  "notes": "Free-text domain context the agent should respect."
}
```

**Leak guard.** `id_columns` are excluded from features but retained in the artifact, and
node 1 warns on any column that is near-unique, constant, or correlates with the target
above a threshold (a likely leak) — regardless of what the contract says. The agent reports;
the user decides.

**`preprocessing.missing` shape (chunk 03).** The illustrative JSON above uses three explicit
fields — `default`, `per_column`, `drop_rows_above` — rather than one dict mixing a `default`
key, per-column keys and a `drop_rows_above` key in the same namespace. That flatter shape
requires the implicit convention "any key that is not one of two reserved names is a column
name", which chunk 03 rejects for the same reason the plan rejects other encoded-string and
implicit-convention shapes elsewhere: three explicit fields carry the same information with no
convention to remember. `outlier_policy` is likewise named `outliers` on `PreprocessingRules`,
for brevity and symmetry with the other three preprocessing sub-fields.

**`modelling` fields constrain node 5; they are not a frozen shortlist.** `modelling.candidates` is the pool of estimators node 5 may draw from. `class_weight`, `calibrate` and `tuning`, when set, apply to *every* candidate; when absent, node 5 may vary them between candidates. That is how a user-supplied value stays authoritative (D5) without turning D28's reasoned shortlist into a fixed list.

**Contract → tool parameter precedence.** Several contract fields duplicate `load_csv` params
(`delimiter`, `encoding`, `decimal`, `na_values`, `header_row`). The rule: **node 1 copies the
contract's `dataset` block into `load_csv` mechanically, and the agent may not override it.** If
loading fails or the parse looks wrong (single column, all-NaN, row count far off), the agent does
not silently retry with different params — it opens a `data`-class escalation (§6.1.1) proposing
the change, because a contract field the user set is authoritative (D5) and silently reinterpreting
their file format is how you get a run that succeeds on the wrong data. The same rule holds
generally: **where the contract specifies a value, tool params are derived from it, not chosen.**

---

### 4.2 Candidates: the run is a comparison, not a path (D26)

**The purpose of a run is to compare approaches**, not to walk one path to one model. So the
spine executes in two regimes:

```
      ┌──────────────────── SHARED, once per run ─────────────────────┐
      │  1 intake → 2 scoping → 3 profiling (EDA) → 4 splitting       │
      │                    → 5 experiment design                     │
      └───────────────┬──────────────────────────┬───────────────────┘
                      │                          │
                      │  3–5 candidates          │  6 inferential analysis
                      │  (D28)                   │  SHARED, once, on a
        ┌─────────────┼─────────────┐            │  canonical matrix (D31)
        ▼             ▼             ▼            │  → ONE coefficient table
   ┌─────────┐  ┌─────────┐   ┌─────────┐        │
   │ cand A  │  │ cand B  │   │ cand N  │        │  (may run concurrently
   │  7 clean│  │  7 clean│   │  7 clean│        │   with the fan-out)
   │  8 feats│  │  8 feats│   │  8 feats│        │
   │  9 train│  │  9 train│   │  9 train│        │
   │ 10 eval │  │ 10 eval │   │ 10 eval │        │
   │ 11 diag │  │ 11 diag │   │ 11 diag │        │
   └────┬────┘  └────┬────┘   └────┬────┘        │
        └─────────────┼─────────────┘            │
                      └──────────┬───────────────┘
                                 ▼
      ┌── 12 champion selection: rank all on VALID, pick one,        ─┐
      │      score that one on TEST once, report it alongside the     │
      │      node-6 inference findings → 13 persistence               │
      └───────────────────────────────────────────────────────────────┘
                                 │
                   (node 10 may loop back to 5 for a
                    second wave, max_search_waves)
```

**A candidate is a fully specified approach**, not just a model. The schema is typed, not a free
dict — `"mutual_info_k20"`-style encoded strings are forbidden, because a parse-from-string
convention is exactly the kind of thing two modules disagree about:

```python
class FeatureSelection(BaseModel):
    method: Literal["none", "variance", "mutual_info"] = "none"
    k: int | None = None                    # required iff method == "mutual_info"
    threshold: float | None = None          # required iff method == "variance"

class CandidateSpec(BaseModel):
    label: str                              # human-readable, e.g. "RF + target-encoded plan"
    rationale: str                          # MUST cite an EDA finding (D28)
    is_baseline: bool = False
    preprocessing: PreprocessingSpec        # imputation per column, outlier policy, drops
    features: FeatureSpec                   # encoding, derived features, selection
    estimator: EstimatorSpec                # name from ESTIMATORS + explicit params
    varies_from_baseline: list[str]         # dimension names, non-empty unless is_baseline
```

**Spec-hash canonicalization (dedupe, D28).** `spec_hash = sha256` of the candidate serialised
with `model_dump(mode="json")` → `json.dumps(..., sort_keys=True, separators=(",", ":"))`, with
`label`, `rationale` and `varies_from_baseline` **excluded** (they are commentary, not identity).
Key order therefore cannot affect identity, and two candidates that differ only in their prose
justification are correctly rejected as duplicates.

So "methodologies" are first-class: two candidates may share an estimator and differ only in
encoding, or share features and differ only in the model. `varies_from_baseline` is what makes
the final comparison *interpretable* — it records the one thing that changed.

**The shortlist is reasoned, not enumerated (D28).** Node 5 does not build a cross-product.
A typical set on an imbalanced binary target:

| # | Candidate | Why (must cite an EDA finding) |
|---|---|---|
| c_01 | logistic regression, one-hot, no engineering | **mandatory baseline** — reference point for whether complexity pays |
| c_02 | logreg + `class_weight="balanced"` | positive rate is 3.1%; unweighted logreg will under-predict the minority |
| c_03 | random forest, one-hot | 4 numeric features show non-monotonic relationships with the target in the PDP-style profile plots |
| c_04 | hist GB + target-encoded `plan` | `plan` has 47 levels; one-hot dilutes it across 47 sparse columns |
| c_05 | logreg + interaction `age × income` | correlation matrix shows both matter and the EDA narrative flagged a plausible interaction |

Enforced by the decision schema: a mandatory baseline, every candidate differing from every
other in at least one recorded dimension (deduped by spec hash), and at most two candidates from
one model family without explicit justification.

**Three rules that make the comparison statistically honest.** These are the reason this
section exists; without them a comparison-focused app produces confident nonsense.

1. **The split is shared.** All candidates use the identical train/valid/test partition from
   node 4. Comparing models fitted on different splits measures the split, not the model.
2. **The test set is locked until champion selection.** Candidates are ranked on validation or
   cross-validation only.

   **Enforcement mechanism (this is a backend guarantee, not a server feature).** The MCP server
   stays stateless (D22) and knows nothing about runs or nodes — asked to fit on test data, it
   would comply. The lock lives one layer up:

   - the artifact registry marks the `test` artifact `locked: true` at creation (node 4);
   - `mcp_client` refuses to pass a locked artifact ID as any tool parameter unless
     `RunState.current_node == "champion_selection"` (node 12) **and** `RunState.champion_candidate_id is
     not None`;
   - the refusal is raised **before dispatch**, so the call never reaches the network;
   - the lock follows **lineage**: any artifact produced from a test-derived input is itself
     test-derived, and `fit_*` tools never receive one (D32a–b);
   - in node 12, once a champion is chosen, transform-only tools (registered `transform_only=True`:
     every `apply_*`, `drop_columns`, `predict`, `predict_proba`, and the row-wise feature tools)
     may receive test-derived artifacts — the champion's fitted pipeline applied to
     test — and **exactly one `score_*` call** is allowed, claimed atomically through
     `runs.test_scored_at`. A second attempt is refused even inside node 12.

   The §14 test drives `mcp_client`, not the server, and asserts the pre-dispatch refusal.
   **Scope limit:** an external MCP client (D13) is *not* bound by this — it can call any tool on
   any artifact. The guarantee is "this application never asks", not "the tools refuse". Stating
   that honestly matters more than pretending otherwise.
3. **The champion is scored on test exactly once.** That number is the only unbiased estimate
   in the run, and the report must say so — with its bootstrap CI, and with the champion's
   validation score labelled as selection-biased (D33a–b). Across runs, the number of earlier
   scorings of the same test set is reported (D32c); a fork is not a fresh test.

**Budget.** `max_candidates` (default 8) and `max_total_fit_seconds` bound the fan-out. The
agent may run an **iterative search**: propose a first wave, read the comparison, propose a
second wave informed by it, up to `max_search_waves` (default 2). This is the retune loop of
D3 generalised — and the reason node 10 → node 9 routing already exists.

**Why the MCP server needs no changes for this.** Tools are stateless, granular and operate on
artifact IDs (§6.1), so running node 5 for eight candidates is the same tools called eight times
with different params. The candidate abstraction lives entirely in the orchestrator. This is the
payoff of D22's granularity that was not obvious when we chose it.

---

### 4.3 Domains: the app is an interface to tools (D27)

The application is a **generic interface over MCP tool phases**. Tabular supervised learning is
the first domain, not a hardcoded assumption. So the node spine is **declared, not compiled in**:

```python
# backend/statlab/domains/tabular_supervised.py

DOMAIN = Domain(
    name="tabular_supervised",
    contract_model=TabularContract,          # Pydantic; domains extend the base
    skill_file="skills/tabular_supervised.md",
    tool_phases=["intake", "profiling", "splitting", "cleaning", "encoding",
                 "features", "inference_matrix", "training", "evaluation",
                 "inference", "interpretation", "champion", "artifacts"],
    nodes=[
        NodeSpec("intake",                 kind=SHARED,        skippable=False),
        NodeSpec("problem_definition",     kind=SHARED,        skippable=False),
        NodeSpec("data_profiling",         kind=SHARED,        skippable=True),
        NodeSpec("data_splitting",         kind=SHARED,        skippable=False),
        NodeSpec("experiment_design",      kind=SHARED,        skippable=False),
        NodeSpec("inferential_analysis",   kind=SHARED,        skippable=True),
        NodeSpec("preprocessing",          kind=PER_CANDIDATE, skippable=True),
        NodeSpec("feature_engineering",    kind=PER_CANDIDATE, skippable=True),
        NodeSpec("model_training",         kind=PER_CANDIDATE, skippable=False),
        NodeSpec("model_evaluation",       kind=PER_CANDIDATE, skippable=False),
        NodeSpec("model_interpretation",   kind=PER_CANDIDATE, skippable=True),
        NodeSpec("champion_selection",     kind=SHARED,        skippable=False),
        NodeSpec("artifact_persistence",   kind=SHARED,        skippable=False),
    ],
)
```

The engine, the control nodes, gates, forking, SSE, the UI and the report are **all
domain-agnostic** — they iterate a `NodeSpec` list and render whatever the tools returned. Adding
a domain touches no existing code:

| To add | You write | You do NOT touch |
|---|---|---|
| **Time series** | lag/rolling/seasonality tools, ARIMA/ETS registry, walk-forward CV, backtest metrics, stationarity + residual autocorrelation tests, a contract with `horizon`/`frequency`/`seasonality`, a spine with `temporal_validation` replacing `data_splitting` | engine, control layer, gates, API, SSE, UI, report renderer |
| **Clustering** | distance/scaling tools, KMeans/DBSCAN/hierarchical registry, silhouette & stability metrics, a spine with no target and no split | as above |
| **Survival analysis** | Kaplan-Meier / Cox tools, censoring-aware contract fields, concordance metrics | as above |

#### 4.3.1 The load-bearing interfaces

These three types are what "domain-agnostic engine" means. They are referenced by M1 (the
`@tool` envelope), M8a (the engine) and M10 (recovery), so they are frozen in **M0.5** before any
of them is built — three independent inventions of the same type is the failure mode here.

```python
Kind = Literal["SHARED", "PER_CANDIDATE"]
GatePolicy = Literal["always", "exception", "never"]   # D29

@dataclass(frozen=True)
class NodeSpec:
    name: str
    kind: Kind
    skippable: bool
    decision_schema: type[BaseModel]   # the node's typed LLM output (D18)
    tool_phases: tuple[str, ...]       # phases whose tools this node may use (D12)
    gate_policy: GatePolicy

class NodeResult(BaseModel):
    status: Literal["completed", "skipped", "failed", "awaiting_user"]
    decision: BaseModel | None         # the validated decision that drove the node
    artifacts: list[str]               # artifact IDs produced
    metrics: dict[str, float]          # flushed to the metrics table
    summary: dict                      # small, human- and LLM-readable
    warnings: list[str]
    skip_reason: str | None            # mandatory when status == "skipped" (D3)
    error: ToolError | None            # populated when status == "failed" (§6.1.1)

ArtifactRef = NewType("ArtifactRef", str)   # validated to exist in the registry at dispatch
```

`ArtifactRef` resolution and existence-checking happen in `mcp_client`, which is also where the
D12 allow-list and the D26 test-lock are enforced — one choke point, three guarantees.

Three constraints make this real rather than aspirational:

1. **No node name is referenced outside its own module and the domain spec.** The engine only
   knows `NodeSpec`. A test greps for hardcoded node names outside `domains/`.
2. **Tool phases are declared by the domain**, and the D12 allow-list is validated against the
   phases the running domain actually exposes — so a time-series skill file cannot reference a
   tabular-only tool.
3. **The contract is a base model plus per-domain extensions**, so `target.column` and
   `constraints` stay shared while `horizon` lives only in the time-series contract.

**v1 ships one domain.** The point is that the seam exists and is tested, not that a second
domain is written now — a second domain written speculatively would be designed against
guesses about an interface nobody has used yet.

---

## 5. Control nodes (the router layer)

Control nodes are *not* positions in the spine. They are handlers the router dispatches to
when something arrives from outside the pipeline, or when a node fails.

```
                     user message arrives mid-run
                                │
                     ┌──────────▼───────────┐
                     │ User interrupt router│  (typed classifier, CLASSIFY role)
                     └──────────┬───────────┘
      ┌──────────┬──────────┬───┴────┬────────────┬──────────────┐
      ▼          ▼          ▼        ▼            ▼              ▼
  Out-of-band  Status    HITL     Change/     Cancel/Pause/   (noise →
     Q&A      explainer  gate     rewind        resume        ack only)
                        answer    handler       handler
```

| Node | Trigger | Behaviour | May mutate state? |
|---|---|---|---|
| **User interrupt router** | any inbound message while a run is active | Typed classifier (`InterruptClass` schema) → one of `qa`, `status`, `decision`, `change`, `lifecycle`, `noise`. Dispatches. Never answers itself. | no |
| **Out-of-band Q&A** | `qa` | Answers from `RunState` + artifacts summaries + project memory. Pipeline keeps running. | no |
| **Modelling status explainer** | `status` | Plain-language "where are we, what's done, what's next, est. remaining". Deterministic template + LLM polish. | no |
| **HITL decision gate** | guided mode: shared nodes 1–6, plus exceptions in per-candidate nodes (D29); or a `decision` message | Emits `gate.opened` with proposal, reasoning, editable params, a default and the 7 actions of §8. Blocks that run's loop indefinitely until answered; persisted to `gates`, so it survives restart. `instruct` loops back for a revised proposal; `approve_all_remaining` flips the run to auto. | yes — applies overrides, may change mode |
| **Change/rewind handler** | `change` | Resolves the target node, **forks a new run** (state copied to node N, artifacts hard-linked, `forked_from_run_id` set), applies the parameter overrides, starts executing from N. Original run is left untouched and marked `completed`/`superseded`. Emits the new `run_id` so the client can follow. | creates a new run |
| **Cancel/pause/resume handler** | `lifecycle` | `pause` = stop after current node, keep state. `resume` = continue from checkpoint. `cancel` = terminate, mark run cancelled, keep artifacts. | yes — status only |
| **Recovery/retry handler** | any node raises | Classifies error: `transient` (LLM/network → exponential backoff, max 3), `tool_param` (bad args → LLM repairs args, max 2), `data` (unfixable → escalate to user even in auto mode), `bug` (escalate + full traceback). | yes — retry bookkeeping |

**Auto mode never blocks** except on a `data`/`bug` escalation from the recovery handler.
Guided mode blocks at the gates defined by each node's `gate_policy` (D29). Out-of-band Q&A and
status work identically in both.

### 5.1 Restart reconciliation

Runs are asyncio tasks (D17); a process restart kills them. Because gates block indefinitely and
may be answered days later (D10), boot-time recovery is a specified behaviour, not an accident.

On startup the backend scans `runs` and acts by status:

| Status found | Action |
|---|---|
| `awaiting_user` | rehydrate `RunState` from `state.json`, re-publish the pending gate from `gates`, leave **dormant** until the gate is answered |
| `paused` | rehydrate, leave dormant until an explicit `resume` |
| `running` | the process died mid-flight → mark the interrupted node execution `failed` with `error_class="bug"`, reason `"process terminated"`, then **resume in place from the last completed node boundary**. This is crash recovery, not a D11 rewind: no fork is created, because no user decision changed |
| `pending` | start normally |
| `completed` / `failed` / `cancelled` | nothing |

**Resume granularity is the node boundary, never mid-node**, because tools are not transactional:
a killed run may have written three of five artifacts for a node, and there is no way to know
which. Re-running the node from its checkpointed input is the only correct recovery.

Consequently the crashed attempt's intermediates are **not pruned** (D24 prunes on success only) —
they are exactly what diagnosis needs, and the retry writes new artifact IDs rather than
colliding with them.

For a run with candidates in flight (D26), each candidate's progress is checkpointed
independently, so recovery replays only the candidates that were mid-node.

---

## 6. The MCP server

`statlab-tools` — a standalone HTTP service (D13) holding **all** computation. The backend
never imports pandas or sklearn.

### 6.1 Design rules

**One tool, one responsibility.** A tool does a single thing and does it correctly. No
mega-tools with a `mode` parameter branching into five behaviours. Consequences:

1. **Stateless.** All state is artifacts. A tool takes artifact IDs plus scalar params and
   returns new artifact IDs. Nothing is held in memory between calls.
2. **Immutable artifacts.** Tools never mutate an input; every transformation produces a new
   artifact with a new ID. This is what makes D11's hard-linked forks safe.
3. **`fit` and `apply` are separate tools** for anything learned from data. `fit_imputer` reads
   `train` and returns an imputer artifact; `apply_imputer` takes that artifact and any frame.
   This is the most valuable consequence of granularity: **train-only fitting becomes
   auditable**. A leak would require visibly calling `fit_imputer` on the test set, which the
   tool-call log records and the report shows.
4. **Compute and render are separate tools.** `compute_roc_curve` returns curve *data*;
   `plot_curve` renders a PNG from it. Numbers stay inspectable and the UI can re-render
   without recomputing.
5. **Uniform envelope.** Every tool returns:
   ```jsonc
   { "ok": true,
     "summary": { /* small, LLM-readable, never raw data */ },
     "artifacts": [{ "id": "a_...", "kind": "dataframe", "rows": 3164, "cols": 22 }],
     "warnings": ["`plan` has 47 levels; above max_cardinality of 30"] }
   ```
6. **Params are Pydantic models**, so MCP's `inputSchema` is generated rather than hand-written
   and the LLM sees exact types and bounds.
7. **Every tool is independently testable** — its test needs a fixture and nothing else.

#### 6.1.1 The result envelope, success and failure

A tool returns exactly one of two shapes. Tools **do not raise** across the MCP boundary; the
`@tool` decorator converts any uncaught exception into the error envelope.

```jsonc
// success
{ "ok": true,
  "summary": { "rows": 3164, "cols": 22 },          // small, LLM-readable, never raw data
  "artifacts": [{ "id": "a_...", "kind": "dataframe", "rows": 3164, "cols": 22 }],
  "warnings": ["`plan` has 47 levels; above max_cardinality of 30"] }

// failure
{ "ok": false,
  "error_class": "tool_param",                      // transient | tool_param | data | bug
  "message": "valid_size + test_size must be < 0.9",
  "field_errors": { "valid_size": "0.5 with test_size=0.5 leaves no training rows" } }
```

**There is no `retryable` field.** Retryability is a pure function of `error_class` (see
`RETRY_BUDGET` below), so carrying it on the wire would be duplicated state that can disagree
with its own source of truth. A tool also cannot know it: whether a retry is still permitted
depends on how many attempts have already happened, which is the recovery handler's state, not
the tool's. Chunk 31 derives it with `is_retryable(error_class)`.

`error_class` is the **only** field the recovery handler (§5) dispatches on, and the taxonomy is
shared with the LLM layer (D18) so one classifier serves both:

| `error_class` | Meaning | Recovery handler action |
|---|---|---|
| `transient` | network, timeout, `429`, transient IO | exponential back-off, max 3 attempts |
| `tool_param` | schema-valid but semantically wrong args | return `field_errors` to the agent, one repair attempt, max 2 |
| `data` | the data cannot support the request (constant target, empty split) | escalate to the user **even in auto mode** — not repairable by retrying |
| `bug` | unexpected exception | escalate with traceback; traceback goes to the server log, **never** into `message` or the event stream |

If a `tool_param` failure genuinely cannot be fixed by changing arguments — no numeric column
exists, so no scaler can be fitted — it is **not** `tool_param`, it is `data`. The taxonomy
already separates "your arguments were wrong" from "the data cannot support this", so no case
needs a per-failure retryability override.

`field_errors` is mandatory for `tool_param` and absent otherwise. This is what makes §6.5's
single repair retry mechanical rather than a guess.

### 6.2 Extensibility

Adding capability is a one-line or one-file change, never a refactor:

```python
# mcp_server/statlab_tools/tools/splitting.py

@tool(phase="splitting", allow_in=["data_splitting"])
def split_stratified(df: ArtifactRef, target: str, test_size: float = 0.2,
                     valid_size: float = 0.2, seed: int = 42) -> ToolResult:
    """Split preserving target class proportions across all three sets."""
```

- **New tool** → a decorated function in the phase's module. `tools/__init__.py` imports the
  package and the decorator registers it. No central list to update.
- **New estimator** → one entry in `registry.ESTIMATORS`.
- **New inferential model** → one entry in `inference.SPECS`.
- **New phase** → a new module. The decorator's `phase` / `allow_in` feed the D12 allow-list.

The `@tool` decorator owns registration, Pydantic validation, artifact resolution, the result
envelope, timing, and semaphore acquisition for CPU-bound tools (`heavy=True`) — so each tool
body contains only its actual logic.

### 6.3 Tool catalogue (83 tools (v1))

`[v1]` marks the walking-skeleton subset built first (§6.4). `H` = heavy, takes a semaphore
permit (D17).

**Signatures below omit orchestrator-supplied parameters.** Every tool with artifact inputs also
receives `project` and `run` (chunk 07); `load_csv` receives `dataset`, the upload's sha256 (chunk
08); stochastic tools receive `seed` (D34e). `mcp_client` fills all of them and overwrites anything
the model proposes, so they are never part of the agent's choice. Any `df`/`train`/`truth`
parameter is an artifact ref, and is subject to the test-set lineage lock (D32).

**intake** — 5
```
load_csv(dataset, delimiter, encoding, decimal, na_values, header_row)  [v1]
    `dataset` is the upload's sha256, not `path` (chunk 08 D-a) -- a filesystem path at this
    trust boundary needs traversal checks; an identifier that cannot express a path does not.
infer_column_roles(df)                                                 [v1]
validate_contract(df)                                                  [v1]
    reads the contract from the run's `contract.snapshot.json`, not a `contract` parameter
    (chunk 08 D-c) -- the agent would otherwise have to reproduce the full contract verbatim in
    every call.
detect_id_columns(df)                       near-unique candidates
detect_leaky_columns(df, target)            suspicious target correlation
```

**profiling** — 9
```
profile_schema(df)                          dtypes, memory, n_unique   [v1]
summarize_numeric(df, columns)              count/mean/std/quantiles   [v1]
summarize_categorical(df, columns)          frequencies, cardinality   [v1]
report_missingness(df)                      per-column + co-occurrence [v1]
summarize_target(df, target)                distribution / balance     [v1]
compute_correlations(df, method)            → matrix artifact
detect_outliers(df, columns, method)        counts + row indices
plot_distribution(df, column)               → png
plot_target_relation(df, column, target)    → png
```

**splitting** — 5 — one tool per strategy; each is ~10 lines and cannot be misused
```
split_random(df, target, test_size, valid_size, seed)                  [v1]
    shuffle and cut. Correct when rows are independent. Default for regression.
    Both v1 split tools take `target`: they drop rows with a missing target, refuse
    undersized splits and classes (D33c), and return `test_fingerprint` (D32c).
split_stratified(df, target, test_size, valid_size, seed)              [v1]
    preserves target class proportions per split. Essential for imbalanced
    classification; meaningless for a continuous target.
split_temporal(df, time_column, test_size, valid_size)             [deferred]
    deferred out of v1 (chunk 20r D-c; D25): ~10 lines when needed, and node 4 (chunk 27a)
    escalates a temporal request as `data` rather than falling back to a leaky random split.
    sort by time, train on past, test on future. Required for time-ordered data —
    a random split there lets the model see the future and inflates every metric.
split_grouped(df, group_column, test_size, valid_size, seed)       [deferred]
    deferred out of v1 (chunk 20r D-c; D25): node 4 escalates a grouped request as `data`.
    all rows of an entity stay in one split. Required when an entity has several
    rows, else the model memorises the entity instead of the pattern.
verify_split(train, valid, test, target)                               [v1]
    four independent checks, each reported separately:
      1. index overlap — row-index intersection between any two splits must be empty
      2. duplicate rows — value-identical rows appearing in two splits (a real leak
         even when indices differ, e.g. after a naive dedupe)
      3. target balance — class proportions per split vs the whole frame; warns
         beyond a configurable drift, and errors when a split has zero of a class
      4. size sanity — no split empty, none below a minimum row count
    each check reports pass / warn / fail / skipped; `passed` is false iff any fails
    "Leakage" in this tool means checks 1 and 2 only. Target leakage from a
    predictor column is `detect_leaky_columns` (intake), a different concern.
```

**cleaning** — 10
```
drop_columns(df, columns)                                              [v1]
drop_duplicate_rows(df, subset)             node 4 only, pre-split (D34d)
drop_high_missing_rows(df, threshold)       node 4 only, pre-split (D34d)
drop_constant_columns(train, target)        decides on train; valid/test via drop_columns
drop_high_missing_columns(train, threshold, target)    same
cast_dtypes(df, target, roles)              pre-split; target never cast; contract roles override `roles`
fit_imputer(train, target, default, per_column) → imputer artifact    [v1]
apply_imputer(imputer, df)                                             [v1]
fit_outlier_clipper(train, target, method, columns)
apply_outlier_clipper(clipper, df)
```

**encoding** — 8
```
fit_onehot_encoder(train, target, columns, max_cardinality)            [v1]
apply_onehot_encoder(encoder, df)                                      [v1]
fit_ordinal_encoder(train, target, columns, level_order)
apply_ordinal_encoder(encoder, df)
fit_target_encoder(train, target, columns, smoothing)  → encoder + OOF-encoded train (D34c)
apply_target_encoder(encoder, df)           refuses any train row label (chunk 12 D-d)
fit_scaler(train, target, method, columns)                             [v1]
apply_scaler(scaler, df)                                               [v1]
```

**features** — 7 — `T` = transform-only (chunk 17 D-a), replayed on test in node 12
```
add_datetime_parts(df, target, column, parts)                      T   replaces the source column
add_interaction_terms(df, target, pairs)                           T
add_ratio_feature(df, target, numerator, denominator)              T   zero denominator → NaN
fit_binner(train, target, columns, n_bins, strategy)                   edges learned on train (D22)
apply_binner(binner, df)                                           T
select_by_variance(train, target, threshold)                           decides on train; drop_columns replays
select_by_mutual_info(train, target, k)                            H   decides on train; drop_columns replays
```

**inference_matrix** — 2 — the D7 second track
```
build_dummy_matrix(df, columns, reference_levels)   no scaling, explicit reference
assemble_inference_matrix(train, valid, target, predictors, reference_levels)    fit on train+valid  [v1]  (replaces build_dummy_matrix in the 33, chunk 16 D-g)
```

**training** — 6
```
list_estimators(task)                       registry introspection     [v1]
train_estimator(train, target, name, params, class_weight, calibrate) H [v1]
tune_estimator(train, target, name, metric, param_grid, class_weight) H  CV within train (D34a); cv_folds and max_fit_seconds from the contract; returns params, not a model
predict(model, df)                                                     [v1]
predict_proba(model, df)                                               [v1]
get_model_params(model)
```

**evaluation** — 11
```
score_classification(probabilities, truth, target, threshold, average) [v1]
score_regression(predictions, truth, target)                          [v1]
compute_confusion_matrix(predictions, truth)                           [v1]
compute_roc_curve(probabilities, truth)     → curve data
compute_pr_curve(probabilities, truth)      → curve data
compute_calibration_curve(probabilities, truth, n_bins)
select_threshold(curve, objective)          F1 / cost / target recall
plot_curve(curve, title)                    → png, renders any curve artifact, incl. partial_dependence (chunk 18 D-d)
cross_validate_estimator(name, params, train, target, cv)          H
compare_models(candidates, metric)          → ranking table; refuses scores on different rows  [v1]
compute_learning_curve(name, params, train, target)                H
```

**inference** — 10 — statsmodels, D7; all called by the shared node 6 (D31)
```
fit_ols(matrix, target, predictors)                                H   [v1]  no formulas: patsy evaluates Python (chunk 15 D-a)
fit_logit(matrix, target, predictors)                              H   [v1]
fit_mnlogit(matrix, target, predictors)                            H         multinomial logit for a multiclass target (chunk 20r); base class chosen not alphabetical (D-a); errstate guard for separation-as-overflow (P3)
fit_glm(matrix, target, predictors, family)                        H         log link for both families: Gamma's canonical InversePower raises a fatal DomainWarning under -W error (chunk 15 §6, B2 review)
coefficient_table(fit, manifest, goals)     coef, SE, z/t, p, CI; joint Wald per goal, Holm (D35)  [v1]
inferential_fit_summary(fit)                R²/pseudo-R², AIC, BIC, LR
compute_vif(matrix)                         multicollinearity
test_residuals(fit, tests)                  normality, heterosk., autocorr
compute_influence(fit)                      Cook's D, leverage, DFFITS
plot_residuals(fit)                         → png
```

**interpretation** — 4 — the model knows its target (chunk 18 D-a)
```
permutation_importance(model, df, metric, n_repeats)                H
coefficient_importance(model, train)        linear models, coef × train sd, sign towards the positive class
partial_dependence(model, df, column, grid_resolution, scaler)     H   curve artifact; plot_curve renders it
analyze_errors(predictions, truth, target, by, threshold)  worst-performing segments
```

**champion & reporting** — 5 — `prune_intermediate_artifacts` and `compute_run_hash` were removed at chunk 20 (D-a): pruning is `statlab.storage.prune_intermediates`, called by the engine; the run hash is a manifest field
```
rank_models(comparison, estimators)         applies the contract's constraints  [v1]
select_champion(ranking, candidate, rationale)   validated pointer; overrides_top recorded (chunk 19 D-b)
render_metrics_table(scores, metrics)       → markdown table; refuses scores on different rows
assemble_report(title, champion, test_score, validation_table, sections,
                coefficient_table, plots)   → report/report.md; the tool writes every number and
                                              every D32c/D33/D35 statement, the LLM only narrative
export_pipeline_script(calls, target)       → pipeline.py (chunk 20)
    Source of truth: the dependency slice of `target` (the champion's test score) over
    the orchestrator-supplied tool log — every call whose output it transitively
    consumes, in log order. This is exactly the shared prefix plus the winning branch;
    other candidates, node 6, profiling and comparisons drop out by construction, so
    the interleaving from parallel execution (D29) does not matter.
    Replays each call through the in-process MCP server (same validation as the live
    run), with seeds as logged; prints the test score. ±0.005 (§14.1).
```

**artifacts & reproducibility** — 3
```
list_artifacts()                                                       [v1]
describe_artifact(artifact)                 head rows, shape, dtypes    [v1]
snapshot_run(calls)                         manifest.json: file hashes, package versions, run_hash
                                              (content hash of data + contract + normalised calls)
```

> **`health` is not a tool.** It is an unauthenticated HTTP route (`GET /health` on the same
> ASGI app, alongside `/mcp`), not an MCP tool call — a liveness probe that required an MCP
> handshake (and the bearer token that comes with it, D13) would fail for reasons unrelated to
> liveness, and `make dev` needs to poll it before an MCP client exists at all. Chunk 07
> implements it in `statlab_tools/server.py`, returning the literal string `ok` and nothing
> else. It was listed here as a `[v1]` tool through chunk 06; corrected at chunk 07.

**Tuning policy:** `tune_estimator` runs `GridSearchCV` over an agent-proposed grid with
`cv_folds` from the contract, bounded by `max_fit_seconds`. Budget exceeded → return
best-so-far and warn.

### 6.4 Walking skeleton (`[v1]` — 33 tools, both tasks)

Enough to take **either** a binary-classification **or** a regression CSV end to end: load,
profile, split (stratified or random), verify the split, drop / impute / one-hot / scale, train
three estimators, score, fit an inferential model and print a coefficient table, rank the three
models — with **zero LLM involvement**. Everything after is added against a working, tested spine.

Initial registries — three estimators per task, one inferential model per target type:

```python
ESTIMATORS = {                          # extend by adding a line
    # classification
    "logistic_regression":      (LogisticRegression,          GRID_LOGREG,  ["binary", "multiclass"]),
    "random_forest_classifier": (RandomForestClassifier,      GRID_RF,      ["binary", "multiclass"]),
    "hist_gb_classifier":       (HistGradientBoostingClassifier, GRID_HGB,  ["binary", "multiclass"]),
    # regression
    "linear_regression":        (LinearRegression,            {},           ["continuous"]),
    "random_forest_regressor":  (RandomForestRegressor,       GRID_RF,      ["continuous"]),
    "hist_gb_regressor":        (HistGradientBoostingRegressor, GRID_HGB,   ["continuous"]),
}

SPECS = {                               # statsmodels inferential track, D7
    "logit": (sm.Logit, ["binary"]),
    "ols":   (sm.OLS,   ["continuous"]),
}
```

The three per task are deliberately contrasting — one linear and interpretable, one bagged
ensemble, one boosted ensemble — so `compare_models`, `rank_models` and champion selection are
genuinely exercised rather than trivially satisfied, and the scaling/no-scaling distinction in
the two-matrix design (D7) gets tested from the start.

Covering both tasks in the skeleton means every metric, plot and diagnostic tool **branches on
task type from day one**. That is more work at M5 than classification alone, and it is the
cheaper order: retrofitting regression later would mean revisiting evaluation, interpretation and
reporting after they were written single-task.

`ridge`, `mnlogit`, `glm_poisson`, `glm_gamma`, target encoding and the temporal/grouped splits
follow at M7 as **registry lines and independent small tools, with no changes to existing tools**
— which is the point of the registry. **Shipped at M7:** `glm_poisson`/`glm_gamma` and target
encoding (chunks 15, 12), then `ridge` (an estimator registry line) and `mnlogit` (a `SPECS` line
plus `fit_mnlogit`, with multinomial support added to `coefficient_table`) in chunk 20r.
**Deferred out of v1:** `split_temporal` and `split_grouped` (chunk 20r D-c; D25) — node 4
escalates a temporal or grouped request as `data` rather than falling back to a leaky random
split. The v1 catalogue is **83 served tools** (84 − 2 deferred splits + `fit_mnlogit`).

### 6.5 Dynamic signatures — how the agent knows the tools (D23)

**Every tool has its own parameter model.** Nothing is shared beyond artifact reference types.
Parameter count, names, types, defaults, bounds and enums differ per tool, and the agent learns
them at runtime from the served schema — never from a list written into a prompt.

```python
class SplitTemporalParams(BaseModel):
    df: ArtifactRef        = Field(description="Frame to split.")
    time_column: str       = Field(description="Datetime column defining order. "
                                               "Rows are sorted ascending before splitting.")
    test_size: float       = Field(0.2, ge=0.05, le=0.4,
                                   description="Final fraction held out as test.")
    valid_size: float      = Field(0.2, ge=0.0,  le=0.4,
                                   description="Fraction before test used for validation.")

class FitScalerParams(BaseModel):
    train: ArtifactRef     = Field(description="TRAIN frame only. Fitting on any other "
                                               "split leaks information.")
    method: Literal["standard", "minmax", "robust"] = Field(
        "standard", description="robust uses median/IQR; prefer it with heavy outliers.")
    columns: list[str] | None = Field(None, description="Numeric columns; None = all numeric.")
```

Two tools, two entirely different shapes. The agent cannot confuse them because it never sees a
merged signature.

**Discovery and dispatch:**

1. **Startup** — the backend calls `tools/list` once and caches the schema registry. The
   skill-file loader validates every `Tools` entry against it and fails at startup on a typo
   (D12), so a misspelled tool is never a silent capability loss.
2. **Per node** — only the schemas of that node's allow-listed tools are injected, as part of the
   *stable* prompt prefix (§7.2), so they cost cache-hit rates rather than full input price.
3. **Decision** — the agent emits `{"tool": "...", "params": {...}}`. `params` is validated
   against **that tool's** schema before dispatch.
4. **Repair** — a validation failure returns the field-level errors to the agent for exactly one
   corrected attempt (D18), then escalates to the recovery handler as `tool_param`.
5. **Enforcement** — `mcp_client` rejects any tool outside the active node's allow-list before
   the call leaves the process (D12).

**Field descriptions are not optional.** They are the entire interface the agent reasons over —
which is why the examples above spend their descriptions on *when* to use a value and *what goes
wrong otherwise*, not on restating the type. `FitScalerParams.train` saying "fitting on any other
split leaks information" is doing more work than any prompt instruction could.

**Rule for phases not enumerated in detail:** give a strategy its **own tool** when its parameter
set or output semantics differ; use a **parameter** when only a value changes and the call shape
is identical. So splits, encoders and scalers are per-strategy tools, while imputation takes a
per-column strategy map (one frame legitimately wants median for `age` and mode for `plan`, and
eleven imputer tools would force eleven calls for one logical step).

---

## 7. Memory

Two tiers, no vector store.

- **Session memory** — the chat transcript (`messages` table), scoped to a session.
  Trimmed to a token budget with the oldest turns summarised into one system note.

  **A session belongs to a project, not a run**, and each message carries a nullable `run_id`.
  This is what makes forks (D11) coherent: rewinding creates a new run but the conversation
  continues, so the transcript spans the parent and the fork with each message tagged by the run
  it was about. Out-of-band Q&A therefore has the whole thread, and the UI can filter to one run
  when the user wants only that branch. A session with no active run is just a conversation.
- **Project memory** — durable structured facts learned across runs, e.g.
  `{"key": "target_imbalance", "value": {"positive_rate": 0.031}, "source_run": "r_04"}`
  or `{"key": "leaky_columns", "value": ["signup_ts"]}`. Read at run start, appended at
  run end (and by the agent explicitly via a `remember` call). A few dozen rows fit in the
  prompt; embedding-search over 40 rows would be theatre.

DSPy is **not** used (D18). Every LLM interaction is a pair of Pydantic models — a context
builder and a typed output schema (`SplitStrategy`, `ProfileReading`, `InterruptClass`,
`ChampionChoice`, …) — whose JSON schema is passed to the provider's structured-output
facility. Outputs are validated structs, never free text to be regex-parsed. The prompt stays
visible and debuggable, which matters for a project that has to be explained.

### 7.1 LLM layer: provider-agnostic (D18)

The default provider is DeepSeek, but **nothing in the code is DeepSeek-specific**. LiteLLM
takes a provider-prefixed model string, so switching provider is configuration:

```bash
# .env — swap any of these, no code change
STATLAB_MODEL_DEFAULT=deepseek/deepseek-flash
STATLAB_MODEL_REASONING=deepseek/deepseek-v4-pro
# STATLAB_MODEL_DEFAULT=anthropic/claude-sonnet-4-5
# STATLAB_MODEL_DEFAULT=openai/gpt-5.4
DEEPSEEK_API_KEY=...        # LiteLLM reads the key each provider expects
```

**Structured output, three tiers, capability-detected** — local prefix overrides inside `llm/`
are consulted before LiteLLM's `supports_response_schema(model)`, so a provider that cannot do strict
schemas still returns valid typed objects:

1. **Native JSON schema** — pass the Pydantic schema, provider guarantees conformance.
   (DeepSeek exposes this on the Responses API via `text.format`; OpenAI and Anthropic have
   equivalents.)
2. **JSON mode** — provider guarantees syntactic JSON only (DeepSeek Chat Completions'
   `response_format: {"type":"json_object"}`); we validate with Pydantic.
3. **Neither** — schema injected into the prompt, response parsed and validated.

All three tiers get **one repair retry**: on `ValidationError`, the errors are fed back and the
call is reissued once, then it escalates to the recovery handler as a `tool_param`-class error.
Native schemas cannot enforce every custom Pydantic validator; empty answers are validation
failures too. This is why the output models are the contract rather than the prompt text.

**Cost accounting** uses LiteLLM's `completion_cost()`, so per-provider rates are its problem,
not a table we maintain. A local override map exists only for providers it prices wrongly.
Every call logs `model`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cost_usd` to
`llm_calls`; the run total streams to the UI. **No hard cap** (D16).

#### DeepSeek specifics (default provider, verified 2026-10-02)

The legacy `deepseek-chat` / `deepseek-reasoner` names were **retired 24 July 2026** and are no
longer routed.

| Model | Cache miss (off-peak / peak), $/1M | Cache hit | Output | Context | Concurrency |
|---|---|---|---|---|---|
| `deepseek-flash` (V4.1-Flash) | 0.15 / 0.30 | 0.003 / 0.006 | 0.60 / 1.20 | 1M | 2,500 |
| `deepseek-v4-pro` | 0.66 / 1.32 | 0.022 / 0.044 | 1.98 / 3.96 | 1M | 500 |

Peak hours are 01:00–04:00 and 06:00–10:00 UTC Monday–Friday. Legacy
`deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are accepted and routed/billed as Flash;
V4-Pro remains available. Sources: [official pricing](https://api-docs.deepseek.com/quick_start/pricing/)
and [current model names](https://api-docs.deepseek.com/updates/).

Chat Completions supports [JSON mode](https://api-docs.deepseek.com/guides/json_mode/), not
strict JSON Schema: pass `response_format={"type":"json_object"}`, include the word `json`
and an example, and repair an empty answer. The local capability override therefore selects
tier 2. Strict schemas on the Responses API are outside v1's transport scope.

[Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) defaults to enabled;
the client disables it for ordinary structured extraction, and enables it with effort `high`
when routing requests reasoning. Only `content` is parsed; `reasoning_content` is not persisted.
The local Flash cost override uses peak rates as an informational upper estimate because the
installed LiteLLM map is stale; exact time-dependent billing is not a gate (D16).

DeepSeek publishes concurrency ceilings only, no RPM/TPM. A `429` is a `transient` error for the
recovery handler — cap in-flight requests, exponential back-off, queue rather than fan out.

### 7.2 Prompt ordering is architectural, not a micro-optimisation

DeepSeek caches prompt *prefixes* automatically and a cache hit costs 1/50th of a miss, so every
prompt is assembled **invariant-first**:

```
[ skill preamble ] [ node's skill section ] [ contract ] [ project memory ]   ← stable prefix
[ run state summary ] [ recent tool results ] [ the actual question ]         ← variable tail
```

Same order for every call in a run, turning most input tokens into cache hits. Getting it
backwards multiplies the input bill by ~50 for identical behaviour.

This ordering is **provider-portable but not provider-uniform**: DeepSeek caches implicitly,
whereas Anthropic requires explicit `cache_control` breakpoints. The invariant-first layout is
what makes both possible — the prefix boundary is already in the right place, so enabling
explicit caching later is a flag, not a restructure.

### 7.3 Model routing — one place to change it (D16, D18)

Nodes map to **roles**, roles map to **model strings**. That indirection is the whole point: a
provider swap changes two env vars, not eleven node entries.

```python
# statlab/llm/routing.py — the ONLY place model choice is decided.

class Role(StrEnum):
    DEFAULT   = "default"     # extraction-shaped work: fill parameters, read a table
    REASONING = "reasoning"   # multi-step judgement a human will read closely
    CLASSIFY  = "classify"    # cheapest capable model: interrupt routing, status text

MODELS: dict[Role, str] = {                    # overridden by STATLAB_MODEL_<ROLE>
    Role.DEFAULT:   "deepseek/deepseek-flash",
    Role.REASONING: "deepseek/deepseek-v4-pro",
    Role.CLASSIFY:  "deepseek/deepseek-flash",
}

NODE_ROLES: dict[str, Role] = {
    "problem_definition":   Role.REASONING,
    "model_interpretation": Role.REASONING,
    "champion_selection":   Role.REASONING,
    # everything else → Role.DEFAULT
}

def model_for(node: str) -> str: ...
```

Precedence, highest first:

1. `### Model` field in the node's skill-file section — an explicit model string or a role name
2. `STATLAB_MODEL_<ROLE>` env var (swap provider or model per role)
3. `MODELS[NODE_ROLES[node]]`
4. `MODELS[Role.DEFAULT]`

Control nodes use the same function: interrupt router and status explainer are `CLASSIFY`, while
out-of-band Q&A takes the role of the node currently executing — a question asked during
interpretation deserves the model doing the interpreting.

---

## 8. Run modes

The canonical token is **`autonomous`** in code, the database and the API; "Auto" is only its UI
label (D36a). Every "auto mode" below means `autonomous`.

| | Auto (`autonomous`) | Guided (`guided`) |
|---|---|---|
| Gates | none (except escalation) | **shared nodes 1–6 gate; the node-5 shortlist is the key decision.** Per-candidate nodes 7–11 run ungated except on exceptions (D29) |
| Agent proposes params | yes, applies immediately | yes, presented for edit/approve |
| Candidate execution | parallel | parallel (D29) |
| User can interrupt | yes (Q&A / status / rewind / lifecycle) | yes, same |
| Typical use | "just get me a comparison" | teaching, auditing, thesis demo |

Gate payload:
```json
{
  "gate_id": "g_...", "node": "data_splitting",
  "question": "Proposed split strategy",
  "proposal": {"strategy": "stratified", "test_size": 0.2, "valid_size": 0.2, "seed": 42},
  "reasoning": "Target positive rate is 3.1%; stratification keeps ...",
  "editable": {"test_size": {"type": "float", "min": 0.05, "max": 0.4}, "...": "..."},
  "default": {"action": "approve"},
  "options": ["approve", "approve_with_edits", "instruct",
              "approve_all_remaining", "skip_node", "rewind", "abort"]
}
```

Gate actions (D10):

| Action | Effect |
|---|---|
| `approve` | apply the proposal as-is, continue |
| `approve_with_edits` | apply the proposal with the user's field overrides, continue |
| `instruct` | free-text reply ("stratify, but hold out the last month as test") → agent revises the proposal and **re-gates**; the exchange is kept in the transcript |
| `approve_all_remaining` | apply, then switch this run to auto mode from here on |
| `skip_node` | mark the node skipped with the user's reason (rejected if non-skippable) |
| `rewind` | hand off to the change/rewind handler with a target node |
| `abort` | cancel the run, keep artifacts |

Gates block indefinitely and are persisted, so an unanswered gate survives a server
restart; the run resumes in `awaiting_user` and the UI re-renders the pending gate from
`gates` on reconnect.

---

## 9. The skill file

`skills/modelling.md` — one hand-authored file, version-controlled, and hashed into the run
manifest so every run records which skill version produced it (`skill.snapshot.md` is copied
into the run folder).

### 9.1 Structure

A shared **preamble** (role, hard rules, output discipline, house style for narration) followed
by one `## <node_name>` section per staging node. Each section has a fixed shape:

```markdown
## data_splitting

### Purpose
One or two sentences: what this node is responsible for deciding.

### Heuristics
- If the positive rate is below 10%, stratify and prefer PR-AUC over ROC-AUC.
- If a datetime column is the natural ordering, split temporally — never randomly.
- If rows are grouped (repeated customers), split by group or the test set leaks.

### Tools
split_random
split_stratified
split_temporal
split_grouped
verify_split

### Outputs
train / valid / test artifacts registered, split report with per-class counts per split.

### Pitfalls
- Random splitting time-ordered data inflates every downstream metric.
- Stratifying on a continuous target is a type error, not a strategy.
```

### 9.2 Loader contract

- Parses `##` headings into node sections and `###` into fields.
- **Fails at startup** (not mid-run) if: a staging node has no section, a section is missing a
  required heading, or `Tools` names a tool the MCP server does not expose, **or names a tool
  whose `allow_in` metadata (served in `Tool.meta`) excludes that node**. A typo in a tool
  name is a startup error, never a silent capability loss — and so is listing a real tool under the
  wrong node, which an existence check alone would pass.
- Hands each node *only* its own section plus the preamble — the graph is the plan, the skill
  file is the policy, and no node sees another node's instructions.
- `Tools` is parsed into a set and passed to `mcp_client` as the active allow-list; a call to
  anything outside it raises before reaching the server.

### 9.3 Authoring plan

The coding agent scaffolds `skills/modelling.md` with every required heading present and
`TODO:` placeholders for `Heuristics` and `Pitfalls` — enough to pass the loader and run
end-to-end, with the agent falling back on its own judgement where heuristics are absent.
The domain content is then written by hand, node by node, once the nodes are implemented
and their real decision points are visible. **This is deliberately the last content task,
not the first**: heuristics written before the nodes exist would be guesses about an
interface that doesn't exist yet.

**Split responsibility, because the two halves have different risk.** The `Tools` sections are
*not* placeholder material — they are validated against the live registry at startup (§9.2), so a
wrong tool name is a crash, not a stub. Therefore:

- **M9 sub-step, gated:** scaffold all 13 sections with **correct, registry-validated `Tools`
  lists** and `TODO:` heuristics. Its check is that the loader starts clean against the real
  server. This is where a phantom tool name like `split_data` would be caught.
- **Human, after M9:** replace the `TODO:` heuristics and pitfalls with real domain knowledge.

Per-project skill overrides are a later addition, out of scope for v1.

---

## 10. Persistence

### 10.1 Filesystem

```
projects/<project_slug>/
├── project.json
├── datasets/           <sha256>.csv -- the original upload, nothing else (chunk 08 D-a: the
│                       │  run's `raw` artifact is already parquet, so a second
│                       │  `<sha256>.parquet` cache has no consumer and was dropped)
├── contracts/          contract_v1.json ...
└── runs/<run_id>/
    ├── state.json              # checkpoint, rewritten after each node
    ├── events.jsonl            # append-only event log (SSE replay source)
    ├── contract.snapshot.json   # as resolved at run start (uploaded or elicited)
    ├── skill.snapshot.md
    ├── nodes/NN_<node>/        # shared nodes (1-6, 12-13): decision, result, tool_calls
    ├── candidates/<cand_id>/   # per-candidate nodes 7-11 (D26)
    │   ├── candidate.json      #   spec + rationale + varies_from_baseline
    │   ├── nodes/NN_<node>/
    │   └── artifacts/
    ├── comparison.json         # ranking across candidates, validation metrics
    ├── artifacts/              # <id>.parquet | .joblib | .json | .png
    │                           #   hard-linked from the parent run up to the fork
    │                           #   point (D11); immutable, so sharing inodes is safe
    │                           #   within-node intermediates pruned on node success (D24)
    ├── report/                 # report.md, plots/
    ├── pipeline.py             # generated reproducibility script (D2)
    └── manifest.json           # hashes, versions, env, timings
```

### 10.2 SQLite (`statlab.db`)

Opened with `journal_mode=WAL` and `busy_timeout=5000` (D17) — required, not optional, once
runs execute concurrently.

```
projects(id, slug, name, created_at)
datasets(id, project_id, filename, path, sha256, n_rows, n_cols, created_at)
contracts(id, project_id, path, json, version, created_at)
runs(id, project_id, contract_id, mode, status, current_node,
     champion_model_id, forked_from_run_id, started_at, ended_at, config_json,
     test_scored_at, test_fingerprint)
sessions(id, project_id, title, created_at)
messages(id, session_id, run_id, role, content, meta_json, ts)
candidates(id, run_id, label, rationale, spec_json, varies_from_baseline_json,
           wave, status, rank, created_at)
node_executions(id, run_id, candidate_id, node_name, seq, attempt, status,
                summary, error, concurrency_at_start, started_at, ended_at)
tool_calls(id, run_id, node_execution_id, tool_name, args_json,
           result_json, duration_ms, ts)
artifacts(id, run_id, kind, path, meta_json, produced_by, ts, pruned_at, split, derived_from_test)
models(id, run_id, candidate_id, name, params_json, artifact_id, created_at)
metrics(id, run_id, candidate_id, model_id, split, name, value)
gates(id, run_id, node_execution_id, question_json, answer_json, status, ts)
llm_calls(id, run_id, node_execution_id, signature, model, prompt_tokens,
          completion_tokens, cached_tokens, cost_usd, duration_ms, ts)
memory(id, project_id, key, value_json, source_run_id, ts)
```

`status` enums: `runs.status` ∈ {pending, running, awaiting_user, paused, failed, cancelled,
completed, superseded} (`superseded`: halted by a rewind fork, D36b); `node_executions.status` ∈ {pending, running, awaiting_user, skipped, failed,
completed}.

**Two node vocabularies, deliberately.** `node_executions.status` is a *row lifecycle* and needs
`pending` and `running`, because `node.started` (§11.2) fires before any outcome exists.
`NodeResult.status` (§4.3.1) is a *terminal outcome* and is the four-member subset
{completed, skipped, failed, awaiting_user} — a `NodeResult` cannot carry `running`, since it only
exists once the node has stopped, and its validators (`skip_reason` iff skipped, `error` iff
failed) have no meaning for a live row. The schema type is derived from the outcome type
(`NodeExecutionStatus = PreTerminalStatus | NodeStatus`), not restated, so the two cannot drift.

**`runs.test_scored_at` is how D26's "scored on test exactly once" survives a crash.** The
guarantee is a durability requirement, so it cannot live in `mcp_client`'s process memory — a crash
is precisely the failure mode it exists to prevent. It is enforced by one atomic compare-and-set,
`UPDATE runs SET test_scored_at = ? WHERE id = ? AND test_scored_at IS NULL`, where `rowcount == 1`
means the caller won and may proceed. Verified: 16 concurrent claimants produce exactly one winner,
and a claim after reopening the database is refused. `mcp_client` remains the enforcement point
(D30(b)); it just consults a durable column instead of a flag. No `locked` column and no artifact
registry lock is needed — refusing a test read *before* node 12 is already derivable from
`runs.champion_model_id IS NULL`.

**Two SQLite NULL traps this schema must not fall into.** A non-INTEGER `PRIMARY KEY` accepts NULL
unless `NOT NULL` is declared, and a composite `UNIQUE` spanning a nullable column enforces nothing
on rows where that column is NULL, since SQL treats every NULL as distinct. The latter would make
`UNIQUE(run_id, candidate_id, node_name, attempt)` decorative for all eight SHARED nodes, which have
no candidate — so it is declared as
`CREATE UNIQUE INDEX ... ON node_executions(run_id, IFNULL(candidate_id, ''), node_name, attempt)`.
Both were verified empirically, in both the broken and fixed forms.

Cancellation of a *running* node is deliberately not a seventh state: chunk 31 resolves a
cancelled run's live rows to `failed` with the cancellation as the error, which needs no new
vocabulary.

---

## 11. API and wire protocol

### REST
```
POST   /api/projects                        create
GET    /api/projects                        list
GET    /api/projects/{id}                   detail + runs + memory
POST   /api/projects/{id}/datasets          multipart upload, one CSV, ≤200 MB (D15)
POST   /api/projects/{id}/contracts         upload+validate JSON (optional)
POST   /api/projects/{id}/contracts/propose agent drafts a full contract from the CSV
POST   /api/projects/{id}/runs              {mode, dataset_ids, contract_id} → run_id
GET    /api/runs/{id}                       state snapshot + graph status
GET    /api/runs/{id}/events                SSE stream (?since=<seq> to replay)
POST   /api/runs/{id}/messages              user chat / interrupt
POST   /api/runs/{id}/gates/{gate_id}       {action, edits}
POST   /api/runs/{id}/control               {action: pause|resume|cancel|rewind, ...}
                                            rewind → 201 {new_run_id} (D11)
GET    /api/runs/{id}/artifacts             list
GET    /api/artifacts/{id}                  download / preview (head rows, png)
GET    /api/runs/{id}/report                rendered report
```

### SSE events
```
run.status  node.started  node.decision  node.completed  node.failed  node.skipped
tool.called  tool.result  gate.opened  gate.closed
message.assistant.delta  message.assistant.complete  message.user
artifact.created  metric.recorded  error
```

### 11.1 Stream contract (D9)

Every frame is one envelope, so the frontend has a single parser:

```jsonc
{ "seq": 412, "run_id": "r_...", "ts": "2026-09-09T17:04:11Z",
  "type": "tool.result",
  "payload": { "tool": "split_stratified", "duration_ms": 84,
               "summary": { "train": 3164, "valid": 678, "test": 679 } } }
```

- **`seq`** is monotonic per run and assigned at append time to `events.jsonl`. The stream
  is a *tail of that file*, not a parallel channel — so what the browser sees and what
  disk records can never diverge.
- **Reconnect**: `GET /api/runs/{id}/events?since=412` replays 413…N then continues live.
  `since` ≥ the current maximum replays nothing and tails live; `since` is never an error.
- **Ordering**: `seq` is one total order per run. Events from parallel candidates interleave in
  it, so per-candidate ordering comes from filtering on `payload.candidate_id`, not from gaps in
  `seq`.
  `EventSource` reconnects on its own; the client just remembers its last `seq`.
- **Heartbeat**: an SSE comment every 15 s keeps proxies from idling the connection out.
- **Prose vs structure**: `message.assistant.delta` carries token chunks for narration and
  chat; `message.assistant.complete` carries the final text and is what gets persisted to
  `messages`. Structured payloads (`node.decision`, `gate.opened`) are emitted once, whole,
  after validation.
- **Late join**: opening the run page mid-run replays from `seq=0`, so the graph, chat and
  metrics rebuild to current state with no special-casing.

---

### 11.2 Auth and exposure (D14)

| Service | Bind | Auth |
|---|---|---|
| Web API (`uvicorn`) | `127.0.0.1:8000` | none — single user, local only |
| MCP server | `127.0.0.1:8765` | `Authorization: Bearer <STATLAB_MCP_TOKEN>` |
| Vite dev server | `127.0.0.1:5173` | none, proxies `/api` to the backend |

The MCP server is the one component that *must* be authenticated even locally: it writes to
the filesystem and starts model fits, so an unauthenticated open port is an abuse surface
regardless of who is on the machine. The web API is left unauthenticated deliberately — with
no user model, a password would be theatre; loopback binding is the actual control.

Secrets (`DEEPSEEK_API_KEY`, `STATLAB_MCP_TOKEN`) live in `.env`, are read via settings, and
are never written into run folders, logs or the event stream. `manifest.json` records the
*model name*, never the key.

---

## 12. Frontend

1. **Project picker** — list existing (name, #runs, best score, last activity) + "New".
2. **Setup wizard** — upload one CSV → *optionally* upload a JSON config, or let the agent
   draft one (profile → proposal → editable column-mapping table → confirm)
   → validation preview → mode toggle (auto/guided) → Start.
3. **Run workspace** — three panes:
   - left: the node graph — shared nodes 1–6 as a spine (with node 6's inference track shown as
     its own branch), then **candidates as parallel columns** through nodes 7–11, converging on
     champion selection; per-node status/timing, click to
     inspect decision + tool calls;
   - centre: chat (agent narration, gate prompts as inline forms with editable params,
     user messages);
   - right: artifacts & live metrics, with a **candidate comparison table** that fills in as
     each candidate finishes — sortable by metric, showing `varies_from_baseline` so the reason
     one candidate beats another is legible.
   Top bar: pause / resume / cancel / rewind-to-node, live cost.
4. **Report viewer** — rendered `report.md` with plots, champion card, download
   (`pipeline.py`, model, manifest).
5. **Run history** — runs of a project as a **lineage tree** (forks indented under their
   parent with the fork node labelled), side-by-side metric comparison across any selected
   runs. This is where D11 pays off: a fork and its parent differ by exactly one decision,
   so the comparison is directly interpretable.

Stack: React + TypeScript + Vite, plain `fetch` + `EventSource`, TanStack Query for REST
cache. Charts: recharts for metrics, server-rendered matplotlib PNGs for statistical plots
(no need to reimplement ROC curves in JS).

---

## 13. Milestones

> **Every milestone's "Done when" implicitly includes: `make test` is green — all tests from all
> prior milestones still pass, not just the new demo.** A milestone that regresses an earlier one
> is not done. The demo in the table is the *additional* proof, never the whole gate.

| M | Deliverable | Done when |
|---|---|---|
| **M0** | Env verification, **pinned dependency set**, skeleton repo, `statlab.db` schema (WAL) | `pytest` green on an empty suite; every dep imports on the chosen interpreter; exact versions committed to `pyproject.toml` (§2.1) |
| **M0.5** | **Interface contracts, frozen before any implementation** — the success/error envelope and `error_class` taxonomy (§6.1.1), `ArtifactRef`, `NodeSpec`, `NodeResult`, `GatePolicy` (§4.3.1), `CandidateSpec` + spec-hash canonicalisation (§4.2) | the types exist with tests asserting envelope round-trips, `error_class` exhaustiveness, and that two candidates differing only in key order hash identically |
| **M1** | **MCP server foundation** — Streamable HTTP transport, bearer auth, `@tool` decorator, artifact store, registries, fit semaphore, `health` / `list_artifacts` / `describe_artifact` | starts via `python -m statlab_tools`; an **external MCP client** completes `tools/list` and a round-trip call; a tokenless request is rejected |
| **M2** | `DataContract` Pydantic schema (§4.1) + **intake** tools | contract tests pass; mismatches produce precise errors; a CSV loads to an artifact |
| **M3** | **profiling** + **splitting** tools | every tool tested on fixtures; `verify_split` catches a deliberately overlapping split |
| **M4** | **cleaning** + **encoding** tools | `fit_*` on train / `apply_*` on valid+test round-trips; a fit-on-test attempt is visible in the call log |
| **M5** | **training** + **evaluation** tools, **plus `rank_models` and `compare_models`** (needed by the skeleton's ranking step even though `rank_models` lives in the champion phase) | every training and evaluation tool tested on fixtures; **all M0–M4 tests still pass** |
| **M6** | **inference** + **inference_matrix** tools (D7, D31, D35), then the **walking skeleton** | `logit` and `ols` both fit on a canonical matrix; **known-answer inference test green**; **walking skeleton (§6.4) runs end to end with zero LLM involvement on BOTH a classification and a regression fixture**, 3 estimators each, producing real metrics, *a ranking* and one coefficient table. The skeleton fits an inferential model, so it cannot precede the inference tools (review 2026-10-01) |
| **M7a** | **features** + **interpretation** tools | every tool tested on fixtures; compute/render separation honoured (`partial_dependence` returns curve data, `plot_curve` renders it — chunk 18 D-d); **all prior tests pass** |
| **M7b** | **champion & reporting** + **artifacts** tools, incl. `export_pipeline_script` | full 84-tool catalogue tested; **`pipeline.py` reproduces the champion's primary metric within the §14.1 tolerance** from a fixed seed; **all prior tests pass** |
| **M8a** | Graph engine core: `RunState`, checkpointing, **declared `NodeSpec` spine (D27)**, single-candidate auto mode, restart reconciliation (§5.1) | a one-candidate auto run completes driving real tools; `state.json` replayable; a synthetic 3-node domain runs on the same engine with no engine edits; a killed mid-node run resumes from its last node boundary; **node 6 runs concurrently with the fan-out without touching candidate state** |
| **M8b** | **Candidate fan-out (D26)** + parallel execution (D29) + **test-set lock enforcement** | auto run completes across ≥3 candidates in parallel; all candidates receive byte-identical splits; **a `test`-artifact read before champion selection is refused by `mcp_client` before dispatch**; champion scored on test exactly once |
| **M9** | LiteLLM client (structured output + repair retry + cost), routing, **skill-file scaffold with registry-validated `Tools` (§9.3)**, per-node decision schemas | agent-decided auto run completes; decisions logged; loader starts clean against the live registry; **provider portability proven in CI by the grep assertion, `model_for()` precedence tests and a recorded-fixture round-trip**; a live run against a second provider is an optional smoke test behind an env var, since §14.2 bans live LLM calls in CI |
| **M10** | Control layer: router, Q&A, status, recovery/retry, lifecycle | injected failures recover; pause/resume/cancel verified |
| **M11** | HITL gates + guided mode + fork-based rewind | guided run gates the node-5 shortlist and exceptions only (D29); all 7 gate actions work; rewind to node 4 produces a fork whose parent is unchanged and whose shared artifacts are hard-linked |
| **M12** | FastAPI surface + SSE + event replay | curl-driven full run, reconnect loses nothing |
| **M13** | React UI: all five screens | click-through run start → watch → gate → report |

Ordering rationale: **the MCP server is built and fully tested before any agent exists**
(M1–M7). By M5 there is a working ML pipeline with zero LLM involvement, which is what makes
the agent layer debuggable later — a bad result is then unambiguously a bad *decision* rather
than a broken tool. Every milestone is demoable.

---

## 14. Testing

### 14.1 Tolerances — no test may use an unstated one

| Test | Tolerance |
|---|---|
| Inference known-answer, coefficients | recovered within **±0.15 absolute** of planted values |
| Inference known-answer, significance | `p < α` for real predictors, `p > α` for the noise column, `α = 0.05` |
| Inference known-answer, collinearity | `VIF > 5` flags the planted collinear pair |
| `pipeline.py` reproduction | champion primary metric within **±0.005 absolute** of the recorded value, fixed seed |
| End-to-end run | same **±0.005** |
| Split balance drift | class proportion per split within **±0.02** of the whole frame |

Any test needing a looser tolerance must justify it in its docstring. "Within tolerance" without
a number is not a test.

### 14.2 Determinism rules

Tests must not depend on timing or core count:

- **Seeds fixed everywhere.** Every fixture, split, and estimator takes an explicit seed.
- **The fit semaphore is injected, not discovered.** Concurrency tests construct the server with
  `permits=2` rather than `os.cpu_count()`, and use a **fixture tool that blocks on an
  `asyncio.Event`** the test controls. The assertion is then deterministic: with `permits=2` and
  three blocked fits, exactly two enter and the third is queued until the test releases one. Never
  assert on wall-clock or on "observed contention".
- **No live LLM calls in CI.** Recorded-response fixtures; one optional live smoke test behind an
  env var.
- **Parallel-candidate tests assert ordering-independent outcomes only** — final metrics and
  artifact sets, never `duration_ms` comparisons (D29 makes those incomparable by design).

### 14.3 Suite

- **Per tool**: one test on a small synthetic fixture (deterministic seed) asserting shape
  + key summary fields. Fixtures: a binary-classification frame with missing values,
  mixed dtypes, an ID column and a deliberately leaky column; a regression frame.
- **Inference cardinality (D31)**: a run with 3 candidates produces **exactly one** coefficient
  table and one inferential fit artifact; node 6's matrix is unscaled and its dummy columns carry
  explicit reference levels; node 6 completing does not depend on any candidate.
- **Interface contracts (M0.5)**: success and error envelopes round-trip through Pydantic;
  `error_class` is exhaustive over the recovery handler's branches; a `tool_param` error always
  carries `field_errors` and other classes never do; two `CandidateSpec`s differing only in dict
  key order or in `rationale` produce the same `spec_hash`.
- **Restart reconciliation (§5.1)**: a run killed mid-node is rehydrated, its interrupted node
  marked `failed`, and execution resumes from the **last completed node boundary** with the
  crashed attempt's intermediates still present; an `awaiting_user` run comes back dormant with
  its gate re-published.
- **Contract precedence (§4.1)**: a contract `delimiter` that disagrees with the file causes a
  `data`-class escalation, **not** a silent retry with a guessed delimiter.
- **Tool schemas (D23)**: every registered tool exposes a JSON Schema through `tools/list`;
  **every field has a non-empty description** and every constrained choice is a `Literal`/enum
  (asserted by iterating the registry — this is what keeps the agent's only documentation from
  rotting); invalid params are rejected before dispatch with field-level errors.
- **MCP transport (D13)**: one integration test that boots the server on an ephemeral port
  and drives it with a real MCP client — `tools/list` matches the registry, a call round-trips,
  and a request without the bearer token is rejected. Per-tool unit tests call the tool
  functions directly (no server) so the suite stays fast.
- **Contract**: valid, missing target, unknown column, dtype conflict, bad ratios.
- **Inference (D7), known-answer**: a fixture with a *planted* linear relationship
  (`y = 3·x1 − 2·x2 + noise`) plus a pure-noise column and a deliberately collinear pair.
  Asserts coefficients recovered within tolerance, p-value below α for the real predictors
  and above for the noise column, and VIF flagging the collinear pair. This is the one test
  that catches a silently wrong inferential pipeline — metric-based tests cannot.
- **Candidates & comparison (D26)**: all candidates receive byte-identical train/valid/test
  artifacts; **any tool call reading the `test` artifact before node 12 with a champion chosen is
  rejected**; the champion is scored on test exactly once (assert one `test`-split metric row **and that its `model_id` is `runs.champion_model_id`**); a test-derived artifact (e.g. imputed test) is refused by every `fit_*` tool and by every tool before node 12 (D32); a fork that re-scores a fingerprinted test set reports the earlier scoring;
  `max_candidates` and `max_search_waves` are respected; **candidates advance in parallel and one
  candidate failing does not abort its siblings** (D29).
- **SSE (§11.1)**: append N events with interleaved candidate ids, reconnect at `since=k`, assert
  exactly `k+1…N` replayed in order then live; `since` beyond the maximum tails without error.
- **Domain seam (D27)**: no node name appears outside `domains/` (grep assertion); a synthetic
  second domain with a 3-node spine executes end to end on the same engine without engine edits.
- **Graph engine**: checkpoint/resume round-trip; **fork leaves the parent run byte-identical
  while the child contains no post-fork artifacts, and shared artifacts have the same inode**;
  the retune loop respects the cap; skip rules enforced.
- **Artifact retention (D24)**: after a node succeeds its within-node intermediates are gone
  while the boundary artifact remains; after a node **fails** every intermediate survives;
  `STATLAB_KEEP_INTERMEDIATES=true` disables pruning.
- **Concurrency (D17)**: three runs started together all reach `completed`, their artifacts do
  not collide, and no `database is locked` is raised. The semaphore is tested separately per
  §14.2 — `permits=2`, three event-blocked fixture fits, assert exactly two admitted and the
  third queued. No timing assertions.
- **Control layer**: router classification on a fixed message set; recovery handler on
  injected transient/param/data errors.
- **Skill file (D12)**: loader rejects a missing node section, a missing required heading, and
  a `Tools` entry the MCP server does not expose; the allow-list actually blocks an
  out-of-scope call (assert `train_estimator` raises when the active node is `data_profiling`).
- **LLM-dependent paths**: recorded-response fixtures (no live calls in CI). One optional
  live smoke test behind an env var.
- **LLM layer (D18)**: `model_for()` honours all four precedence levels; a malformed model
  response triggers exactly one repair retry then escalates; **no module outside `llm/` mentions
  a provider or model name** (assert by grepping the package — this is what keeps the provider
  swappable rather than nominally swappable).
- **End-to-end**: auto-mode run on the synthetic frame → champion exists, report written,
  `pipeline.py` executes and reproduces metrics within tolerance.

---

## 15. Open questions

Resolved answers get folded into the sections above; the question stays with its answer
recorded so the reasoning is not lost.

| Q | Question | Status |
|---|---|---|
| **Q1** | Prediction only, or also classical **statistical inference**? | **RESOLVED = b.** Both tracks. `statsmodels` added; see **D7**, the two-matrix table in §4, the `inference` tool group in §6, and the known-answer test in §14. Champion is still chosen on predictive performance. |
| **Q2** | Multiple datasets/configs per run? | **RESOLVED.** Exactly one CSV + at most one JSON config per run. Config is **optional**; if absent or partial the agent drafts and elicits the rest. Joins and multi-dataset fan-out are out of scope. See **D5** and §4.1. |
| **Q2b** | Auto mode vs. missing config: where does elicitation happen, what is the minimum input? | **RESOLVED = a.** Elicitation in the setup wizard, pre-run. `target.column` mandatory at run creation, everything else inferable. See **D8**. |
| **Q4** | Transport and streaming granularity? | **RESOLVED = a.** Persistent SSE down + REST up, token streaming for prose only. See **D9** and §11.1. |
| **Q3** | Guided-mode gate granularity and timeout? | **RESOLVED = c + iii.** Every non-skipped node gates, with defaults, `instruct` and `approve_all_remaining`; blocks indefinitely, survives restart. See **D10** and §8. |
| **Q9** | Rewind: in place or fork? | **RESOLVED = b.** Fork with hard-linked artifacts. See **D11**, §5, §10, §12.5. |
| **Q8** | Skill file layout, authorship, format? | **RESOLVED = a + i.** One hand-written `skills/modelling.md`, fixed section shape, loader validates at startup, tool allow-list enforced in code. Agent scaffolds headings with `TODO`s; heuristics written by hand **after** the nodes exist. See **D12** and §9. |
| **Q12** | MCP transport? | **RESOLVED = b.** Standalone MCP server over Streamable HTTP on its own port, bearer-token gated, loopback-bound, usable by any MCP client. See **D13**. |
| **Q5** | Auth and network exposure? | **RESOLVED = a + i.** Loopback-only everywhere, no login, MCP server bearer-token gated, no user columns. See **D14** and §11.2. |
| **Q6** | Dataset size ceiling and sampling? | **RESOLVED = a.** In-memory pandas, 200 MB upload cap, 200k-row warning, never sampled. See **D15**. |
| **Q7** | Model routing and budget? | **RESOLVED = b + i.** Flash default, Pro for 3 reasoning nodes, all routing in one dict with a 4-level precedence chain; cost logged and displayed, never capped. See **D16** and §7.3. |
| **Q13** | Concurrent runs? | **RESOLVED = c.** Unbounded parallel runs, protected by a global fit semaphore, SQLite WAL, and recorded `concurrency_at_start`. See **D17** and §3.3. |
| **Q15** | Mandated libraries, deadline, written component? | **RESOLVED.** Nothing mandated, no deadline. Consequence: DSPy dropped and the LLM layer made provider-agnostic — see **D18** and §7.1–7.3. Milestones stay as planned since there is no date to cut scope against. |
| **Q10** | Report format? | **RESOLVED = a.** Markdown + PNGs, no PDF toolchain; HTML export deferred. See **D19**. |
| **Q11** | Dev ergonomics? | **RESOLVED = a.** Makefile, three plain processes, no Docker, fail-fast on missing secrets. See **D20**. |
| **Q14** | UI and agent-output language? | **RESOLVED = a + i.** English throughout, no i18n. See **D21**. |
| **Q16** | Granularity boundary: strategy as its own tool vs a parameter? | **RESOLVED = a, via fully dynamic per-tool schemas.** Every tool has its own Pydantic param model served through `tools/list`; the agent discovers names/types/defaults/bounds/enums at runtime. Mandatory field descriptions. See **D23** and §6.5. |
| **Q17** | Intermediate artifact retention? | **RESOLVED = b.** Prune within-node intermediates on success only, keep node boundaries, never prune on failure, debug flag to keep all. See **D24**. |
| **Q18** | Walking-skeleton scope and registry size? | **RESOLVED.** Both tasks, 3 estimators each, `logit` + `ols`, 2 split strategies, 33 tools. See **D25** and §6.4. |

| **Q19** | Where is the candidate set designed? | **RESOLVED = a.** A dedicated staging node, `experiment_design` (node 5), producing a **reasoned 3–5 candidate shortlist justified by EDA findings**, not a grid. Mandatory baseline, dedupe, family cap. See **D28** and §4.2. |
| **Q20** | Candidate parallelism and gating? | **RESOLVED = b + i, with iii.** Candidates run in parallel; guided mode gates the node-5 shortlist plus exceptions only. Amends D10. See **D29** and §8. |

| **Q21** | Is the inferential fit per-run or per-candidate? | **RESOLVED = a.** Per-run, in its own SHARED node 6 `inferential_analysis`; spine is 13 nodes; exactly one coefficient table per run. See **D31**. |

**All 21 questions resolved. 31 decisions locked.** Status: **COMPLETE v1.7** — ready for hand-off.

An external audit of v1.5 found two internal contradictions (a phantom `split_data` tool, node
renumbering not propagated to §3/§10), one core guarantee specified as an outcome with no
mechanism (the test-set lock), and milestone gates that were demo-only. All are fixed above:
§4's normative spine table, §4.2's lock mechanism, §4.3.1's frozen interfaces, §6.1.1's error
envelope, §5.1's restart reconciliation, §14.1's tolerances, §14.2's determinism rules, M0.5,
and the M7a/M7b + M8a/M8b splits. Q21 is the one item the audit raised that is a genuine design
fork rather than a defect.

---

## 16. Hand-off prompt

The prompt below is the hand-off to a coding agent. It deliberately does **not** restate this
document — it points at it, because a 900-line paste would drift from the source the moment
either changed.

````text
Build the application specified in PLAN.md in this repository. Read PLAN.md in full
before writing any code; it is the specification and it is complete — 29 locked
decisions (D1–D31) and 21 resolved questions. Do not re-litigate decided points.

Read §4's normative spine table first — it supersedes any node numbering elsewhere in the
document. Read §4.3.1 (frozen interfaces) and §6.1.1 (result envelope) before writing any
tool or engine code; M0.5 exists to freeze those types before three modules invent them
separately.

WHAT IT IS
A local web app that is a generic interface over MCP tool phases. Its first domain is
tabular statistical modelling. An LLM agent DESIGNS AND EXECUTES A COMPARISON of several
approaches on one uploaded CSV — several models AND several methodologies (encodings,
feature strategies, preprocessing variants) — then ranks them and picks a champion.

13 staging nodes. Nodes 1-6 are SHARED (run once): intake, problem definition, profiling
(EDA), splitting, experiment design, inferential analysis. Nodes 7-11 run ONCE PER
CANDIDATE: preprocessing, feature engineering, training, evaluation, interpretation.
Nodes 12-13 are SHARED: champion selection, artifact persistence.

Node 6 (inferential_analysis) fits the statsmodels track ONCE PER RUN on its own canonical
unscaled matrix — never per candidate (D31). A coefficient's significance depends on what
else is in the model, so N tables from N feature sets are not comparable. Exactly one
coefficient table per run. Node 6 depends only on nodes 1-4 and may run concurrently with
the candidate fan-out. Plus 7 control nodes (interrupt router,
out-of-band Q&A, status explainer, HITL gate, change/rewind, cancel/pause/resume,
recovery/retry) and auto/guided modes. Python + FastAPI backend, React + TypeScript
frontend, a standalone MCP server holding all computation.

Node 5 (experiment_design) is the point of the app: the agent proposes a REASONED
SHORTLIST of 3-5 candidates, each justified by a specific EDA finding, including a
mandatory cheap interpretable baseline. It is NOT a grid search over the cross-product —
that is what AutoML does and it buys nothing but compute.

EIGHT INVARIANTS — violating any of these is a defect, not a style choice
1. Data never enters the LLM context. MCP tools exchange artifact IDs and small JSON
   summaries; dataframes live on disk as parquet and are read by tools themselves.
2. All computation lives in MCP tools. The orchestrator never touches pandas or sklearn.
   No agent-authored code execution (D2).
3. Prediction and inference are two tracks with two feature matrices (§4, D7). The
   inferential matrix keeps original units — scaling it destroys coefficient meaning.
4. No module outside backend/statlab/llm/ names a provider or model (D18). Tested.
5. One run = one reproducible path. Rewind forks (D11); it never mutates history.
6. THE SPLIT IS SHARED across all candidates (D26). Comparing models fitted on different
   splits measures the split, not the model.
7. THE TEST ARTIFACT IS LOCKED until node 12 with a champion chosen, and the champion is
   scored on it exactly once (D26). Enforce in code, not by instruction — with N
   candidates, peeking at test is how selection becomes overfitting to test.
8. No node name appears outside backend/statlab/domains/ (D27). Tested. The engine only
   knows NodeSpec, which is what makes a second domain (time series, clustering,
   survival) additive rather than a rewrite.

BUILD ORDER — M0 through M13 in §13. Do not skip ahead; each milestone has a
done-criterion and must be demonstrable before the next begins. The MCP server is built
and fully tested FIRST (M1–M7), before any agent code exists.
  M0  Task 0 FIRST: verify litellm, mcp, pydantic, scikit-learn, statsmodels, pandas,
      pyarrow all install on Python 3.14. statsmodels is the canary (compiled
      extensions). If any wheel is missing, drop requires-python to 3.12 and say so.
      Then skeleton repo, SQLite schema (WAL + busy_timeout=5000), empty test suite green.
  M0.5 FREEZE INTERFACE CONTRACTS before any implementation: the success/error envelope and
      error_class taxonomy (§6.1.1), ArtifactRef, NodeSpec, NodeResult, GatePolicy (§4.3.1),
      CandidateSpec + spec-hash canonicalisation (§4.2). Tests: envelope round-trip,
      error_class exhaustiveness, key-order-independent spec hash.
  M1  MCP server foundation: Streamable HTTP transport, bearer auth, the @tool decorator
      (registration, Pydantic validation, artifact resolution, result envelope, timing,
      fit semaphore), artifact store, registries, health/list_artifacts/describe_artifact.
      Done when an EXTERNAL MCP client — not your backend — completes tools/list and a
      round-trip call, and a tokenless request is rejected.
  M2  DataContract Pydantic model (§4.1, every field optional except target.column) +
      the 5 intake tools.
  M3  profiling (9) + splitting (5) tools. verify_split must catch a deliberately
      overlapping split.
  M4  cleaning (10) + encoding (8) tools. fit_* on train / apply_* on valid+test.
  M5  training (6) + evaluation (11) tools. DONE WHEN THE WALKING SKELETON (§6.4, 33
      tools) RUNS END TO END WITH ZERO LLM INVOLVEMENT on BOTH a classification and a
      regression fixture, WITH ZERO LLM INVOLVEMENT, producing real metrics.
  M6  inference (9) + inference_matrix (2) tools. Known-answer inference test green.
  M7a features + interpretation tools; compute/render separation honoured.
  M7b champion & reporting + artifacts tools incl. export_pipeline_script. It emits the
      CHAMPION'S PATH ONLY: the dependency slice of the champion's test score over the
      tool log (chunk 20 D-c), so parallel interleaving is irrelevant.
      pipeline.py must reproduce the champion primary metric within +/-0.005 (§14.1).
  M8a Graph engine core: RunState, checkpointing, the DECLARED NodeSpec spine (D27),
      single-candidate auto mode, RESTART RECONCILIATION (§5.1: resume at node boundary,
      never mid-node, because tools are not transactional).
  M8b Candidate fan-out (D26) + parallel execution (D29) + TEST-SET LOCK enforcement. The
      lock lives in mcp_client via a locked:true registry flag, NOT in the stateless MCP
      server (§4.2 rule 2). Assert the refusal happens before dispatch.
  M9  LiteLLM client (3-tier structured output + one repair retry + cost logging),
      role-based routing (§7.3), skill-file loader with startup validation.
  M10 Control layer: router, Q&A, status explainer, recovery/retry, lifecycle.
  M11 HITL gates, guided mode, all 7 gate actions, fork-based rewind.
  M12 FastAPI + SSE (§11.1: envelope with monotonic seq, tail of events.jsonl,
      ?since= replay, 15s heartbeat).
  M13 React UI: project picker, setup wizard, run workspace (graph + chat + artifacts),
      report viewer, run history as lineage tree.

MCP TOOL RULES (§6.1, §6.5, D22–D25) — these are what make the catalogue correct
- One tool, one responsibility. No mega-tool with a mode parameter branching into five
  behaviours.
- Stateless. All state is artifacts; tools take artifact IDs + scalars, return new IDs.
- Immutable. Never mutate an input artifact.
- fit and apply are SEPARATE tools for anything learned from data. This is what makes
  train-only fitting auditable.
- Compute and render are separate: compute_roc_curve returns data, plot_curve makes a PNG.
- Uniform envelope: {ok, summary, artifacts, warnings}. summary is small and LLM-readable
  and never contains raw data.
- FULLY DYNAMIC SIGNATURES (D23): every tool has its OWN Pydantic param model — different
  parameter counts, names and types per tool. No shared param bag, no generic dict, no
  mutually-exclusive optionals. Served through tools/list so the agent discovers them at
  runtime. EVERY field needs a non-empty description and every constrained choice must be
  a Literal/enum; the schema is the agent's only documentation and there is a test that
  asserts this. Validate params against the tool's own schema BEFORE dispatch; a violation
  is repairable feedback, not a runtime error.
- Rule for phases not enumerated: give a strategy its own tool when its parameter set or
  output semantics differ; use a parameter when only a value changes and the call shape is
  identical. (So splits/encoders/scalers are per-strategy tools; imputation takes a
  per-column strategy map.)
- ARTIFACT RETENTION (D24): prune within-node intermediates when a node SUCCEEDS, keep
  node-boundary artifacts, NEVER prune on a failed node, and honour
  STATLAB_KEEP_INTERMEDIATES=true to disable pruning.
- Registration is via the @tool decorator; there is no central list to update.
- Start with the §6.4 walking skeleton: 33 tools, BOTH tasks, 3 estimators each
  (logistic_regression / random_forest_classifier / hist_gb_classifier and
  linear_regression / random_forest_regressor / hist_gb_regressor), logit + ols for
  inference, split_stratified + split_random. Extending is a registry line.

MODES AND GATING (D10, D29)
- Auto mode never blocks except on a data/bug escalation from the recovery handler.
- Guided mode gates the SHARED nodes 1-6 — the node-5 candidate shortlist is the key
  decision of the whole run. Per-candidate nodes 7-11 run UNGATED except on exceptions
  (a candidate failing, a material warning, a metric far outside the expected range).
  Do NOT gate every node of every candidate: 5 candidates x 5 nodes is 25+ stops and a
  gate nobody reads is worse than no gate.
- Gate actions: approve, approve_with_edits, instruct (free-text -> revised proposal ->
  re-gate), approve_all_remaining, skip_node, rewind, abort. Gates block indefinitely,
  never auto-approve on timeout, and survive a server restart.
- Candidates execute in PARALLEL, serialising only at CPU-bound fits via the semaphore.
  One candidate failing must not abort its siblings. Per-candidate wall-clock timings are
  therefore NOT comparable — say so in the report rather than implying fit costs can be
  ranked.

VERIFICATION — non-negotiable
- Every MCP tool gets a test on a small deterministic synthetic fixture.
- Fixtures: a binary-classification frame with missing values, mixed dtypes, an ID
  column and a deliberately leaky column; a regression frame; and for inference a frame
  with a planted relationship (y = 3*x1 - 2*x2 + noise), a pure-noise column and a
  collinear pair.
- The inference known-answer test must assert recovered coefficients within tolerance,
  p-value below alpha for real predictors and above for the noise column, and VIF
  flagging the collinear pair. Metric-based tests cannot catch a wrong inferential
  pipeline; this one can.
- No live LLM calls in CI. Use recorded-response fixtures; one optional live smoke test
  behind an env var.
- Run the tests after every milestone. Fix failures before proceeding.
- EVERY milestone's done-criterion includes `make test` fully green — all prior milestones'
  tests, not just the new demo. A milestone that regresses an earlier one is not done.
- No test may use an unstated tolerance. §14.1 fixes every number; §14.2 fixes determinism
  (seeds pinned, semaphore permits INJECTED not discovered, no wall-clock assertions).

SKILL FILE
Scaffold skills/modelling.md with the preamble and one section per staging node, every
required heading present (Purpose, Heuristics, Tools, Outputs, Pitfalls), with TODO
placeholders for Heuristics and Pitfalls. The loader must fail AT STARTUP on a missing
section, a missing heading, or a Tools entry the MCP server does not expose. Enforce the
Tools allow-list in mcp_client against the active node. Do not invent domain heuristics —
the human author writes those after the nodes exist (D12).

STOP AND ASK if you find a genuine contradiction in PLAN.md, if a dependency will not
install and the fallback is unclear, or if a decision seems wrong for a reason the plan
does not already address. Otherwise proceed autonomously through the milestones.

REPORT after each milestone: what was built, the done-criterion output that proves it,
test results, and anything you deviated from with the reason.
````

If the plan and this prompt ever disagree, PLAN.md wins.
