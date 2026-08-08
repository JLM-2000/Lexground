# Lexground

Grounded retrieval over EU regulatory law, with the evaluation harness wired into CI as a
build gate.

Not a RAG demo. The interesting part is the part that decides whether a change made the
system worse: a golden set, six metrics, versioned thresholds, and a pipeline that goes
red when retrieval quality drops or a fabricated quote gets through.

```
make install && make seed && make eval
```

---

## What the gate measures

Latest run against the committed fixture corpus — 37 graded questions, 111 indexed
provisions, English and Spanish. Reproduce with `make eval`.

| Metric | Value | Floor | What a regression here means |
|---|---:|---:|---|
| `recall_at_5` | 0.906 | 0.85 | The governing provision stopped reaching the context window |
| `ndcg_at_10` | 0.806 | 0.75 | It still surfaces, but buried under distractors |
| `mrr` | 0.741 | 0.70 | It surfaces late — the first hit is usually wrong |
| `citation_precision` | 0.625 | 0.60 | The answer cites an article it did not rest on |
| `quote_fidelity` | 1.000 | 0.95 | A supporting quote is not verbatim in the chunk it cites |
| `refusal_accuracy` | 0.865 | — | Out-of-corpus questions get answered instead of declined |
| `latency_p95_ms` | 10 | 500 | Retrieval got slow |

These are the **offline profile**: deterministic extractive synthesis, no provider API
key, which is what CI runs so the pipeline is hermetic and free. `citation_precision` is
capped at ~0.63 by that backend — it can only ever cite the top-ranked chunk. Running
`make eval-judge` swaps in Claude synthesis plus an LLM groundedness judge and grades
against `data/thresholds.json`, which adds the `groundedness` floor.

I have not published numbers for the judged profile, because I have not run it at a
sample size worth quoting.

---

## Three things I would want to be asked about

**Retrieval score is not an abstention signal.** The first design gated answerability on
the fused retrieval score. Measuring it killed the idea: across the golden set the top
score for answerable and unanswerable questions overlaps almost completely — medians 0.70
vs 0.50 lexical, 0.42 vs 0.35 dense — because out-of-scope questions reuse in-domain
vocabulary. Worse, reciprocal rank fusion scores depend only on rank *position*, so the
top result scores `1/(k+1)` whether it is the governing article or the closest thing in an
unrelated act. The floors are now a backstop for an index that returned nothing, and
abstention is the synthesiser's decision, made with the context in front of it.
[docs/evaluation.md](docs/evaluation.md#abstention)

**Chunking at the level people cite.** Fixed-window chunking cuts across article
boundaries, and once a span straddles two provisions you cannot say which one a claim
rests on — which makes citation accuracy unmeasurable. Lexground splits on the legal
structure instead: recitals whole, articles broken at their numbered paragraphs, one pin
cite per chunk (`ADSR Art. 4(2)`). Golden-set relevance is keyed on the provision rather
than the chunk id, so the labels survive re-chunking.
[docs/architecture.md](docs/architecture.md#chunking)

**Two arms because they fail differently.** Postgres full-text search with per-language
stemming handles exact statutory terms and article numbers; pgvector cosine handles
paraphrase and cross-lingual matching. Fused with reciprocal rank rather than a weighted
blend, because `ts_rank_cd` and cosine similarity are not on a comparable scale and any
fixed weight needs retuning whenever either side changes.
[docs/architecture.md](docs/architecture.md#retrieval)

---

## What the eval caught that review did not

Every one of these shipped into a passing test suite and was found by measurement:

- **nDCG above 1.0.** Three paragraph chunks of one article each counted as a hit against
  an ideal ranking containing that article once. Rankings are now deduplicated to
  provisions before scoring.
- **The lexical arm returned nothing at all.** Every result was coming from the dense
  side. `websearch_to_tsquery` ANDs every term, and no single provision contains every word
  of a natural-language question. Terms are ORed now, so `ts_rank_cd` ranks on term
  coverage instead.
- **No stemming.** The stored `tsvector` used the `simple` config, so "records" never
  matched "record"; fixing it moved recall@5 from 0.72 to 0.78 on a fixed embedding
  backend. It is now a generated column that picks its stemmer from the row's language —
  `to_tsvector(regconfig, text)` is immutable, and so is a `CASE` over a stored column,
  which is what keeps it declarative instead of a trigger.
- **A 4-character sentinel in a `varchar(2)` column.** Query traces wrote `"auto"` into the
  language column when no filter was given. Found by an integration test, not a unit test.
- **A structured-output schema the API would have rejected.** Pydantic omits defaulted
  fields from `required`, and structured outputs reject a partial one.

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

With `ANTHROPIC_API_KEY` exported, synthesis switches from the extractive baseline to
Claude automatically — no config change — and `make eval-judge` adds the groundedness
judge.

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

The real corpus — GDPR, the AI Act and the DSA, graded by `data/golden/cases.jsonl` — is
configured in `data/corpus.json` and runs with `make eval-live`. See
[docs/corpus.md](docs/corpus.md) for seeding it past the challenge.

---

## Layout

```
backend/src/lexground/
  ingest/       fetch → parse → chunk → embed → index
  retrieval/    lexical + dense arms, RRF fusion, answerability floors
  synthesis/    grounded answers, mandatory citations, refusal as a typed outcome
  evaluation/   metrics, golden set, LLM judge, threshold gate
  api/          query, trace and corpus endpoints
frontend/       query console, retrieval inspector, evaluation dashboard
infra/terraform ECS Fargate, RDS Postgres, ALB, Secrets Manager
data/           corpus manifests, golden sets, gate thresholds
```

The API and the eval harness both go through one `QueryService`, so what CI grades is
what production serves.

---

## Deployment

Terraform in `infra/terraform` stands up ECS Fargate in private subnets behind an ALB,
RDS Postgres with pgvector, and Secrets Manager for the database DSN and API key. Two
health checks do different jobs: the ALB checks liveness, so a task serving an empty index
stays visible and alarms rather than quietly leaving rotation; the ECS container check is
index-aware, so a bad ingest cannot replace a working deployment and the circuit breaker
rolls it back.

Validated with `tofu validate` against the AWS provider schema. Not applied against a live
account — there is no running deployment behind this repository.

[docs/deployment.md](docs/deployment.md)

---

## Documentation

- [docs/architecture.md](docs/architecture.md) — pipeline, chunking, retrieval, schema
- [docs/evaluation.md](docs/evaluation.md) — what each metric means, how thresholds were set
- [docs/corpus.md](docs/corpus.md) — corpora, the EUR-Lex challenge, adding an act
- [docs/deployment.md](docs/deployment.md) — infrastructure and rollout

---

Javier Lucia Marco · [github.com/JLM-2000](https://github.com/JLM-2000)
