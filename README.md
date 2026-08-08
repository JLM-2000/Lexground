# Lexground

Grounded retrieval over EU regulatory law, with the evaluation harness wired into CI as a
build gate.

Retrieval over a corpus is the easy half. The half I cared about is knowing whether a
change made the answers worse, because a worse RAG system does not throw an exception, it
returns a confident wrong answer. So there is a golden set, seven metrics, thresholds in
version control, and a build that goes red when retrieval quality drops or a quote turns
out not to be in the article it cites.

```
make install && make seed && make eval
```

---

## What the gate measures

Latest run against the committed fixture corpus. 37 graded questions, 111 indexed
provisions, English and Spanish. Reproduce with `make eval`.

| Metric | Value | Floor | What a regression here means |
|---|---:|---:|---|
| `recall_at_5` | 0.906 | 0.85 | The governing provision stopped reaching the context window |
| `ndcg_at_10` | 0.806 | 0.75 | It still surfaces, but buried under distractors |
| `mrr` | 0.741 | 0.70 | It surfaces late; the first hit is usually wrong |
| `citation_recall` | 0.625 | 0.60 | The answer failed to cite the provision it should rest on |
| `citation_precision` | 0.625 | 0.60 | The answer cites an article it did not rest on |
| `quote_fidelity` | 1.000 | 0.95 | A supporting quote is not verbatim in the chunk it cites |
| `refusal_accuracy` | 0.865 | — | Out-of-corpus questions get answered instead of declined |
| `latency_p95_ms` | 16 | 500 | Retrieval got slow |

These are the **offline profile**: deterministic extractive synthesis, no provider API
key, which is what CI runs so the pipeline is hermetic and free. `citation_precision` is
capped at ~0.63 by that backend, which can only ever cite the top-ranked chunk.

### With a real model generating the answers

`make eval-judge` swaps in a live provider for both synthesis and the groundedness judge.
Measured over **five runs** on DeepSeek (`deepseek-chat`, which the API served as
`deepseek-v4-flash`), $0.015 per run:

| Metric | min | mean | max | spread |
|---|---:|---:|---:|---:|
| `groundedness` | 0.967 | **0.967** | 0.967 | 0.000 |
| `refusal_accuracy` | 0.946 | **0.946** | 0.946 | 0.000 |
| `citation_recall` | 0.906 | **0.938** | 0.969 | 0.063 |
| `quote_fidelity` | 0.938 | **0.948** | 0.969 | 0.031 |
| `citation_precision` | 0.672 | **0.706** | 0.732 | 0.060 |

Three runs, because one is not enough to trust. The retrieval metrics are absent from that
table because they do not move at all: same index, same embedder, nothing sampling.

`groundedness` was 0.61 with a spread of 0.23 until I read the judge's rationales instead
of its scores. Six of eleven failures said some version of "the content is supported but
source [3] does not exist". My judge was filtering the context down to the cited chunks and
then renumbering them from 1, so the answer's markers pointed at nothing. The judge was
right and my harness was broken. It now builds its context with the same `format_context()`
the answerer used, so the numbering is identical by construction.

The one case that still fails is the cross-reference trap the fixture corpus was built
around: `DRRR Art. 2(2)` excludes records of automated decisions and points at the other
act, and the model asserts the opposite. That is a real failure and the judge catches it,
which is the evidence that the jump came from fixing a bug rather than from a more
forgiving prompt.

`citation_precision` at 0.71 is now the weak spot. The model cites a supporting recital
alongside the governing article, which costs precision while recall stays at 0.94. Whether
that should be penalised at all is a judgement about the metric, not about the answer.

### Asking instead of guessing

Some questions cannot be answered until the reader says which regime governs them. "How
long do I have to keep my records?" is five years under one act and ten under the other,
and picking one is worse than asking which. `clarification_accuracy` scores that both ways:
asking when the sources branch, and not asking when they do not.

Prompting alone moved it from 0 of 4 ambiguous cases to 2 of 4, at the cost of one false
positive on a clear question. The first attempt failed in a way worth recording: the model
did not guess, it answered *both* regimes at once with correct citations for each. My rule
said do not pick one and do not refuse, and it found a third option I had not forbidden.
Naming that option explicitly is what moved the number.

Two of four is not a solved problem, and the honest next step is a cheap pre-check that
classifies the question against the index before synthesis rather than a longer prompt.

One case was cut because the label was wrong rather than the answer: the Spanish index
holds only one of the two acts, so with a language filter applied there is no second regime
to be ambiguous between. Clarification labels depend on the index in a way relevance labels
do not.

