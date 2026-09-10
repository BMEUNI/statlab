---
status: untouched
dependencies: [01, 02]
completion_date: null
implementation_notes: ""
---

# Chunk 05 — SQLite persistence: schema, pragmas, migrations, repositories

**Goal.** A correctly configured database that survives concurrent runs. `PLAN.md` §10.2 plus D17.

First chunk with IO, so the tests change character: not "does this validate" but "does this survive
a rollback, a second writer, and a restart". Three failure modes drive the design — pragmas that
silently do not apply, a write-lock upgrade deadlock that `busy_timeout` cannot rescue, and a JSON
round-trip that silently changes a candidate's identity.

---

## 1. Prerequisites & Context

### State after chunk 04 (verified, committed at `cd13adc`)

564 tests, 100 % line and branch coverage, mypy clean on 31 files, ruff clean,
`filterwarnings = ["error"]` with zero ignores.

Relevant existing surface:

| Symbol | Location | Use here |
|---|---|---|
| `Settings.db_path`, `Settings.data_dir` | `backend/statlab/config.py` | resolved absolute already, by `_absolute` |
| `Settings.max_concurrent_runs` | same | `0` means unbounded (D17); not consumed here |
| `ensure_writable` | same | `load_settings` already calls it on `db_path.parent` |
| `StatlabError` | `backend/statlab/errors.py` | base for the new `PersistenceError` |
| `NodeStatus` | `backend/statlab/domains/base.py` | **reuse, do not redefine** — see §3.5 |
| `DataContract` | `contracts/statlab_contracts/contract.py` | stored in `contracts.json` |
| `CandidateSpec` | `contracts/statlab_contracts/candidate.py` | its `spec_hash` is what §3.7 protects |

### Non-negotiable environment facts

- **Never test against `:memory:`.** In-memory databases do not support WAL, so every pragma and
  concurrency test would pass vacuously while proving nothing. Use `tmp_path` file databases
  throughout. This is the single easiest way to make this whole chunk worthless.
- Python 3.14's `sqlite3`. `datetime.utcnow()` is deprecated and `filterwarnings=["error"]` turns
  its warning into a failure — use `datetime.now(UTC)`.
- Chunk 04's finding, now a requirement: JSON columns holding hashed payloads must store
  `model_dump_json()` output **verbatim**, never re-serialised through another path.

### What this chunk does NOT do

| Not here | Where | Why |
|---|---|---|
| Repositories for 11 of the 15 tables | the chunk that first writes to them | §3.6. Writing them now means 11 test suites for callers that do not exist |
| Candidate ID minting (`c_01`) | chunk 22 | Sequential and human-readable, unlike `new_id`; the decision belongs with the engine that creates waves |
| Artifact file IO | chunk 06 | This chunk stores artifact *rows*; bytes are chunk 06's |
| Run-folder layout (§10.1) | chunk 06 | |
| The fit semaphore and `concurrency_at_start` *values* | chunks 21/24 | The column exists here; who fills it is the engine's business |
| Any API or HTTP concern | chunks 33–35 | |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `backend/statlab/persistence/__init__.py` |
| create | `backend/statlab/persistence/db.py` — connect, pragmas, `transaction`, migrations |
| create | `backend/statlab/persistence/ids.py` — `new_id`, `utc_now`, `parse_ts` |
| create | `backend/statlab/persistence/models.py` — row models, `RunStatus`, `RunMode` |
| create | `backend/statlab/persistence/repository.py` — the four spine repositories |
| create | `backend/statlab/persistence/migrations/001_initial.sql` |
| **modify** | `backend/statlab/errors.py` — add `PersistenceError`, `MigrationError` |
| **modify** | `pyproject.toml` — package-data for `*.sql` (§3.3) |
| create | `tests/test_db_connection.py` |
| create | `tests/test_db_migrations.py` |
| create | `tests/test_db_ids.py` |
| create | `tests/test_db_schema.py` |
| create | `tests/test_repository.py` |
| create | `tests/test_db_concurrency.py` |
| **modify** | `tests/conftest.py` — `db_path`, `migrated_db` fixtures |

