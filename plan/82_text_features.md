---
status: in_progress
dependencies: [81]
completion_date: null
implementation_notes: "82a text vectorizer tools; 82b candidate field, design checks, pipeline wiring; 82c offline app-review scenario"
---

# Chunk 82 — text columns as competing feature methods (course integration)

Course integration (BME "Integrated LLMs", lectures 1–4): free-text columns become first-class
features, and the run compares the lecture's classical NLP methods against each other on the
shared split. The comparison engine, validation-only selection and the single test score (D32)
are unchanged; only the feature step learns new ways to turn text into numbers (PLAN D37).

Today: intake already tags a `text` role (`intake.py:475`), but feature engineering treats text
as categorical (`candidate_pipeline.py:430`): one-hot skips it as high-cardinality and the
target encoder then encodes each distinct string, which carries almost nothing.

Methods (candidate field `features.text_encoding`):

| value | what the text column becomes | lecture |
|---|---|---|
| `categorical` (default) | unchanged legacy path (categorical encoder) | — |
| `drop` | removed ("does text help at all?") | L3 "simplest model that meets the bar" |
| `lexicon` | per class: count of class keywords; keywords = top 25 words by add-one smoothed log-odds, learned on train, train output out-of-fold; classification only | L1 Ex 1.1, L3 Laplace smoothing |
| `tfidf` | TF-IDF of word 1–2-grams, ≥2 train rows, top 300 | L3 n-grams, sparsity |
| `hashed_chars` | 256 hashed character 3–5-gram buckets (handles unseen words/typos) | L3 FastText/floret hashing trick |

