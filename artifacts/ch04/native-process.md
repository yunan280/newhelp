# SDD ledger — plan: docs/superpowers/plans/2026-09-30-ch04-retrieval-quality.md

Execution: Native; user said 用Native; base 0e3f3e2; baseline files C:/Users/27497/projects/ch04-baselines/20260930-native/files.
Runtime: skill shell scripts copied with LF to external baseline directory; installed Git Bash E:/develop/Git/bin/bash.exe; plugin originals unchanged.

## Pre-flight shared interfaces
- Task 1 -> Task 2: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 1 -> Task 4: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 2 -> Task 4: SearchFilters/HybridMilvusIndex/Strategy — names/types consistent; no conflict.
- Task 3 -> Task 4: QueryUnderstanding/understand_query — names/types consistent; no conflict.
- Task 1 -> Task 5: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 3 -> Task 5: QueryUnderstanding/understand_query — names/types consistent; no conflict.
- Task 4 -> Task 5: RetrievalResult/rerank_chunks/metadata — names/types consistent; no conflict.
- Task 1 -> Task 6: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 5 -> Task 6: AnswerResult/SourceDTO/RagRuntime/answer_question — names/types consistent; no conflict.
- Task 2 -> Task 7: SearchFilters/HybridMilvusIndex/Strategy — names/types consistent; no conflict.
- Task 3 -> Task 7: QueryUnderstanding/understand_query — names/types consistent; no conflict.
- Task 4 -> Task 7: RetrievalResult/rerank_chunks/metadata — names/types consistent; no conflict.
- Task 5 -> Task 7: AnswerResult/SourceDTO/RagRuntime/answer_question — names/types consistent; no conflict.
- Task 6 -> Task 7: source endpoints and KbRuntime.docs_root — names/types consistent; no conflict.
- Task 1 -> Task 8: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 2 -> Task 8: SearchFilters/HybridMilvusIndex/Strategy — names/types consistent; no conflict.
- Task 1 -> Task 9: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 2 -> Task 9: SearchFilters/HybridMilvusIndex/Strategy — names/types consistent; no conflict.
- Task 3 -> Task 9: QueryUnderstanding/understand_query — names/types consistent; no conflict.
- Task 4 -> Task 9: RetrievalResult/rerank_chunks/metadata — names/types consistent; no conflict.
- Task 5 -> Task 9: AnswerResult/SourceDTO/RagRuntime/answer_question — names/types consistent; no conflict.
- Task 8 -> Task 9: EvalCase/metrics/dataset — names/types consistent; no conflict.
- Task 6 -> Task 10: source endpoints and KbRuntime.docs_root — names/types consistent; no conflict.
- Task 7 -> Task 10: SSE sources/JSON/filter injection/runtime factory — names/types consistent; no conflict.
- Task 1 -> Task 11: ChunkSnapshot/RefusalInput/record_refusal/citations — names/types consistent; no conflict.
- Task 2 -> Task 11: SearchFilters/HybridMilvusIndex/Strategy — names/types consistent; no conflict.
- Task 3 -> Task 11: QueryUnderstanding/understand_query — names/types consistent; no conflict.
- Task 4 -> Task 11: RetrievalResult/rerank_chunks/metadata — names/types consistent; no conflict.
- Task 5 -> Task 11: AnswerResult/SourceDTO/RagRuntime/answer_question — names/types consistent; no conflict.
- Task 6 -> Task 11: source endpoints and KbRuntime.docs_root — names/types consistent; no conflict.
- Task 7 -> Task 11: SSE sources/JSON/filter injection/runtime factory — names/types consistent; no conflict.
- Task 8 -> Task 11: EvalCase/metrics/dataset — names/types consistent; no conflict.
- Task 9 -> Task 11: calibration JSON/runner — names/types consistent; no conflict.
- Task 10 -> Task 11: citations/feedback browser state — names/types consistent; no conflict.

## Tasks
- Task 1: pending
- Task 2: pending
- Task 3: pending
- Task 4: pending
- Task 5: pending
- Task 6: pending
- Task 7: pending
- Task 8: pending
- Task 9: pending
- Task 10: pending
- Task 11: pending

Baseline: pytest -q exit=0; full log baseline-tests.log.

