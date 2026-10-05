# Whole-branch review package

Repo: C:/Users/27497/projects/mewhelp-wt/ch02-tools
Base: 6d7603c5924ec85bbeaee8193f7dc06fb808640c
Head: f6aa8b8c092c377d95f3ee7d4060388510edcf41
Spec: docs/superpowers/specs/2026-10-04-ch07-context-management-design.md
Plan: docs/superpowers/plans/2026-10-04-ch07-context-management.md
Ledger: .superpowers/sdd/2026-10-04-ch07-context-management/progress.md

## Status
859 passed5deselected43.25s; Ruffpassed; 2JS ALL OK. Summary12/12 calibration+12/12 acceptance; reference8/8. Real HTTP default13, demo12, upstream402, complete=false. Router source binding stale after import/lint cleanup; refreshpending once balance restored. Do not count these acknowledged unfinished acceptance gates as passed; evaluate correctness beyond them. All fixed technology choices retained. Only factual labeledPrompt evaluation substitutes TDD.

## Review Focus verbatim

2026-10-04 执行中已获用户批准的预算修订：演示预算改为 5300/3709/1590，联合 profile v2 见 spec §15；下文初始 5650 仅为当时工程包，正式 Task 8 按修订验收。

1. 全库消息 ID 有空洞、跨会话穿插，或旧 checkpoint 缺 ledger 元数据：边界必须只覆盖该会话完整已提交轮，不能把全库连续 ID 当会话顺序。Task 2/3/5。
2. 摘要生成期间又降级一批、摘要插入失败或重复触发：只提交输入快照范围，原文不漏、S 不先行；后台不拖住用户 done。Task 4/5。
3. 原生订单 interrupt 跨进程恢复、恢复请求重放、流中断：新摘要可见但 next/interrupt/receipt 不被上下文刷新清掉；失败消息不能变成已提交历史。Task 5。
4. 超大中文输入、并行多工具、异常工具长结果和控制纠正：预算同时覆盖实际请求与剩余峰值；Layer 1 仍原文，不能截结果后声称未压缩。Task 1/6。
5. 切换请求迟到、聊天仍在流式、侧栏断网和两用户同序号：迟到响应不覆盖当前会话，读取失败不破坏继续聊天，原文与摘要不串用户。Task 7。


