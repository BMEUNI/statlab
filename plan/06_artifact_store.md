---
status: in_progress
dependencies: [01, 02, 05]
completion_date: 2026-09-10
implementation_notes: |
  Implemented as specified. `layout.py` (2 frozen dataclasses, stdlib+statlab_contracts only),
  `storage.py` (layout creation, artifacts repository, fork, prune -- no pandas, no
  statlab_tools), `mcp_server/statlab_tools/artifacts.py` (the one pandas module).
  `ArtifactRow` added to persistence/models.py; `artifacts.pruned_at TEXT` added to
  001_initial.sql in place (no 002 migration, same archaeology argument as chunk 05 §6.4).
  Exactly one existing assertion broke (test_db_schema.py's artifacts column list), as
  predicted.

  All seven dtypes (str, category, int64, float64, bool, datetime64[us], Int64) round-trip
  through parquet unchanged, measured. save_dataframe/load_dataframe needed zero dtype
  handling. No refcounting was added for fork/prune: P10/P11 confirm both directions
  empirically (fork then prune child -> parent survives; fork then prune parent -> child
  survives), matching the two independent reasons in the spec (disjoint sets; unlink removes a
  directory entry, not an inode). Felt no urge to add a guard while implementing -- the
  filesystem behaviour is direct enough that the reasoning read as obviously correct before
  any test ran, not just after.

  pandas confirmed in exactly one module by grep across all three packages.

  One spec inconsistency found, not silently resolved: §1's context table instructs wrapping
  IO failures in ArtifactError (statlab.errors) inside mcp_server/statlab_tools/artifacts.py,
  but §5's DoD checkbox says that module "imports no statlab.*" verbatim -- those two
  instructions contradict for this one import. Kept the ArtifactError import (matches the
  context table and the wrapping requirement stated four separate times in §3.3), and scoped
  the A18 test to the boundary the rule is actually protecting: no statlab.persistence,
  statlab.storage, or statlab.domains import (the backend's stateful modules), which is what
  D26's "MCP server is stateless" is about. statlab.errors is a shared, dependency-free
  taxonomy already imported by both processes elsewhere; it is not backend state.

  Judgement call: create_artifact's minted id is independent of the ref used to build the
  artifact's file path -- the caller must choose the ref/filename before knowing the row's own
  id (which create_artifact mints internally). This is fine for chunk 06 (tests always write
  the file first, using a locally chosen ref, then create the row), but the mismatch is worth
  chunk 07's attention: see report item 7.

  945 new tests (39 layout, 38 storage, 12 fork, 20 prune, 45 tool_artifacts, less overlaps),
  total 955 (954 passed + 1 skipped -- F9, by spec, since nlink already proves the no-copy
  claim without writing 200 MB). 100% line+branch coverage on all three new modules and
  project-wide. mypy --strict clean on 50 source files (up from 42). ruff clean.
---

# Chunk 06 — Artifact store: layout, registry, forks, pruning

**Goal.** Everything about artifacts on disk. `PLAN.md` §10.1, D11, D24, plus the artifacts
repository chunk 05 deferred.

First chunk to touch pandas, and the first to span all three packages — which is the design, not an
accident: the folder layout is a contract between two processes, the registry is backend state, and
content IO belongs with the tools that produce it.

---

## 1. Prerequisites & Context

### State after chunk 05 (verified, committed at `4e31b96`)

785 tests, 100 % line and branch coverage, mypy clean on 42 files, ruff clean.

| Symbol | Location | Use here |
|---|---|---|
| `ArtifactRef`, `ArtifactKind`, `ArtifactMeta`, `parse_artifact_ref` | `statlab_contracts/artifacts.py` | reuse unchanged |
| `ArtifactError`, `ArtifactLockedError` | `statlab/errors.py` | already exist; wrap IO failures in the former |
| `connect`, `transaction`, `migrate` | `statlab/persistence/db.py` | the artifacts repository uses these |
| `new_id`, `utc_now` | `statlab/persistence/ids.py` | `new_id("a")` matches `ARTIFACT_ID_PATTERN` |
| `Settings.data_dir`, `Settings.keep_intermediates` | `statlab/config.py` | both already exist; `keep_intermediates` is D24's debug switch and has had no consumer until now |
| `NodeStatus` | `statlab/domains/base.py` | the prune guard dispatches on it |
| `ToolSettings.data_dir` | `statlab_tools/settings.py` | the MCP side's copy of the same path |

### Where each piece goes, and why

`PLAN.md` §3.2 already assigns these; follow it.

| Module | Package | Imports pandas? | Rationale |
|---|---|---|---|
| `layout.py` | `contracts/statlab_contracts/` | **no** — pure `pathlib` | Both processes resolve the same paths (D13: "artifact paths are config-driven, shared with the backend"). The folder layout *is* a contract. Duplicating it is the drift risk `config.py`'s `ponytail` comment accepted only because that case was six lines; this is not |
| `storage.py` | `backend/statlab/` | **no** | Registry rows, hard-link forks and pruning are database and filesystem work. The backend never needs to open a frame |
| `artifacts.py` | `mcp_server/statlab_tools/` | **yes** | Tools produce and consume content. This is the only module in the project that reads a dataframe |

`GET /api/artifacts/{id}` (§11) does not force pandas into the backend: a download streams bytes, and
a *preview* goes through chunk 07's `describe_artifact` tool. Routing the preview through the tool
that already knows how to read a frame beats a second parquet reader in another process.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| `list_artifacts` / `describe_artifact` MCP tools | 07 |
| Any tool that *produces* an artifact | 08 onward |
| The fork *decision* and state copying | 31 (rewind); this chunk provides only the hard-link primitive |
| Deciding which artifacts a node hands forward | 22 (the engine passes `keep`) |
| `manifest.json`, `pipeline.py`, `report/` contents | 13, 38 |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `contracts/statlab_contracts/layout.py` |
| create | `backend/statlab/storage.py` |
| create | `mcp_server/statlab_tools/artifacts.py` |
| **modify** | `backend/statlab/persistence/models.py` — `ArtifactRow` |
| ~~modify~~ | ~~`persistence/repository.py`~~ — **corrected (§7.3)**: the artifacts repository belongs in `storage.py`, per `PLAN.md` §3.2 |
| **modify** | `backend/statlab/persistence/migrations/001_initial.sql` — `artifacts.pruned_at` |
| create | `tests/test_layout.py` |
| create | `tests/test_storage.py` |
| create | `tests/test_storage_fork.py` |
| create | `tests/test_storage_prune.py` |
| create | `tests/test_tool_artifacts.py` |
| **modify** | `tests/conftest.py` — `run_paths`, `sample_frame` fixtures |
| **modify** | `tests/test_db_schema.py` — append `pruned_at` to the artifacts column list |

---

## 3. Implementation Spec

### 3.1 `layout.py` — two frozen dataclasses, not fifteen functions

`PLAN.md` §10.1's tree has about a dozen named locations. A function each is a dozen call sites
importing a dozen names; a frozen dataclass built once from the ids gives one import and attribute
access.

```python
@dataclass(frozen=True, slots=True)
class ProjectPaths:
    data_dir: Path
    slug: str

    @property
    def root(self) -> Path: ...          # <data_dir>/<slug>
    @property
    def project_json(self) -> Path: ...
    @property
    def datasets(self) -> Path: ...
    @property
    def contracts(self) -> Path: ...
    @property
    def memory_json(self) -> Path: ...
    @property
    def runs(self) -> Path: ...
    def run(self, run_id: str) -> RunPaths: ...


@dataclass(frozen=True, slots=True)
class RunPaths:
    data_dir: Path
    slug: str
    run_id: str

    @property
    def root(self) -> Path: ...          # <data_dir>/<slug>/runs/<run_id>
    # state_json, events_jsonl, contract_snapshot_json, skill_snapshot_md,
    # comparison_json, manifest_json, pipeline_py, artifacts, nodes, candidates, report
    def node(self, seq: int, node_name: str) -> Path:
        """<root>/nodes/NN_<node_name>, zero-padded to two digits so lexical order is spine
        order — `10_model_evaluation` must not sort before `09_model_training`."""
    def candidate(self, candidate_id: str) -> Path: ...
    def candidate_node(self, candidate_id: str, seq: int, node_name: str) -> Path: ...
    def artifact(self, ref: ArtifactRef, kind: ArtifactKind) -> Path: ...
```

Note `data_dir` is already absolute — `Settings._absolute` resolves it — so nothing here resolves
paths again. State that in the class docstring so no one adds a second `.resolve()`.

`PLAN.md` §10.1 writes the project root as `projects/<project_slug>/`, where `projects/` is
`STATLAB_DATA_DIR`'s default value, not a hardcoded segment. `ProjectPaths.root` is therefore
`data_dir / slug`, with no literal `"projects"` anywhere. A test asserts that string is absent from
the module.

**Extension mapping**, exhaustive over `ArtifactKind`:

```python
EXTENSION_FOR_KIND: Final[dict[ArtifactKind, str]] = {
    "dataframe": ".parquet", "predictions": ".parquet",
    "transformer": ".joblib", "model": ".joblib", "inferential_fit": ".joblib",
    "curve": ".json", "table": ".json",
    "plot": ".png", "report": ".md",
}
```

Tested exhaustive via `get_args(ArtifactKind)`, same discipline as chunk 04's
`DIMENSION_FOR_FIELD` and chunk 05's CHECK-constraint drift test. A new kind without an extension
must fail the suite, not produce a file with no suffix.

### 3.2 `storage.py` — layout creation, registry, forks, pruning

```python
def ensure_run_layout(paths: RunPaths) -> None:
    """Create the run's directory tree. Idempotent (`exist_ok=True`), because a resumed run
    (chunk 23) calls it again."""
```

Artifacts repository, following chunk 05 §3.8's conventions exactly — `get_*` returns `None`,
writes return the row read back, `sqlite3.IntegrityError` wraps into `PersistenceError`:

```python
def create_artifact(conn, *, run_id: str, kind: ArtifactKind, path: Path,
                    meta: ArtifactMeta, produced_by: str) -> ArtifactRow: ...
def get_artifact(conn, artifact_id: str) -> ArtifactRow | None: ...
def list_artifacts(conn, run_id: str, *, include_pruned: bool = False) -> tuple[ArtifactRow, ...]: ...
def mark_pruned(conn, refs: Sequence[ArtifactRef]) -> int: ...
```

`produced_by` is the tool name, per §10.2's column. `include_pruned` defaults to `False` because
every caller that lists artifacts wants the ones that still exist.

#### `pruned_at` — why this is a schema column

Add `pruned_at TEXT` (nullable) to the `artifacts` table.

Deleting the row instead would break the tool-call log: `tool_calls` records that a tool produced
`a_xxx`, and a report rendering that log could not resolve the id. Leaving the row with a `path`
pointing at a missing file is worse — it makes a deliberately pruned artifact indistinguishable
from data loss. Recording *when* it was pruned is the same reasoning that put `test_scored_at` in
the schema rather than in process memory: D24's premise is that pruning is deliberate and bounded,
and an unexplained missing file does not support that claim.

**Edit `001_initial.sql` in place; do not add `002_*.sql`.** Nothing is deployed and no database
outside `tmp_path` has ever run this schema, so a migration correcting it is archaeology — the same
argument as chunk 05 §6.4, unchanged by the passage of time. `test_db_schema.py`'s artifacts column
list gains one entry.

#### Forking — `fork_artifacts`

```python
def fork_artifacts(parent: RunPaths, child: RunPaths,
                   artifacts: Sequence[ArtifactRow]) -> tuple[ArtifactRef, ...]:
    """Hard-link the parent's artifacts into the child run (D11), returning the refs linked.

    Hard links, not copies: artifacts are immutable (§6.1 rule 2), so sharing an inode is safe
    and disk cost is near zero — a 200 MB dataset forked four times still occupies 200 MB.

    Skips artifacts already pruned in the parent, since there is no file to link.

    `os.link` fails with EXDEV across filesystems. Both runs live under STATLAB_DATA_DIR so
    same-device is structurally guaranteed, but the failure is wrapped in ArtifactError naming
    both paths — an operator who symlinked one project directory onto another volume gets a
    diagnosable message instead of a bare OSError.
    """
```

The child's registry rows are new rows with new ids pointing at the child's paths. Refs are not
shared across runs: D11 says one run is exactly one reproducible path, and a shared artifact id
would make `list_artifacts(child)` return rows whose `run_id` is the parent's.

#### Pruning — `prune_intermediates`

```python
def prune_intermediates(
    conn: sqlite3.Connection,
    paths: RunPaths,
    *,
    node_status: NodeStatus,
    produced: Sequence[ArtifactRow],
    keep: frozenset[ArtifactRef],
    keep_intermediates: bool,
) -> tuple[ArtifactRef, ...]:
    """Delete within-node intermediates after a node finishes (D24). Returns what was pruned.

    No-op unless `node_status == "completed"`. D24 says never prune a failed node — those
    intermediates are exactly what diagnosis needs — and a failed or skipped node returns
    early. The guard lives here rather than in each caller: one check in the shared function
    beats one per call site, and a caller that forgets is a silent data-loss bug.

    No-op when `keep_intermediates` (STATLAB_KEEP_INTERMEDIATES) is set.

    Unlinks the file and sets `pruned_at`, in one transaction per artifact set.
    """
```

**No refcounting, and the reason is worth stating.** Pruning is per-run and safe for two
independent reasons, both verified empirically before this spec was written:

1. **The sets do not intersect.** Hard-linked artifacts are node-*boundary* artifacts of a
   completed, halted parent (D17: rewind marks the parent superseded). Pruned artifacts are
   within-node intermediates from the pruning run's own execution of its own node. A fork never
   prunes something it linked.
2. **Even if they did, `unlink` removes a directory entry, not an inode.** Confirmed in both
   directions: after linking a parent artifact into a fork, unlinking the fork's entry leaves the
   parent readable with `nlink` back to 1, and unlinking the parent's entry leaves the fork's copy
   readable. The data survives while any link remains.

So a refcount would be a second bookkeeping system protecting against something the filesystem
already makes impossible. Required test: fork, prune in the child, assert the parent's file is
still readable — and the reverse.

### 3.3 `mcp_server/statlab_tools/artifacts.py` — content IO

The only module in the project that imports pandas.

```python
def save_dataframe(path: Path, frame: pd.DataFrame) -> tuple[int, int]: ...   # (rows, cols)
def load_dataframe(path: Path) -> pd.DataFrame: ...
def save_model(path: Path, obj: object) -> None: ...            # joblib
def load_model(path: Path) -> object: ...
def save_plot(path: Path, figure: Figure) -> None: ...          # matplotlib, then close it
def save_json(path: Path, payload: JsonValue) -> None: ...
def load_json(path: Path) -> JsonValue: ...
def save_text(path: Path, text: str) -> None: ...               # report/*.md
```

Every function wraps `OSError`, `pyarrow` errors and `joblib` errors in `ArtifactError` naming the
path. A caller gets "cannot write artifact a_x.parquet: No space left on device", not a traceback
from a library three layers down.

**pandas 3.0 facts, re-verified for this chunk.** Parquet round-trips `str`, `category`, `int64`,
`float64`, `bool`, `datetime64[us]` and `Int64` with the dtype **unchanged** — measured, all seven.
So `save_dataframe` needs no dtype preservation logic and `load_dataframe` needs no restoration
logic. Do not add any.

Three traps, all in `00_index.md` already:

- String columns have dtype `str`, **not** `object`. Any `dtype == object` check is wrong; use
  `pandas.api.types.is_string_dtype`.
- Datetimes default to `datetime64[us]`, not `[ns]`.
- `filterwarnings = ["error"]` plus `Pandas4Warning` means a deprecated API is a test failure. If a
  call warns, change the call rather than filtering the warning.

`save_plot` must close the figure. Matplotlib keeps figures in a global registry, so a tool that
renders 30 plots without closing them leaks until the process dies — and under D17's parallel runs
that is not hypothetical.

**No atomic temp-file-then-`os.replace` write.** It buys nothing here: every tool call mints a
fresh artifact id (§5.1, "the retry writes new artifact IDs rather than colliding"), so a truncated
file from a crashed tool is never registered and never read. Its id is not in the registry, so the
worst case is an orphan file on disk. Adding atomicity would guard a path that cannot be taken.

---

## 4. Test Suite Spec

Target **~170 new tests**, total ≥ **955**. Use `tmp_path` throughout; never the real `data_dir`.

### 4.1 `conftest.py` additions

```python
@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    """RunPaths rooted in tmp_path, layout already created."""

@pytest.fixture
def sample_frame() -> pd.DataFrame:
    """A frame covering every dtype the store must round-trip: str, category, int64, float64,
    bool, datetime64[us], Int64 (nullable, with a null). If a dtype is missing here, no test
    proves it survives storage."""
```

### 4.2 `test_layout.py` (~34)

Every property of both dataclasses returns the §10.1 path (compare against literals built in the
test — this is the one place hand-written expectations are correct, since the test *is* the
specification of the layout). Both classes frozen. `ProjectPaths.run()` returns a `RunPaths`
carrying the same `data_dir` and `slug`.

| # | Test | Assertion |
|---|---|---|
| L1 | node dir is zero-padded | `node(9, "model_training")` → `.../nodes/09_model_training` |
| L2 | **lexical order is spine order** | sort `node(n, ...)` names for n in 1..13; assert the sorted order matches numeric order. Catches the un-padded bug that puts `10_` before `09_` |
| L3 | `EXTENSION_FOR_KIND` exhaustive | derived from `get_args(ArtifactKind)`, not hand-listed |
| L4 | every extension starts with `.` | |
| L5 | `artifact()` composes ref and extension | `a_0000000000000001` + `dataframe` → `.../artifacts/a_0000000000000001.parquet` |
| L6 | no literal `"projects"` in the module | source scan — the segment is `STATLAB_DATA_DIR`'s default, not a path component |
| L7 | no `.resolve()` in the module | `data_dir` arrives absolute; a second resolve would silently follow symlinks |
| L8 | `layout.py` imports only stdlib + `statlab_contracts` | no pydantic needed, no pandas possible |

### 4.3 `test_storage.py` (~44)

`ensure_run_layout` creates every directory and is idempotent. Then the artifacts repository, per
chunk 05's conventions: create-then-get round-trip, `None` for unknown ids, `PersistenceError` on a
bad `run_id` FK, `list_artifacts` ordering, and:

| # | Test | Expect |
|---|---|---|
| S1 | `create_artifact` id matches `ARTIFACT_ID_PATTERN` | reuses chunk 02's pattern |
| S2 | `meta_json` stored verbatim | byte-identical to `ArtifactMeta.model_dump_json()`, per chunk 05 §3.7 |
| S3 | `ArtifactRow.meta()` parses back | equals the original `ArtifactMeta` |
| S4 | `pruned_at` is `None` on creation | |
| S5 | `list_artifacts` excludes pruned by default | |
| S6 | `include_pruned=True` includes them | |
| S7 | `mark_pruned` returns the count and sets timestamps | parseable via `parse_ts` |
| S8 | `mark_pruned` on an already-pruned ref | idempotent, does not overwrite the first timestamp |
| S9 | artifacts CASCADE on run delete | inherited from chunk 05's schema; assert it still holds |

### 4.4 `test_storage_fork.py` (~26)

| # | Test | Assertion |
|---|---|---|
| F1 | linked file is the same inode | `st_ino` equal, `st_nlink == 2` |
| F2 | child gets new registry rows with new ids | refs differ from the parent's; `run_id` is the child's |
| F3 | content is identical | read both, compare |
| F4 | pruned parent artifacts are skipped | no file, so no link, and no child row |
| F5 | forking twice from one parent | `nlink == 3`, both children readable |
| F6 | forking a fork | lineage of three, all readable |
| F7 | EXDEV wraps in `ArtifactError` | monkeypatch `os.link` to raise `OSError(errno.EXDEV, ...)`; assert both paths appear in the message |
| F8 | empty artifact list | returns `()`, creates no files |
| F9 | 200 MB forked four times | *skip by default*; if included, assert `nlink` rather than measuring disk. The point is that no bytes are copied, and `nlink` proves it without writing 200 MB |

### 4.5 `test_storage_prune.py` (~30)

| # | Test | Expect |
|---|---|---|
| P1 | `completed` prunes everything not in `keep` | files gone, `pruned_at` set |
| P2 | artifacts in `keep` survive | file and row untouched |
| P3 | `failed` prunes nothing | D24's core rule. Files all present, `pruned_at` all `None` |
| P4 | `skipped` prunes nothing | |
| P5 | `awaiting_user` prunes nothing | a suspended node may resume and need its intermediates |
| P6 | parametrised over every `NodeStatus` | derived from `get_args(NodeStatus)`; only `completed` prunes. Adding a status forces a decision here |
| P7 | `keep_intermediates=True` prunes nothing | even when `completed` |
| P8 | empty `produced` | returns `()`, no error |
| P9 | `keep` containing a ref not in `produced` | ignored, not an error — the engine may pass the whole node-boundary set |
| P10 | **fork then prune child: parent survives** | the D11×D24 interaction. Assert the parent's file is readable after the child prunes its link |
| P11 | **fork then prune parent: child survives** | the reverse. Together P10 and P11 are why there is no refcount |
| P12 | pruning is transactional | patch `Path.unlink` to raise on the second artifact; assert no `pruned_at` was committed |

P3 and P10/P11 are the tests that matter. P3 is a data-loss guard; P10/P11 justify the absence of a
refcounting system.

### 4.6 `test_tool_artifacts.py` (~36)

| # | Test | Assertion |
|---|---|---|
| A1 | **every dtype round-trips** | parametrised over all seven columns of `sample_frame`; `str(dtype)` before == after |
| A2 | values round-trip | `assert_frame_equal` |
| A3 | nullable `Int64` null survives | `pd.isna` on the reloaded value |
| A4 | `save_dataframe` returns `(rows, cols)` | matches `frame.shape` |
| A5 | empty frame | zero rows round-trips without error |
| A6 | single-column frame | |
| A7 | model round-trip | joblib on a fitted sklearn estimator; predictions equal before and after |
| A8 | plot writes a valid PNG | first 8 bytes are the PNG signature |
| A9 | `save_plot` closes the figure | `plt.get_fignums()` is empty afterwards |
| A10 | json round-trip | including nested structures and `None` |
| A11 | text round-trip | UTF-8 with non-ASCII |
| A12 | write to a nonexistent directory | `ArtifactError` naming the path, not a bare `OSError` |
| A13 | read a missing file | `ArtifactError` naming the path |
| A14 | read a corrupt parquet | write junk bytes; `ArtifactError`, not a pyarrow traceback |
| A15 | read a corrupt joblib | same |
| A16 | **no `dtype == object` in the module** | source scan. The pandas 3.0 trap most likely to be written from habit |
| A17 | no warning is raised by any operation | `filterwarnings=["error"]` covers this, but assert explicitly on `save_dataframe`/`load_dataframe` so a future pandas deprecation is attributed here |
| A18 | module imports no `statlab.*` | boundary: the MCP side must not reach into the backend |

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **955**.
- [ ] Coverage **100 %** line and branch on all three new modules; project stays 100 %.
- [ ] P3 (never prune a failed node) and P10/P11 (fork/prune independence) pass.
- [ ] A1 proves all seven dtypes round-trip.
- [ ] `EXTENSION_FOR_KIND` exhaustiveness derives from `get_args`.
- [ ] P6 parametrises over `get_args(NodeStatus)`.

**Static analysis**
- [ ] `make typecheck` and `make lint` exit 0.
- [ ] No `Any`, no `pragma: no cover`. `# type: ignore` only in the three permitted test shapes
      (`WHATS_NEXT.md`); none in production code.
- [ ] pandas appears in `mcp_server/statlab_tools/artifacts.py` and **nowhere else**. Grep and
      confirm — `contracts/` and `backend/` must both be clean.

**Boundary**
- [ ] `layout.py` imports only stdlib.
- [ ] `mcp_server/statlab_tools/artifacts.py` imports no `statlab.*`.
- [ ] `backend/statlab/storage.py` imports no pandas and no `statlab_tools.*`.

**Behaviour**
- [ ] Hard links share an inode; `nlink` rises with each fork.
- [ ] A failed node's intermediates are all present afterwards.
- [ ] `save_plot` leaves no open figures.
- [ ] Stored `meta_json` is byte-identical to `model_dump_json()`.

**Housekeeping**
- [ ] Frontmatter → `completed` with notes covering the report items.
- [ ] `plan/00_index.md` chunk 06 row → `completed`.
- [ ] `plan/WHATS_NEXT.md` state table updated, and its chunk 06 design question marked resolved.

### Stop and ask if

- Any dtype in `sample_frame` does **not** round-trip. That would invalidate the "no preservation
  logic needed" decision and mean `save_dataframe` has real work to do.
- `os.link` fails on this filesystem for a reason other than EXDEV.
- The `keep` set cannot be expressed as `frozenset[ArtifactRef]` because a node hands forward
  something that is not an artifact.
- Adding `pruned_at` to `001_initial.sql` breaks more than the one column-list assertion in
  `test_db_schema.py`.

### Report back with

1. Test count, coverage, mypy source-file count, ruff output.
2. The dtype round-trip table — all seven, before and after.
3. Whether `save_dataframe`/`load_dataframe` needed any dtype handling, or whether parquet did it
   all as measured.
4. Confirmation that pandas appears in exactly one module, with the grep output.
5. Whether P10/P11 confirmed fork/prune independence, and whether anything about the interaction
   surprised you.
6. Any judgement call on the §10.1 layout — particularly anything in the tree that has no obvious
   owner or whose name is ambiguous.
7. For chunk 07 (MCP server foundation): `artifacts.py` is the first module the tool layer will
   depend on. Say whether its signatures are the right shape for a `@tool`-decorated function to
   use — in particular whether returning `tuple[int, int]` from `save_dataframe` is what a tool
   needs to build an `ArtifactMeta`, or whether it should return the `ArtifactMeta` itself and take
   the label.

---

## 7. Addendum — boundary, ref identity, and the signature item 7 asked about

**Status: gated. Chunk 07 is blocked until this passes.**

Three of these come from the implementation report. Two are my spec errors; the third is a latent
corruption my spec made unavoidable. The fourth adopts the report's own recommendation.

### 7.1 The `statlab.errors` import is a real boundary violation, not a spec ambiguity

My §1 table said to wrap IO failures in `ArtifactError`; my §5 DoD said the module imports no
`statlab.*`. The report correctly identified the contradiction, but resolved it the wrong way —
keeping the import and narrowing the test.

The codebase already settled this, in the file next door. `ToolSettingsError`'s docstring, written in
chunk 01:

> Deliberately NOT `statlab.errors.ConfigError`: importing the backend package here would couple two
> processes that must stay independently deployable (D13).

So define `ToolArtifactError(Exception)` in `mcp_server/statlab_tools/artifacts.py`, mirroring that
precedent, and drop the `statlab.errors` import. Six lines across a process boundary is cheaper than
coupling — the same trade `config.py`'s `ponytail` comment already made for `Settings`.

`backend/statlab/storage.py` keeps raising `ArtifactError` for fork and prune failures. Two classes,
because they are two failure surfaces in two processes: the tool-side error becomes a `ToolFailure`
envelope, the backend-side error propagates to the engine.

**This slipped through because nothing was watching.** The existing boundary tests cover only
`contracts/`. Add the two missing ones, in both directions:

- No module under `mcp_server/` imports `statlab.*`.
- No module under `backend/` imports `statlab_tools.*`.

Source-scan style, same as `test_contracts_imports_only_pydantic_and_stdlib`. Restore A18 to the
strict form; do not narrow it.

### 7.2 `create_artifact` must take the ref, not mint one

The report flagged this as "convention, not type-enforced". It is worse than that — I reproduced the
divergence:

```
ref used for the filename : a_6ebb776d5c00e261
id minted for the row     : a_61df42416c2c0d1c
row.id == path stem       : False
```

The row's own id points at a file that does not exist, while `meta.ref` points at the real one. So
`get_artifact(conn, ref)` for the ref a tool returned finds nothing, and the row that does exist
references a path it does not own. Every registry lookup after that is wrong.

My spec caused it: `create_artifact(conn, *, run_id, kind, path, meta, produced_by)` has no `ref`
parameter, so minting was the only thing left to do.

**An artifact ref is born exactly once, in the process that writes the file.** That is the MCP server
— but `new_id` lives in `statlab.persistence.ids`, which the tool side must not import (§7.1). So:

```python
# contracts/statlab_contracts/artifacts.py
def new_artifact_ref() -> ArtifactRef:
    """Mint a fresh artifact ref. Lives here, beside ARTIFACT_ID_PATTERN and parse_artifact_ref,
    because both processes mint refs and this is the only package both may import. `secrets`
    is stdlib, so the contracts boundary holds."""
    return ArtifactRef(f"a_{secrets.token_hex(8)}")
```

Verified: `secrets.token_hex(8)` satisfies `ARTIFACT_ID_PATTERN` (`^a_[0-9a-f]{16}\Z`).

`statlab.persistence.ids.new_id` keeps the other thirteen prefixes — those name backend-only
entities the tool side never creates. Delete `"a"` from its prefix constant so there is one way to
mint an artifact ref, and add a test asserting `"a"` is absent.

Then `create_artifact(conn, *, ref: ArtifactRef, run_id, kind, path, meta, produced_by)`.

**Required invariant test, derived not asserted by convention:**
`row.id == Path(row.path).stem` and `ArtifactMeta.model_validate_json(row.meta_json).ref == row.id`.
Also add a guard in `create_artifact` raising `PersistenceError` when `meta.ref != ref` — the two
arrive from the same caller and disagreeing means the caller is confused.

### 7.3 My files table was wrong; your call was right

The §2 table said modify `repository.py`; §3.2's code block put the artifacts functions in
`storage.py`. You followed §3.2. That is correct — `PLAN.md` §3.2 assigns `storage.py` the
"project/run folders, artifact registry" role explicitly, and the architecture table outranks my
file list. §2 is now corrected. No code change.

### 7.4 `save_*` returns `ArtifactMeta` — adopting your item 7 answer

Your evidence decides it: thirty-plus call sites in tests each building
`ArtifactMeta(ref=..., kind=..., rows=result[0], cols=result[1])` immediately after the save, with
eighty-five tools coming. That is assembly the caller should not repeat, and every repetition is a
chance for `kind` to disagree with the function that wrote the file.

```python
def save_dataframe(path: Path, frame: pd.DataFrame, *, ref: ArtifactRef,
                   kind: ArtifactKind = "dataframe", label: str) -> ArtifactMeta: ...
def save_model(path: Path, obj: object, *, ref: ArtifactRef,
               kind: ArtifactKind, label: str) -> ArtifactMeta: ...
def save_plot(path: Path, figure: Figure, *, ref: ArtifactRef, label: str) -> ArtifactMeta: ...
def save_json(path: Path, payload: JsonValue, *, ref: ArtifactRef,
              kind: ArtifactKind, label: str) -> ArtifactMeta: ...
def save_text(path: Path, text: str, *, ref: ArtifactRef, label: str) -> ArtifactMeta: ...
```

`kind` stays explicit where one writer serves several kinds — `dataframe` and `predictions` are both
parquet, `model`/`transformer`/`inferential_fit` are all joblib — because the tool knows which it
produced and guessing from the extension would be a lie. `save_plot` and `save_text` have one kind
each and hardcode it.

`rows`/`cols` are populated only by `save_dataframe`; the others leave them `None`.

**`path` stays a parameter** rather than being derived from `RunPaths`, because not every written
file lives in `artifacts/` — §10.1 puts report plots under `report/plots/`. Instead, add the guard
where both values are present: `save_*` raises `ToolArtifactError` unless `path.stem == ref`. Same
shape as §7.2's guard and as the prune status check — one check where the two facts meet, rather than
trust spread across every call site.

The `load_*` functions are unchanged.

### 7.5 Definition of Done for the addendum

- [ ] `make test` green; expect roughly +25 net after the call-site churn.
- [ ] Coverage stays 100 % line and branch.
- [ ] `make typecheck`, `make lint` clean, no new suppressions.
- [ ] `grep -rn 'statlab\.' mcp_server/` returns nothing; `grep -rn 'statlab_tools' backend/`
      returns nothing. Both directions have a test.
- [ ] A18 is back to the strict form.
- [ ] `new_id` no longer accepts `"a"`, with a test.
- [ ] `row.id == Path(row.path).stem` holds, tested.
- [ ] `create_artifact` rejects `meta.ref != ref`.
- [ ] Every `save_*` returns `ArtifactMeta` and rejects a `path`/`ref` mismatch.
- [ ] No test builds an `ArtifactMeta` by hand immediately after a `save_*` call — if any still
      does, the signature change did not land where it mattered.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. The two boundary greps, verbatim.
3. Whether removing `"a"` from `new_id` broke anything unexpected.
4. How many test call sites the `save_*` change simplified, and whether any needed an
   `ArtifactMeta` built by hand afterwards anyway — that would mean the signature is still wrong.
5. Anything in §7 that was wrong or impossible.