Nothing in `contracts/` or `mcp_server/` changes. `mcp_server/` must never import
`statlab.persistence` — the MCP server is stateless (D26) and a test asserts it.

---

## 3. Implementation Spec

### 3.1 Connection and pragmas — `db.py`

```python
def connect(path: Path) -> sqlite3.Connection:
    """Open `path` with the pragmas D17 requires, and PEP 249 transaction control.

    `isolation_level=None` puts the driver in autocommit mode so transactions are begun
    explicitly by `transaction()`. Without this, sqlite3's legacy control begins a DEFERRED
    transaction implicitly and swallows the choice §3.2 depends on.
    """
```

Applied in this order, before any transaction:

| Pragma | Value | Note |
|---|---|---|
| `journal_mode` | `WAL` | Persisted in the file, so idempotent, but must be set outside a transaction or it silently fails |
| `busy_timeout` | `5000` | **Per connection.** Set on every connect or concurrent writers raise immediately |
| `foreign_keys` | `ON` | **Per connection, and OFF by default.** Forgetting it makes every FK in the schema decorative |
| `synchronous` | `NORMAL` | Safe with WAL, and the default `FULL` fsyncs on every commit — needlessly slow for a local single-user app |

`row_factory = sqlite3.Row` so rows map to dicts for pydantic.

**Verify `journal_mode` actually took.** Read it back with `PRAGMA journal_mode` and raise
`PersistenceError` if it is not `wal`. This is not defensive noise: the pragma is a silent no-op on
a filesystem without shared-memory support (some network mounts), and a run executing without WAL
under D17's unbounded concurrency corrupts rather than fails.

No global connection object, no thread-local caching. `connect()` returns a fresh connection and
the caller closes it. Opening SQLite is microseconds and a shared connection across threads is the
bug this design refuses to have.

### 3.2 `transaction()` — and why `BEGIN IMMEDIATE`

```python
@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write transaction, committing on success and rolling back on any exception.

    Issues `BEGIN IMMEDIATE`, not the driver's default DEFERRED. This is the load-bearing
    choice in this chunk.

    A DEFERRED transaction takes no lock until its first statement, and takes a *read* lock if
    that statement is a SELECT. When it later writes, it must upgrade to a write lock — and if
    another connection already holds one, SQLite returns SQLITE_BUSY **immediately, ignoring
    busy_timeout**, because waiting could only deadlock: both sides would be holding read locks
    neither can release. `BEGIN IMMEDIATE` takes the write lock upfront, so contention becomes a
    wait that busy_timeout resolves rather than an instant error.

    Under D17's unbounded parallel runs this is the difference between two runs serialising and
    two runs failing.
    """
```

Reads need no transaction and must not be wrapped in one — an autocommit `SELECT` under WAL never
blocks a writer, which is the whole reason WAL was chosen.

Nesting is not supported: `BEGIN` inside a transaction is an error. Do not add re-entrancy — if a
caller needs it, the caller's transaction boundaries are wrong.

### 3.3 Migration runner — `db.py`

Forward-only. No down-migrations: this is a local single-user app with `make db-reset`, and a
rollback path nobody exercises is a rollback path that does not work.

```python
def migrate(conn: sqlite3.Connection) -> tuple[int, ...]:
    """Apply every pending migration in version order. Returns the versions applied.

    Idempotent: a second call on the same database applies nothing and returns ().
    """
```

Mechanism:

1. Create `schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)`
   if absent.
2. Discover `migrations/*.sql`, parse `NNN_name.sql`, sort by the **integer** prefix — not the
   string, or `010` sorts before `009`.
3. For each version absent from `schema_migrations`: apply the file's statements and insert the
   row **in one `transaction()`**, so a failing migration leaves no partial schema.
4. Raise `MigrationError` naming the file and the underlying `sqlite3.Error` on failure.

Reject these at runtime, each with a distinct message: a duplicate version number across two
files, a filename not matching `^(\d{3})_([a-z0-9_]+)\.sql$`, and a database whose recorded version
is absent from disk (a downgrade, meaning the code is older than the data).