Ruling: use Python stdlib extraction/task completion on Windows because shipped Bash helper needs unavailable awk; preserves task brief, BASE, test log and successful-only completion contract; cost if wrong: ledger could miss a task.
Task 1: in_progress (base 0e3f3e201a0a1883181c35635969ab1b2a7cfc74).
Baseline: 339 passed, 5 deselected in 19.08s.
Task 1: RED 18 failed; initial GREEN 65 passed; index-drift RED 1 failed; final suite 358 passed, 5 deselected; scoped Ruff pass.
Task 1: Ruling: track unchanged knowledge/__init__.py and splitter.py baseline because modified ingest imports them and a clean checkout must contain direct dependencies; other Ch03 work is not staged; cost if wrong: extra baseline files included in this task.
Task 1: complete (commits 0e3f3e2..7b789da, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_storage.py tests/test_ch04_migration.py tests/test_knowledge_store.py tests/test_knowledge_ingest.py tests/test_db_repository.py tests/test_db_ddl_drift.py tests/test_db_models.py -q → 80 passed in 0.97s)

Task 2: RED 25 failed/1 passed; CLI RED 3 failed; GREEN 29 passed; suite 385 passed/5 deselected; Ruff pass.
Task 2: Ruling: add CLI behavior tests beyond the brief file list and update the existing KB remote-writer test to consume snapshots; the public command and selected-row behavior need coverage; cost if wrong: additional baseline test file tracked.
Task 2: complete (commits 7b789da..35dffec, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_vectors.py tests/test_ch04_filters.py tests/test_knowledge_sync.py tests/test_ch04_cli.py -q → 29 passed in 1.20s)

Task 3: RED 17 failed; numeric/synonym RED 2 failed; GREEN 19 passed; suite 404 passed/5 deselected; live labeled samples 12/12 passed, failed=0; Ruff pass.
Task 3: complete (commits 35dffec..c4d9e2a, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_query.py -q → 19 passed in 0.05s)

Task 4: RED 11 failed; GREEN 11 passed; suite 415 passed/5 deselected; Ruff pass; actual official config/tokenizer revision 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e max_length=8192 verified. Weights download running, real inference pending Task 11.
Task 4: complete (commits c4d9e2a..8259baa, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_retrieval.py tests/test_ch04_reranking.py -q → 11 passed in 4.18s)

Task 5: RED 16 failed; GREEN 18 passed; suite 433 passed/5 deselected; Ruff pass; real labeled answering 11/11 passed (10 real generations, empty evidence bypass); initial output preserved. Fixed reranker weights loaded successfully.
Task 5: Ruling: label the evidence-backed answer that arrival time cannot be guaranteed as answerable; explicit prohibition is supported evidence, while unknown actual arrival time still refuses; cost if wrong: this labeled example affects prompt validation, not retrieval ground truth.
Task 5: complete (commits 8259baa..de3b65c, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_answering.py -q → 18 passed in 1.66s)

Task 6: RED 13 failed/9 passed; GREEN sources/KB/main 28 passed; suite 451 passed/5 deselected; Ruff pass; actual Windows junction containment checked. source.html content/visual QA pending Task 10 as planned.
Task 6: Ruling: include existing main KB router wiring and /kb route while tracking the modified KB API; these are direct wiring dependencies for new source endpoints; cost if wrong: baseline KB wiring included before the frontend task.
Task 6: complete (commits de3b65c..6d9329f, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_sources.py tests/test_kb_api.py tests/test_main.py -q → 28 passed in 2.59s)

Task 7: RED 6 failed/1 passed; business fallback RED 1 failed; final suite 463 passed/5 deselected; scoped Ruff pass.
Task 7: Ruling: isolate old service/API unit tests from real query classification with module-scoped labeled fakes, while live Prompt sample checks remain independent — avoids provider-dependent regressions without weakening new RAG boundary tests — cost if wrong: classifier integration must be caught by live acceptance.
Task 7: Ruling: treat a known business tool failure as a trustworthy operational result eligible for the business response; unknown tool names fall back to evidence-gated RAG — preserves existing tool error behavior — cost if wrong: ordinary generation could overstate an operational failure.
Task 7: Ruling: include existing embedding.py and registry per-tool timeout wiring as direct dependencies, and verify CLI/config beyond the brief command — modified retrieval imports the baseline encoder and FAQ requires its timeout field — cost if wrong: extra baseline dependency changes in this commit.
Task 7: complete (commits 6d9329f..2ee26f9, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_chat.py tests/test_ch02_service_prepare.py tests/test_ch02_service_run.py tests/test_ch02_service_stream.py tests/test_ch02_api_agent.py tests/test_ch02_api_chat.py tests/test_tool_knowledge.py tests/test_tool_infra.py tests/test_tool_ticket.py tests/test_config.py tests/test_ch04_cli.py -q → 113 passed in 7.63s)

