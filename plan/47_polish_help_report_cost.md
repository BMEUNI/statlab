---
status: completed
dependencies: [46]
completion_date: 2026-10-08
implementation_notes: "light run header; close icon in inspector; help menu from registry; champion tiles; full-width justified report; plot grid with full-size links; Flash off-peak half price; make check 100%; npm 89/89"
---

# Chunk 47 — header colours, inline close, help menu, report layout, true Flash cost

User feedback 2026-10-08 (verbatim list in the session):

1. The navbar and the "Waiting for you" run header (`.run-band`) share `var(--navy)`.
2. "Close the details" sits outside the detail box and wastes vertical space.
3. Add a small "?" dropdown in the navbar that says VERY briefly what the app is and what it can
   do now (tasks + models). It must stay current, so the model list comes from the registry.
4. The report champion card is a flat `dl`; make it look better.
5. The report does not fill the pane (`.report { max-width: 72ch }`); text should be justified;
   tables and plots should fill the width.
6. Recorded cost is about double the DeepSeek bill. Cause: `client.py` `_cost` always uses
   Flash **peak** rates. Every one of the 110 recorded calls (2026-10-08 11:52–18:42 UTC) was
   off-peak; re-priced total $0.3116 versus $0.6231 recorded. PLAN §7.1: off-peak is exactly half
   of every Flash rate; peak = 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday.

Non-goals: no `RunSummary` change (user likes it); no rewrite of historical `llm_calls.cost_usd`
rows; no Pro-model override (Pro still goes through LiteLLM); no click-outside handling for the
help menu (native `<details>`; clicking "?" again closes it).

## Stage 47a

Owned files: `backend/statlab/llm/client.py`, `tests/test_llm_client.py`,
`frontend/src/App.tsx`, `frontend/src/run/model.ts`, `frontend/src/run/NodeInspector.tsx`,
`frontend/src/run/RunTimeline.tsx`, `frontend/src/pages/RunPage.tsx`,
`frontend/src/report/ReportView.tsx`, `frontend/src/style.css`,
`frontend/tests/run-model.test.ts`. Other edits in the tree are not yours; preserve them.

### 1. Time-aware Flash cost — `backend/statlab/llm/client.py`

- Add `from datetime import UTC, datetime` to the stdlib imports.
- New module functions, placed directly above `_cost`:
  - `def _utc_now() -> datetime:` returns `datetime.now(UTC)`. (Tests monkeypatch it.)
  - `def _flash_peak(at: datetime) -> bool:` returns
    `at.weekday() < 5 and (1 <= at.hour < 4 or 6 <= at.hour < 10)`. Docstring one line:
    DeepSeek Flash peak window, UTC Monday–Friday (PLAN §7.1). `at` is always UTC-aware.
- `_cost` Flash branch: delete the `ponytail:` comment. Compute the peak amount exactly as now,
  then multiply by `1.0 if _flash_peak(_utc_now()) else 0.5`. Comment above it:
  `# Off-peak Flash rates are exactly half of every peak rate (PLAN §7.1).`
  Nothing else in `_cost` or `_record_response` changes.

### 2. Tests — `tests/test_llm_client.py`

- Add `from datetime import UTC, datetime`; import the module as
  `from statlab.llm import client as client_module` (beside the existing `statlab.llm.client`
  import).
- New autouse fixture near the top-level fixtures:
  `_peak_clock(monkeypatch)` sets `client_module._utc_now` to a lambda returning
  `datetime(2026, 10, 6, 7, 0, tzinfo=UTC)` (a Tuesday, peak). All existing cost assertions
  (lines ~342-343, ~593, ~941) stay unchanged and now are deterministic.
- New parametrized test `test_flash_peak_window(at, expected)` calling
  `client_module._flash_peak` with UTC datetimes:
  Mon 2026-10-05 00:59 → False; Mon 01:00 → True; Mon 03:59 → True; Mon 04:00 → False;
  Mon 05:59 → False; Mon 06:00 → True; Mon 09:59 → True; Mon 10:00 → False;
  Fri 2026-10-09 07:00 → True; Sat 2026-10-10 07:00 → False; Sun 2026-10-11 02:00 → False.
