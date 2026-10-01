# SDD ledger — plan: docs/superpowers/plans/2026-10-01-ch05-workflow-agent.md

Approved: 用户「按Native开始」，2026-10-01；Native 主代理执行，最终独立整分支评审。baseline a2a98a8，Task 1 BASE 88315f0。
Baseline: pytest -q → 536 passed, 5 deselected, 25.85s.

## Interface preflight
| Producer → Consumer | Finding / disposition |
| --- | --- |
| Ch02 tools → bare / Agent | ToolRegistry async run + structured error; only three read tools. |
| limits / config → models / nodes | One shared AgentLimits and TokenUsage; total tokens reserve final call; DeepSeek extra_body verified before code. |
| intent / schemas → State / graph | Fixed seven literals / four routes; JSON never supplies node names. |
| retrieval → gate → Agent | Existing reranker score and calibrated threshold; QueryUnderstanding passthrough, SourceDTO serialized. |
| State / Context → graph runtime | add_messages completed history only; live dependencies exclusively Runtime.context. |
| Agent → SSE / front end | Buffered decisions, stream final only; ToolTrace call_id and round. |
| offers → confirmed tickets | Persist offer UUID, confirmed=True, existing tool closed-over request_id; DB unique guard. |
| JSON / SSE → logs / checkpoint | Same graph stream; done only after log_turn and checkpoint completion. |
| old ticket tool → new UI | Remove human status side effect; local handoff and confirmed ticket independent. |

## Rulings
Ruling: R1: Git Bash ships gawk-5.4.0.exe without awk alias. Export a bash awk function delegating to that binary for original skill scripts; no project technology change. First task-start failed before brief extraction; rerun with alias, preserve real RED/GREEN gates.

## Tasks
1 bare loop: in progress; 2 contracts; 3 gate; 4 Agent; 5 workflow; 6 actions; 7 UI; 8 acceptance; 9 review / finish: pending.
Task 1: complete (commits 88315f0..a448fb9, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05/test_bare.py tests/ch05/test_limits.py -q → 10 passed in 0.44s)
Task 2: complete (commits a448fb9..da68ff5, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05 -q → 25 passed in 2.64s)
Ruling: R2: State 增加 filters/entry_point/decision/refused/low_confidence_question_id/offer 作为明确的本轮传输及控制字段；begin_turn 必须重置，Context 仍独占运行依赖。
Task 3: complete (commits da68ff5..05c23ce, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05/test_evidence.py tests/test_ch04_retrieval.py tests/test_ch04_sources.py tests/test_ch04_calibration.py -q → 47 passed in 2.78s)
Task 4: complete (commits 05c23ce..7263145, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05 -q → 50 passed in 2.80s)
Ruling: R3: open_runtime 增加可选 rag_factory 注入以在集成测试保留真实图/文件 saver、只替换外部检索；默认仍复用原 get_rag_runtime，不改生产检索技术。代价是额外一个测试接缝。
Task 5: complete (commits 7263145..86f645b, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05/test_workflow.py tests/ch05/test_persistence.py tests/ch05/test_api.py -q → 20 passed in 2.52s)
Ruling: R4: 回执另存 State.ticket_receipts，不污染 ActionOffer 的固定DTO字段；保留跨轮 map。原因是回执需 checkpoint 但 offer 契约禁止额外字段；代价是一个持久字段。
Ruling: R5: 正确 request_id 列存在但目标索引缺失时允许补齐；同名不兼容索引拒绝。原因是 MySQL DDL 隐式提交后可能只完成列，需要安全可重入；代价是将缺失索引视为未完成迁移而非立即配置错误。
Task 6: complete (commits 86f645b..8f708e9, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05/test_actions.py tests/ch05/test_ticket_migration.py tests/test_tool_ticket.py tests/test_db_ddl_drift.py -q → 42 passed in 1.33s)
Ruling: R6: IAB 在 native confirm 后控制接口阻塞，真实人工确认和两种完整顺序由 Node 实际点击处理器测试覆盖；真实浏览器仍核验投诉/继续聊天/工单取消与确认/刷新，保存限制而不换技术。代价是人工确认缺少真实浏览器成功证据。
Task 7: complete (commits 8f708e9..29f521a, tests: node tests/page-smoke.js src/mewhelp/static/index.html → ALL OK)
Ruling: R7: 增加 docs/ch05-logging.json 并在演示启动命令传 --log-config；默认 Uvicorn 未给应用 INFO 配 handler，会隐藏现有节点日志。仅配置输出级别，不增加可观测技术。代价是启动需带配置参数。
Task 8: complete (commits 29f521a..0bfe640, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05/test_evaluation_contract.py tests/ch05/test_acceptance_contract.py -q → 10 passed in 0.94s)

Final review: fresh-context gpt-6-astra, a2a98a8..0bfe640: 3 Important, 1 Minor, 0 Critical. Important accepted for one root TDD fix pass.
Final: minor (deferred): 分类前极长输入/人为调低预算触发 BudgetExceeded，返回泛化502；零模型、无写入，影响提示清晰度，后续统一限额响应。
Final: Ruling: R8 样例外语义可靠性 — 保留已批准的最简分类/决策并明确28+12样例边界，正式版本留后续；成本是新问法仍可能误分类或控制输出不合格。
Final: Ruling: R9 IAB人工确认真实成功 — 保留R6限制，实际HTML点击测试覆盖人工和两种顺序，真实浏览器证据只覆盖可完成部分；成本是原生确认仍需人在常规浏览器最终核验。
Final: fixed length falsely completed — test_truncated_answer_stream_checkpoint_and_ledger_agree + Node scenario G RED→GREEN, suite 627 passed / 5 deselected.
Final: fixed confirmed ticket parameters lost on reload — Node scenario F RED→GREEN, same confirmed body retrieves original number, one write; suite 627 passed / 5 deselected.
Final: fixed reranker oversize bypassing gate and pool — test_reranker_oversize_goes_through_gate_and_commits_before_token RED→GREEN, suite 627 passed / 5 deselected.
Final evidence: prompts-post-review 28/28 + 12/12, HTTP acceptance-post-review 14/14, service errors 0; Ruff and Node ALL OK. No second reviewer.
Task 9: complete (commits 0bfe640..2cbef74, tests: .venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/ch05 -q → 91 passed in 4.31s)
Finish: 627 passed / 5 deselected (21.52s), Ruff and format clean; linked worktree ch02-tools preserved, baseline main merge-base dd455748, integration pending human choice.
Finish: owned 8005 stopped; original 8000 PID34852 and original eight untracked Ch03 files preserved. Archived ledger/review/evidence before deleting only this plan scratch workspace.
