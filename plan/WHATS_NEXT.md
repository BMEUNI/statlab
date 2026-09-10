# What's next

Handoff note. Written 2026-09-10, after chunk 05. Read this, then `00_index.md`, then `PLAN.md`.

This file holds only what is **not** written down elsewhere: live state, the working loop, and the
lessons that cost something to learn. Everything else is in `PLAN.md` (architecture, 31 locked
decisions) and `00_index.md` (the 40-chunk map, hard rules, verified environment facts). Do not
duplicate them here.

---

## State

**6 of 40 chunks complete.** Phase A done; `07` is next (MCP server foundation), unblocked.

| Verified, uncommitted at write time | |
|---|---|
| Tests | 971 (970 passed, 1 skipped -- F9, by spec) |
| Coverage | 100 % line **and** branch (1322 stmts, 280 branches, 0 missed) |
| mypy | `--strict` clean on 50 source files, `init_typed = true` |
| ruff | `check` and `format --check` clean |
| Warnings | `filterwarnings = ["error"]`, zero ignores |
| Suppressions | zero in production code; zero `pragma: no cover`; zero `Any`. `# type: ignore` in tests only, all in the three permitted shapes |
| Git | local `master`, never pushed, tree clean before this chunk's edits |

Re-verify with `make test && make typecheck && make lint` before trusting this table.

---

## The working loop

1. I draft `plan/NN_name.md` — the complete, unambiguous spec.
2. I write a handoff prompt for a separate implementation agent.
3. The agent implements and reports back against a fixed set of questions.
4. **I verify independently** — never on the report alone. Run the gates, then probe the specific
   claims empirically (does the constraint actually reject? does the hash actually match?).
5. I commit. Then draft the next chunk.

Step 4 is the one that has caught everything. Three of the last five chunks had a defect that a
green test suite did not reveal, found by executing a probe against the claim rather than reading
the assertion.

**Commits happen only when the user asks.** Never push.

---

## Chunk 06, complete: artifact store (incl. §7 addendum)

Run-folder layout (`PLAN.md` §10.1), the artifacts registry (+`pruned_at`), hard-link fork
copies (D11), prune-on-success (D24), and content IO (parquet/joblib/png/json/text) in
`mcp_server/statlab_tools/artifacts.py` — the only module in the project that imports pandas.

**The D11×D24 design question is closed, not just settled on paper.** P10/P11 confirmed both
directions empirically: forking then pruning the child leaves the parent's file readable, and
the reverse. No refcounting was added. All seven pandas 3.0 dtypes (`str`, `category`, `int64`,
`float64`, `bool`, `datetime64[us]`, `Int64`) round-trip through parquet unchanged — measured,
not assumed — so `save_dataframe`/`load_dataframe` carry no dtype-preservation logic.

**Accepted with a gated addendum, then closed.** The first pass had two real defects, both the
same shape as this session's other findings — a guarantee that read as present while enforcing
nothing:

- The `statlab.errors` import inside the pandas module was a real D13 boundary violation, not
  a spec ambiguity. `ToolSettingsError`'s own chunk-01 docstring had already settled this
  exact question for `settings.py`; the fix mirrors it — `ToolArtifactError(Exception)`,
  defined locally, `statlab.errors` import dropped. The boundary tests had only ever covered
  `contracts/`, so neither direction between `backend/` and `mcp_server/` had a test; both
  directions are required tests now.
- `create_artifact` minted its own id independently of the ref used to build the file path.
  Reproduced: the row's id pointed at a file that did not exist, while the real file's ref
  went unregistered. `new_artifact_ref()` now lives in `statlab_contracts.artifacts` (the one
  package both processes may import, `secrets.token_hex(8)`), is the only minter, and
  `new_id("a")` raises rather than silently allowing a second one. `create_artifact` takes
  `ref` and rejects a `meta.ref` that disagrees with it. Invariant tests:
  `row.id == Path(row.path).stem`, `meta.ref == row.id`.

`save_dataframe`/`save_model`/`save_plot`/`save_json`/`save_text` now return `ArtifactMeta`
directly and take `ref`/`label` (and `kind`, where one writer serves several kinds). Zero test
call sites build an `ArtifactMeta` by hand after a save now; 36 call sites in
`test_tool_artifacts.py` alone stopped needing it.

**The general lesson, worth carrying into every remaining chunk:** a test that confirms a
guard exists is not the same as a test that demonstrates the failure the guard prevents.
`UNIQUE` over a nullable column, a `PRIMARY KEY` accepting `NULL`, a boundary test covering one
of three directions, a registry row whose id and path can silently disagree — all four read as
correct and were green. The fix each time was the same shape as chunk 05's `IntegrityError`
probes and this chunk's P10/P11: insert the duplicate, delete the parent, import across the
boundary, mint the id twice — and check the property holds, not that the code exists.

Next: chunk 07 (MCP server foundation — Streamable HTTP app, bearer auth, the `@tool`
decorator), now unblocked.

---

## Conventions that are binding and non-obvious