- New test `test_flash_off_peak_cost_is_half`: copy the setup of the test that asserts
  `(8 * 0.30 + 3 * 0.006 + 5 * 1.20) / 1_000_000` (~line 941; same fixtures, same fake
  response, same `deepseek/deepseek-flash` model), but first monkeypatch
  `client_module._utc_now` to return `datetime(2026, 10, 8, 12, 0, tzinfo=UTC)` (Thursday,
  off-peak). Assert the recorded row equals the same tuple with cost
  `(8 * 0.30 + 3 * 0.006 + 5 * 1.20) / 1_000_000 * 0.5`. Use `math.isclose` on the cost only
  if exact equality fails; report which one you used.

### 3. Run header colour — `frontend/src/style.css`

Light band instead of navy. Change these existing rules (keep layout properties):
- `.side-pane .run-band`: `background: var(--target); color: var(--ink);
  border-bottom: 1px solid var(--rule);` (replaces navy/navy-ink).
- `.run-band-kicker`, `.run-band-sub`, `.run-stats dt`: `color: var(--graphite)`.
- `.run-band-status.tone-signal` → `var(--signal)`; `.tone-gate` → `var(--gate)`;
  `.tone-fault` → `var(--fault)`.
- Delete the three `.run-band button`, `.run-band button.primary`, `.run-band button.danger`
  rules so the band uses the normal button styles.

### 4. Close button inside the inspector

- `NodeInspector.tsx`: Props gain `onClose: () => void`. Import `X` from `lucide-react`.
  Replace the selected-branch `<h2 id="inspector-heading">…</h2>` with
  `<div className="inspector-head">` containing the same h2 unchanged, then
  `<button type="button" className="icon-button" aria-label="Close the details"
  title="Close the details" onClick={onClose}><X className="icon" aria-hidden="true" /></button>`.
  The `selected === null` branch is unchanged.
- `RunTimeline.tsx`: `detailBlock` keeps the `div.tl-detail` with `ref={detailRef}` and
  renders only `{detail}`; delete the button and the `X` import. `onSelect` prop stays (still
  used by toggles).
- `RunPage.tsx` line ~127: pass `onClose={() => setSelected(null)}` to `NodeInspector`.
- `style.css`: delete the rule `.tl-detail > .tl-close { … }`. Add:
  - `.inspector-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }`
  - `.inspector-head h2 { margin: 0 0 8px; }`
  - `.icon-button { padding: 4px; border: 0; background: none; color: var(--graphite); flex: none; }`
  - `.icon-button:hover:not(:disabled) { color: var(--ink); background: var(--paper); }`

### 5. Help menu — `frontend/src/run/model.ts`, `frontend/src/App.tsx`

- `model.ts`, right after `estimatorLabel`:
  `export function modelsFor(estimators: EstimatorInfo[], task: string): string` returns the
  `estimatorLabel(name)` of every estimator whose `task === task`, in input order, joined with
  `', '`; empty string when none. Import `EstimatorInfo` as a type from `../api/types.ts`
  (merge into the existing type import if one exists).
- `App.tsx`: import `CircleQuestionMark` from `lucide-react`, `useQuery` from
  `@tanstack/react-query`, `getVocabulary` from `./api/client.ts`, `modelsFor` from
  `./run/model.ts`. In `App`, add
  `const vocabulary = useQuery({ queryKey: ['vocabulary'], queryFn: ({ signal }) => getVocabulary(signal) });`
  (same key as SetupPane, so the cache is shared). Define
  `const models = (task: string) => { const list = modelsFor(vocabulary.data?.estimators ?? [], task); return list === '' ? '' : `: ${list}`; };`
  As the last child of `<nav className="topnav">` add:

  `<details className="help-menu">`
  - `<summary aria-label="What is Statlab?" title="What is Statlab?"><CircleQuestionMark className="icon" aria-hidden="true" /></summary>`
  - `<div className="help-pop">`
    - `<p><strong>Statlab</strong> runs a modelling study on a table you upload. An AI agent cleans the data, builds features, fits and tunes several models, and picks a champion scored once on held-out test data. You approve the key decisions in the chat.</p>`
    - `<p className="help-pop-title">What it can do now</p>`
    - `<ul>` with three `<li>`: `{`Classification (two or more classes)${models('classification')}`}`,
      `{`Regression (a numeric target)${models('regression')}`}`,
      `Effect tests: whether a column affects the target`.
