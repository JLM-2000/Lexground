# Corpora

## Where documents come from

Ingestion goes through a `DocumentSource`, so a jurisdiction is one interface away rather
than a fork of the pipeline.

| Source | What it reads | Notes |
|---|---|---|
| `eurlex` | EU legislation by CELEX id | Fronted by a JavaScript bot challenge; see below |
| `boe` | Spanish consolidated law by BOE id | Open XML API, article numbering already structured |
| `file` | Any PDF, DOCX, HTML or text file | For corpora with no public API |

Adding a jurisdiction means implementing `fetch()` and returning plain text. What is *not*
generic is the citation convention: `Art. 22(1)` suits EU and Spanish acts, while US, UK
and German citation forms differ enough to need their own `build_citation`. That, rather
than fetching, is the real work in a new jurisdiction, which is why the shipped set is
small and honest instead of a long list of half-tested connectors.

Anything without a public API goes through `file`, which covers every jurisdiction for
anyone who already holds the documents.

## Documents with no article structure

`chunk_document()` runs the legal parser first and falls back to `chunk_prose()` when it
finds no provisions. Prose chunks cite the nearest heading, then a page number, then a
paragraph ordinal, so the locator is still something a reader can find in the original.
That is what keeps quote fidelity meaningful for a medical handbook or an economics report
rather than only for legislation.

Two corpora ship, for two different jobs.

## The fixture corpus (what CI runs)

`data/fixtures/` — two **synthetic** regulations written for this repository:

| Short title | Act | Languages |
|---|---|---|
| `ADSR` | Regulation (EU) 2024/9001 on Automated Decision Systems | en, es |
| `DRRR` | Regulation (EU) 2024/9002 on Digital Records Retention | en |

**These are not real law.** The fictional numbering and the `FIXTURE` CELEX prefix are
deliberate so they cannot be mistaken for genuine acts or cited as such.

They exist because the gate has to be hermetic. EUR-Lex serves a bot challenge to
non-browser clients (below), so a CI job fetching the live corpus would be flaky for
reasons that have nothing to do with the change under test. A red pipeline has to mean the
system regressed.

Being written rather than found, they are drafted to exercise what is easy to get wrong:

- **Paragraph-level citation.** Articles carry numbered paragraphs, so chunking is tested
  at the level lawyers cite.
- **Cross-document distractors.** Both acts have a record-keeping article with a
  multi-year retention period and a penalties article with the same two-tier fine
  structure. A retriever keying on surface wording confuses them — and the golden set
  contains both questions, so it notices.
- **A cross-reference that must be followed.** `DRRR Art. 2(2)` excludes records of
  automated decisions and points at the other act. Stopping at the first lexical hit gives
  the wrong answer.
- **Multilingual alignment.** The Spanish text of 9001 is a partial translation, so a
  Spanish question has to resolve to the Spanish chunk of the same provision — and the gaps
  test what happens when a provision exists in one language only.

Graded by `data/fixtures/golden.jsonl` (37 cases) against `data/thresholds.offline.json`.

```bash
make seed && make eval
```

## The real corpus

`data/corpus.json` — GDPR, the AI Act and the DSA, in English and Spanish, graded by
`data/golden/cases.jsonl` (40 cases) against `data/thresholds.json`.

```bash
make seed-live && make eval-live
```

### The EUR-Lex challenge

EUR-Lex fronts its HTML views with a JavaScript bot challenge. Every non-browser client —
`httpx`, `curl`, a browser user-agent string, retries with backoff, cookie persistence —
gets **HTTP 202 with a ~2 KB stub body**. The CELLAR content-negotiation endpoint
(`publications.europa.eu/resource/celex/...`) returns 400 for the same requests.

This is not a bug in the fetcher and it is not worked around in code. `EurLexClient`
detects the stub, retries, and then fails with an actionable message:

```
32016R0679/en: EUR-Lex served a bot challenge (HTTP 202, 2035 bytes) after 3 attempts.
Seed the cache in data/corpus/ from a browser session, or run against the committed
fixture corpus.
```

EUR-Lex content is freely reusable under Commission Decision 2011/833/EU. The obstacle is
the delivery mechanism, not the licence.

### Seeding the cache

The ingest pipeline reads `data/corpus/{CELEX}.{lang}.txt` before it reaches for the
network, so any route that gets you the text works:

1. Open `https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679` in a
   browser, save the rendered text, and write it to `data/corpus/32016R0679.en.txt`.
2. Repeat per act and language listed in `data/corpus.json`.
3. `make seed-live`, or `lexground ingest --offline` to guarantee no network access.

Cached bodies are gitignored: they are large, reproducible, and subject to the source's
terms. The committed fixture corpus is what makes the repository runnable without them.

## Adding an act

1. Add an entry to the manifest. `short_title` becomes the citation prefix, so `"GDPR"`
   yields `GDPR Art. 22(1)`:

   ```json
   {
     "celex_id": "32022L2555",
     "short_title": "NIS2",
     "title": "Directive (EU) 2022/2555 on measures for a high common level of cybersecurity",
     "languages": ["en", "es"],
     "version": "original",
     "adopted_on": "2022-12-14"
   }
   ```

2. Seed its cache entry, then re-run ingestion. Existing chunks for that
   `(celex_id, language, version)` are replaced, not duplicated.

3. **Add golden cases before trusting it.** An act in the index with no cases behind it is
   an act the gate cannot see. Include at least one question whose answer sits in a
   provision that resembles one in another act, and one out-of-scope question the system
   should decline.

4. The `index_version` hash changes, so evaluation history before and after is not directly
   comparable — the dashboard groups by it for that reason.

## Languages

Stemming is configured for English, Spanish, French and German; anything else falls back to
the English stemmer, which is wrong but not catastrophic — the dense arm carries those
queries. Adding a language means extending `TEXT_SEARCH_CONFIG`, the `CASE` in the
generated column, and `ARTICLE_KEYWORD` in the parser.

The embedding model is multilingual, so a Spanish question can match English text. The
golden set deliberately does not rely on that: cross-language matching is a fallback, and
the language filter on the API is the supported path.