---

## Stack

| | |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic 2 |
| Sources | EUR-Lex, BOE (Spanish consolidated law), and any uploaded PDF, DOCX, HTML or text |
| Storage | PostgreSQL 16, pgvector with an HNSW index, Postgres full-text search |
| Retrieval | fastembed (`paraphrase-multilingual-MiniLM-L12-v2`, 384d), reciprocal rank fusion |
| Generation | Anthropic and DeepSeek behind one provider interface |
| Frontend | Next.js 14 App Router, TypeScript |
| Infra | Docker Compose, Terraform (ECS Fargate, RDS, ALB, Secrets Manager), GitHub Actions |
| Quality | 158 tests, ruff, mypy strict, evaluation gate in CI |

One Postgres instance backs both retrieval arms. I did consider a dedicated vector store
and decided against it: one system to run, backups that cover the index and the metadata
together, and it is what a customer already has.

---

## How it works

Ingestion runs once. `data/corpus.json` lists the acts, `ingest/fetch.py` pulls the text,
`ingest/parse.py` splits it into recitals and articles, `ingest/chunk.py` turns each
numbered paragraph into a chunk carrying its own pin cite, and each chunk is stored twice:
as a stemmed `tsvector` for keyword search and as a 384-dimension vector for similarity
search.

Then a question arrives. Say *"How long must records of automated decisions be kept?"*

1. **`retrieval/service.py`** runs two queries against Postgres. The lexical one turns the
   question into `records | automated | decisions | kept` and ranks by `ts_rank_cd`. The
   dense one embeds the question and ranks by cosine distance. Each returns 40 candidates.
2. **`retrieval/fusion.py`** merges the two rankings. A chunk that placed well in both
   rises to the top. The best 8 become the context.
3. **`synthesis/prompts.py`** builds the prompt: eight numbered blocks, each headed by its
   exact pin cite, plus the rules: cite every claim, quote verbatim, refuse if the answer
   is not in the blocks.
4. **`synthesis/providers.py`** calls the configured model and gets back JSON with four
   fields: `answerable`, `answer`, `citations`, `refusal_reason`. With no API key an
   extractive backend quotes the top chunk instead, so the stack runs with no account.
5. **`pipeline.py`** stores the whole ranking against the answer, which is what lets the
   retrieval inspector show, after the fact, whether a bad answer came from retrieval
   missing the article or synthesis ignoring it.

Evaluation reuses steps 1 to 5 unchanged. `evaluation/harness.py` walks the golden set,
`score_case()` grades each answer against the provisions it should have rested on, and
`Thresholds.evaluate()` decides whether the run passes. The API and the harness share one
`QueryService`, so what CI grades is what production serves.

---

## What I got wrong

Most of these shipped into a green test suite. Measurement found them, review did not.

**I tried to decide "should I refuse?" from the retrieval score.** Seemed obvious. Low
score means nothing relevant, so refuse. I measured it before trusting it and the top
scores for answerable and unanswerable questions turned out to overlap almost completely:
medians 0.70 against 0.50 lexical, 0.42 against 0.35 dense. Out-of-scope questions reuse
the same vocabulary as real ones ("supervisory authority", "deployer"), so they retrieve
strongly against an index that cannot answer them.

There was a second problem underneath that one. Reciprocal rank fusion scores depend only
on rank *position*, so the top hit always scores `1/(k+1)` whether it is the governing
article or noise from an unrelated act. I had been about to gate on a constant. There is
now a test, `test_score_depends_only_on_rank_position`, pinning that property so I do not
make the mistake twice.

Abstention moved into the model, which reads the context and decides. Refusal accuracy went
from 0.86 to 0.93. The score floors survive only as a backstop for an index that returned
nothing at all.

**My nDCG came back as 1.138.** It is capped at 1.0 by definition, so the metric was
simply wrong. Article 4 is stored as chunks 4(1), 4(2), 4(3), and all three counted as
separate hits against an ideal ranking that contains Article 4 once. Rankings are collapsed
to distinct provisions before scoring now. A metric that can exceed its own maximum is one
nobody is reading.

**The lexical arm was returning nothing at all.** Every result on the page was coming from
the vector side and I had not noticed, because the answers still looked fine.
`websearch_to_tsquery` ANDs every term, and no single article contains every word of a
natural-language question. Terms are ORed now so `ts_rank_cd` ranks on coverage.

