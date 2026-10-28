# Integrated Large Language Models (BME iLLM) — what this project uses from the course

This file is for the **iLLM course** only. It lists where the course's ideas show up in Statlab,
how each one helps, and where you can see it working. (The lab-project plan lives in `plan/`.)

Statlab is a statistical modelling workbench. An LLM agent walks a fixed 13-step pipeline
(intake, profiling, quality, problem framing, experiment design, candidates, champion, report),
calls a separate tool server for every computation, and compares several modelling candidates on
one shared validation split. The LLM decides; code computes and checks.

Lecture short names used below:

| Short | Lecture |
|---|---|
| L1 | Introduction |
| L2 | History of language models |
| L3 | The predecessors of LLMs (n-grams, smoothing, word vectors, hashing) |
| L4 | LLM pretraining and post-training (transformers, decoding, SFT, RLHF/DPO, hallucination) |
| L7 | Basic integration techniques (RAG, sovereign/offline models, LLM vs agent, harness) |
| AG | Agentic integrations (memory, tools, tool selection, tool errors) |
| SV | Writing scientific surveys (fair comparison, evidence matrix, claims traceable to evidence) |

## 1. Course integrations built for this course

### 1a. Text columns as competing NLP methods — L1, L3, SV (chunk 82, done)

**Problem.** Free-text columns (reviews, comments) were treated as one category per distinct
string, which carries almost no signal.

**What we built.** Each candidate chooses how text becomes numbers, and the run compares the
methods against each other on the same validation split:

| Method | Course idea |
|---|---|
| `drop` | baseline: "does text help at all?" |
| `lexicon` | class keywords by add-one (Laplace) smoothed log-odds (L1 exercise, L3 smoothing) |
| `tfidf` | word 1–2-grams, sparse bag-of-words (L3 n-grams) |
| `hashed_chars` | hashed character 3–5-grams, robust to typos and unseen words (L3 hashing trick) |

**How it helps.** The champion step picks the simplest method that wins on validation, so the
report answers "which text method is worth it for this data" with numbers, not opinion. This is
the survey lecture's rule: compare methods on the same task, data and metric, or the numbers mean
nothing.

**Where to see it.** Candidate table on the run page (`text_encoding` per candidate); offline
scenario S20 (synthetic app reviews) compares all four. Spec: `plan/82_text_features.md`.

### 1b. Report numbers must come from evidence — L4, L7, SV (chunk 87, done)

**Problem.** The LLM writes free-text report sections ("why this model won"). The prompt says
"do not invent numbers", but nothing checked it. A hallucinated AUC in a report looks exactly
like a real one.

**What we build.** Before the report is assembled, code extracts every number from the
LLM-written sections and looks it up in the evidence the LLM was given (validation metrics,
rankings, earlier step results, the data contract). A sentence whose number is not there is cut;
a heading with such a number drops its section. The report states how many sentences were cut.

**Course ideas.**
- L4: base and aligned models still hallucinate; fluent text is not evidence (the "facsimile"
  point: the model imitates competence).
- L7: retrieval-augmented generation grounds the answer in retrieved data; here we go one step
  further and *verify* the grounding after generation.
- SV: "separate what authors claim from what the evidence supports"; every claim must be
  traceable to evidence.

**How it helps.** A reader can trust every number in the report: it is either printed by code
from an artifact, or it was checked against one. It also catches a narrative that mentions a test
score, which the LLM never saw (test-set honesty).

**Where to see it.** The report's "Selection and execution caveats" section ends with a
"Number check" line; the champion step's result lists the cut sentences.

### 1c. Episodic memory across runs — AG (chunk 88, done)

**Problem.** A project already stores one outcome fact per finished run, but every stored fact
goes into every step's prompt, unranked and unbounded, and the facts name candidates only by run-
local IDs (`c_01`), so the next run cannot tell *what* worked.

**What we build.** The agent lecture's memory lifecycle, completed:
1. **Record:** each finished run stores what each candidate was (estimator, features, text
   method), its validation score, and why failed ones failed. Never a test score.