Files are located with `importlib.resources.files("statlab.persistence") / "migrations"`, not
`Path(__file__).parent` — the former works when the package is zipped or installed. `pyproject.toml`
needs the `*.sql` package-data entry for this to work from an installed distribution; add it and
say why in a comment.

### 3.4 IDs and timestamps — `ids.py`

```python
def new_id(prefix: str) -> str:
    """A collision-resistant id, `<prefix>_<16 lowercase hex>`.

    Same shape as chunk 02's ARTIFACT_ID_PATTERN (`^a_[0-9a-f]{16}$`) so every id in the system
    reads alike. 64 bits of `secrets.token_hex(8)` — random, not sequential, because sequential
    ids would need a counter that two concurrent runs must coordinate on.
    """

def utc_now() -> str:
    """Current time as an ISO-8601 UTC string, the storage format for every `*_at` column.

    SQLite has no datetime type. ISO-8601 text sorts chronologically as text, is readable in any
    DB browser, and round-trips through `datetime.fromisoformat` — which the epoch-integer
    alternative does not do for the first two.
    """

def parse_ts(value: str) -> datetime:
    """Inverse of `utc_now`. Returns a timezone-aware datetime; raises PersistenceError if the
    stored text is not parseable or not timezone-aware."""
```

Prefixes, defined as a module constant so they are greppable: `p` project, `ds` dataset,
`ct` contract, `r` run, `s` session, `m` message, `ne` node execution, `tc` tool call, `mo` model,
`me` metric, `g` gate, `lc` llm call, `mem` memory entry. Candidates and artifacts are excluded
deliberately — artifacts are chunk 06's registry (`a_`), candidates are chunk 22's `c_01`.

A naive-datetime rejection in `parse_ts` matters: a naive timestamp compared against an aware one
raises `TypeError` at some distant call site, and the useful place to fail is on read.

### 3.5 Status vocabularies — `models.py`

```python
RunStatus = Literal["pending", "running", "awaiting_user", "paused", "failed",
                    "cancelled", "completed"]
RunMode = Literal["guided", "autonomous"]
```

**`NodeStatus` already exists** in `backend/statlab/domains/base.py` from chunk 02. Import it;
do not define a second one. Verify its members equal `PLAN.md` §10.2's node set
(`pending, running, awaiting_user, skipped, failed, completed`) and **report the comparison** — if
they differ, stop and ask, because that is a real inconsistency between the spine and the schema
rather than something to paper over.

Every status column carries a `CHECK (... IN (...))` constraint in the DDL. That duplicates the
Literal in SQL text, which §3.8's drift test exists to police.

### 3.6 Schema — `migrations/001_initial.sql`

All 15 tables from `PLAN.md` §10.2, verbatim in column names and order. The full schema ships in
one migration even though only four tables get repositories, because a schema is declarative and
nearly free, while adding tables later means a migration per chunk and 35 migrations by the end.

Requirements:

- Every `*_id` column referencing another table gets a real `REFERENCES` clause.
- `ON DELETE CASCADE` for rows meaningless without their parent (a run's `node_executions`,
  `tool_calls`, `candidates`, `metrics`, `models`, `artifacts`, `gates`, `llm_calls`).
  `runs.forked_from_run_id` is `ON DELETE SET NULL` — a fork outlives its parent's deletion, since
  D11 hard-links the artifacts and the child is independently valid.
- `node_executions.candidate_id` is nullable: shared nodes (1–6, 12–13) have no candidate. This is
  the schema's expression of D26's shared/per-candidate split.
- `runs.champion_model_id` is nullable and set only at node 12.
- `NOT NULL` on everything not genuinely optional. Lean on the database.
- `UNIQUE` where it is true: `projects.slug`, `datasets(project_id, sha256)`,
  `contracts(project_id, version)`, `candidates(run_id, label)`,
  `node_executions(run_id, candidate_id, node_name, attempt)`,
  `metrics(run_id, model_id, split, name)`.
