# Architecture

```
EUR-Lex ──▶ fetch ──▶ parse ──▶ chunk ──▶ embed ──▶ Postgres (pgvector + tsvector)
                                                          │
                        question ──▶ HybridRetriever ──────┤
                                          │  lexical (BM25-ish, stemmed)
                                          │  dense   (cosine, HNSW)
                                          ▼
                                    RRF fusion ──▶ top-k context
                                                          │
                                                          ▼
                                              Answerer ──▶ GroundedAnswer
                                                   (answer, citations, refusal)
```

The API and the evaluation harness both call the same `QueryService`. What CI grades is
what production serves — if those diverge, the gate stops meaning anything.

## Ingestion

`fetch` → `parse` → `chunk` → `embed` → upsert, driven by a corpus manifest
(`data/corpus.json`) and re-runnable: ingesting a document that already exists replaces its
chunks rather than duplicating them.

### Fetching

EUR-Lex fronts its HTML views with a JavaScript bot challenge that answers **HTTP 202 with
a ~2 KB stub body**. That is the awkward failure: it is not an error status, and a naive
client treats it as success and hands nonsense to the parser, which fails much later with a
confusing message. `EurLexClient` checks the response size explicitly, retries with
exponential backoff, and then raises `CorpusUnavailableError` naming the status and byte
count. Bodies are cached to disk so re-ingestion costs the upstream nothing.

### Parsing

`parse_document` splits an act into the units a lawyer cites:

- **Recitals** — numbered `(1) …` blocks, kept whole. A recital is the interpretive unit
  courts cite; splitting one destroys its meaning.
- **Articles** — split at their numbered paragraphs, since `Article 22(1)` is the level
  citations actually address. Articles with no numbered paragraphs stay whole.

The article keyword is per-language (`Article`, `Artículo`, `Artikel`), so the same parser
handles every official-language edition. An unnumbered continuation block attaches to the
paragraph above it rather than becoming a chunk with no citation.

### Chunking

Every chunk carries exactly one pin cite — `ADSR Art. 4(2)`, `GDPR Recital 71` — because
that is what makes citation accuracy measurable. Fixed-window chunking cuts across article
boundaries, and once a span straddles two provisions there is no honest answer to "which
article does this claim rest on".

Provisions longer than 2,400 characters are split with 200 characters of overlap and
labelled `[1/2]`, so a sentence straddling the boundary stays retrievable from either half.
`citation_key()` strips both the paragraph and the part suffix when scoring, so the golden
set does not care how the split landed.

## Storage

One Postgres instance backs both retrieval arms. Two indexes, two access paths:

```sql
CREATE INDEX ix_chunks_search_vector ON chunks USING gin (search_vector);
CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
```

`search_vector` is a **generated column**, not a trigger:

```sql
to_tsvector(
    CASE language
        WHEN 'es' THEN 'spanish'::regconfig
        WHEN 'fr' THEN 'french'::regconfig
        WHEN 'de' THEN 'german'::regconfig
        ELSE 'english'::regconfig
    END,
    text
)
```

`to_tsvector(regconfig, text)` is immutable, and a `CASE` over a stored column is immutable
too, so per-language stemming stays declarative. The first version used the `simple` config
for everything, which does no stemming — "records" never matched "record". Fixing it moved
recall@5 from 0.72 to 0.78 on a fixed embedding backend.

Using one database for both arms is a deliberate choice over a dedicated vector store. It
is one system to operate, backups cover the index and the metadata together, and it is what
a customer already runs.

## Retrieval

Two arms with different failure modes, run independently and fused.

**Lexical.** Postgres full-text search ranked by `ts_rank_cd`. Strong on exact statutory
terms, defined phrases and article numbers — the things a paraphrase model smooths away.
Question terms are **ORed** into the tsquery: `websearch_to_tsquery` ANDs them, and no
single provision contains every word of a natural-language question, so the arm returned
nothing at all until this was fixed. Tokens shorter than three characters are dropped
unless they are digits, so "72" survives in "within 72 hours".

**Dense.** pgvector cosine over multilingual embeddings
(`paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions). Handles paraphrase and the
cross-lingual case — a Spanish question resolving to the Spanish edition of the right
provision.

Embedding backends are swappable behind an `Embedder` interface. `HashEmbedder` is
deterministic, dependency-free and needs no download, so tests assert real retrieval
behaviour instead of mocking it; `FastEmbedEmbedder` is the real one. E5-style
`query:`/`passage:` prefixes are applied only for E5 models — sentence-transformers models
are trained symmetrically and score *worse* with them.

### Fusion

Reciprocal rank fusion, `score = Σ 1/(k + rank)` with `k = 60`, rather than a weighted score
blend. `ts_rank_cd` and cosine similarity are not on a comparable scale, and any fixed
weight needs retuning whenever either side changes; rank position is stable. A provision
found by both arms outranks one found by either alone, which is the behaviour worth having.

The cost is that fused scores carry no magnitude information — see
[evaluation.md](evaluation.md#abstention) for why that ruled them out as an abstention
signal.

## Synthesis

Answers come back as a closed JSON schema:

```python
class GroundedAnswer(BaseModel):
    answerable: bool
    answer: str            # with [n] markers
    citations: list[Citation]   # marker, pin cite, verbatim supporting quote
    refusal_reason: str
```

`answerable` being a typed field rather than prose is what makes refusal gradeable instead
of pattern-matched. The verbatim `supporting_quote` is what makes `quote_fidelity` a
deterministic check rather than a judgement call.

The schema is hardened before it goes to the API: every object closed with
`additionalProperties: false`, and **every** property forced into `required`. Pydantic
omits fields carrying defaults from `required`, and structured outputs reject a partial one
— `refusal_reason` would have been dropped.

Two backends behind one interface. `ClaudeAnswerer` is the real path. `ExtractiveAnswerer`
quotes the top-ranked provision instead of composing prose, which keeps the stack runnable
and the tests deterministic with no API key — and cannot abstain, which the offline
threshold profile accounts for honestly rather than papering over.

## Observability

Prometheus metrics on `/metrics`: query counts split by whether the system was willing to
answer, retrieval and end-to-end latency histograms, cumulative provider spend, indexed
chunk count, and the latest value of each evaluation metric.

Every answered question persists its full ranking to `query_traces` — both arms' ranks and
scores per chunk. When an answer is wrong, that is the difference between "retrieval missed
the article" and "synthesis ignored it", answerable after the fact without re-running
anything. The retrieval inspector in the UI reads it.

`/health` is liveness. `/health/ready` is index-aware and reports `degraded` when the index
is empty, because a service that is up with nothing indexed is up and useless — a deploy
gate should treat that as not-ready.