2. **Retrieve:** a new run gets only the few most relevant earlier runs: same dataset first, then
   same target, newest first (score = similarity, then recency).
3. **Augment:** those runs go into the prompt; the experiment-design step names which earlier
   runs it used, and the report shows them.

**How it helps.** The second run on the same data starts from what worked and skips what failed,
and the user sees which earlier run influenced the design. The prompt stays small as a project
grows.

**Where to see it.** Experiment-design decision ("earlier runs used"), report caveats section.

### 1d. Testing the agent: recorded runs and an LLM judge — L4, L7, AG, SV (chunk 89, done)

**Problem.** The end-to-end tests checked that the pipeline runs (steps, gates, one test score),
but not whether the LLM's text is any good. Every real-model run also cost money and gave a
different answer each time, so a failure could not be repeated.

**What we built.** The scenario suite runs under pytest with three layers:
1. **Behaviour checks** (code): which steps ran, which gates opened, what was stored.
2. **Judged quality** (DeepEval, an LLM-as-judge library): did the agent call the expected
   tools; is every claim in the report supported by the evidence; does the champion choice
   follow the validation ranking without claiming a test score.
3. **Record and replay**: one paid run records every LLM answer (product and judge) at the
   provider seam; later runs replay them for free and identically. A changed prompt shows as a
   drift warning; a missing answer fails loudly.

**Course ideas.**
- L4: decoding is sampling, so the same prompt gives different answers; replay makes a run
  repeatable. Fluent text is not evidence, so faithfulness is checked against the evidence.
- L7: the harness around the model is engineering: evaluation belongs in it, not after it.
- AG: tool selection is a skill the agent can get wrong; tool correctness measures it.
- SV: claims must be traceable to evidence; the faithfulness judge checks exactly that.

**How it helps.** A prompt change can be checked in minutes without spending money, and a
quality regression (an unsupported claim, a wrong tool) fails a test instead of reaching a
report.

**Where to see it.** `integration/SCENARIOS.md`; `make itest-replay`; spec
`plan/89_integration_tests_pytest.md`.

## 2. Course ideas the architecture already follows

| Course idea | Where in Statlab |
|---|---|
| AG: an LLM agent = planning + tools + memory + environment | fixed step pipeline with an LLM decision per step; MCP tool server; project memory |
| AG: a tool is a name, a description and a JSON parameter schema | every MCP tool has a typed signature the agent reads (`mcp_server/statlab_tools/tools/`) |
| AG: tool selection is hard with many tools; narrow the set | each step may only call its allow-listed tools (`skills/modelling.md`) |
| AG: LLMs are bad at calculation, so calculation is a tool | the LLM never computes; every statistic comes from a tool |
| AG: tool errors — transient or persistent, how often to retry | transient errors retry with backoff; an invalid LLM answer is repaired up to twice, then unusable parts are dropped instead of failing the step |
| AG: procedural memory (system prompt, skills) | `skills/modelling.md`, one section per step |
| AG: memory consolidation (summaries) | long chat histories are summarised and shortened |
| L7: online vs offline models, sovereignty | provider-agnostic LLM layer; an offline rule-based provider runs the whole pipeline without any API |
| L7: LLM vs intelligent agent — the LLM has no goals and is indifferent to truth | goals, success thresholds and honesty rules live in code; the LLM only chooses within checked options |
| L4: decoding is sampling, so answers vary | structured outputs validated by schema; decisions re-checked by code |
| SV: compare fairly — same task, data, metric | one shared split; validation-only selection; exactly one test score per run, prior test use disclosed |
| SV: decision aid, not a catalogue | the report is a comparison table plus the reason the champion won |

## 3. Status

| Item | Status |
|---|---|
| 1a text methods (chunk 82) | done 2026-10-19 |
| 1b report number check (chunk 87) | done 2026-10-27 |
| 1c episodic memory (chunk 88) | done 2026-10-27 |
| 1d recorded runs and LLM judge (chunk 89) | done 2026-10-28 |