- Indexes on the FK columns that get queried by parent: `runs(project_id)`,
  `candidates(run_id)`, `node_executions(run_id)`, `tool_calls(run_id)`,
  `tool_calls(node_execution_id)`, `artifacts(run_id)`, `metrics(run_id)`, `models(run_id)`,
  `messages(session_id)`, `llm_calls(run_id)`, `memory(project_id)`. No speculative indexes.

`datasets(project_id, sha256)` being unique is what makes re-uploading the same file idempotent
rather than a duplicate row, which matters because the frontend has no upload de-dup.

### 3.7 Row models and JSON fidelity — `models.py`

One frozen pydantic model per repository-backed table: `ProjectRow`, `DatasetRow`, `ContractRow`,
`RunRow`. `ConfigDict(frozen=True, extra="forbid")` — `extra="forbid"` catches a migration that
added a column the model does not know about.

**JSON columns are typed `str`, holding verbatim `model_dump_json()` output.** Not
`pydantic.Json[T]`, which would parse on read and re-serialise on write through a different code
path. Parsing is an explicit accessor:

```python
class ContractRow(BaseModel):
    ...
    json: str
    """Verbatim `DataContract.model_dump_json()`. Stored as text, never re-serialised.

    Chunk 04 established why: `CandidateSpec.spec_hash` is a sha256 over canonical JSON, so a
    store-then-reload path that re-serialises through a different code path can change a
    candidate's identity, silently breaking dedupe across a restart — producing duplicate work
    and no error message. Treating stored JSON as opaque text makes that class of bug
    impossible rather than merely unlikely.
    """

    def contract(self) -> DataContract:
        """Parse the stored JSON. Raises PersistenceError on invalid JSON, which means the row
        predates a schema change or was hand-edited."""
```

Required test: write a `CandidateSpec`'s JSON into a text column, read it back,
`model_validate_json` it, and assert the `spec_hash` is unchanged **and** the retrieved text is
byte-identical to what was written.

### 3.8 Repositories — `repository.py`

Plain module-level functions taking `conn` first. No repository classes, no base class, no ORM,
no session object — a class holding one connection attribute is a namespace with extra steps.

```python
def create_project(conn: sqlite3.Connection, *, slug: str, name: str) -> ProjectRow: ...
def get_project(conn: sqlite3.Connection, project_id: str) -> ProjectRow | None: ...
def get_project_by_slug(conn: sqlite3.Connection, slug: str) -> ProjectRow | None: ...
def list_projects(conn: sqlite3.Connection) -> tuple[ProjectRow, ...]: ...

def create_dataset(conn, *, project_id: str, filename: str, path: Path, sha256: str,
                   n_rows: int, n_cols: int) -> DatasetRow: ...
def get_dataset(conn, dataset_id: str) -> DatasetRow | None: ...
def find_dataset_by_hash(conn, *, project_id: str, sha256: str) -> DatasetRow | None: ...

def create_contract(conn, *, project_id: str, path: Path, contract: DataContract) -> ContractRow: ...
def get_contract(conn, contract_id: str) -> ContractRow | None: ...
def latest_contract(conn, project_id: str) -> ContractRow | None: ...

def create_run(conn, *, project_id: str, contract_id: str, mode: RunMode,
               config_json: str, forked_from_run_id: str | None = None) -> RunRow: ...
def get_run(conn, run_id: str) -> RunRow | None: ...
def list_runs(conn, project_id: str) -> tuple[RunRow, ...]: ...
def update_run_status(conn, run_id: str, *, status: RunStatus,
                      current_node: str | None = None) -> RunRow: ...
```

Conventions later chunks must follow, so state them here:

- `get_*` returns `None` when absent; `create_*` and `update_*` return the written row read back
  from the database, so the caller sees defaults and generated ids rather than a hopeful echo.
- Absence is `None`, not an exception. A missing project is a 404 the API formats, not an error the
  data layer decides is exceptional.
- `create_contract` derives `version` as `MAX(version) + 1` for the project inside its own
  transaction, so two concurrent uploads cannot both claim v1. Test this with two threads.
- `update_run_status` raises `PersistenceError` on an unknown `run_id` — updating nothing is a bug
  in the caller, unlike reading nothing.
