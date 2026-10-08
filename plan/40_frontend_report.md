---
status: implemented
dependencies: [39]
completion_date: 2026-10-08
implementation_notes: "Frontend-only on c068e02 (Python gate evidence from c068e02 reused: no Python change). npm test 63/63, typecheck 0, build 0; browser: report tab renders tables, champion card, all six downloads 200. Lineage/comparison click-through not run: user redirected the UI to a chat-first redesign (2026-10-08); see 41."
---

# Chunk 40 — report viewer, champion card, downloads, run lineage and comparison

User requirement (2026-10-08): every backend feature must be usable in the UI. PLAN §12 items 4
and 5. Frontend only; the backend already serves `GET /api/runs/{id}/files/{name}` (report.md,
pipeline.py, manifest.json, comparison.json, contract.snapshot.json, skill.snapshot.md) and
`/api/runs/{id}/files/plots/a_<16hex>.png`; project memory already shows on the project page (37).

Decisions:
- **Markdown: in-repo subset renderer, no dependency.** `report.md` comes from
  `assemble_report` (headings, paragraphs with `**bold**`, GFM pipe tables with `\|` escapes,
  `![ref](plots/<ref>.png)` figures) plus LLM narrative sections spliced verbatim (chunk 19
  D-e), so input is untrusted. The parser returns plain data and the renderer builds React
  elements only (never `dangerouslySetInnerHTML`), so raw HTML always shows as text. Links render
  only for `http(s)://` targets; images only for `plots/a_<16 hex>.png`, mapped to the run's
  plot route.
- Not built: project-picker "best score" column (needs one snapshot per run per project; add
  if asked) and recharts charts.

Verified facts (offline stack, 2026-10-08): champion outcome `summary` (node
`champion_selection`) has `candidate_id`, `estimator`, `model` (artifact ref of the fitted model),
`test_metrics {task, metrics {name: {value, ci_low, ci_high}}}`, `prior_test_scorings`
(number), `n_waves` (number), `selection_bias` and `fit_timing` (strings). `run.config_json.fork`
= `{source_run_id, target, ...}` or null. `comparison.json` may 404; it is not offered.

Owned files: `frontend/src/api/client.ts`, new `frontend/src/report/markdown.ts`, new
`frontend/src/report/Markdown.tsx`, new `frontend/src/report/champion.ts`, new
`frontend/src/report/ReportView.tsx`, new `frontend/src/project/lineage.ts`, new
`frontend/src/project/RunComparison.tsx`, `frontend/src/pages/RunPage.tsx`,
`frontend/src/pages/ProjectPage.tsx`, `frontend/src/style.css` (append), new
`frontend/tests/markdown.test.ts`, new `frontend/tests/report.test.ts`,
`frontend/tests/client.test.ts`, `frontend/package.json` (test list only).

## Client (`src/api/client.ts`)

- `RequestOptions` gains optional `text?: boolean`; when true, a successful response returns
  `await response.text()` inside the existing try (same abort/invalid-response handling).
- `runFileUrl(runId, name)` -> `` `/api/runs/${enc(runId)}/files/${name}` `` (name is one of
  our fixed literals; `name` may contain `/` for plots, so do not encode it).
- `getRunFile(runId, name, signal?): Promise<string>`: `request(runFileUrl(...), {method: 'GET', signal, text: true})`;
  non-string -> invalid response.
- Tests: `runFileUrl('r 1', 'plots/a.png')` -> `'/api/runs/r%201/files/plots/a.png'`;
  `getRunFile` returns the body text (withFetch variant returning a plain text Response: add a
  local stub in the test).

## Markdown parser (`src/report/markdown.ts`, pure)

Types:
`Inline = { type: 'text'; text: string } | { type: 'code'; text: string } | { type: 'strong'; children: Inline[] } | { type: 'em'; children: Inline[] } | { type: 'link'; href: string; children: Inline[] } | { type: 'image'; alt: string; src: string }`;
`Block = { type: 'heading'; level: number; inlines: Inline[] } | { type: 'paragraph'; inlines: Inline[] } | { type: 'code'; text: string } | { type: 'list'; ordered: boolean; items: Inline[][] } | { type: 'quote'; inlines: Inline[] } | { type: 'table'; header: Inline[][]; rows: Inline[][][] } | { type: 'rule' }`.