## Range
 .env.example                                       |  11 +
 README.md                                          |  38 +++
 .../agent-control-baseline/results.json            |  44 +++
 .../agent-control-candidate-01/results.json        |  53 ++++
 .../agent-control-candidate-02/answer-check.json   |  14 +
 .../agent-control-candidate-02/phase-checks.json   |  16 +
 .../agent-control-candidate-02/results.json        |  53 ++++
 .../ch07/20261004-native/default-01/summary.json   |  40 +++
 .../ch07/20261004-native/default-02/summary.json   |  23 ++
 .../ch07/20261004-native/default-03/summary.json   |  40 +++
 .../20261004-native/default-03/upstream-error.json |  10 +
 .../ch07/20261004-native/demo-01/summary.json      |  26 ++
 .../ch07/20261004-native/demo-02/summary.json      |  41 +++
 .../20261004-native/mysql/after-migration.json     |  76 +++++
 .../20261004-native/mysql/before-migration.json    |  18 ++
 .../references-acceptance-01/results.jsonl         |   8 +
 .../references-acceptance-01/summary.json          |  40 +++
 .../references-baseline/results.jsonl              |   5 +
 .../references-calibration-01/results.jsonl        |   8 +
 .../references-calibration-01/summary.json         |  40 +++
 .../router-calibration-01/results.jsonl            |  32 ++
 .../router-calibration-01/router.json              |   9 +
 .../router-calibration-01/summary.json             |  98 +++++++
 .../summary-acceptance-01/results.jsonl            |  12 +
 .../summary-acceptance-01/summary.json             |  49 ++++
 .../summary-calibration-01/results.jsonl           |  12 +
 .../summary-calibration-01/summary.json            |  49 ++++
 .../summary-calibration-02/results.jsonl           |  12 +
 .../summary-calibration-02/summary.json            |  49 ++++
 .../summary-calibration-03/results.jsonl           |  12 +
 .../summary-calibration-03/summary.json            |  49 ++++
 .../summary-calibration-04/results.jsonl           |  12 +
 .../summary-calibration-04/summary.json            |  49 ++++
 .../summary-calibration-05/results.jsonl           |  12 +
 .../summary-calibration-05/summary.json            |  49 ++++
 .../tokens-calibration-01/results.jsonl            |  17 ++
 .../tokens-calibration-01/summary.json             | 207 +++++++++++++
 .../tokens-calibration-02/context-profile.json     | 105 +++++++
 .../tokens-calibration-02/results.jsonl            |  17 ++
 .../tokens-calibration-02/reuse-audit.json         |  10 +
 .../tokens-calibration-02/summary.json             | 105 +++++++
 .../tokens-calibration-03/context-profile.json     | 106 +++++++
 .../tokens-calibration-03/results.jsonl            |  17 ++
 .../tokens-calibration-03/reuse-audit.json         |  11 +
 .../tokens-calibration-03/summary.json             | 106 +++++++
 dev-notes/ch07.md                                  | 134 +++++++++
 docs/ch07-logging.json                             |  13 +
 .../plans/2026-10-04-ch07-context-management.md    |  10 +-
 .../2026-10-04-ch07-context-management-design.md   |   8 +-
 eval/ch07/README.md                                |  15 +
 eval/ch07/freeze.json                              |  30 ++
 eval/ch07/references-acceptance.jsonl              |   8 +
 eval/ch07/references-calibration.jsonl             |   8 +
 eval/ch07/summary-acceptance.jsonl                 |  12 +
 eval/ch07/summary-calibration.jsonl                |  12 +
 eval/ch07/tokens-calibration.jsonl                 |  16 +
 scripts/migrate_ch07_schema.py                     |   6 +
 scripts/run_ch07.ps1                               |  34 +++
 scripts/smoke_ch07_acceptance.py                   | 260 +++++++++++++++++
 sql/ch07-ddl.sql                                   |  12 +
 sql/ch07-layers.sql                                |  33 +++
 src/mewhelp/ch05/agent.py                          | 130 ++++++---
 src/mewhelp/ch05/evidence.py                       |   7 +
 src/mewhelp/ch05/intent.py                         |  14 +-
 src/mewhelp/ch05/prompts.py                        |  22 ++
 src/mewhelp/ch05/runtime.py                        |  29 +-
 src/mewhelp/ch05/service.py                        |   4 +-
 src/mewhelp/ch05/state.py                          |   8 +
 src/mewhelp/ch05/workflow.py                       | 123 +++++---
 src/mewhelp/ch06/assessment.py                     |  13 +-
 src/mewhelp/ch06/evaluation.py                     |  12 +-
 src/mewhelp/ch06/prompts.py                        |   8 +-
 src/mewhelp/ch06/selection.py                      |   4 +-
 src/mewhelp/ch06/structured.py                     |  11 +-
 src/mewhelp/ch06/understanding.py                  |  39 ++-
 src/mewhelp/ch07/__init__.py                       |   1 +
 src/mewhelp/ch07/api.py                            |  33 +++
 src/mewhelp/ch07/budget.py                         |  67 +++++
 src/mewhelp/ch07/config.py                         |  62 ++++
 src/mewhelp/ch07/context.py                        | 113 ++++++++
 src/mewhelp/ch07/evaluation.py                     | 322 +++++++++++++++++++++
 src/mewhelp/ch07/migration.py                      |  77 +++++
 src/mewhelp/ch07/observability.py                  |  67 +++++
 src/mewhelp/ch07/projection.py                     | 166 +++++++++++
 src/mewhelp/ch07/prompts.py                        |  10 +
 src/mewhelp/ch07/provenance.py                     |  79 +++++
 src/mewhelp/ch07/schemas.py                        |  31 ++
 src/mewhelp/ch07/store.py                          | 128 ++++++++
 src/mewhelp/ch07/summary.py                        | 124 ++++++++
 src/mewhelp/ch07/summary_model.py                  |  95 ++++++
 src/mewhelp/ch07/tokens.py                         |  39 +++
 src/mewhelp/ch07/types.py                          |  71 +++++
 src/mewhelp/db/models.py                           |  25 ++
 src/mewhelp/main.py                                |   4 +
 src/mewhelp/static/index.html                      | 176 ++++++++++-
 src/mewhelp/tools/infra.py                         |   3 +-
 src/mewhelp/tools/registry.py                      |   4 +-
 tests/ch05/conftest.py                             |   4 +-
 tests/ch05/test_agent.py                           |   1 +
 tests/ch05/test_persistence.py                     |   7 +-
 tests/ch05/test_workflow.py                        |  15 +-
 tests/ch06/test_workflow.py                        |   3 +-
 tests/ch07/__init__.py                             |   1 +
 tests/ch07/conftest.py                             |   8 +
 tests/ch07/page-conversations.js                   |  59 ++++
 tests/ch07/test_acceptance_contract.py             |  44 +++
 tests/ch07/test_acceptance_resume.py               |  43 +++
 tests/ch07/test_budget.py                          |  77 +++++
 tests/ch07/test_context_logging.py                 |  57 ++++
 tests/ch07/test_conversation_api.py                |  75 +++++
 tests/ch07/test_evaluation_contract.py             |  64 ++++
 tests/ch07/test_joint_calibration.py               |  22 ++
 tests/ch07/test_migration.py                       |  58 ++++
 tests/ch07/test_model_context.py                   |  62 ++++
 tests/ch07/test_projection.py                      | 108 +++++++
 tests/ch07/test_prompt_order.py                    |  31 ++
 tests/ch07/test_reference_context.py               |  47 +++
 tests/ch07/test_resume_context.py                  |  24 ++
 tests/ch07/test_runtime_budget.py                  | 120 ++++++++
 tests/ch07/test_state_context.py                   |  88 ++++++
 tests/ch07/test_store.py                           | 110 +++++++
 tests/ch07/test_summary_jobs.py                    | 185 ++++++++++++
 tests/ch07/test_summary_length.py                  |  18 ++
 tests/ch07/test_tokens.py                          |  36 +++
 tests/page-smoke.js                                |   5 +
 tests/test_db_ddl_drift.py                         |  14 +-
 tests/test_db_models.py                            |   1 +
 127 files changed, 5790 insertions(+), 139 deletions(-)