**No stemming, so "records" never matched "record".** The stored `tsvector` used the
`simple` config. Fixing it moved recall@5 from 0.72 to 0.78 with the embedding backend held
constant. It is a generated column that picks its stemmer from the row's language:
`to_tsvector(regconfig, text)` is immutable and so is a `CASE` over a stored column, which
is what keeps it declarative rather than a trigger.

**It looked like the model was fabricating quotes.** Quote fidelity sat at 0.81, meaning
almost a fifth of supporting quotes did not appear in the article they cited. I read the
failing cases instead of tuning the prompt. My own context blocks were labelled
`[1] ADSR Art. 4(2) — SYNTHETIC FIXTURE — Regulation (EU) 2024/9001...` and the model was
copying the entire header line as the citation. Dropping the title took quote fidelity to
0.97 and citation precision from 0.61 to 0.75. The prompt was ambiguous, not the model.

**One evaluation run is not a measurement.** After that fix I got groundedness 0.71 and
nearly wrote it down as the number. Running the same code five times gave 0.71, 0.48, 0.67,
0.57, 0.62. Meanwhile all three retrieval metrics were identical every run, because the
index is deterministic and nothing there samples.

**Then the variance turned out to be a bug of mine.** A metric swinging by 0.23 on
unchanged code should have been the clue, and I treated it as noise to be tolerated rather
than a symptom. When I finally read the judge's rationales instead of its scores, six of
eleven failures said the content was supported but the cited source number did not exist.
The judge was filtering my context down to the cited chunks and renumbering them from 1,
while the answer's markers referred to the original numbering. Building the judge's context
with the same function the answerer uses took groundedness to 0.967 with zero spread across
three runs. Scores tell you a number moved; rationales tell you why.

**A four-character string in a `varchar(2)` column.** Query traces wrote `"auto"` into the
language column when no filter was set. Only the integration tests caught it, because it
needs a real Postgres to fail.

**A response schema the API would have rejected.** Pydantic leaves fields that have
defaults out of `required`, and structured outputs reject a partial `required` list, so
`refusal_reason` was being dropped.

**Five things broke the first time I ran it from a clean clone**, none of which appeared
while developing against a local venv. The CLI resolved its data paths from the installed
package location, which is `site-packages` in a container. A folded YAML block split the
seed command's arguments into separate shell commands. The embedding model downloaded at
runtime into a root-owned volume while the container ran as uid 10001. Next.js standalone
binds to the container hostname unless `HOSTNAME` is set. And server-rendered pages fetched
the API at `localhost`, which inside the frontend container is the frontend.

---

## Design decisions I would defend

