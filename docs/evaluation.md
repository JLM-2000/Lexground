# Evaluation

The evaluation harness is the point of this repository. Everything else exists so there is
something to measure.

## Why it is a build gate

A test suite answers "does the code still do what it did". It cannot answer "did that
prompt edit make answers worse", because there is no exception to raise — the system keeps
returning fluent, plausible text while quietly citing the wrong article. The only way to
notice is to measure a fixed set of questions before and after, and to fail the build when
a number moves the wrong way.

So `lexground evaluate` exits non-zero on a threshold breach and runs as a CI step. The
deploy workflow refuses to run unless CI concluded successfully.

## The golden set

`data/fixtures/golden.jsonl` — 37 cases against the hermetic fixture corpus (32 answerable,
5 not). `data/golden/cases.jsonl` — 40 cases against the real EUR-Lex corpus.

Each case names the provisions an answer must rest on:

```json
{"id": "fx-review-deadline-en",
 "question": "How long does a deployer have to complete a human review once it has been requested?",
 "language": "en",
 "answerable": true,
 "relevant_citations": ["ADSR Art. 4"],
 "notes": "30 days, extendable once by 30."}
```

Two decisions worth defending:

**Relevance is keyed on provisions, not chunk ids.** A chunk id changes every time the
corpus is re-ingested, and the paragraph split changes whenever the chunker does. The claim
being asserted is "the answer must rest on Article 4", so that is what is stored;
`citation_key()` reduces `ADSR Art. 4(2) [1/2]` to `adsr art. 4` before any comparison.

**Unanswerable cases are deliberately adjacent.** `fx-unanswerable-dpo-en` asks whether a
deployer must appoint a data protection officer. Neither fixture act creates that
obligation, but the question is in-domain and a model carrying GDPR knowledge will want to
answer it. Refusing on out-of-scope questions is a graded behaviour, not an error path.

## The metrics

Three tiers, cheapest first. Each answers a different question, and the expensive one is
reserved for what the cheap ones cannot decide.

### Retrieval — did the right provision reach the context window?

| Metric | Question |
|---|---|
| `recall_at_5` | Was the governing provision in the top 5 distinct provisions? |
| `ndcg_at_10` | Was it ranked near the top, or buried under distractors? |
| `mrr` | How far down was the first correct hit? |

All three deduplicate the ranking to distinct provisions first. Without that, three
paragraph chunks of Article 4 each score a hit against an ideal ranking that contains
Article 4 once — which is how nDCG ended up at **1.138**, a value it cannot take. A metric
that can exceed its own maximum is a metric that is not being read.

### Answer — did the answer actually rest on it?

Retrieval can surface the right article while the answer cites a different one, and that
is the failure a reader notices.

- **`citation_precision`** — of the pin cites the answer claims, how many were expected.
- **`quote_fidelity`** — is each claimed supporting quote genuinely a verbatim span of the
  chunk it cites? A deterministic substring check after unicode, case and whitespace
  folding. No model involved, so it runs on every case at zero cost and catches fabricated
  quotes outright. Quotes under 12 characters fail by construction: short strings match
  almost any source by chance and prove nothing.
- **`refusal_accuracy`** — did answerable questions get answered and unanswerable ones get
  declined?

### Groundedness — does the prose overstate the sources?

The only metric needing a judge model, and it is asked exactly one question the cheap
checks cannot answer: does every legal proposition in the answer follow from the quoted
context? A claim can cite the right article, quote it verbatim, and still assert a
threshold or exception the article does not contain.

The judge is told explicitly that a claim which is correct as a matter of law but absent
from the supplied text counts as unsupported. Otherwise it grades its own legal knowledge
instead of the answer's grounding.

### Operational

`latency_p50_ms`, `latency_p95_ms`, and estimated USD spend per run. Cost is on the report
because an eval that triples token spend for two points of recall is a regression too.

## Abstention

The original design gated answerability on the fused retrieval score: below a floor, refuse.
Measuring it on the golden set killed the idea.

| | lexical median | dense median |
|---|---:|---:|
| Answerable (n=32) | 0.70 | 0.42 |
| Unanswerable (n=5) | 0.50 | 0.35 |

The distributions overlap almost completely. Out-of-scope questions reuse in-domain
vocabulary — "supervisory authority", "deployer", "automated decision" — so they retrieve
strongly against an index that does not answer them. Any threshold separating these
sacrifices real questions to catch a few fake ones.

There is a second, worse problem specific to fusion. Reciprocal rank fusion scores depend
only on rank *position*: the top result always scores `1/(k+1)`, whether it is the
governing article or the nearest miss in an unrelated act. Gating on the fused score is
gating on a constant. `test_score_depends_only_on_rank_position` pins that property so the
mistake cannot come back.

So: `min_lexical_score` and `min_dense_similarity` are set low, as a backstop for an index
that returned nothing usable, and abstention is the synthesiser's decision, taken with the
context in front of it. The prompt states that refusing on thin context is correct
behaviour rather than a failure, and `answerable` is a typed field so the harness can
score it instead of pattern-matching prose.

The extractive backend cannot do this — judging that none of the context answers the
question requires reading the context. That is why the offline profile drops the refusal
and groundedness floors rather than pretending they were met.

## Thresholds

Two profiles in version control:

- **`data/thresholds.offline.json`** — what CI runs. Deterministic extractive synthesis, no
  API key, no network. `citation_precision` is floored at 0.60 because that backend can only
  cite the top-ranked chunk; `refusal_accuracy` and `groundedness` are zeroed for the reason
  above.
- **`data/thresholds.json`** — Claude synthesis plus the judge, with a real
  `groundedness` floor.

Floors sit just under measured values, so a regression trips the gate but ordinary noise
does not. Raising a floor after an improvement is a deliberate commit, which is what keeps
the ratchet honest.

`latency_p95_ms` is a ceiling; every other threshold is a floor. The gate skips metrics
absent from a run rather than scoring them zero — a run without the judge should not fail
on `groundedness`.

## Index versions

Every run records an `index_version`: a hash of the corpus manifest and the embedding model
name. Results are only comparable within one. Changing the embedding model changes every
vector in the index, and comparing recall across that boundary compares two different
systems.

The evaluation dashboard groups history by index version for this reason.

## Reading a failure

```
GATE FAILED
  - recall_at_5: 0.7188 < floor 0.8000
  - citation_precision: 0.4375 < floor 0.8500
```

`--report reports/eval.json` writes per-case detail: the question, retrieved provisions in
rank order, expected and actual citations, and each score. CI uploads it as an artifact, so
a red build tells you which questions broke, not just that something did.

Recall failing while citation precision holds points at ingestion or the query side.
Citation precision failing while recall holds points at synthesis — the provision was
there and the answer did not use it. The retrieval inspector in the UI shows the same
split per query for questions that are not in the golden set.