- Every function opens its own `transaction()` for writes; none commits implicitly.
- Wrap `sqlite3.IntegrityError` in `PersistenceError` naming the constraint. A unique-slug
  collision must not surface as a raw driver exception three layers up.

**Drift test (required).** Parse `001_initial.sql` and assert each status column's `CHECK (... IN
(...))` values equal `get_args` of the corresponding Literal. Adding a status to the Python type
without the DDL yields an `IntegrityError` in production on a rare path; this makes it a test
failure. Same pattern as chunk 04's `DIMENSION_FOR_FIELD` exhaustiveness — derived, not hand-listed.

### 3.9 Errors — `errors.py` additions

```python
class PersistenceError(StatlabError):
    """Database operation failed. Wraps sqlite3 errors at the persistence boundary."""

class MigrationError(PersistenceError):
    """A migration could not be applied. Carries `version` and `name`."""
```

Per chunk 02's precedent, `PersistenceError` is a plain subclass with no structured payload;
`MigrationError` carries `version` and `name` because a caller (and `make db-reset`) needs to say
which migration failed.

---

## 4. Test Suite Spec

Target **~180 new tests**, total ≥ **744**. Every test uses a `tmp_path` file database.

### 4.1 `conftest.py` additions

```python
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A file path for a fresh database. NOT ':memory:' — WAL is unsupported there and every
    pragma and concurrency assertion in this chunk would pass vacuously."""

@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open, pragma-configured, unmigrated connection. Closed on teardown."""