**Chunk at the level people cite.** Fixed-window chunking cuts across article boundaries,
and once a span straddles two provisions there is no honest answer to "which article does
this claim rest on". That makes citation accuracy unmeasurable, which defeats the point of
the project. So recitals stay whole and articles split at their numbered paragraphs, one
pin cite per chunk. Golden-set relevance is keyed on the provision rather than the chunk
id, so the labels survive a change to the chunker.
[docs/architecture.md](docs/architecture.md#chunking)

**Two retrieval arms because they fail differently.** Postgres full-text search with
per-language stemming handles exact statutory terms and article numbers. pgvector cosine
handles paraphrase and the cross-lingual case. Fused by reciprocal rank rather than a
weighted blend, because `ts_rank_cd` and cosine similarity are not on a comparable scale
and any fixed weight needs retuning whenever either side changes.
[docs/architecture.md](docs/architecture.md#retrieval)

**The provider interface is not a base-URL swap.** Anthropic enforces the answer schema
server-side, so the response is valid by construction. DeepSeek has a JSON mode that
guarantees syntax and nothing about shape, so that provider puts the schema in the prompt
and validates with a bounded retry that feeds the error back. Costs price on the served
model and fall back to the requested one, since asking for `deepseek-chat` is served as
`deepseek-v4-flash`, and an unknown model reports zero rather than a plausible wrong number.

**Readiness is index-aware.** `/health` is liveness. `/health/ready` reports `degraded`
when the index is empty, because a service that is up with nothing indexed is up and
useless. The ALB checks the first so a bad task stays visible and alarms; the ECS container
check uses the second, so a broken ingest cannot replace a working deployment and the
circuit breaker rolls it back.

---

## Running it

Docker only. Nothing else installed, no API key, no network beyond the image pulls.

```bash
git clone https://github.com/JLM-2000/Lexground.git && cd Lexground
make start
```

That builds both images, waits for Postgres, creates the schema, indexes the fixture
corpus, and comes up with the API on **:8000** and the console on **:3000**. The embedding
model is baked into the image, so the index builds with no runtime download.

```bash
make eval-docker   # run the gate; the result appears on /evaluation
make logs          # follow the stack
make down          # stop and drop the volumes
```

### Developing against it

```bash
make install    # venv + backend deps + npm install
make up         # Postgres only
make seed       # schema + fixture index
make dev        # API on :8000, reload
make dev-ui     # console on :3000
make check      # everything CI runs: lint, types, tests, gate
```

`make help` lists the rest.

Export `LEXGROUND_ANTHROPIC_API_KEY` or `LEXGROUND_DEEPSEEK_API_KEY` and synthesis
switches from the extractive baseline to that provider automatically, with no config change.
`make eval-judge` then adds the groundedness judge.

The two providers are not interchangeable under the hood. Anthropic enforces the answer
schema server-side, so a response is valid by construction. DeepSeek offers a JSON *mode*
(syntactically valid, shape unguaranteed), so its provider puts the schema in the prompt
and validates with a bounded retry. That difference is the reason the provider interface
exists rather than a base-URL swap.

### Trying it

Once `make start` is up, open <http://localhost:3000>:

| Ask | Expect |
|---|---|
| *How long does a deployer have to complete a human review?* | Answers, cites `ADSR Art. 4` |
| *¿En qué plazo debe completarse la revisión humana?* | Same provision, Spanish chunk |
| *How long must records of automated decisions be kept?* | `ADSR Art. 6` — not `DRRR Art. 3`, the distractor |
| *What is the standard rate of VAT in Germany?* | Declines; nothing in the corpus supports an answer |

Every answer expands into the retrieval table showing both arms' ranks per provision, so
you can see whether a miss came from retrieval or from synthesis.

```bash
curl -s localhost:8000/health/ready
curl -s -X POST localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the deadline for reporting a malfunction?","language":"en"}'
```

Interactive API docs at <http://localhost:8000/docs>, Prometheus metrics at
<http://localhost:8000/metrics>.

### The corpus

CI runs against `data/fixtures/`: two **synthetic** regulations written for this
repository, numbered 2024/9001 and 2024/9002 so they cannot be mistaken for real law. They
exist because EUR-Lex serves a JavaScript bot challenge to non-browser clients, so a CI job
fetching the live corpus would be flaky for reasons unrelated to the change under test.
They are drafted to exercise what is easy to get wrong: paragraph-level citation,
near-identical record-keeping and penalty articles across two acts as distractors, a
cross-reference that has to be followed, and a partial Spanish translation.

The real corpus (GDPR, the AI Act and the DSA, graded by `data/golden/cases.jsonl`) is
configured in `data/corpus.json` and runs with `make eval-live`. See
[docs/corpus.md](docs/corpus.md) for seeding it past the challenge.

---

## Layout

```
backend/src/lexground/
  ingest/       fetch.py parse.py chunk.py runner.py
  retrieval/    embedder.py service.py fusion.py types.py
  synthesis/    providers.py answerer.py prompts.py schema.py
  evaluation/   metrics.py harness.py judge.py golden.py
  api/          routes/query.py routes/corpus.py routes/health.py
  pipeline.py   the one path a question takes
  cli.py        init-db, ingest, evaluate
frontend/       query console, retrieval inspector, evaluation dashboard
infra/terraform ECS Fargate, RDS Postgres, ALB, Secrets Manager
data/           corpus manifests, golden sets, gate thresholds
```

The two files worth reading first are `evaluation/metrics.py`, which is pure functions and
no I/O, and `evaluation/harness.py`, where `score_case()` is the whole grading rule in
twenty lines.

---

## Deployment

Terraform in `infra/terraform` stands up ECS Fargate in private subnets behind an ALB,
RDS Postgres with pgvector, and Secrets Manager for the database DSN and API key. Two
health checks do different jobs: the ALB checks liveness, so a task serving an empty index
stays visible and alarms rather than quietly leaving rotation; the ECS container check is
index-aware, so a bad ingest cannot replace a working deployment and the circuit breaker
rolls it back.

Validated with `tofu validate` against the AWS provider schema. Not applied against a live
account. There is no running deployment behind this repository.

[docs/deployment.md](docs/deployment.md)

---

## Documentation

- [docs/architecture.md](docs/architecture.md): pipeline, chunking, retrieval, schema
- [docs/evaluation.md](docs/evaluation.md): what each metric means, how thresholds were set
- [docs/corpus.md](docs/corpus.md): corpora, the EUR-Lex challenge, adding an act
- [docs/deployment.md](docs/deployment.md): infrastructure and rollout

---

Javier Lucia Marco · [github.com/JLM-2000](https://github.com/JLM-2000)