Non-goals: word vectors (spaCy/GloVe need a new dependency plus a model download; later
option), stop-word lists (language-dependent; IDF already down-weights common words), contract
-level text options (the contract's `features.roles` already lets a user mark a text column),
tunable vectorizer sizes (fixed constants; the LLM chooses only the method), frontend changes
(dimension strings render generically), the inferential matrix (node 6 unchanged).

Stages (sequential): **82a** tools → **82b** candidate field, design checks, pipeline wiring →
**82c** offline app-review scenario. 82b/82c detailed specs are written after the previous
stage's handoff.

## Stage 82a (detailed spec) — `fit_text_vectorizer` / `apply_text_vectorizer`

Owned files: `mcp_server/statlab_tools/tools/encoding.py`, `skills/modelling.md`,
`tests/test_tools_text.py` (new), `tests/test_tool_decorator.py`. Not alone: preserve others'
edits. No other file changes; no new dependency (sklearn is installed).

Reuse (all already in `encoding.py`'s scope): `_Train`, `_Target`, `_Frame`, `_FIT_PHASES`,
`_APPLY_PHASES`, `_FOLDS`, `_reject_target_in_columns`, `_save_transformer`, `_replace_columns`;
from `_frame`: `as_str`, `is_class_target`, `json_list`, `json_scalar`, `load_fitted`,
`require_columns`, `save_frame`; `ORCHESTRATOR_SUPPLIED`, `ToolDataError`, `ToolParamError`, `tool`.

### 1. `encoding.py`

a. Module docstring: first sentence becomes "The encoding tools: `PLAN.md` §6.3's `encoding`
   phase, this chunk's spec (`12_tools_encoding.md`) plus chunk 82's text vectorizer." Add one
   paragraph at the end of the docstring: "Chunk 82 adds `fit_text_vectorizer` /
   `apply_text_vectorizer`: a text-role column becomes numeric columns (keyword lexicon,
   TF-IDF or hashed character n-grams). The fit returns the encoder and the transformed train,
   like target encoding; the lexicon's train output is out-of-fold because it reads the target."

b. Imports: add `import math`, `import re`, `from collections import Counter` (stdlib block).
   Add `is_class_target` is already imported; nothing else new from `_frame`.

c. New section after section 3.4 (target encoder, i.e. after `apply_target_encoder`) and before
   `# --- 3.5 scaler`: header comment `# --- text (chunk 82, D37) ---`. Contents in this order:

   Constants:
   - `TextMethod = Literal["lexicon", "tfidf", "hashed_chars"]`
   - `_TOKEN: Final = re.compile(r"(?u)\b\w\w+\b")` — docstring: sklearn's default word token
     pattern, so the lexicon and TF-IDF see the same words. (Import `Final` from `typing`.)
   - `_LEXICON_WORDS: Final = 25` — keywords kept per class.
   - `_TFIDF_TERMS: Final = 300` — vocabulary cap.
   - `_HASH_BUCKETS: Final = 256` — hashed bucket count. Comment line above the three sizes:
     `# ponytail: fixed sizes; dense output is rows x size floats per text column, a parameter
     if a real dataset needs more.`

   Fitted state:
   ```
   @dataclass(frozen=True)
   class FittedTextVectorizer:
       target: str
       method: str
       columns: tuple[str, ...]
       vectorizers: dict[str, object]                     # tfidf / hashed_chars, per column
       keywords: dict[str, dict[str, tuple[str, ...]]]    # lexicon: column -> class -> words
       kept: dict[str, tuple[int, ...]]                   # column -> kept raw output indices
       output_columns: dict[str, tuple[str, ...]]         # column -> kept output names
       train_labels: pd.Index
   ```
   Docstring: what each field holds; `vectorizers` is typed `object` for the same reason as
   `FittedTargetEncoder.encoder`; `train_labels` lets apply refuse train rows.

   Private helpers (exact signatures):

   - `def _texts(series: pd.Series) -> list[str]`: `return [str(value) for value in
     as_str(series).fillna("")]`. A null becomes the empty string (no features).

   - `def _learn_lexicon(texts: list[str], labels: list[str], classes: tuple[str, ...]) ->
     dict[str, tuple[str, ...]]`. Algorithm, in order:
     1. `docs = [_TOKEN.findall(text.lower()) for text in texts]`.
     2. `document_frequency = Counter(word for tokens in docs for word in set(tokens))`;
        `vocabulary = {word for word, n in document_frequency.items() if n >= 2}`.
     3. `totals = Counter(word for tokens in docs for word in tokens if word in vocabulary)`;
        `size = len(vocabulary)`; `all_words = sum(totals.values())`.
     4. For each `cls` in `classes` (given order): `inside = Counter(word for tokens, label in
        zip(docs, labels, strict=True) if label == cls for word in tokens if word in
        vocabulary)`; `n_in = sum(inside.values())`; `n_out = all_words - n_in`;
        `scores = {word: math.log((inside[word] + 1) / (n_in + size)) - math.log((totals[word]
        - inside[word] + 1) / (n_out + size)) for word in vocabulary}`;
        `ranked = sorted((word for word, score in scores.items() if score > 0), key=lambda
        word: (-scores[word], word))`; result `[cls] = tuple(ranked[:_LEXICON_WORDS])`.
     5. Return the dict (keys in `classes` order). An empty vocabulary gives empty tuples (no
        error here; the caller's constant-output check reports it).
     Docstring: add-one (Laplace) smoothed log-odds of a word in the class versus the rest;
     only words in at least two train rows.

   - `def _lexicon_matrix(keywords: dict[str, tuple[str, ...]], texts: list[str]) ->
     np.ndarray`: `sets = [set(words) for words in keywords.values()]`; `rows = [[float(sum(1
     for token in _TOKEN.findall(text.lower()) if token in words)) for words in sets] for text
     in texts]`; `return np.asarray(rows, dtype="float64").reshape(len(texts), len(sets))`.

   - `def _raw_features(method: str, vectorizer: object, keywords: dict[str, tuple[str, ...]],
     texts: list[str]) -> np.ndarray`:
     if `method == "lexicon"`: return `_lexicon_matrix(keywords, texts)`. Else lazily import
     `from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer` (comment:
     lazy, see `fit_target_encoder` (scipy)); `model = cast(TfidfVectorizer |
     HashingVectorizer, vectorizer)` with the same "cast, not a runtime check" justification
     comment as `apply_target_encoder` (fit is the sole constructor); return
     `np.asarray(model.transform(texts).toarray(), dtype="float64")`.

   - `def _raw_names(method: str, column: str, vectorizer: object, classes: tuple[str, ...]) ->
     list[str]`:
     lexicon → `[f"{column}__lex_{cls}" for cls in classes]`;
     tfidf → lazy import as above, `model = cast(TfidfVectorizer, vectorizer)`,
     `[f"{column}__tfidf_{term.replace(' ', '+')}" for term in model.get_feature_names_out()]`
     (a bigram's space becomes `+`, which no word token contains, so names cannot collide);
     hashed_chars → `[f"{column}__hash_{index}" for index in range(_HASH_BUCKETS)]`.

d. Tool `fit_text_vectorizer`, decorated `@tool(phase="encoding", allow_in=_FIT_PHASES)`:
   ```
   def fit_text_vectorizer(
       paths: RunPaths,
       train: _Train,
       target: _Target,
       columns: Annotated[tuple[str, ...], Field(min_length=1, description="Text-role columns to vectorize.")],
       method: Annotated[TextMethod, Field(description="lexicon: class keyword counts learned on train (class targets only); tfidf: word 1-2-gram TF-IDF; hashed_chars: hashed character 3-5-grams.")],
       seed: Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)],
   ) -> ToolSuccess:
   ```
   Docstring (LLM-visible): one paragraph stating the three methods, that everything is learned
   on train, the lexicon's train output is out-of-fold (D34c), the fit returns the encoder and
   the transformed train (use that train; `apply_text_vectorizer` is for valid/test only), a
   null text is an empty text, and output columns that are constant on train are dropped.

   Body, in order:
   1. `data = load_dataframe(paths.artifact(train, "dataframe"))`; `require_columns(data,
      columns, "columns")`; `_reject_target_in_columns(columns, target)`.
   2. `classes: tuple[str, ...] = ()`; `labels: list[str] = []`. If `method == "lexicon"`:
      - `require_columns(data, (target,), "target")`;
      - if `not is_class_target(data[target])`: raise `ToolParamError("lexicon text features
        need a class target", field_errors={"method": "lexicon needs a class target"})`;
      - `labels = [str(value) for value in as_str(data[target])]`;
        `classes = tuple(sorted(set(labels)))`;
      - `smallest = min(Counter(labels).values())`; if `smallest < _FOLDS`: raise
        `ToolDataError(f"the smallest target class has {smallest} rows in train, fewer than the
        {_FOLDS} cross-fitting folds; the lexicon cannot cross-fit it")`.
   3. Initialise `vectorizers: dict[str, object] = {}`, `keywords: dict[str, dict[str,
      tuple[str, ...]]] = {}`, `kept: dict[str, tuple[int, ...]] = {}`, `output_columns:
      dict[str, tuple[str, ...]] = {}`, `matrices: dict[str, np.ndarray] = {}`.
   4. For each `column` in `columns` (given order):
      - `texts = _texts(data[column])`; `vectorizer: object = None`.
      - If `method == "lexicon"`:
        `keywords[column] = _learn_lexicon(texts, labels, classes)`;
        lazily `from sklearn.model_selection import StratifiedKFold`;
        `raw = np.zeros((len(texts), len(classes)), dtype="float64")`;
        `for fit_rows, held_rows in StratifiedKFold(_FOLDS, shuffle=True,
        random_state=seed).split(np.zeros(len(texts)), labels):`
        `fold_words = _learn_lexicon([texts[i] for i in fit_rows], [labels[i] for i in
        fit_rows], classes)`; `raw[held_rows] = _lexicon_matrix(fold_words, [texts[i] for i in
        held_rows])`.
      - Else: lazily import `HashingVectorizer, TfidfVectorizer`;
        `model = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=_TFIDF_TERMS,
        sublinear_tf=True)` if `method == "tfidf"` else `HashingVectorizer(analyzer="char_wb",
        ngram_range=(3, 5), n_features=_HASH_BUCKETS, alternate_sign=False)` (sklearn is
        untyped, so `model` is dynamically typed and `.fit` type-checks);
        `try: model.fit(texts)` `except ValueError: raise ToolDataError(f"text column
        {column!r} has no word that appears in at least 2 train rows") from None`;
        `vectorizers[column] = model`; `vectorizer = model`;
        `raw = _raw_features(method, model, {}, texts)`.
      - `names = _raw_names(method, column, vectorizer, classes)`.
      - `indices = tuple(int(i) for i in np.flatnonzero(np.ptp(raw, axis=0) > 0))`; if not
        `indices`: raise `ToolDataError(f"text column {column!r} gives no feature that varies
        across train rows")`.
      - `kept[column] = indices`; `output_columns[column] = tuple(names[i] for i in indices)`;
        `matrices[column] = raw[:, list(indices)]`.
   5. Collision check, after the loop and BEFORE any column is inserted (pandas `insert` would
      otherwise raise its own `ValueError` first): `made = [name for names in
      output_columns.values() for name in names]`; `collisions = sorted(set(made) &
      (set(data.columns) - set(columns)))`; if any: raise `ToolParamError(f"generated text
      feature names collide with existing columns: {collisions!r}", field_errors={"columns":
      f"name collision: {collisions!r}"})`. Then `encoded = data`; for each `column` in
      `columns`: `encoded = _replace_columns(encoded, (column,), pd.DataFrame(matrices[column],
      columns=list(output_columns[column]), index=data.index))`.
   6. `fitted = FittedTextVectorizer(target=target, method=method, columns=tuple(columns),
      vectorizers=vectorizers, keywords=keywords, kept=kept, output_columns=output_columns,
      train_labels=data.index)`; `meta = _save_transformer(paths, fitted, "text_vectorizer")`.
   7. `summary: dict[str, JsonValue] = {"method": method, "columns": {column:
      json_scalar(len(output_columns[column])) for column in columns}, "output_columns":
      json_scalar(len(made)), "keywords": {column: {cls: json_list(words) for cls, words in
      found.items()} for column, found in keywords.items()}}`.
   8. `return ToolSuccess(summary=summary, artifacts=(meta, save_frame(paths, encoded,
      "text_vectorized_train")))`.

e. Tool `apply_text_vectorizer`, decorated `@tool(phase="encoding", allow_in=_APPLY_PHASES,
   transform_only=True)`:
   ```
   def apply_text_vectorizer(
       paths: RunPaths,
       vectorizer: Annotated[ArtifactRef, Field(description="A fitted vectorizer from fit_text_vectorizer.")],
       frame: _Frame,
   ) -> ToolSuccess:
   ```
   Docstring: replaces each fitted text column with the columns learned on train (nothing is
   relearned); for valid/test only; unseen words give zeros; a null is an empty text; works
   with or without the target.
   Body, in order:
   1. `fitted = load_fitted(paths, vectorizer, FittedTextVectorizer, "vectorizer")`;
      `data = load_dataframe(paths.artifact(frame, "dataframe"))`;
      `require_columns(data, fitted.columns, "frame")`.
   2. `overlap = data.index.intersection(fitted.train_labels)`; if `len(overlap) > 0`: raise
      `ToolParamError(f"{len(overlap)} frame rows share a label with the vectorizer's train;
      apply is for valid/test only -- use fit_text_vectorizer's transformed train",
      field_errors={"frame": f"{len(overlap)} rows overlap train labels"})`.
   3. `out = data`; for each `column` in `fitted.columns`: `raw = _raw_features(fitted.method,
      fitted.vectorizers.get(column), fitted.keywords.get(column, {}), _texts(data[column]))`;
      `out = _replace_columns(out, (column,), pd.DataFrame(raw[:, list(fitted.kept[column])],
      columns=list(fitted.output_columns[column]), index=data.index))`.
   4. `summary: dict[str, JsonValue] = {"columns": json_list(fitted.columns)}`;
      `return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out,
      "text_vectorized"),))`.

### 2. `skills/modelling.md`

- `## feature_engineering` → `### Heuristics`: append bullet `- Turn text-role columns into
  numbers only with fit_text_vectorizer on train; its train output is the one to keep (the
  lexicon's is out-of-fold), and apply_text_vectorizer is for held-out rows.`
  `### Tools`: append lines `fit_text_vectorizer` and `apply_text_vectorizer` after
  `apply_target_encoder`.
- `## champion_selection` → `### Tools`: add `apply_text_vectorizer` after
  `apply_target_encoder`.
- `## experiment_design` → `### Heuristics`: append bullet `- When a text-role column exists,
  compare features.text_encoding methods (drop, lexicon for classification, tfidf,
  hashed_chars) as separate candidates; prefer the simplest one that meets the bar.`

### 3. `tests/test_tool_decorator.py`

`_TRANSFORM_ONLY_TOOLS`: add `"apply_text_vectorizer"` after `"apply_target_encoder"`;
docstring "The exact 13 transform-only tools (chunk 17 D-a)" → "The exact 14 transform-only
tools (chunk 17 D-a; chunk 82 adds apply_text_vectorizer)".

### 4. `tests/test_tools_text.py` (new)

Header docstring: `"""Chunk 82a: text vectorizer tools. Learned on train, replayed on held-out
rows; the lexicon's train output is out-of-fold."""`. Helpers copied in shape from
`tests/test_tools_encoding.py`: `_save(paths, frame) -> ArtifactRef` and `_loaded(paths, res,
index=0) -> pd.DataFrame` (same bodies). Use the `run_paths` fixture. Import the two tools and
`FittedTextVectorizer` is not needed. Tests (exact data):

`REVIEWS = ["great app love it", "great app", "love it great", "bad crash", "bad app crash",
"crash bad"]`; `TRAIN = pd.DataFrame({"review": REVIEWS, "n": [1, 2, 3, 4, 5, 6], "y": [1, 1,
1, 0, 0, 0]})` (index 0..5).

1. `test_tfidf_learns_train_vocabulary_and_replays_on_valid`: `fit = fit_text_vectorizer(
   run_paths, _save(run_paths, TRAIN), target="y", columns=("review",), method="tfidf",
   seed=0)`. `train_out = _loaded(run_paths, fit, 1)`. Assert `"review" not in
   train_out.columns`; `train_out["n"].tolist() == [1, 2, 3, 4, 5, 6]` and
   `train_out["y"].tolist() == [1, 1, 1, 0, 0, 0]`; `{"review__tfidf_great",
   "review__tfidf_great+app", "review__tfidf_crash"} <= set(train_out.columns)`;
   `fit.summary["method"] == "tfidf"`. `valid = pd.DataFrame({"review": ["great love", "new
   words only", None], "n": [7, 8, 9], "y": [1, 0, 1]}, index=[10, 11, 12])`;
   `out = _loaded(run_paths, apply_text_vectorizer(run_paths, fit.artifacts[0].ref,
   _save(run_paths, valid)))`. Assert `list(out.columns) == list(train_out.columns)`;
   `text = [c for c in out.columns if c.startswith("review__")]`;
   `out.loc[10, "review__tfidf_great"] > 0`; `(out.loc[11, text] == 0).all()` and
   `(out.loc[12, text] == 0).all()`.
2. `test_hashed_chars_drops_constant_buckets_and_is_deterministic`: fit `method=
   "hashed_chars"` on `TRAIN`; `train_out = _loaded(run_paths, fit, 1)`; `hashed = [c for c in
   train_out.columns if c.startswith("review__hash_")]`; assert `0 < len(hashed) < 256`;
   `_dict(fit.summary["columns"]) == {"review": len(hashed)}` (copy the `_dict` helper from
   test_tools_encoding); `(train_out[hashed].max() - train_out[hashed].min() > 0).all()`.
   `valid = pd.DataFrame({"review": ["bad crash"], "n": [9], "y": [0]}, index=[20])`; `out =
   _loaded(run_paths, apply_text_vectorizer(run_paths, fit.artifacts[0].ref, _save(run_paths,
   valid)))`; assert `np.allclose(out.loc[20, hashed].to_numpy(dtype="float64"),
   train_out.loc[3, hashed].to_numpy(dtype="float64"))`.
3. `test_lexicon_learns_class_keywords_out_of_fold`: rows `i` in `range(20)`: positives `i <
   10` text `f"good great love w{i}"`, negatives text `f"bad awful crash w{i}"`; rows 0 and 1
   get `" zeta"` appended; `y = [1] * 10 + [0] * 10`; frame columns `review`, `y`. Fit
   `method="lexicon", seed=0`. `kw = _dict(_dict(fit.summary["keywords"])["review"])`; assert
   `{"good", "great", "love", "zeta"} <= set(_list(kw["1"]))`, `{"bad", "awful", "crash"} <=
   set(_list(kw["0"]))`, `"w3" not in _list(kw["1"])` (copy `_list` helper). `train_out =
   _loaded(run_paths, fit, 1)`; assert `[c for c in train_out.columns if c != "y"] ==
   ["review__lex_0", "review__lex_1"]`; out-of-fold: `train_out.loc[0, "review__lex_1"] ==
   3.0` (zeta is in only one other row, so the fold that holds row 0 out never learns it).
   Apply on `pd.DataFrame({"review": ["good great love zeta"], "y": [1]}, index=[100])`:
   `out.loc[100, "review__lex_1"] == 4.0` and `out.loc[100, "review__lex_0"] == 0.0`.
4. `test_lexicon_refusals`: (a) continuous target: frame `review` = `[f"word{i % 3} text" for
   i in range(30)]`, `y = [i * 0.5 for i in range(30)]`; `pytest.raises(ToolParamError,
   match="class target")`. (b) absent target: `TRAIN.drop(columns=["y"])` with `target="y"`,
   `pytest.raises(ToolParamError)`. (c) small class: `TRAIN` (3 rows per class) →
   `pytest.raises(ToolDataError, match="cross-fitting folds")`.
5. `test_unusable_text_is_a_data_error`: tfidf on `pd.DataFrame({"review": ["alpha", "beta",
   "gamma"], "y": [0, 1, 0]})` → `pytest.raises(ToolDataError, match="at least 2 train
   rows")`; hashed_chars on `pd.DataFrame({"review": ["same text"] * 4, "y": [0, 1, 0, 1]})` →
   `pytest.raises(ToolDataError, match="varies across train rows")`.
6. `test_apply_refuses_train_rows_and_names_must_not_collide`: fit tfidf on `TRAIN`; apply to
   `_save(run_paths, TRAIN)` → `pytest.raises(ToolParamError, match="share a label")`. Then
   `TRAIN.assign(review__tfidf_great=0.0)` fitted with tfidf → `pytest.raises(ToolParamError,
   match="collide")`.

### Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_tools_text.py tests/test_tool_decorator.py tests/test_skills.py tests/test_tools_encoding.py"`
- `.venv/bin/python -m ruff check mcp_server/statlab_tools/tools/encoding.py tests/test_tools_text.py tests/test_tool_decorator.py`
- `.venv/bin/python -m ruff format --check` on the same three files (run `ruff format` on them
  first if needed)
- `.venv/bin/python -m mypy mcp_server/statlab_tools/tools/encoding.py tests/test_tools_text.py`

Stop and report (no workaround) if: a sklearn warning fails a test under warnings-as-errors;
mypy rejects the `cast(TfidfVectorizer | HashingVectorizer, ...)` form or the summary dict;
`fit_text_vectorizer` on a 1-row-per-word vocabulary raises something other than `ValueError`;
an expected value in tests 1–3 differs (report the actual value, do not change the assertion).

Receipt: changed files, commands with genuine exits, pytest summary line, deviations.

## Stage 82a handoff (2026-10-19)

Implementer receipt: `make test-fast` over `test_tools_text.py test_tool_decorator.py
test_skills.py test_tools_encoding.py` exit 0 (127 passed, dots only); after one correction
(test line 69 typed via `.to_numpy(dtype="float64")[0]`), mypy on `encoding.py` +
`test_tools_text.py` exit 0, Ruff check/format exit 0, `test_tools_text.py` exit 0 (6 passed).
Accepted deviations: test 4(b) uses `method="lexicon"`; formatter reflows; ternary in the
lexicon fixture loop. Main reviewed the full diff against the recipe: matches. Global gate
deferred to the batch acceptance after 82b.

## Stage 82b (detailed spec) — candidate field, design checks, pipeline wiring

Owned files: `contracts/statlab_contracts/vocabulary.py`, `contracts/statlab_contracts/
candidate.py`, `backend/statlab/domains/design_inference.py`, `backend/statlab/domains/
candidate_pipeline.py`, `tests/test_candidate_set.py`, `tests/test_candidate_hash.py`,
`tests/test_nodes_design_inference.py`, `tests/test_nodes_candidate_pipeline.py`. Not alone:
preserve others' edits (82a's files are done; do not touch them).

### 1. `vocabulary.py`

- After `CategoricalEncoding`: `TextEncoding = Literal["categorical", "drop", "lexicon",
  "tfidf", "hashed_chars"]` with docstring: `"""How a candidate turns text-role columns into
  numbers (chunk 82, D37). `categorical` is the behaviour from before the field existed: text
  goes through `encoding` like a category. `drop` removes text columns; the other three call
  `fit_text_vectorizer`."""`
- `VariationDimension`: add `"text_features",` right after `"cardinality_handling",`.
- `DIMENSION_FOR_FIELD`: add `"features.text_encoding": "text_features",` right after the
  `"features.max_cardinality"` entry.

### 2. `candidate.py`

- Import `TextEncoding` (alphabetical place in the vocabulary import list).
- `FeatureSpec`: after `max_cardinality`, add
  `text_encoding: TextEncoding = Field(default="categorical", description="How text-role
  columns become numbers: 'categorical' encodes them like categories (default), 'drop' removes
  them, 'lexicon' counts class keywords learned on train (classification only), 'tfidf' weights
  words and word pairs, 'hashed_chars' hashes character 3-5-grams.")`.
- After the `_UNHASHED_FIELDS` constant and its docstring, add
  `_LEGACY_TEXT: Final[dict[str, set[str]]] = {"features": {"text_encoding"}}` with docstring
  `"""Left out of the hash at its default `categorical` (chunk 82): that value is the behaviour
  from before the field existed, so every earlier spec keeps its hash (H17)."""`.
- `spec_hash`: the line `payload: dict[str, JsonValue] = self.model_dump(mode="json")` becomes
  `payload: dict[str, JsonValue] = self.model_dump(mode="json", exclude=_LEGACY_TEXT if
  self.features.text_encoding == "categorical" else None)`. The `_UNHASHED_FIELDS` pop loop
  stays. No other change in the method.
- `changed_dimensions`: in `field_values`, after the `"features.max_cardinality"` entry add
  `"features.text_encoding": (baseline.features.text_encoding,
  candidate.features.text_encoding),`.

### 3. `design_inference.py`

a. `validate_design`, inside the per-candidate loop, right after the `if bad: raise
   _RefusalError(... interactions need numeric columns)` block, add in order:
   ```
   text = candidate.features.text_encoding
   if text != "categorical" and not any(roles.get(column) == "text" for column in eligible):
       raise _RefusalError("text_encoding needs an eligible text-role column; keep 'categorical'")
   if text == "lexicon" and problem.task != "classification":
       raise _RefusalError("lexicon text features need a classification target")
   ```
b. `_design` question string: after the fragment `"later at model_training. Interactions only
   between numeric-role columns. "` insert the fragment `"features.text_encoding applies only
   to text-role columns: 'categorical' (default) encodes them like categories, 'drop' removes
   them, 'lexicon' (classification only), 'tfidf' and 'hashed_chars' turn them into numeric
   columns; when a text column exists, compare text methods as separate candidates. "` (split
   into adjacent string literals within 100 columns, like its neighbours).

### 4. `candidate_pipeline.py`

a. `_fit_apply_output`: `train_output=output if fit == "fit_target_encoder" else None` becomes
   `train_output=output if fit in ("fit_target_encoder", "fit_text_vectorizer") else None`.
b. `_finish`: the key tuple `("imputer", "clipper", "encoder", "scaler", "binner")` gains
   `"vectorizer"` at the end.
c. `_features`: between the end of the `transforms` loop (the imputer-drop block) and
   `categorical = sorted(`, insert:
   ```
   text_columns = sorted(
       column
       for column in available
       if roles.get(column) == "text"
       and column not in feature.datetime_parts
       and column not in feature.binned
   )
   if text_columns and feature.text_encoding == "drop":
       pipeline = await self._transform(
           invocation, evidence, pipeline, "drop_columns", {"columns": text_columns}
       )
   elif text_columns and feature.text_encoding != "categorical":
       pipeline = await self._fit_apply(
           invocation, evidence, pipeline,
           "fit_text_vectorizer", "apply_text_vectorizer", "vectorizer",
           {
               "columns": text_columns,
               "method": feature.text_encoding,
               "seed": self.runtime.contract.constraints.random_seed,
           },
       )
   # Text goes through the categorical encoder only under the legacy 'categorical' choice.
   encoded_roles = (
       ("categorical", "ordinal", "text")
       if feature.text_encoding == "categorical"
       else ("categorical", "ordinal")
   )
   ```
   and in the `categorical = sorted(...)` comprehension replace `roles.get(column) in
   ("categorical", "ordinal", "text")` with `roles.get(column) in encoded_roles`. If mypy
   rejects `{"columns": text_columns}` as `dict[str, JsonValue]`, use `list(text_columns)`
   (mechanical only). Nothing else changes; one-hot's auto-selection no longer sees text
   columns because they are gone from the frame.

### 5. Tests

a. `tests/test_candidate_set.py` `_DIMENSION_VARIANTS`: add after the `max_cardinality` entry
   `"features.text_encoding": make_candidate(features=FeatureSpec(text_encoding="tfidf")),`;
   the G2 docstring "all 13" → "all 14".
b. `tests/test_candidate_hash.py` `_FEATURE_SPEC_VARIANTS`: add `("text_encoding",
   FeatureSpec(text_encoding="tfidf")),` after `max_cardinality`; header comment "all 7" →
   "all 8". New test after H18-group end of file:
   `test_h19_default_text_encoding_is_left_out_of_the_hash`: `a = _candidate(features=
   FeatureSpec())`; `b = _candidate(features=FeatureSpec(text_encoding="categorical"))`;
   `c = _candidate(features=FeatureSpec(text_encoding="drop"))`; assert `a.spec_hash ==
   b.spec_hash` and `c.spec_hash != a.spec_hash`. (H17's pinned literal must still pass
   unchanged; never edit it.)
c. `tests/test_nodes_design_inference.py` `test_candidate_set_valid_external_refusals`: add
   `"text_column"` and `"lexicon"` to the parametrize list (after `"interaction_type"`), and
   two branches before the `elif violation == "explicit":` branch:
   - `elif violation == "text_column": candidates[0]["features"]["text_encoding"] = "tfidf"`
   - `elif violation == "lexicon":` set `h.runtime = replace(h.runtime,
     contract=DataContract(target=h.runtime.contract.target,
     objective=h.runtime.contract.objective, features=FeatureRules(roles={"x": "text"})))` then
     `candidates[0]["features"]["text_encoding"] = "lexicon"`.
   New test after it, `test_text_encoding_design_accepts_text_column(migrated_db, project,
   tmp_path)` (`@pytest.mark.asyncio`): `h, tools = await prepared(migrated_db, project,
   tmp_path, target_type="binary")`; contract replace as in the `lexicon` branch;
   `proposed = shortlist("logistic_regression").model_dump(mode="json")`;
   `proposed["candidate_set"]["candidates"][0]["features"]["text_encoding"] = "lexicon"`;
   `h.llm.responses[DesignDecision] = DesignDecision.model_validate(proposed)`; `output = await
   design(h)`; assert `output.status == "completed"` and `candidate_set_from_state(h.state)
   .candidates[0].features.text_encoding == "lexicon"`.
d. `tests/test_nodes_candidate_pipeline.py`:
   - `CandidateTools.call`: `if tool == "fit_target_encoder":` → `if tool in
     ("fit_target_encoder", "fit_text_vectorizer"):`.
   - `candidate_harness` `names`: add `"fit_text_vectorizer"` and `"apply_text_vectorizer"`
     after `"apply_target_encoder"`.
   - New test after `test_target_encoding_keeps_oof_train_and_only_applies_to_validation`:
     `test_text_vectorizer_replaces_text_before_categorical_encoding`: `h, tools = await
     candidate_harness(migrated_db, project, tmp_path, feature=FeatureSpec(text_encoding=
     "tfidf", scaling="none"))`; contract replace exactly as the target-encoding test but
     `roles={"x": "text"}`; `await prepare_candidate(h)`; `calls = [(name, params) for _, name,
     params in tools.calls]`; `names = [name for name, _ in calls]`; `fit = next(params for
     name, params in calls if name == "fit_text_vectorizer")`; assert `fit["columns"] == ["x"]`,
     `fit["method"] == "tfidf"`, `fit["seed"] == h.runtime.contract.constraints.random_seed`;
     assert `names.index("apply_text_vectorizer") < names.index("fit_onehot_encoder")`;
     `pipeline = pipeline_from_state(h.state, h.state.branches[0])`; `applied = [params for
     name, params in calls if name == "apply_text_vectorizer"]`; assert `len(applied) == 1 and
     applied[0]["frame"] != str(pipeline.train)`; assert `[step.tool for step in
     pipeline.replay].index("apply_text_vectorizer") < [step.tool for step in
     pipeline.replay].index("apply_onehot_encoder")`.
   - New test `test_text_drop_removes_text_and_keeps_it_out_of_categorical_encoding`: harness
     with `feature=FeatureSpec(text_encoding="drop", encoding="target", scaling="none")`;
     contract replace with `roles={"x": "text"}`; `await prepare_candidate(h)`; assert some
     call is `("drop_columns", params)` with `params["columns"] == ["x"]`; assert
     `"fit_text_vectorizer" not in names` and `"fit_target_encoder" not in names`.

### Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_candidate_set.py tests/test_candidate_hash.py tests/test_candidate_vocabulary.py tests/test_nodes_design_inference.py tests/test_nodes_candidate_pipeline.py tests/test_candidate_models.py"`
- `.venv/bin/python -m ruff format` then `--check`, and `.venv/bin/python -m ruff check`, on
  the eight owned files.
- `.venv/bin/python -m mypy` on the four owned source files plus the four owned test files.

Stop and report (no workaround, never edit H17's literal) if: H17 fails; mypy rejects the
`exclude=` expression; the `lexicon` refusal case fails for another reason (report the error
message); a harness test hits a tool outside `ctx.allowed`; any other unexpected failure.
Receipt: changed files, commands, genuine exits, pass counts (redirect output to a scratchpad
log and quote its last lines), deviations.

## Stage 82a+82b acceptance receipt (2026-10-19)

82b implementer: focused selection (candidate set/hash/vocabulary, design/inference nodes,
candidate pipeline, candidate models) exit 0, 244 passed; Ruff format/check and mypy on the 8
owned files exit 0. Mechanical deviations accepted: `list(text_columns)` (spec fallback),
unused `tools` renamed `_tools` (RUF059), formatter reflows. Main reviewed the full source and
test diff against the recipe: matches; H17 literal untouched and passing.

Batch acceptance on `faf26e1` + this chunk's uncommitted diff: `make check` exit 0 (log
`check82.log`, temporary scratchpad): pytest 3331 passed + 1 designed skip (dot count; `-q`
prints no summary line), coverage 100% (TOTAL 13932 stmts, 0 missed, 3940 branches, 0
partial), mypy 213 files clean, Ruff check clean, 222 files formatted.

## Stage 82c (outline)

Offline integration scenario: a synthetic app-review dataset (review text + rating count +
category, binary sentiment target) in `integration/datasets.py` and `scenarios.toml`, the
offline LLM proposing one candidate per text method; checks that the run completes and the
comparison table has a row per method.
