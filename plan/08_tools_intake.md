---
status: untouched
dependencies: [03, 06, 07]
completion_date: null
implementation_notes: ""
---

# Chunk 08 — Intake tools

**Goal.** The five intake tools from `PLAN.md` §6.3: `load_csv`, `infer_column_roles`,
`validate_contract`, `detect_id_columns`, `detect_leaky_columns`. Node 1 (`intake`, §4) uses them to
turn an uploaded CSV into the run's `raw` artifact and check the data contract against the real data.
Milestone **M2**: *"contract tests pass; mismatches produce precise errors; a CSV loads to an
artifact."*

These are the first tools written against chunk 07's `@tool` decorator for real, and `load_csv` holds
the only trust boundary in the ~86-tool catalogue: the one place where a value from outside the
system picks a file to read.

---

## 1. Prerequisites & Context

### State after chunk 07 (verified, committed at `4d9c0da`)

1112 tests (1111 passed, 1 skipped by design), 100 % line and branch coverage, mypy clean on 60
files, ruff clean. Two production `# type: ignore`s, both the justified `__signature__` stub gap.

| Symbol | Location | Use here |
|---|---|---|
| `tool`, `ToolParamError`, `ToolDataError` | `statlab_tools/tool.py` | decorator; raise these, never return `ToolFailure` |
| `RunPaths`, `ProjectPaths` | `statlab_contracts/layout.py` | `ProjectPaths(paths.data_dir, paths.slug).datasets` is where uploads live |
| `save_dataframe`, `load_dataframe` | `statlab_tools/artifacts.py` | write the `raw` artifact; read it back in the other four tools |
| `new_artifact_ref`, `ArtifactRef` | `statlab_contracts/artifacts.py` | mint the `raw` ref |
| `DataContract`, `DatasetOptions` | `statlab_contracts/contract.py` | `validate_contract` parses the snapshot; `load_csv` takes its defaults from `DatasetOptions` |
| `ColumnRole`, `TargetType` | `statlab_contracts/vocabulary.py` | `infer_column_roles` output; `validate_contract` target checks |
| `full.json` | `tests/fixtures/contracts/` | names seven columns: `customer_id`, `age`, `plan`, `signup_date`, `satisfaction`, `notes_free_text`, `churned` |

Read `00_index.md`'s **pandas 3.0** and **MCP SDK 2.2.0** blocks first. Both apply.

### Six findings from probing pandas 3.0.5 before writing this spec

Each one changes what the tools must do. All verified against the installed package.

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `UnicodeDecodeError`, `EmptyDataError` and `ParserError` are **all `ValueError` subclasses** | The decorator classifies any other `ValueError` as `bug`. Unhandled, a wrong encoding would be reported as a server bug with no repair path. `load_csv` must catch and reclassify (§3.2) |
| P2 | Duplicate header names are **silently renamed**: `a,b,a` reads as `a`, `b`, `a.1` | A contract naming `a.1` would "validate". Detect from the raw header (§3.2) |
| P3 | A wrong delimiter does **not** fail: `a;b;c` read with `,` gives one column named `a;b;c` | Detect the single-column case and turn it into a repairable `tool_param` on `delimiter` |
| P4 | `keep_default_na=True` (the default) turns the strings `"None"` and `"nan"` into missing **even when the contract's `na_values` does not list them** | Silent data loss for a category literally named `None`. Use `keep_default_na=False` so the contract's list is authoritative |
| P5 | A header-only file reads with dtype `object` columns and zero rows, even in pandas 3.0 | Zero rows is a `data` failure; nothing downstream can run |
| P6 | `Series.corr(method="spearman")` imports scipy; `a.rank().corr(b.rank())` gives the same value without it | Use the rank form. Note it returns `0.9999999999999999`, not `1.0` — compare with `>=` against a threshold, never `==` |

Also confirmed: a UTF-8 BOM is stripped by plain `encoding="utf-8"`, so no special case. Datetimes are
left as `str` by `read_csv`, so date detection is `infer_column_roles`' job, not `load_csv`'s.

### Five decisions this chunk forces