`parseInline(text: string): Inline[]` — scan left to right, accumulating literal text; at each
index check in order:
1. `\` followed by any character -> that character is literal; skip both.
2. `` ` `` -> if a later `` ` `` exists, `code` with the text between; else literal.
3. `![` -> regex `^!\[([^\]]*)\]\(([^)\s]+)\)` at the index -> `image {alt, src}`; else literal `!`.
4. `[` -> regex `^\[([^\]]+)\]\(([^)\s]+)\)` -> `link {href, children: parseInline(label)}`; else literal.
5. `**` -> if a later `**` exists with non-empty content, `strong` with `parseInline(content)`; else literal `**`.
6. `*` -> if a later single `*` (not part of `**`) exists with non-empty content, `em`; else literal.
Underscores are always literal (metric names like `balanced_accuracy`). Adjacent text nodes merge.

`parseMarkdown(source: string): Block[]` — split on `\n` (strip `\r`), walk lines:
- blank -> ends the current paragraph.
- line starting with ```` ``` ```` -> code block until the next line starting with ```` ``` ````
  (or end), contents joined with `\n`.
- `^(#{1,6})\s+(.*)$` -> heading (trailing `#`s trimmed).
- `^(\*{3,}|-{3,}|_{3,})\s*$` -> rule.
- line starting with `|` and next line matching `^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$`
  -> table: header row plus following lines that start with `|`. Row split: remove one leading
  and one trailing `|`, split on `|` not preceded by `\`, trim each cell, then `parseInline`
  (which turns `\|` into `|`).
- `^\s*[-*+]\s+(.*)$` -> unordered list item; `^\s*\d+[.)]\s+(.*)$` -> ordered item;
  consecutive items of the same kind form one list.
- `^>\s?(.*)$` -> quote; consecutive quote lines join with a space.
- anything else -> paragraph text; consecutive lines join with a space.

## Renderer (`src/report/Markdown.tsx`)

`export default function Markdown({ source, imageUrl }: { source: string; imageUrl: (src: string) => string | null })`:
`parseMarkdown(source)` -> elements: heading -> `h{level+1}` capped at `h6` (the page owns
`h1`); paragraph `p`; code `pre > code`; list `ul`/`ol` > `li`; quote `blockquote`; rule `hr`;
table `.table-wrap > table` with `thead`/`tbody` (`th scope="col"`). Inline: text as string,
`code`, `strong`, `em`; link -> `<a href rel="noreferrer noopener" target="_blank">` only when
`/^https?:\/\//i.test(href)`, otherwise its children rendered as plain content; image ->
`imageUrl(src)` non-null -> `<img className="plot" alt={alt} src={url} />`, else
`` `[figure: ${alt}]` `` text. Keys by index. No `dangerouslySetInnerHTML` anywhere.

## Champion (`src/report/champion.ts`, pure)

`type Champion = { candidateId: string; label: string; estimator: string | null; modelRef: string | null; testMetrics: Record<string, MetricValue>; primaryMetric: string | null; validation: MetricValue | null; priorScorings: number | null; waves: number | null; caveats: string[] }`.
`champion(view: RunView): Champion | null`: last outcome in `view.shared` whose `summary.candidate_id`
is a string and `summary.test_metrics` an object; none -> null. `label` from the branch with
that id (else the id); `estimator` string or null; `modelRef` string `summary.model` or null;
`testMetrics` from `test_metrics.metrics` entries with finite `value` (`ci_low`/`ci_high`
finite else null); `primaryMetric`/`validation` from `candidateRows(view)` row with that id
(`validation = row.metrics[primaryMetric]` or null); `priorScorings` = number
`prior_test_scorings` else null; `waves` = number `n_waves` else null; `caveats` = string
values of `selection_bias` and `fit_timing` in that order.
`plotImageUrl(runId: string): (src: string) => string | null`: returns a function mapping
`^plots/(a_[0-9a-f]{16}\.png)$` to `runFileUrl(runId, 'plots/' + name)`, else null.

## `src/report/ReportView.tsx`

Props `run: RunSummary`, `view: RunView`. Query `['file', run.id, 'report.md']` -> `getRunFile`
with `retry: false`. Layout:
1. Champion card (when `champion(view)` non-null): `<section className="panel champion" aria-labelledby="champion-heading">`,
   `<h2 id>Champion</h2>`, facts: Candidate (`label` + id), Estimator, `` `Validation ${primaryMetric}` ``
   (`formatMetric` or '—'), Test scorings before this one (`priorScorings` or '—'), Search
   waves. Table caption "Test set scores" (Metric | Score with interval via `formatMetric`).
   `<p className="help">The test score is computed once, after selection. Validation scores are optimistic because they were used to choose the champion.</p>`
   then each caveat as `<p className="help">`.
2. Downloads: `<h2>Downloads</h2>`; when `run.status === 'completed'` a `.actions` row of
   `<a className="button" href=... download>`: "Report (report.md)", "Pipeline script
   (pipeline.py)", "Run manifest (manifest.json)", "Contract used (contract.snapshot.json)",
   "Agent instructions used (skill.snapshot.md)" via `runFileUrl`, and "Champion model" via
   `artifactUrl(modelRef)` when a champion with `modelRef` exists. Otherwise
   `<p className="secondary-line">Downloads appear when the run completes.</p>`.
3. Report: `<h2>Report</h2>`; pending "Loading the report…"; error with `ApiError` status 404
   -> "The report appears after champion selection."; other error alert `errorText`; success
   -> `<article className="report"><Markdown source={data} imageUrl={plotImageUrl(run.id)} /></article>`.

## `src/pages/RunPage.tsx`