Task 8: RED 22 failed; GREEN 22 passed; suite 485 passed/5 deselected; scoped Ruff pass; actual dataset validate 80/60/20/40 and source-first author annotation checklist frozen before retrieval.
Task 8: complete (commits 2ee26f9..8062eab, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_metrics.py tests/test_ch04_dataset.py -q → 22 passed in 0.70s)

Task 9: RED 20 failed; GREEN 20 passed; prompt drift RED 1 failed -> GREEN; suite 505 passed/5 deselected; scoped Ruff pass; actual judge labeled samples 5/5 passed. Real four-strategy numbers remain Task 11, as brief permits.
Task 9: complete (commits 8062eab..eaabd7e, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_eval_runner.py tests/test_ch04_calibration.py tests/test_ch04_judge.py -q → 20 passed in 1.67s)

Task 10: actual browser QA passed for mapped/unmapped citations, bigint, malicious HTML text, document section, stale snapshots, both locked feedback choices, refresh, refusal, SSE error/interruption, and unavailable storage. Full backend regression 505 passed/5 deselected.
Task 10: Ruling: apply the user's explicit Vibe exception, with no frontend brainstorm/TDD/code review; Native completion command uses backend regression alongside recorded browser proof — user requirements outrank the skill's generic TDD gate — cost if wrong: frontend regressions rely on browser QA rather than automated tests.
Task 10: complete (commits eaabd7e..d8a7989, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_sources.py tests/test_ch04_chat.py -q → 26 passed in 2.68s)

Task 11: acceptance script RED 9 -> GREEN 9; document .env root regression RED 1/1 -> source/KB GREEN 24; full suite 516 passed/5 deselected; Ruff pass. Real MySQL backup/migration twice/reindex20/audit0, prepare80/60 and calibrate20 complete. Comparison160 saved with73 provider402 errors, status completed_with_errors and exit1. Official balance GET200/is_available=false. User recharge question pending; no production config cutover. Task11 is NOT complete.
Task 11: Ruling: verify the full retrieval stage independently against the same frozen corpus/query cache while paid generation is unavailable; keep the formal failed comparison unchanged and label retrieval-only output explicitly — provides real retrieval evidence without changing provider/stack — cost if wrong: retrieval-only numbers cannot establish end-to-end generation success.
Task 11: real retrieval-only verification160 passed, GT32 per strategy, N40/type8/difficulty15-15-10, errors0; Recall50/5/10 all1; finalMRR dense1 bm25.9635416667 hybrid.984375 rerank1. Native BM25 product-filter exact HX-210S source4048793780376354890 hit. Formal generation report remains completed_with_errors.
Task 11: Ruling: include unchanged Ch03 direct runtime/schema/source/task dependencies and their existing regression tests, plus the existing MySQL/Compose/FAQ description/version constraints, to make the new branch runnable in a clean checkout; compare unchanged files byte-for-byte with baseline and leave unrelated Ch03 drafts/backup scripts unstaged — cost if wrong: extra old-chapter baseline files in the checkpoint.
Task 11: checkpoint 793007c10da861bca6795eb7d858d4b19aa43401 committed; provider recharge pending. Completion contract, full HTTP acceptance, final review and finish are still outstanding. Do not mark complete or delete this workspace.
Task 11: exported clean Git checkpoint793007c, verified imported source from exported src with fake/nonsecret unit config: pytest516 passed/5 deselected in19.45s. This rules out hidden untracked baseline dependencies. Task11 remains incomplete.
Task 11: user reset DeepSeek and authorized continue; balance available and real probe OK. Preserved failed attempt. Restored comparison160 completed, service/judge errors0, actual denominators32 and scored29/31/29/30. Paused previously-enabled Ready mining task; sync0/audit[]; latest-provider env privately backed up; switched to knowledge_ch04/calibration/budget32000/docsroot and started own online8000. First smoke JSONknown+unknown passed, SSEknown invalid_generation safely pooled; preserved failure and rechecking without loosening validation.
Task 11: diagnosed repeated invalid generation from raw invalid_tool_calls: citation_numbers emitted as bare1,2,3 (invalid JSON), parsedNone and parsing_errorNone. No schema coercion/repair; added explicit JSON array Prompt examples. Labeled12/12 passed; original real-source repeat3/3 parsed after fix (before2/3 invalid). New run02 prepare/calibrate/compare started, preserving run01.
Task 11: Ruling: after answer-only Prompt change, create a new run/collection/database but copy the unchanged normalization cache only after verifying the canonical QUERY_SYSTEM hash against run01 — preserves identical query inputs and avoids unnecessary paid normalization — cost if wrong: stale normalization could bias the new run.

