# What's next

Handoff note. Written 2026-09-10, after chunk 05. Read this, then `00_index.md`, then `PLAN.md`.

This file holds only what is **not** written down elsewhere: live state, the working loop, and the
lessons that cost something to learn. Everything else is in `PLAN.md` (architecture, 31 locked
decisions) and `00_index.md` (the 40-chunk map, hard rules, verified environment facts). Do not
duplicate them here.

---

## State

**5 of 40 chunks complete.** Phase A's schema work is done; `06` is next.

| Verified at commit `4e31b96` | |
|---|---|
| Tests | 785, all passing |
| Coverage | 100 % line **and** branch (1048 stmts, 248 branches, 0 missed) |
| mypy | `--strict` clean on 42 source files, `init_typed = true` |
| ruff | `check` and `format --check` clean |
| Warnings | `filterwarnings = ["error"]`, zero ignores |
| Suppressions | zero in production code; zero `pragma: no cover`; zero `Any`. 15 `# type: ignore` in tests, all legitimate — see below |
| Git | local `master`, never pushed, tree clean |

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

## Next: chunk 06, artifact store

Scope is in `00_index.md`. Unblocked, and **smaller than originally specced** — chunk 05's
`runs.test_scored_at` compare-and-set supersedes the planned `locked` flag, so the registry needs no
lock of its own and no artifact-level lock column. See `PLAN.md` §10.2's note.

What it covers: run-folder layout (`PLAN.md` §10.1), parquet/joblib/png/json IO, the artifact
registry, hard-link fork copies (D11), prune-on-success (D24).

**First chunk to touch pandas.** The 3.0 facts are already verified and recorded in `00_index.md` —
read them there rather than rediscovering. The two that will bite:

- String columns have dtype `str`, **not** `object`. Any `dtype == object` check is wrong.
- `filterwarnings = ["error"]` plus `Pandas4Warning` means a deprecated API is a hard test failure,
  not a warning.

**Design question to settle while specifying it:** D24 prunes within-node intermediates on success
only, never on failure. Hard links (D11) mean a pruned file may still be referenced by a forked run.
Deleting a hard link is safe — the inode survives while any link remains — but the *registry rows*
in both runs must stay consistent. Decide whether pruning is per-run or refcounted before writing
the spec.

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