**D-a. `load_csv` takes a dataset identifier, not a path.** §6.3 writes `load_csv(path, ...)`. A path
parameter at a trust boundary needs traversal checks; an identifier that cannot express a path does
not. So the parameter is `dataset: str` constrained to `^[0-9a-f]{64}$` — the upload's sha256 — and the
file is `ProjectPaths.datasets / f"{dataset}.csv"`. `../`, `/etc/passwd` and every other path shape are
unrepresentable by the schema, rejected before the body runs. This is the WHATS_NEXT rule applied at
the boundary: make the wrong thing unrepresentable instead of checking for it.

Defence in depth, still required: after building the path, `resolve()` it and require
`is_relative_to(datasets.resolve())`. That catches a symlink planted inside `datasets/` pointing out —
verified that `resolve()` + `is_relative_to` rejects it. Two lines, at the one trust boundary; not
lazy here.

`dataset` carries the orchestrator-supplied description. D5 and Q2 say exactly one CSV per run, so the
agent never chooses it.

**Consequences for other chunks**, record them: chunk 33's upload endpoint must store the original as
`datasets/<sha256>.csv`. And §10.1's `datasets/<sha256>.parquet` has no consumer — the run's `raw`
artifact is already parquet — so it is dropped. Patch §10.1.

**D-b. The pandas boundary rule was written too tight; widen it to what it is meant to protect.**
Chunk 06 and `WHATS_NEXT.md` say pandas appears in *exactly one module*, `statlab_tools/artifacts.py`.
That rule cannot survive this chunk: `infer_column_roles` needs `pd.to_datetime` and
`pd.api.types.is_numeric_dtype`, and ~80 later tools need far more. The property it exists to protect is
`PLAN.md` §6: *"the backend never imports pandas or sklearn."* So the rule becomes **pandas, numpy,
sklearn, statsmodels and matplotlib are confined to `mcp_server/`**, enforced by a new source-scan test
over `backend/` and `contracts/`. Today nothing enforces the backend half at all — chunk 06's DoD
grepped for it once.

`tools/artifacts.py` keeps its existing no-pandas test; it is still true there and costs nothing.

**D-c. `validate_contract` reads the contract from the run, not from a parameter.** §6.3 writes
`validate_contract(df, contract)`. Passing the contract as an argument would make the LLM reproduce a
~60-field JSON object verbatim in a tool call — expensive, and an opportunity to change it. The
contract is already in the run directory: `RunPaths.contract_snapshot_json`, written at run start (§10.1,
chunk 23). The tool reads it. In chunk 08's tests, a fixture writes it.

**D-d. A contract mismatch is a finding, not a failure.** `validate_contract` returns `ToolSuccess`
whose summary lists *every* mismatch. Raising on the first would hand the agent one problem per call,
and node 1's job (§4) is to "flag mismatches and propose fixes" — all of them, at once. Tool failures
are reserved for the tool being unable to run: no snapshot, an unreadable artifact.

**D-e. Heuristics are deterministic, conservative and explain themselves.** Every heuristic threshold
is a named module constant, and every flagged column carries a short `evidence` string the agent can
quote. `ordinal` is never inferred — it needs a level order only the contract can supply. Datetimes are
detected only when they parse as ISO 8601; `03/01/2026` is ambiguous between January and March, and
guessing would silently corrupt every date-derived feature.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| The upload endpoint, the 200 MB cap, writing `datasets/<sha256>.csv` | 33 |
| Writing `contract.snapshot.json` at run start | 23 |
| Profiling (`profile_schema`, `summarize_*`, `report_missingness`) | 09 |
| Node 1's agent logic: deciding what to do with mismatches | 27 |
| Applying `features.exclude`, `id_columns`, or dropping anything | 10+ — intake reports, it never modifies |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/intake.py` — all five tools |
| **modify** | `mcp_server/statlab_tools/tools/__init__.py` — import `intake` |
| **modify** | `contracts/statlab_contracts/layout.py` — `SHA256_PATTERN`, `ProjectPaths.dataset_csv(sha256)` |
| create | `tests/test_tools_intake_load.py` |
| create | `tests/test_tools_intake_roles.py` |
| create | `tests/test_tools_intake_validate.py` |
| create | `tests/test_tools_intake_detect.py` |
| create | `tests/test_boundary_compute_libs.py` — D-b |
| **modify** | `tests/conftest.py` — `churn_frame`, `seeded_run`, `write_snapshot` fixtures |
| **modify** | `plan/PLAN.md` §6.3 (`load_csv`/`validate_contract` signatures), §10.1 (drop the parquet cache) |
| **modify** | `plan/WHATS_NEXT.md` conventions (D-b), `plan/00_index.md` chunk 33 row (D-a's consequence) |

One module for all five tools, following §6.2 (`tools/<phase>.py`). Each tool is small; five files would
be five headers around twenty lines each.

---

## 3. Implementation Spec

All five tools: `phase="intake"`, `allow_in=["intake"]`, `needs_run=True`. Every parameter carries a
`Field(description=...)` — the decorator rejects the module at import otherwise.

### 3.1 `layout.py` additions

```python
SHA256_PATTERN: Final = r"^[0-9a-f]{64}\Z"
"""Lowercase hex sha256. \\Z, not $, so a trailing newline is rejected (same reason as
ARTIFACT_ID_PATTERN)."""