@pytest.fixture
def migrated_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open connection with all migrations applied."""

@pytest.fixture
def project(migrated_db: sqlite3.Connection) -> ProjectRow:
    """A persisted project, for tests needing a valid parent FK."""
```

### 4.2 `test_db_connection.py` (~24)

| # | Test | Assertion |
|---|---|---|
| N1 | `journal_mode` is `wal` | read back via pragma |
| N2 | `busy_timeout` is 5000 | read back |
| N3 | `foreign_keys` is on | read back — the one most likely to be forgotten |
| N4 | `synchronous` is 1 (NORMAL) | read back |
| N5 | `row_factory` is `sqlite3.Row` | identity |
| N6 | two connections to one path both get WAL | pragmas are per-connection; prove the second is configured too |
| N7 | FK enforcement is real | inserting a child with a bogus parent id raises |
| N8 | non-WAL filesystem | monkeypatch the pragma read to return `delete`; `connect` raises `PersistenceError` |
| N9 | `connect` on an unwritable directory | `PersistenceError`, not a bare `sqlite3.OperationalError` |
| N10 | reopening an existing DB preserves WAL | idempotent |
| N11–N14 | `transaction()` commits on success | row visible from a second connection after exit |
| N15–N18 | `transaction()` rolls back on exception | exception propagates **and** no row is visible |
| N19 | rollback on `PersistenceError` specifically | the wrapped-error path still rolls back |
| N20 | nested `transaction()` raises | pin the documented non-support |
| N21 | a `SELECT` outside any transaction works | reads need no transaction |
| N22 | `transaction` uses IMMEDIATE | assert via `sqlite3.Connection.in_transaction` plus a second connection observing the write lock; if that is not observable, assert on the issued SQL and say so in the report |

### 4.3 `test_db_migrations.py` (~28)

| # | Test | Expect |
|---|---|---|
| M1 | `migrate` on an empty DB returns `(1,)` | |
| M2 | all 15 tables exist afterwards | query `sqlite_master`, compare against a set derived from the SQL file, not hand-typed |
| M3 | `schema_migrations` has one row with a parseable `applied_at` | |
| M4 | second `migrate` returns `()` | idempotence, the property that makes startup safe |
| M5 | integer ordering | synthetic `009`/`010` fixtures apply in numeric order |
| M6 | a failing migration leaves no partial schema | synthetic SQL with a valid statement then a syntax error; assert the first statement's table is absent and `schema_migrations` is empty |
| M7 | `MigrationError` names file and version | |
| M8 | duplicate version numbers | `MigrationError` |
| M9 | malformed filename | `MigrationError` naming the file |
| M10 | recorded version absent on disk | `MigrationError` mentioning a downgrade |
| M11 | migrations load via `importlib.resources` | assert the discovered path resolves inside the package |
| M12 | `001_initial.sql` is valid SQL | applies with no error |
| M13 | every FK in the schema is enforceable | for each `REFERENCES`, insert a child with a bad parent and assert `IntegrityError`. Derive the pairs by introspecting `PRAGMA foreign_key_list`, do not hand-list them |
| M14 | CASCADE deletes work | delete a project, assert its runs and their children vanish |
| M15 | `forked_from_run_id` is SET NULL, not CASCADE | delete a parent run, assert the fork survives with a null parent — D11's requirement, and the easiest one to get backwards |

### 4.4 `test_db_ids.py` (~16)

`new_id` format, prefix preservation, uniqueness across 10 000 calls, lowercase hex, and that its
shape matches chunk 02's `ARTIFACT_ID_PATTERN` when the prefix is `a`. `utc_now` returns
UTC-aware ISO text; `parse_ts` inverts it; text ordering matches chronological ordering for
timestamps spanning a year; `parse_ts` rejects naive text, empty text, and non-timestamps with
`PersistenceError`.

### 4.5 `test_db_schema.py` (~26)

Column names and order per table match `PLAN.md` §10.2, driven by a table→columns mapping in the
test read from `PRAGMA table_info`. Nullability is as specified: assert `node_executions.candidate_id`,
`runs.champion_model_id`, `runs.ended_at` and `runs.forked_from_run_id` are nullable and that
`runs.project_id`, `runs.status` and every `*_at` creation timestamp are not. Every `UNIQUE` from
§3.6 rejects a duplicate. Every declared index exists. Plus the §3.8 CHECK-versus-Literal drift
test for `runs.status` and `node_executions.status`.

### 4.6 `test_repository.py` (~56)

Per entity: create-then-get round-trip, `get_*` returning `None` for an unknown id, list ordering,
and the unique-constraint violation surfacing as `PersistenceError` naming the constraint. Then:

| # | Test | Expect |
|---|---|---|
| R1 | `create_project` returns the row read back | generated id matches `new_id` shape, `created_at` parseable |
| R2 | duplicate slug | `PersistenceError` naming `slug`, not a raw `IntegrityError` |
| R3 | `get_project_by_slug` | found and not-found |
| R4 | `find_dataset_by_hash` | makes re-upload idempotent |
| R5 | same sha256, different project | allowed — the unique is composite |
| R6 | `create_contract` assigns version 1 then 2 | monotonic per project |
| R7 | versions are per project, not global | two projects both start at 1 |
| R8 | `latest_contract` | returns the highest version, not the newest `created_at` |
| R9 | **JSON byte fidelity** | `create_contract` then `get_contract`: stored text is byte-identical to `contract.model_dump_json()`, and `ContractRow.contract()` equals the original |
| R10 | **`spec_hash` survives storage** | store a `CandidateSpec`'s JSON in a text column, reload, `model_validate_json`, assert `spec_hash` unchanged. Chunk 04 item 7's requirement, made a test |
| R11 | `ContractRow.contract()` on corrupt JSON | `PersistenceError` |
| R12 | `create_run` with a bad `contract_id` | `PersistenceError` from the FK |
| R13 | `update_run_status` returns the updated row | and `current_node` updates |
| R14 | `update_run_status` on an unknown id | `PersistenceError` — writing nothing is a caller bug |
| R15 | `update_run_status` with an invalid status | rejected by the CHECK, wrapped |
| R16 | `create_run` with `forked_from_run_id` | D11's fork lineage persists |
| R17 | `list_runs` is newest-first | pin the order the UI depends on |
| R18 | a failing repository call rolls back | patch to raise mid-transaction; assert nothing was written |

### 4.7 `test_db_concurrency.py` (~12)

The tests that justify the design. Real `threading.Thread`s, real file database.

| # | Test | Assertion |
|---|---|---|
| K1 | 8 threads × 10 `create_project` each | all 80 rows present, no exception. Fails if `busy_timeout` is unset |
| K2 | concurrent `create_contract` for one project | 8 threads; versions are exactly 1..8 with no duplicates. This is the test that catches a `MAX(version)+1` computed outside the transaction |
| K3 | reader during a long writer | a thread holding an open write transaction does not block a `SELECT` on another connection — WAL's entire purpose |
| K4 | write contention resolves by waiting | two threads each writing in a transaction both succeed |
| K5 | rollback under concurrency | one thread's failing transaction does not roll back another's work |
| K6 | WAL files appear | `-wal` and `-shm` exist beside the database while a connection is open |

If K2 fails, the bug is that the version query ran outside the write transaction — fix the
transaction boundary, do not add a retry loop.

### 4.8 Boundary test (add to the existing boundary suite)

`mcp_server/` must not import `statlab.persistence`. Same source-scan technique as the existing T9/T10
tests. The MCP server is stateless by D26; a tool reaching into the database would break the
locking guarantees chunk 21 enforces on the client side.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **744**.
- [ ] Coverage **100 %** line and branch on all five new modules; project stays 100 %.
- [ ] Zero tests use `:memory:`. Grep and confirm.
- [ ] The concurrency suite uses real threads and passes 10 consecutive runs — record that you ran
      it repeatedly, since a flaky lock test that passes once is not evidence.
- [ ] Schema assertions are derived from `PRAGMA table_info` / `foreign_key_list` /
      `sqlite_master`, not hand-typed lists.
- [ ] The CHECK-versus-Literal drift test exists and fails when a Literal member is added.

**Static analysis**
- [ ] `make typecheck` and `make lint` exit 0.
- [ ] No `Any`, no `type: ignore`, no `pragma: no cover`. If a line seems unreachable, it probably
      is — delete it, per chunk 04's S7.
- [ ] `sqlite3.Row` is converted to a typed model at the boundary; no untyped row escapes
      `repository.py`.

**Behaviour**
- [ ] `PRAGMA journal_mode` returns `wal`, verified on a real file database.
- [ ] `foreign_keys` is on for every connection, proven by an enforcement test.
- [ ] `transaction()` issues `BEGIN IMMEDIATE`.
- [ ] A failed migration leaves the schema untouched.
- [ ] Stored contract JSON is byte-identical to `model_dump_json()`, and a stored-then-reloaded
      `CandidateSpec` reproduces its `spec_hash`.

**Boundary**
- [ ] `mcp_server/` does not import `statlab.persistence`; `contracts/` unchanged.
- [ ] `persistence/` imports no pandas.

**Housekeeping**
- [ ] Frontmatter → `completed` with notes covering the seven report items below.
- [ ] `plan/00_index.md` chunk 05 row → `completed`.

### Stop and ask if

- `NodeStatus` from chunk 02 does not match `PLAN.md` §10.2's node status set.
- `BEGIN IMMEDIATE` cannot be asserted from Python without inspecting emitted SQL — I want to know
  what you could and could not observe rather than a test that pretends.
- Any `PLAN.md` §10.2 column cannot be given a sensible type or constraint, which would mean the
  schema is underspecified and needs a decision.
- A concurrency test is flaky rather than failing. Flaky means the design is wrong, and a retry or
  a `sleep` would hide it.

### Report back with

1. Test count, coverage, mypy source-file count, ruff output.
2. `NodeStatus` versus §10.2's node set — the actual comparison.
3. How you asserted `BEGIN IMMEDIATE`, and what was observable.
4. Whether K2 (concurrent contract versioning) passed first time, and if not, what the transaction
   boundary bug was.
5. Consecutive clean runs of the concurrency suite.
6. Any §10.2 column whose type or constraint required a judgement call, and what you chose.
7. For chunk 06 (artifact store): the artifacts table now exists but holds no `locked` column,
   because D26's test-set lock is enforced in `mcp_client` (chunk 21) rather than the schema. Say
   whether anything you saw suggests the lock needs a persisted column to survive a restart —
   a run resumed after a crash must not be able to score on test twice.