Task 11: restored final run02 comparison160 complete, errors0; online HTTP4/4 and isolated HTTP4/4 passed; extra5/5 passed with four real generation-stage insufficient_evidence pool rows; user manually confirmed real frontend effects. Timestamp precision RED1 -> GREEN12; final suite519 passed/5 deselected; Ruff pass. Mining task restored enabled/Ready.
Task 11: Ruling: finish Task11 implementation/verification before the one Native whole-branch review, treating its review/finish checklist entries as branch gates — resolves conflict with Native ordering without dropping either gate — cost if wrong: task-complete line alone would not prove review/finish complete.
Final: Ruling: review the recorded Ch04 implementation base0e3f3e2 through final HEAD, with the generated full package preserved and frontend/generated-output diff excluded from the code reading package; read spec/plan and actual frozen reports separately — isolates this chapter and honors the explicit frontend review exception — cost if wrong: an interaction with earlier Ch02 commits might be missed; live/regression checks cover integration.
Task 11: complete (commits d8a7989..996a1d0, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest -q → 519 passed, 5 deselected in 21.11s)

Final review: fresh gpt-6-astra/xhigh, reviewed996a1d0, reran519/5 and recomputed run02 metrics. Critical0 Important7 Minor0. Executor verified/regraded all7 Important; one root RED->GREEN fix pass starts. Report artifacts/ch04/final-review.md.
Final: Ruling: frontend implementation/visuals remain excluded by explicit Vibe exception; prior protocol UI proof and human real UI acceptance stand — cost if wrong: visual regressions lack a code review seat.
Final: Ruling: reviewer did not repeat paid/heavy/live checks; root actual reports and post-fix live reruns establish these separately — cost if wrong: reviewer cannot independently confirm current external service state.
Final: Ruling: sample corpus cannot establish real-product generalization or guaranteed hybrid superiority; retain explicit limits — cost if wrong: example scores may overestimate production quality.
Final: Ruling: exact provider weights behind returned alias are unverified; retain requested/response names and observed fingerprint without claiming a pinned provider revision — cost if wrong: later provider drift reduces reproducibility.
Final: Ruling: retain baseline demo identity architecture without adding authentication/operations UI in this chapter; service remains loopback — cost if wrong: this baseline is unsuitable for a multiuser public deployment.
Final: Ruling: keep frozen unanswerable-11 ground truth despite model correction output; do not relabel after seeing scores — cost if wrong: an annotation boundary may count a defensible correction as a false allow.
Final fix pass: all 16 initial regression cases RED; scoped GREEN76; additional unknown-Chinese-unit synonym RED1 then query GREEN36. Full suite536 passed/5 deselected; Ruff pass. All7 Important implemented; live query label first11/12, failed output retained, Prompt refinement under validation. Actual MySQL PK/autoincrement migration passed twice; README uv extras dry-run89 dependencies and actual app import passed.
Final: Ruling: preserve each numeric/conditional clause verbatim and require an explicit whole-turn business_only certificate, with raw knowledge hints still overriding it — prevents changed units/modifier attachment and mixed-route bypass — cost if wrong: fewer colloquial rewrites and mixed business questions may be refused as a whole when KB lacks operational evidence.
Final fix pass: refined Query Prompt labeled12/12 real provider passed; frozen labels unchanged. New run03 prepare/calibrate/compare launched sequentially with fresh60 query normalization, no old cache copied, own SQLite/Milvus collection.
Final fix pass: fresh run03 comparison160 exit0 errors0, frozen corpus/GT unchanged; calibrated threshold .9243238568305969. Final source suite536/5 passed27.48s. Post-fix actual online MySQL JSON/SSE acceptance4/4 passed. Sequential isolated80+2 setup launched on8001, own verified online8144 stopped, scheduled mining remains enabled/Ready.
Final fix pass COMPLETE: all7 Important resolved, no Critical/Minor/pending issues; one root pass, no re-review. Actual run03 comparison160 errors0; online4/4 isolated4/4 extra5/5 mixedJSON/SSE2/2 passed. Four missing-parameter and both mixed requests durably pooled as generation/insufficient_evidence. Source/GT/cache/Prompt hashes and aggregate denominators verified. Latest finish suite536 passed/5 deselected35.21s, full Ruff pass. After interruption, online8000 restored in hidden background and actual current HTML bytes/healthz verified; scheduled mining enabled/Ready. Public process ledger and proof archived before scratch cleanup. Integration remains pending: named branchch02-tools, user-owned worktree, base main; no push/merge.
