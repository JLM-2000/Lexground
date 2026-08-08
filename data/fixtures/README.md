# Fixture corpus

Two **synthetic** regulations, written for this repository. They are not real law and
must never be cited as such. The fictional numbering (2024/9001, 2024/9002) and the
`FIXTURE` CELEX prefix are deliberate so they cannot be mistaken for genuine acts.

They exist because the evaluation gate has to be hermetic. EUR-Lex fronts its HTML
views with a JavaScript bot challenge, so a CI job that fetched the real corpus would be
flaky in a way that has nothing to do with the change under test. Running the gate on a
committed fixture means a red pipeline always means the retrieval or synthesis
behaviour regressed.

They are drafted to exercise the parts of the pipeline that are easy to get wrong:

- **Paragraph-level citation.** Articles carry numbered paragraphs, so chunking is
  tested at the level lawyers actually cite (`ADSR Art. 4(2)`, not `ADSR Art. 4`).
- **Cross-document distractors.** Both acts contain a record-keeping article with a
  five-to-ten year retention period and a penalties article with the same two-tier fine
  structure. A retriever that keys on surface wording alone will confuse them.
- **An explicit cross-reference.** `DRRR Art. 2(2)` carves out records governed by the
  other regulation, so a correct answer has to follow the pointer rather than stop at
  the first lexical hit.
- **Multilingual alignment.** The Spanish text of 9001 is a partial translation, so a
  Spanish question must resolve to the Spanish chunk of the same provision.

The real corpus lives in `../corpus.json` and is graded by `../golden/cases.jsonl`.
Run it with `make eval-live` once `data/corpus/` has been seeded.