class ProjectPaths:
    def dataset_csv(self, sha256: str) -> Path:
        """The original upload: <root>/datasets/<sha256>.csv. Chunk 33 writes it."""
```

### 3.2 `load_csv`

```python
@tool(phase="intake", allow_in=["intake"])
def load_csv(
    paths: RunPaths,
    dataset: Annotated[str, Field(pattern=SHA256_PATTERN, description=ORCHESTRATOR_SUPPLIED)],
    delimiter: Annotated[str, Field(min_length=1, max_length=4, description=...)] = _OPTS.delimiter,
    encoding: Annotated[str, Field(min_length=1, description=...)] = _OPTS.encoding,
    decimal: Annotated[str, Field(min_length=1, max_length=1, description=...)] = _OPTS.decimal,
    na_values: Annotated[tuple[str, ...], Field(description=...)] = _OPTS.na_values,
    header_row: Annotated[int, Field(ge=0, description=...)] = _OPTS.header_row,
) -> ToolSuccess:
    """Read the run's uploaded CSV into the `raw` dataframe artifact."""
```

`_OPTS = DatasetOptions()`. Defaults and bounds come **from `DatasetOptions`**, not restated — the
contract and the tool must agree, and a second copy of `"utf-8"` is a second place to forget. A test
asserts each default equals `DatasetOptions()`'s. `ORCHESTRATOR_SUPPLIED` is the string `tool.py`
already uses for `project`/`run`; import it rather than retyping it (make it public if it is not).

Body, in order:

1. Build `ProjectPaths(paths.data_dir, paths.slug).dataset_csv(dataset)`, resolve, check containment
   (D-a). Outside → `ToolParamError(field_errors={"dataset": ...})`. Missing →
   `ToolDataError("dataset <sha> not found")` — no argument the agent can choose fixes it.
2. `pd.read_csv(path, sep=delimiter, encoding=encoding, decimal=decimal, na_values=list(na_values),
   keep_default_na=False, header=header_row)`. `keep_default_na=False` is P4: the contract's list is
   the whole list.
3. Classify the reader's errors (P1). Catch exactly these; anything else propagates as `bug`:

   | Raised by pandas | Raise | Why |
   |---|---|---|
   | `UnicodeDecodeError` | `ToolParamError`, `field_errors={"encoding": ...}` naming the byte offset | A different `encoding` fixes it — the repair loop (§6.5) works |
   | `LookupError` (unknown codec name) | `ToolParamError` on `encoding` | |
   | `EmptyDataError` | `ToolDataError("file is empty")` | No argument fixes an empty file |
   | `ParserError` | `ToolDataError` with pandas' line number | A ragged row is malformed data |

4. **Duplicate headers (P2).** Re-read only the header row raw:
   `pd.read_csv(path, header=None, nrows=1, skiprows=header_row, sep=..., encoding=...)`. Any value
   appearing twice → `ToolDataError` naming them. Verified that the raw read sees `a,b,a` correctly,
   including with `header_row > 0`.
5. **Single column (P3).** If the frame has one column and its name contains any of `;`, `\t`, `|`,
   `,` other than `delimiter` → `ToolParamError(field_errors={"delimiter": "parsed one column; header
   contains ';' — try delimiter=';'"})`. If one column and no such character → `ToolDataError`: a target
   and at least one feature are needed. Either way one column cannot be modelled.
6. **Zero rows (P5)** → `ToolDataError`.
7. Save with `save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=new_artifact_ref(),
   label="raw")`.
8. Warnings: rows > `ROW_WARNING_THRESHOLD = 200_000` → D15's "fitting will be slow" warning.

Summary: `rows`, `cols`, `columns` (names, in file order), `dtypes` (`str(dtype)` per column),
`null_counts`. Column names are metadata, not raw data, so they belong in the summary (§6.1 rule 5).
`ponytail:` comment — a 2 000-column file makes a large summary; cap later if one appears.

No type conversion beyond pandas' defaults. Dates stay `str`.

### 3.3 `infer_column_roles`

```python
def infer_column_roles(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
) -> ToolSuccess:
    """Infer a role for every column from its dtype and values. Never infers `ordinal`."""
```

Rules, applied in this order, first match wins. Thresholds are module constants:

| # | Condition | Role | Evidence string |
|---|---|---|---|
| R1 | every value null | — (listed under `all_null`) | `"all values missing"` |
| R2 | bool dtype | `boolean` | `"bool dtype"` |
| R3 | numeric dtype, non-null values ⊆ {0, 1}, both present | `boolean` | `"numeric with values {0, 1}"` |
| R4 | numeric dtype | `numeric` | `"<dtype>"` |
| R5 | datetime dtype | `datetime` | `"datetime dtype"` |
| R6 | string dtype, lowercased non-null values ⊆ {true, false, yes, no, y, n}, exactly 2 distinct | `boolean` | `"two boolean-like strings"` |
| R7 | string dtype, ≥ `DATETIME_PARSE_SHARE = 0.95` of non-null values parse with `pd.to_datetime(format="ISO8601", errors="coerce")` | `datetime` | `"97% parse as ISO 8601"` |
| R8 | string dtype, mean length ≥ `TEXT_MEAN_LENGTH = 40` and unique share ≥ `TEXT_UNIQUE_SHARE = 0.5` | `text` | `"mean length 63, 91% unique"` |
| R9 | anything else | `categorical` | `"<n> distinct values"` |

String dtype checks use `pd.api.types.is_string_dtype`, never `dtype == object` (`00_index.md`). R7's
`format="ISO8601"` is D-e: no day/month guessing. Verified to produce `datetime64[us]` and no warning
under `filterwarnings=["error"]`.

Summary: `roles` (`{column: ColumnRole}`, all-null columns omitted), `evidence` (`{column: str}`),
`all_null` (list). No artifact — the result is small.

`roles` values must be members of `ColumnRole`; a test derives this from `get_args`.

### 3.4 `validate_contract`

```python
def validate_contract(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to validate, usually `raw`.")],
) -> ToolSuccess:
    """Check the run's data contract against the actual data and report every mismatch."""
```

Reads `DataContract.model_validate_json(paths.contract_snapshot_json.read_text())` (D-c). Missing file
→ let `FileNotFoundError` propagate (`transient`, per the decorator). A snapshot that fails validation
is an orchestrator bug, and propagates as `bug`.

Mismatches, each a `{"field": <dotted contract path>, "message": <str>}`. The `field` is the path a
person would edit: `features.roles.age`, not `roles`.

| # | Check | Fires when |
|---|---|---|
| C1 | every column the contract names exists | for each of: `target.column`; `features.include`, `.exclude`, `.id_columns`; keys of `.roles` and `.ordinal_levels`; each `objective.inference_goals[i].predictors`; `split.time_column`, `.group_column`; keys of `preprocessing.missing.per_column` |
| C2 | target not entirely null | |
| C3 | `binary` target has exactly 2 distinct non-null values | |
| C4 | `multiclass` target has ≥ 3 | |
| C5 | `continuous` / `count` target is numeric | |
| C6 | `count` target is non-negative and integral | |
| C7 | `positive_class`, when set, occurs in the target | |
| C8 | `class_order`, when set, equals the observed set of target values | |
| C9 | a `numeric` role is on a numeric column | |
| C10 | a `datetime` role parses (R7's rule, same threshold) | |
| C11 | `ordinal_levels[c]` ⊇ the observed non-null values of `c` | an unlisted level cannot be ordered |
| C12 | `split.time_column` parses as datetime | |

**C1 is the gate for the rest.** A check on a column that does not exist is skipped; C1 has already
reported it. A missing column must produce one mismatch, not five.

Checks fire only when the contract field is set — chunk 03's rule: absent means "agent will infer",
not invalid. `minimal.json` therefore produces at most a C1 on `target.column`.

`positive_class` comparison: the contract holds JSON (`1`, `"yes"`), the frame holds `int64` or `str`.
Compare after `str()` on both sides, and say so in a comment — `1 == "1"` is the bug this prevents.

Intra-contract consistency (target in `exclude`, etc.) is **not** checked here: chunk 03's 27 validators
already reject such a contract at parse time.

Summary: `valid` (bool, `len(mismatches) == 0`), `mismatches` (list, in C-order then column order — a
test pins the order so output is stable), `checked` (count). `valid` is derived from `mismatches`, not
tracked separately (convention 4).

### 3.5 `detect_id_columns`

```python
def detect_id_columns(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect.")],
) -> ToolSuccess:
    """Flag near-unique columns that identify rows rather than describe them."""
```

A column is a candidate when its unique share (`n_unique / n_non_null`) ≥ `ID_UNIQUE_SHARE = 0.95`
**and** it is integer or string dtype. Floats are excluded — a continuous measurement is near-unique by
nature. Columns with fewer than `MIN_ROWS_FOR_ID = 20` non-null rows are skipped: uniqueness means
nothing on five rows.

Evidence strengthens, never decides: `"name matches id pattern"` when the lowercased name is `id` or
ends in `_id` / `id` / `uuid`; `"monotonic integer sequence"` when an integer column is strictly
increasing. Name and monotonicity alone never flag a column.

Summary: `candidates` — list of `{"column", "unique_share", "evidence": [str]}`, sorted by
`unique_share` descending then name.

### 3.6 `detect_leaky_columns`

```python
def detect_leaky_columns(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect.")],
    target: Annotated[str, Field(min_length=1, description="The target column.")],
) -> ToolSuccess:
    """Flag columns that predict the target suspiciously well on their own."""
```

`target` not in the frame → `ToolParamError(field_errors={"target": ...})`.

A leak here means **one column, alone, nearly determines the target** — usually because it was recorded
after the outcome. §6.3 separates this from split leakage (`verify_split`, chunk 10).

| # | Rule | Applies to | Flag when |
|---|---|---|---|
| L1 | identical to the target | every column | values equal on every row (after `str()` alignment) — reported as `"identical to target"` |
| L2 | rank correlation | numeric feature, numeric target with > `CLASS_TARGET_MAX_LEVELS = 20` distinct values | `abs(f.rank().corr(t.rank())) >= LEAK_CORRELATION = 0.98` (P6: rank form, `>=`) |
| L3 | level purity | any feature, class target (≤ 20 distinct values) | numeric features first binned with `pd.qcut(q=10, duplicates="drop")`; purity = share of rows whose target equals their level's majority target; flag when purity ≥ `LEAK_PURITY = 0.99` **and** the overall majority share < `0.95` |
| L4 | name contains the target's name | every column | evidence only — never flags alone |

L3's majority-share guard matters: on a 99 %-negative target every feature is "99 % pure". L3 also
skips features with unique share > `0.5` — those are ID-like, `detect_id_columns`' concern, and a
unique key is trivially 100 % pure.

Rows where the target is null are dropped before L2/L3. Summary: `suspects` — list of
`{"column", "rule", "score", "evidence": [str]}`, sorted by score descending.

**This is a heuristic and its docstring must say so.** A flagged column is a question for node 1's gate,
not a verdict; a column that genuinely predicts the outcome is the whole point of modelling.

### 3.7 `tests/test_boundary_compute_libs.py` (D-b)

Source scan, same technique as the existing boundary tests: no `.py` under `backend/` or `contracts/`
imports `pandas`, `numpy`, `sklearn`, `scipy`, `statsmodels`, `matplotlib` or `joblib`. Match import
statements by regex (`^\s*(import|from)\s+<lib>\b`), not substrings — prose may mention them.

---

## 4. Test Suite Spec

Target **~150 new tests**, total ≥ **1262**.

### 4.1 Fixtures (`conftest.py`)

```python
@pytest.fixture
def churn_frame() -> pd.DataFrame:
    """240 rows, seeded, matching the seven columns full.json names, with known properties:
    customer_id unique and monotonic; age int; plan with 3 levels; signup_date ISO strings;
    satisfaction in {low, medium, high}; notes_free_text long unique strings; churned 0/1 at
    roughly 30 %. Built in code, not checked in, so the properties are readable."""

@pytest.fixture
def seeded_run(tmp_path) -> RunPaths:
    """A run directory with layout created and a dataset CSV written under datasets/<sha256>.csv
    (sha256 of the file's bytes). Returns the RunPaths; the sha is on an attribute or a second
    fixture."""

@pytest.fixture
def write_snapshot(seeded_run) -> Callable[[DataContract], None]:
    """Writes a contract to the run's contract.snapshot.json via model_dump_json()."""
```

Call tools through the decorated callable directly for unit tests. Add a small number of `mcp_session`
round-trips (M2's acceptance) — not one per case.

### 4.2 `test_tools_intake_load.py` (~45)

| # | Test | Expect |
|---|---|---|
| L1 | happy path | `raw` artifact exists, round-trips, summary rows/cols/columns/dtypes correct |
| L2 | each default equals `DatasetOptions()` | parametrised over the five options, derived from `model_fields` |
| L3 | `dataset` not hex / wrong length / uppercase / trailing newline | rejected **by the schema** — assert via a real `mcp_session` call that the body never runs |
| L4 | symlink in `datasets/` pointing outside | `tool_param` on `dataset` |
| L5 | dataset file missing | `data` |
| L6 | latin-1 file read as utf-8 | `tool_param`, `field_errors` has `encoding` — **then** the same call with `encoding="latin-1"` succeeds. This is the repair loop working end to end |
| L7 | unknown codec name | `tool_param` on `encoding` |
| L8 | empty file | `data` |
| L9 | ragged row | `data`, message names the line |
| L10 | duplicate headers | `data`, names the duplicate — and with `header_row=1` too |
| L11 | `;`-separated read with `,` | `tool_param` on `delimiter`, message suggests `;` — then retry with `;` succeeds |
| L12 | genuine single column | `data` |
| L13 | header only | `data` |
| L14 | **P4**: `"None"` and `"nan"` strings survive | not in `na_values` → still strings; added to `na_values` → missing |
| L15 | contract `na_values` like `-999` honoured | |
| L16 | `decimal=","` with `delimiter=";"` | float column parsed |
| L17 | BOM | first column name has no `\ufeff` |
| L18 | > 200 000 rows warns | monkeypatch `ROW_WARNING_THRESHOLD` low rather than writing 200k rows |
| L19 | dates stay `str` | `load_csv` does not convert |
| L20 | no `ValueError` escapes as `bug` | for each P1 case, `error_class != "bug"` |

### 4.3 `test_tools_intake_roles.py` (~35)

One test per rule R1–R9 with a passing case, plus: rule order (a 0/1 numeric column is `boolean` not
`numeric`; an ISO-date string column is `datetime` not `categorical`); R7 below threshold
(94 % parseable → `categorical`); non-ISO dates (`03/01/2026`) are **not** `datetime`; `ordinal` never
appears for any input (derive a broad frame, assert); every role is in `get_args(ColumnRole)`; every
column has an evidence string; `churn_frame` produces the roles its docstring promises.

### 4.4 `test_tools_intake_validate.py` (~40)

Each of C1–C12 with one passing and one failing case. Plus:

| # | Test | Expect |
|---|---|---|
| V1 | `full.json` against `churn_frame` | `valid=True`, zero mismatches |
| V2 | **C1 gates the rest** | rename `age` away; exactly one mismatch for `age`, not one per check |
| V3 | several mismatches reported together | three planted problems → three entries, one call |
| V4 | `field` is the dotted path | `features.roles.age`, `objective.inference_goals.0.predictors` |
| V5 | stable order | same input twice → identical list |
| V6 | `minimal.json` | checks only what it sets |
| V7 | `positive_class=1` against `int64` 0/1 | no mismatch — the `str()` alignment |
| V8 | no snapshot | `transient` |
| V9 | `valid` derived | no code path sets it independently (assert by constructing the only two shapes) |

### 4.5 `test_tools_intake_detect.py` (~30)

IDs: unique int column flagged; float near-unique not flagged; < 20 rows skipped; name-only not
flagged; evidence lists name and monotonic hints; sort order. Leaks: L1 copy of target flagged; L2 on a
numeric target at 0.99 flagged, at 0.90 not; L3 a post-outcome categorical flagged; **L3's guard** — a
99 %-negative target flags nothing; an ID column is not an L3 suspect; L4 name-only not flagged; null
target rows dropped; unknown `target` → `tool_param`; `churn_frame` produces no suspects.

### 4.6 Boundary (~5)

`test_boundary_compute_libs.py` per §3.7, plus: `tools/intake.py` imports no `statlab.*`; it is
registered (appears in `tools/list` over a real session with all five names and `meta.phase ==
"intake"`).

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **1262**.
- [ ] Coverage **100 %** line and branch; project stays 100 %.
- [ ] L6 and L11 prove the repair loop: a `tool_param` failure, then success with the suggested value.
- [ ] L3 proves a malformed `dataset` never reaches the tool body, over real MCP.
- [ ] L14 proves P4. V2 proves C1 gates the rest. L3-guard proves the imbalanced-target case.

**Static analysis**
- [ ] `make typecheck`, `make lint` exit 0.
- [ ] No `Any`, no `pragma: no cover`. `# type: ignore` only in the four permitted shapes
      (`WHATS_NEXT.md`).
- [ ] No `dtype == object` anywhere in `tools/intake.py`.

**Boundary**
- [ ] `test_boundary_compute_libs.py` passes: no compute library under `backend/` or `contracts/`.
- [ ] `tools/intake.py` imports no `statlab.*`.

**Behaviour**
- [ ] A CSV loads to a `raw` artifact over a real MCP session (M2).
- [ ] No pandas `ValueError` subclass is ever classified `bug`.
- [ ] Every threshold is a named constant.

**Housekeeping**
- [ ] `PLAN.md` §6.3: `load_csv(dataset, delimiter, ...)` and `validate_contract(df)` with a one-line note
      each pointing at D-a and D-c. §10.1: drop `<sha256>.parquet`, state the upload is
      `<sha256>.csv`.
- [ ] `00_index.md` chunk 33 row: "stores the original upload as `datasets/<sha256>.csv` (chunk 08
      D-a)".
- [ ] `WHATS_NEXT.md` conventions: replace "pandas appears in exactly one module" with D-b's rule.
- [ ] Frontmatter, `00_index.md` chunk 08 row, `WHATS_NEXT.md` state table.

### Stop and ask if

- Any of P1–P6 does not hold on your machine. They were probed on pandas 3.0.5.
- A real tool needs to *modify* the frame. Intake reports; it does not change data.
- `validate_contract` needs information the contract snapshot does not hold.
- A heuristic threshold produces a false positive on `churn_frame`. That means the threshold is wrong,
  not the fixture.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. The `input_schema` of `load_csv`, verbatim — I want to see the pattern and the orchestrator-supplied
   description on `dataset`.
3. How L3 proves the body never runs for a malformed `dataset`.
4. Whether any P1–P6 behaved differently than recorded.
5. Which heuristic thresholds you would change after writing the tests, and why. Answer from what the
   tests showed you, not from principle.
6. Whether D-c (contract from the snapshot, not a parameter) felt right once `validate_contract` was
   written, or whether it made testing awkward.
7. For chunk 09 (profiling): these five tools all re-read `raw` with `load_dataframe`. Did repeated
   loading show up as a cost worth caring about at 240 rows, and is there anything in how `raw` is
   produced that profiling will need but does not get?
