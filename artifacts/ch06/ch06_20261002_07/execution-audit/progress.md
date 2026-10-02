# SDD ledger — plan: docs/superpowers/plans/2026-10-02-ch06-router.md

Execution: Native, user explicitly approved 2026-10-02; implementation review base 74e325b.
Workspace: existing linked worktree ch02-tools, no superproject, HEAD 53f41b0.

Pre-flight shared interfaces:
- 1 → 3/4/6/8: strict DTOs and StructuredCall cumulative patch; consumers agree on parsed/error/raw_responses/usage/calls.
- 1 → 2/4/6/11: settings with distinct router/policy calibration; absent production calibration must fail explicitly; calibration runner can use raw classification.
- 2 → 3/4/6/8/11: frozen labels and independent calibration split; no changing labels to hide failed runs.
- 3 → 4/7/8: question/scope/trusted_order_id; source validated before choosing order.
- 4 → 6/8: route_intent(intent, scope), general refund/aftersales → knowledge, order_specific → aftersales.
- 5 → 7/8/9/10: single owned catalog and OrderDTO shared with existing read tools; no untrusted client facts.
- 6 → 8/11: multi-query authoritative policy evidence, independent calibration, gate before assessment.
- 7 → 8/10/11: prepare selection before side-effect-free interrupt; JSON/SSE same terminal status; resume retains token ledger.
- 8 → 9/11: ledger stable nullable event key; additive migration, no overwriting old data.
- 9 → 10/11: stable offer_id, DB authoritative receipt, exact confirmation and fixed reason; frontend sends only action IDs.
- 10/11 → 12: browser evidence plus full backend tests; frontend explicitly excluded from final review.
Pre-flight: no conflicting shared interfaces found.

Tasks: 1 pending; 2 pending; 3 pending; 4 pending; 5 pending; 6 pending; 7 pending; 8 pending; 9 pending; 10 pending; 11 pending; 12 pending.
Ruling: 用 scratch Python helper 等价替代缺 awk 的官方 task-start/task-done/review-package — 本机工具缺失且不安装新依赖，保持提取全文、BASE、测试通过才写完成状态；review包保留精确范围并引用巨大报告、不展开前端审查差异 — 若 helper 有误会丢审计证据，逐次核验正文、祖先关系和结果。