- `style.css` (after the `.topnav` rules):
  - `.help-menu { position: relative; }`
  - `.help-menu > summary { list-style: none; display: inline-flex; align-items: center; padding: 8px; border-radius: var(--radius); color: var(--navy-ink); cursor: pointer; }`
  - `.help-menu > summary::-webkit-details-marker { display: none; }`
  - `.help-menu > summary:hover, .help-menu[open] > summary { background: var(--navy-2); }`
  - `.help-pop { position: absolute; right: 0; top: calc(100% + 8px); z-index: 30; width: min(22rem, calc(100vw - 32px)); padding: 12px 16px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); color: var(--ink); box-shadow: var(--shadow); font-size: 0.875rem; }`
  - `.help-pop p { margin: 0 0 8px; }`
  - `.help-pop-title { font-weight: 600; }`
  - `.help-pop ul { margin: 0; padding-left: 18px; }`

### 6. Report layout — `frontend/src/report/ReportView.tsx`, `frontend/src/style.css`

- `style.css`: delete `.report { max-width: 72ch; }`. Add
  `.report p, .report li { text-align: justify; hyphens: auto; }` and
  `.report img.plot { display: block; width: 100%; }`.
- Champion card (ReportView): import `Trophy` from `lucide-react`; import `estimatorLabel`,
  `humanize`, `metricLabel` from `../run/model.ts`. Replace the section's `h2` and `dl` with:
  - `<p className="champion-kicker"><Trophy className="icon" aria-hidden="true" />Champion</p>`
  - `<h2 id="champion-heading">{humanize(best.label)}</h2>` (keeps `aria-labelledby`)
  - `<p className="secondary-line">{`${estimatorLabel(best.estimator)} · `}<code>{best.candidateId}</code></p>`
  - `<dl className="champion-stats">` with three `<div>` tiles, each `<dt>` + `<dd>`:
    1. dt `{`Validation ${best.primaryMetric === null ? 'score' : metricLabel(best.primaryMetric)}`}`;
       dd: `best.validation === null ? '—' : best.validation.value.toFixed(4)`, and when
       `best.validation` has both `ciLow` and `ciHigh` non-null, a following
       `<small>{`95% interval ${ciLow.toFixed(3)} to ${ciHigh.toFixed(3)}`}</small>` inside the dd.
    2. dt `Earlier test scorings`; dd `{best.priorScorings ?? '—'}`.
    3. dt `Search waves`; dd `{best.waves ?? '—'}`.
  - Test table unchanged except the metric cell renders `{metricLabel(name)}`.
  - Help/caveat paragraphs unchanged.
- `style.css`, after `.champion { … }`:
  - `.champion-kicker { display: inline-flex; align-items: center; gap: 6px; margin: 0; color: var(--signal); font-weight: 600; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.04em; }`
  - `.champion h2 { margin: 4px 0 2px; }`
  - `.champion-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 12px; margin: 16px 0; }`
  - `.champion-stats > div { padding: 10px 12px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); }`
  - `.champion-stats dt { color: var(--graphite); font-size: 0.8125rem; }`
  - `.champion-stats dd { margin: 2px 0 0; font-size: 1.25rem; font-weight: 700; font-variant-numeric: tabular-nums; }`
  - `.champion-stats dd small { display: block; font-size: 0.75rem; font-weight: 400; color: var(--graphite); }`

### 7. Frontend test — `frontend/tests/run-model.test.ts`

Add `modelsFor` to the existing import from `../src/run/model.ts`. New test
`modelsFor lists registry models of one task`: estimators
`[{ name: 'logistic_regression', task: 'classification' }, { name: 'ridge', task: 'regression' }, { name: 'hist_gb_regressor', task: 'regression' }]`;
assert `modelsFor(e, 'regression') === 'Ridge regression, Gradient boosting'`,
`modelsFor(e, 'classification') === 'Logistic regression'`, `modelsFor(e, 'survival') === ''`.

### Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_llm_client.py"`
- `.venv/bin/python -m ruff check backend/statlab/llm/client.py tests/test_llm_client.py`
- `.venv/bin/python -m ruff format --check backend/statlab/llm/client.py tests/test_llm_client.py`
- `.venv/bin/python -m mypy backend/statlab/llm/client.py tests/test_llm_client.py`
- From `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Stop and report on any failure or any spec gap; do not improvise. Receipt: changed files,
commands, exit codes, test counts, deviations.

Main then: PLAN §7.1 note update (peak/off-peak now applied by call time), review, `make check`,
offline browser check (help menu, inspector close, run header, report page), commit.

## Addendum A (RUF002)

The `_flash_peak` docstring uses ASCII only:
`DeepSeek Flash peak window, UTC Monday to Friday (PLAN section 7.1).` The `_cost` comment
likewise reads `# Off-peak Flash rates are exactly half of every peak rate (PLAN section 7.1).`
Recheck: Ruff check on the two Python files. The `ruff format` of the test file is accepted.

## Addendum B (user feedback after 47a, 2026-10-08)

User: the "Report" heading duplicates the report's own title; plots in the report touch each
other and are too big; prefer two per row with a way to zoom. Report figures are consecutive
`![id](plots/x.png)` lines, which `parseMarkdown` joins into ONE paragraph whose inlines are
images separated by `text(' ')`.

Additional owned files: `frontend/src/report/markdown.ts`, `frontend/src/report/Markdown.tsx`,
`frontend/tests/markdown.test.ts` (plus `ReportView.tsx`, `style.css` from 47a).

1. `markdown.ts`: add
   `export function figureRow(inlines: Inline[]): { alt: string; src: string }[] | null`.
   Collect every `image` inline (alt, src) in order. Return `null` when there is no image or
   when any non-image inline is not a `text` item whose `text.trim() === ''`; otherwise return
   the collected list.
2. `Markdown.tsx` paragraph branch: first `const figures = figureRow(block.inlines)`. When not
   null, return `<div className="plot-grid" key={index}>` with, per figure `at`:
   `const url = imageUrl(fig.src)`; url null gives `<p key={at}>{`[figure: ${fig.alt}]`}</p>`;
   otherwise `<a key={at} href={url} target="_blank" rel="noreferrer noopener" title="Open full size"><img className="plot" alt={fig.alt} src={url} /></a>`.
   Otherwise the existing `<p>` rendering, unchanged.
3. `ReportView.tsx`: render `<h2>Report</h2>` only when `!report.isSuccess` (the report's own
   title heads it once loaded).
4. `style.css`: replace `.report img.plot { display: block; width: 100%; }` with
   `.plot-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr)); gap: 16px; margin: 12px 0; }`,
   `.plot-grid a { display: block; cursor: zoom-in; }` and
   `.plot-grid img.plot { display: block; width: 100%; }`.
5. `markdown.test.ts`: import `figureRow`; test `figureRow keeps only image-only paragraphs`:
   `figureRow(parseInline('![a](plots/a.png) ![b](plots/b.png)'))` deep-equals
   `[{ alt: 'a', src: 'plots/a.png' }, { alt: 'b', src: 'plots/b.png' }]`;
   `figureRow(parseInline('see ![a](plots/a.png)'))` is `null`;
   `figureRow(parseInline('plain'))` is `null`.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

## Acceptance receipt (2026-10-08)

Implementer: `make test-fast TESTS="tests/test_llm_client.py"` exit 0 (exact equality in the
off-peak test); Ruff check/format and mypy on the two Python files exit 0 after Addendum A;
npm test/typecheck/build exit 0 after Addendum B. Main: `make check` exit 0 on 47a source
(coverage 100 %, 12,021 statements, 3,458 branches; mypy 195 files and Ruff clean; log
`check47.log`, temporary). Addendum B touched frontend only (no Python input changed), so that
evidence stands. Main npm after Addendum B: 89/89 pass, typecheck 0, build 0.
Offline browser check (ports 8766/5174): help menu lists the registry models; the run header is
light and distinct from the navbar; the inspector close icon sits in its header and closes it;
the champion card shows tiles; report text is justified at full pane width; the duplicate
"Report" heading is gone; the plot grid gives 2 columns of 384 px with a 16 px gap at 1440 px
(injected images; the offline-rich run stopped at the known experiment_design stand-in failure,
not related to this chunk). Historical `llm_calls` (110 rows, all off-peak) re-price to $0.3116
from $0.6231 recorded; rows left unchanged.