Add tab id `'report'` labelled "Report" after Files, rendering `<ReportView run={snapshot.data.run} view={view} />`.

## Lineage (`src/project/lineage.ts`, pure)

`type LineageRow = { run: RunSummary; depth: number; forkTarget: string | null; parentId: string | null }`.
`forkTarget(run)`: `JSON.parse(run.config_json)` in try; `fork.target` string else null.
`lineage(runs: RunSummary[]): LineageRow[]`: ids = set of run ids; roots = runs whose
`forked_from_run_id` is null or not in ids, sorted `started_at` descending; children of a run
= runs with that `forked_from_run_id`, sorted `started_at` ascending; depth-first pre-order,
root depth 0, child depth parent + 1; `parentId` = `forked_from_run_id` when in ids else null.
Cycles cannot occur (backend rejects them); guard anyway with a visited set (skip revisits).

## `src/project/RunComparison.tsx`

Props `runIds: string[]`. `useQueries` over `['run', id]` -> `getRun`. Any pending ->
"Loading runs…"; any error -> alert with `errorText`. Else per run: `view = parseRunView(state)`,
`c = view ? champion(view) : null`. `.table-wrap` table caption "Run comparison", header
`Measure` + one `<th scope="col">` per run with a link to the run (`formatRoute`) and its
`forkTarget` as `<small className="help">Rewound at {nodeLabel(target)}</small>` when set.
Rows: Status (`STATUS_LABELS`), Champion (`label (id)` or '—'), Estimator,
`` `Validation ${primary}` `` (primary from the first run with one; value via `formatMetric`
of that run's `champion.validation` when its `primaryMetric` matches, else '—'), then one row
per metric name in the union of all champions' `testMetrics` (sorted, primary first) labelled
`` `Test ${name}` ``. Missing values '—'.

## `src/pages/ProjectPage.tsx`

Runs section: replace the table body with `lineage(project.data.runs)`; columns: Compare
(checkbox `aria-label={`Compare run ${id}`}`), Run (link `formatRoute`, `<code>{id}</code>`,
indented with `style={{ paddingLeft: `${12 + depth * 24}px` }}` and, for depth > 0, a
`<small className="help">Rewound at {nodeLabel(forkTarget ?? '')}</small>` when `forkTarget`
is non-null), Status (existing pill), Mode, Started, Ended, Champion (Yes/No). Remove the
"Forked from" and "Open" columns (the run link replaces them). State `selected: string[]`;
below the table a button "Compare selected runs" (disabled unless at least two selected)
toggling `<RunComparison runIds={selected} />`; help text "Select two or more runs. A rewound
run differs from its parent by one decision."

## `src/style.css` (append)

`.report { max-width: 72ch; }`, `.report table { font-size: 0.875rem; }`,
`.report blockquote { margin: 8px 0; padding-left: 12px; border-left: 3px solid var(--rule); color: var(--graphite); }`,
`.report pre { padding: 8px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); }`,
`.champion { border-left: 4px solid var(--signal); }`.

## Tests

- `tests/markdown.test.ts`: `parseInline` — `'a **b** *c* `d`'`, escape `'a \\* b'`, unclosed
  `'**x'` literal, `'balanced_accuracy'` single text, link, image, `'<script>x</script>'` stays
  one text node. `parseMarkdown` — heading levels with trailing `#`, paragraph joining lines,
  fenced code keeps `**` literal, unordered and ordered lists, quote, rule, table with escaped
  pipe cell `'a \\| b'` -> text `'a | b'`, a line starting `|` without separator is a paragraph,
  and the real report head (`# Modelling report: churned`, `## Result`, a paragraph with
  `**c_01**`, a 2-row table) -> exact block types.
- `tests/report.test.ts`: `champion` on a view with champion outcome (candidate c_01 matching a
  branch label, test metrics with/without CI, prior 0, waves 1, both caveats) -> exact object;
  no champion -> null; `plotImageUrl('r1')('plots/a_0123456789abcdef.png')` ->
  `'/api/runs/r1/files/plots/a_0123456789abcdef.png'`, `'plots/../x.png'` -> null,
  `'https://x/y.png'` -> null; `lineage` with runs (started_at): A `…T01:00`, A2 `…T02:00`
  (parent A), A1 `…T03:00` (parent A), A1a `…T04:00` (parent A1), O `…T05:00` (parent `gone`,
  not in the list), B `…T06:00` -> ids in order `[B, O, A, A2, A1, A1a]`, depths
  `[0, 0, 0, 1, 1, 2]`, parentIds `[null, null, null, A, A, A1]`; A1's `config_json`
  `'{"fork": {"target": "problem_definition"}}'` -> forkTarget `'problem_definition'`;
  `config_json: '{'` -> null.
- Add both files to the `package.json` test list.

## Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build`. Stop and report on failure or
missing decision. Main then does browser acceptance (report tab with tables, champion card,
downloads return 200, lineage indentation, two-run comparison) and the final `make check`.