`00_index.md` has the full list. These are the ones most likely to be violated by someone who has
not read it:

- **Derive, don't duplicate-and-validate.** If field A is fully determined by B, don't store both.
  This has been violated twice and both times the "fix" was deletion.
- **`model_validate({...})` in negative tests**, kwargs in production and positive tests. Under
  `init_typed`, kwargs are type-checked, so a deliberately-wrong kwarg needs a suppression.
- **Typed keyword-parameter test helpers**, never `def helper(**overrides: object)` — that shape
  forces `**dict` construction and an `arg-type` suppression. The surviving `**overrides` helper in
  `tests/test_domains_base.py` is *not* the pattern to copy.
- **Derive schema and exhaustiveness assertions**, never hand-list them. Use `get_args`,
  `model_fields`, `PRAGMA table_info`, `PRAGMA foreign_key_list`. A hand-typed list is a second
  place to forget to update.
- **`tuple`, not `list`, on frozen models** — `frozen=True` does not freeze list contents.
- **Boundary isolation:** `backend/` and `mcp_server/` never import each other; both may import
  `statlab_contracts`, which imports only stdlib + pydantic. `mcp_server/` must never import
  `statlab.persistence` — the MCP server is stateless (D26). Source-scan tests enforce all three.
- **`disallow_any_explicit` must stay OFF** — it is incompatible with the pydantic mypy plugin for
  *every* `BaseModel`. `tests/test_no_explicit_any.py` replaces it and is stronger.
- **No unreachable code, and therefore no `pragma: no cover`.** If a line cannot be reached, delete
  it. If you want to bypass a validator to reach a line, that line is the problem.
- **`# type: ignore` is banned in production code and permitted in exactly one test situation:**
  when a runtime test must deliberately violate a static type. Mutating a frozen model to prove it
  raises, passing an invalid `Literal` to prove the runtime rejects it, or passing an extra kwarg to
  prove `extra="forbid"` fires — mypy is *correct* to object in all three, so the suppression is the
  test working, not the test cheating. There are 15, each with an inline justification naming why.
  Precedent: chunk 02's `retry_budget("nonsense")`.

  Do not "clean these up". Do challenge any new one that is not one of those three shapes — in
  particular `arg-type` on a `**dict` construction, which means a test helper took
  `**overrides: object` instead of typed keyword parameters.

---

## Corrections so far, and the pattern in them

Five defects have been found and fixed. Four were mine, in the specs; one was latent in `PLAN.md`.

| # | Defect | Resolution |
|---|---|---|
| 1 | `disallow_any_explicit` mandated without testing it against a model | Flag removed; source-scan test instead |
| 2 | `retryable` field on `Failure`, fully determined by `error_class`, policed by a validator | Field deleted; derived helper functions |
| 3 | Chunk 04's rule S7, redundant with S3, kept for message quality | Rule deleted; also removed an unreachable `AssertionError` |
| 4 | Two node-status vocabularies sharing one name since `PLAN.md` §10.2 was written | `NodeExecutionStatus` derived from `NodeStatus` |
| 5 | `UNIQUE` and `PRIMARY KEY` that read as present but enforced nothing over NULLs | `NOT NULL` on all ids; expression index over `IFNULL(candidate_id, '')` |

**1–3 share a shape:** adding a mechanism that *reads* as rigour — an extra flag, an extra field, an
extra rule — where the correct move was subtraction. 2 and 3 are the same error twice, which is why
it is written down here. The detector: **if a check can only be satisfied trivially, or can never
fire, it is not a check.**

**4–5 share a different shape:** both came from writing a schema as *names* rather than *types*. A
markdown table saying `node ∈ {pending, running, ...}` or `UNIQUE(run_id, candidate_id, ...)` never
forces the question "is this enforceable?" or "is this the same set as that one?" — an executed
`CREATE TABLE` or a `Literal` union does, immediately. Both were invisible until something had to
execute against them.

---

## Deferred deliberately

Not oversights. Each is waiting on something that does not exist yet.

| Deferred | Lands in | Waiting on |
|---|---|---|
| Estimator-name validation, model-family cap (D28), `max_candidates` | 27 | The registry (13) and the contract together |
| Candidate id minting (`c_01`, sequential and human-readable) | 22 | The engine that creates waves |
| Repositories for 11 of the 15 tables | Whichever chunk first writes to each | Callers that exist |
| Provider-name env check (`DEEPSEEK_API_KEY`) | 26 | D18's rule that no provider is named outside `llm/` |
| Cancelling a *running* node | 31 | Resolves live rows to `failed`; needs no new status |
| `skills/modelling.md` heuristics and pitfalls | **the human, by hand** | Nodes existing first (D12, §9.3). Chunk 26 scaffolds headings with `TODO:` only |

---

## Two named checkpoints ahead

From `00_index.md`: **chunk 15** (walking skeleton — first end-to-end run) and **chunk 24**. Treat
both as integration gates, not as ordinary chunks. If the architecture is wrong somewhere, 15 is
where it shows.
