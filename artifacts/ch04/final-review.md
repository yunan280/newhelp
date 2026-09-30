# Ch04 最终独立评审

Reviewer: fresh gpt-6-astra / xhigh, read-only, no child reviewers.
Base: 0e3f3e201a0a1883181c35635969ab1b2a7cfc74
Head: 996a1d0de7c4affac52afe64627c052fe625aa38
Outcome: No Critical; seven Important; no Minor; Ready to merge: No, with fixes.
Fresh reviewer pytest:519 passed,5 deselected. Recomputed run02 metrics match summary.

## Confirmed Important findings

1. service.py:339,449,534: 订单1001的状态和HX-210的蓝牙版本？ with route=business and only query_order permits uncited HX-210蓝牙9.9 in JSON/SSE. A business result does not cover all knowledge in mixed requests. Require whole-turn business certainty, otherwise controlled knowledge path.
2. query.py:33,61: 5Ah ->5A accepted, 保修期内 removed accepted, HX-210不支持65W但支持10W ->支持65W但不支持10W accepted. Root additionally reproduced65Wh->65W, added否定, removed除非/否则. Protect whole sensitive facts/clauses and fallback original, rerun frozen eval under new ID.
3. sources.py:41 / ingest.py:63: real refunds.md ingest produces content_type=document, but reader permits only policy/manual, so source document returns404. Accept ingester types while retaining containment checks.
4. tools/knowledge.py:58 / service.py:359: FAQ retrieval UnsupportedContextError gets generic tool failure then RuntimeError, no pool. Preserve this domain outcome into common refusal; infrastructure errors remain errors.
5. migrate_ch04_schema.py:28: same fields/FK/indexes but missing primary key passes migration; later PoolCommitError NOT NULL id. Check PK and generated identifier, MySQL AUTO_INCREMENT.
6. evaluation/runner.py:385: successful retrieval followed by generation ConnectionError loses IDs and retrieval metrics, effective denominator0. Save retrieval before generation, reuse same evidence; stage-separated error metrics.
7. README.md:28: initial .[dev] install omits required rag extra; main import fails without pymilvus. Update initial install and verify app dependencies/install command.

## Strengths

Published metadata/hash snapshots, conditional sync and MySQL revalidation; numbering independent of layout; bigint strings; refusal/message transaction boundaries; fixed native retrieval stack; actual reports honestly show denominators/failures.

## Declined to judge (each resolved by executor ledger Ruling)

- Frontend index.html/source.html: explicit Vibe review exception.
- Paid calls/heavy downloads/live services: review assignment prohibits independent repetition; saved evidence inspected only.
- Real-product generalization or guaranteed hybrid superiority: example corpus cannot establish it.
- Provider alias underlying exact weights: response alias is observed, internal mapping unavailable.
- New authentication/cross-user authorization/production operations UI: demo identities and baseline architecture, outside this chapter.
- Relabel unanswerable-11 as answerable after model correction: frozen labeling boundary, no result-driven GT changes.

Review did not mutate source,index,HEAD,branch. No re-review will be dispatched; root fixes verified by RED->GREEN and whole suite.