Task 1: complete (commits 53f41b0..64c9953, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_contracts.py', 'tests/ch06/test_model_requests.py', 'tests/ch06/test_model_budget.py', '-q'] → 22 passed in 4.03s)

Task 2: complete (commits 64c9953..e4b614e, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_evaluation.py', '-q'] → 9 passed in 3.61s)

Task 3: complete (commits e4b614e..bd3304b, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_understanding.py', '-q'] → 11 passed in 4.43s)
Task 4: Ruling: 同任务接入 workflow 的正式分类调用与 other 出口，并调整 persistence/evaluation 旧夹具 — 新签名移除 model= 和本地问候后旧调用无法运行，保持每阶段回归可验证 — 若兼容判断错误会改变旧章节一般路径，正式固定退款子流程仍在 Task 8 完整接入。

Task 4: complete (commits bd3304b..e2329fb, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_intent.py', 'tests/ch06/test_calibration.py', 'tests/ch06/test_evaluation.py', 'tests/ch05/test_intent_contract.py', '-q'] → 42 passed in 1.00s)
Task 5: Ruling: 1002 商品名统一为冻结演示标签里的无线耳机 — 计划简称降噪耳机与已冻结事实名称不一致，保留已冻结名称并让卡片/工具只有一个来源 — 若用户偏好旧名称会产生展示差异，可改目录名但需重新审核事实型评估。

Task 5: complete (commits e2329fb..30f76b7, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_orders.py', 'tests/test_tool_business.py', '-q'] → 22 passed in 2.36s)

Task 6: Ruling: 政策阈值允许有限的 >1 拒绝全部值 — 既有校准器明确保留 nextafter(max_score, inf) 候选，DTO 的 le=1 会排除合法校准结果 — 若校准有误会更保守拒答，不能绕过校准。
Task 6: Ruling: 独立 policy_evaluation 模块承载真实政策校准 CLI，并修正 expansion 评估适配器为计划签名 — 原 runner 无政策检索运行入口，复用现有 BGE/Milvus/校准器与隔离验收库，不增组件 — 若隔离绑定错误可能污染知识，限定专用集合并保留 audit/hash。

Task 6: complete (commits 30f76b7..fb4af5b, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_expansion.py', 'tests/ch06/test_policy_retrieval.py', 'tests/test_ch04_retrieval.py', '-q'] → 22 passed in 3.33s)

Task 7: complete (commits fb4af5b..27e9ea9, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_selection.py', 'tests/ch06/test_resume_api.py', 'tests/ch06/test_resume_persistence.py', '-q'] → 8 passed in 1.91s)

Task 8: Ruling: 资格评估适配器改为消费计划规定的dict状态补丁 — 旧适配器假定Result对象而本任务接口明确->dict，保留真实raw/usage/calls — 若外部自定义评估依赖旧临时接口需适配，冻结标签未改。

Task 8: complete (commits 27e9ea9..ed3ef4f, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_workflow.py', 'tests/ch06/test_assessment.py', 'tests/ch06/test_ledger.py', 'tests/ch06/test_migration.py', 'tests/ch05', '-q'] → 119 passed in 7.73s)
Task 9: Ruling: 同步旧数据库表集合/DDL漂移测试以验证新增退款表 — 计划文件列表未列test_db_models且旧断言只认三张表，新业务表是已批准需求 — 若遗漏结构核验会漏报DDL漂移，额外48条测试覆盖。

Task 9: complete (commits ed3ef4f..101894d, tests: ['.venv-ch03/Scripts/python.exe', '-X', 'utf8', '-m', 'pytest', 'tests/ch06/test_refunds.py', 'tests/ch06/test_refund_api.py', 'tests/ch06/test_migration.py', '-q'] → 17 passed in 2.67s)
Task 10: Ruling: 真实浏览器前用scratch启动相同main.app的隔离服务并重新校准router — Task10依赖真实服务而长期可复用serve命令计划在Task11，实现临时验收装配而非替换业务组件 — 若绑定错误可能写线上，限定既有独立SQLite/专用Milvus，正式CLI另按TDD完成。

Task 10: complete (commits 101894d..6567d76, tests: ['C:/Users/27497/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe', 'tests/page-smoke.js', 'src/mewhelp/static/index.html'] → ALL OK)
Task 11: Ruling: 新增可重跑demo_publish CLI与旧知识保护测试 — 计划要求真实MySQL演示政策发布但原隔离CLI仅SQLite；单份政策新增MySQL原文，旧SQL逐列不变，专用Milvus集合映射既有原文供FAQ继续检索 — 若装配错误可能污染演示知识，以完整备份、旧行差异即rollback、独立集合和audit约束，不换组件。
Task 11: Ruling: 扩展既有ingest_documents可选include_paths并把stale处理同样限制到该白名单 — 已批准发布范围只有aftersales-policy.md，整目录入口曾额外新增两条配送章节；默认调用保持既有行为，只回退本次有备份的新ID — 若范围过滤有误可能漏掉预期更新或误删其他来源，以审批来源和旧行保护/限定范围回归约束。
Task 11: in progress — current code 776 passed/5 deselected, Ruff/page-smoke green; actual MySQL HTTP run-05 20/20, native commit/checkpoint failure recovery one row, actual browser picker/resume/form/receipt verified. Current source changed after run-05; run-06 402 recurred. Await restored balance for new calibration/full model runs/manual review/current HTTP; do not mark complete or reuse stale hashes. dev-notes interim recorded immediately.
Task 11: Ruling: 用户明确拒绝重复跑，复用已保存32条分类校准响应并离线核对测量输入未变，重算原阈值和当前兼容绑定；沿用已看过的776全量控制日志及MySQL/页面验证，不重复task-done命令或完整模型/HTTP — 分类Prompt/请求/校准代码、模型hash、冻结输入完全未变，后续变化只在理解；保留历史hash及来源审计 — 若理解改变影响实际问题分布，则最新总体模型准确率未再认证，明确列为未重跑项，交独立审查裁定。
Task 11: complete (commits 6567d76..38f04c9; reused latest unchanged-code verification: pytest 776 passed/5 deselected, Ruff/page-smoke green; actual MySQL HTTP run-05 20/20. User explicitly waived repeated model/HTTP/task-done runs; no claim of current full model certification.)

Final review: fresh independent gpt-6-astra high, backend only, 74e325b..38f04c9; read-only, no new tests/model/HTTP calls. Parent verified effects: Critical 0, Important 2, Minor 1; With fixes. Report artifacts/ch06/ch06_20261002_07/review.md. One fix pass for old receipt replay destroying new interrupt and pending-policy publish retry omission; no re-review.
Final: Ruling: 前端审查排除 — 用户明确 Vibe 例外，沿用已保存浏览器/page-smoke证据 — 若 UI 有未覆盖交互缺陷则独立审查不会发现。
Final: Ruling: 生产登录与真实订单隔离不在审查内 — 用户批准 demo identity/固定归属订单，演示内仍检查请求归属 — 不具备生产鉴权保障，不能直接承载真实订单。
Final: Ruling: 真实支付与审批不在审查内 — 本章只持久化 pending 退款申请及幂等回执 — 申请提交不代表审批或到账。
Final: Ruling: 多 worker/多实例不在审查内 — 交付单 worker，与当前会话锁/本地checkpoint约束一致 — 横向扩容会缺跨进程协调，须先另行设计。
Final: Ruling: 最新整体模型与真实联调不重复运行 — 用户明确拒绝重跑，独立review认可分类测量未变而兼容重绑不认证新理解分布，报告保留历史 — 最新端到端泛化与真实联调未获新认证。
Final: minor (deferred): cascade 小模型已成功而升级预检预算不足时漏记小模型 calls/usage；业务正确停止，但该极限预算分支用量审计不完整，默认 primary 不受影响。
Final: fixed 旧回执重放破坏新等待 — test_old_receipt_replay_preserves_new_native_interrupt RED→GREEN，恢复范围9 passed，suite 781 passed/5 deselected。
Final: fixed 发布重试漏 pending 政策 — test_retry_publishes_committed_pending_policy_only[collection/embedding/upsert]、test_demo_publish_rejects_incomplete_policy_sync RED4→GREEN7，suite 781 passed/5 deselected。只补偿规范化根目录内批准来源；其他 pending 行逐列不变。
Task 12: complete (38f04c9..dfed253; one independent review, Important 2 fixed RED→GREEN, full local suite 781 passed/5 deselected, Ruff green; no repeated model/HTTP/browser; user waiver and all five declined-to-judge rulings above). Fixes committed. Demo restarted on owned port 9007 with unchanged calibrated classifier inputs; single health check ready, no model requests.
Finish: keep existing ch02-tools branch/worktree as user plan requires, no merge/push; archive this plan's 186 scratch files (plus final archive helper) into artifacts/ch06/ch06_20261002_07/execution-audit before removing only this exact ignored plan directory. No user worktree/service/checkpoint cleanup. Tests are not repeated for documentation/archive-only finishing. All Ruling/minor decisions preserved for exhaustive final delivery.
Final: Ruling: 本计划ignored scratch保留 — 187文件已SHA256核验归档并提交后，自动安全审核拒绝经路径/归档核验的递归删除，工具仅给blocked by policy；不绕过拒绝，功能交付与审计不受影响 — 留下约1MB临时材料，占用本地空间，后续可由用户自行清理。
Finish cleanup status: retained, not deleted; automatic approval/safety review rejection. Archived audit committed 6fb55f9, fix commit dfed253. No test/model/HTTP/browser reruns for this documentation-only correction.
