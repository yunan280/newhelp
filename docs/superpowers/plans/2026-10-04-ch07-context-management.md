# Ch07 Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前客服会话内保留完整历史，以原文、规则投影和后台分段摘要管理模型上下文，并支持多会话原文回载。

**Architecture:** 接入已有 Ch05/Ch06 的同一 StateGraph；messages/add_messages/checkpoint 保存完整消息，独立上下文模块生成模型输入副本。MySQL 保存可见原文、两个边界和只追加摘要段，lifespan 管理不等待前台的摘要任务。指代、分类、Agent 使用一致的历史快照，实际请求记录到 log/app.log。

**Tech Stack:** LangGraph 1.2.12、checkpoint-sqlite 3.1.1、langchain-core 1.6.5、langchain-openai 1.6.6、FastAPI 0.141.1、SQLAlchemy 2.1.1 + PyMySQL、既有原生 HTML/CSS/JS。

**Spec:** `docs/superpowers/specs/2026-10-04-ch07-context-management-design.md`，用户以「已确认」批准，设计提交 `1cad129`。用户已审阅本计划并选择「Native」，按 executing-plans 顺序实施、末尾一次独立整体审查。

## Global Constraints

- 工作目录 `C:/Users/27497/projects/mewhelp-wt/ch02-tools`；既有 linked worktree/分支 ch02-tools，实施时使用 using-git-worktrees 的环境检测并复用，不能默认重建或切换分支。
- 只限当前 conversation_id；不实现跨会话长期记忆、用户画像、历史语义检索或重要度筛选。
- 用户 DDL 两步均 apply；S/L 的定义为 `id<=S` 已摘要、`S<id<=L` Layer 2、`id>L` Layer 1。NULL 读取为逻辑 0；数据库字段仍可 NULL。
- 摘要段只追加、不改写；summary 列只拼接最近连续段；原文不搬、不改、不删除。默认 Layer 2 客服前缀 60 字，用户原话不截断。
- 工具原文不新写 messages 表；真实工具消息进入完整 State/checkpoint 并参与原文层计量；模型输入副本不回写完整消息。
- 演示变量严格为 `MODEL_CONTEXT_WINDOW=18000 MAX_OUTPUT_TOKENS=2000 MAX_USER_INPUT_TOKENS=2000 MAX_AGENT_STEPS=3 TOOL_RESULT_MAX_TOKENS=1200 RERANK_TOP_K=5`，初始工程包 H/L1/L2=5650/3954/1695；正式验收已由用户批准为5300/3709/1590（spec §15）。
- 起始包：prefix 1350、证据每条 400、摘要 500、安全 500、控制结构 400；desired_turns 40，稳态 1064；CJK 1 token/字、ASCII 4 字符/token。真实校准与预算版本绑定，不能凑数伪造实测。
- 默认软件窗口 128000；输出/用户/工具步数/单工具结果/TopK=4096/4096/4/1200/10；只有供应商实际能力支持该上限才启用。共享 .env 和 LLM_MODEL 不自动覆盖。
- MAX_AGENT_STEPS 为整轮工具执行次数，批次内每个调用均占名额；收尾控制/回答另预留，原 max_tools=8 不能逃过额度。
- 只有稳定规则位于 system；摘要/证据/订单/决策等可变数据在当前用户句之后。保持实际 Function Calling，不以文本 schema 替代工具调用。
- 同步 SQLAlchemy 每个线程/后台任务独立 Session；摘要模型等待期间不持有会话锁或 MySQL 行锁。单 worker，沿用官方 AsyncSqliteSaver。
- 每阶段即时追记 dev-notes/ch07.md 的四项；每项任务提交后记录 hash 与证据路径。Prompt/数据以先冻结标注集、真实评估替换 TDD，控制代码先 RED/GREEN。
- 具体库/API 先 Context7 再核实本机签名；已经查证的接口可复用，新增接口必须补查。选型矛盾携证据问用户，不能自行替换。
- 新增运行产物写 artifacts/ch07/<run>；原始上下文、数据库备份和凭据保持本地/ignored，不提交用户原文日志或 secrets。禁止删改旧失败报告与冻结标签。

## Review Focus

2026-10-04 执行中已获用户批准的预算修订：演示预算改为 5300/3709/1590，联合 profile v2 见 spec §15；下文初始 5650 仅为当时工程包，正式 Task 8 按修订验收。

1. 全库消息 ID 有空洞、跨会话穿插，或旧 checkpoint 缺 ledger 元数据：边界必须只覆盖该会话完整已提交轮，不能把全库连续 ID 当会话顺序。Task 2/3/5。
2. 摘要生成期间又降级一批、摘要插入失败或重复触发：只提交输入快照范围，原文不漏、S 不先行；后台不拖住用户 done。Task 4/5。
3. 原生订单 interrupt 跨进程恢复、恢复请求重放、流中断：新摘要可见但 next/interrupt/receipt 不被上下文刷新清掉；失败消息不能变成已提交历史。Task 5。
4. 超大中文输入、并行多工具、异常工具长结果和控制纠正：预算同时覆盖实际请求与剩余峰值；Layer 1 仍原文，不能截结果后声称未压缩。Task 1/6。
5. 切换请求迟到、聊天仍在流式、侧栏断网和两用户同序号：迟到响应不覆盖当前会话，读取失败不破坏继续聊天，原文与摘要不串用户。Task 7。

## 文件职责与公共契约

所有下述路径相对实际仓库根；交付链接用绝对路径。

| 文件 | 职责 |
| --- | --- |
| ch07/config.py、tokens.py、budget.py | 配置、CJK/结构估算、预算包与调用预检 |
| ch07/types.py | 仓储/投影/摘要任务之间的不可变 DTO；checkpoint 用 JSON 兼容 payload |
| ch07/store.py、migration.py | MySQL 会话/段/消息 ID 查询、事务和两步迁移 |
| ch07/projection.py | 完整轮分层、60 字/工具标识投影、稳定模型输入 |
| ch07/summary_model.py、prompts.py、summary.py | 事实摘要模型与校验、单会话后台去重/生命周期 |
| ch07/context.py、provenance.py | 请求快照、State 桥接、摘要/原文指代来源验证 |
| ch07/observability.py | UTF-8 文件日志、实际 messages/tools 序列化和计量 |
| ch07/api.py、schemas.py | 两个 GET 的归属校验及原文 DTO |
| ch07/evaluation.py、eval/ch07/* | 冻结样例、Prompt 评估、token 联合校准与审计报告 |
| scripts/migrate_ch07_schema.py、smoke_ch07_acceptance.py | 迁移命令、真实 HTTP/SSE 22 轮验收 |

完整前缀为 `src/mewhelp/`。新模块不接管既有业务判断或创建另一套聊天 service。

`types.py` 统一定义并在相邻任务导入：

- `LedgerMessage(id:int, role:str, content:str, citations:tuple[dict,...], event_key:str|None)`。
- `SummarySegment(id:int, seq:int, from_msg_id:int, upto_msg_id:int, content:str)`。
- `ConversationSnapshot(conversation_id:int, session_id:str, user_id:str, summary_upto_msg_id:int, layer1_from_msg_id:int, messages:tuple[LedgerMessage,...], summaries:tuple[SummarySegment,...])`。
- `HistoryTurn(turn_id:str, from_msg_id:int, upto_msg_id:int, messages:tuple[AnyMessage,...])`，工具归属使用所在轮的 from/upto。
- `HistoryContext(conversation_id:int, summary_upto_msg_id:int, layer1_from_msg_id:int, summary:str, summary_segments:tuple[SummarySegment,...], layer2:tuple[AnyMessage,...], layer1:tuple[AnyMessage,...], turns:tuple[HistoryTurn,...], tokens:dict[str,int], budget:ContextBudget)`。
- `SummaryJob(conversation_id:int, old_upto_msg_id:int, layer1_snapshot_id:int, turns:tuple[HistoryTurn,...])`。
- `SummaryResult(content:str, usage:dict[str,int], elapsed_ms:int)`。

DTO 不直接成为 checkpoint 的自定义序列化对象；`history_payload(ctx)->dict` 和 `history_from_payload(payload)->HistoryContext` 定义在 context.py，payload 包含版本、原始消息字典、预算字段和出处。message.additional_kwargs 的 `ch07` 保存 `{turn_id, ledger_id, from_msg_id, upto_msg_id, committed}`，只更新元数据，原 content/工具正文不可改变。

执行命令统一 PowerShell：`$pyCh07 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path`。每次测试把完整输出保存到本 run 的 process-evidence；只有相关改动/失败才重跑已经有效的真实评估。

### Task 1：统一 token 口径、配置和历史预算

**Files:** Create `src/mewhelp/ch07/{__init__,config,tokens,budget}.py`；Test `tests/ch07/test_budget.py`、`test_tokens.py`。本任务不修改旧章节固定 2048 或累计预算语义。

**Interfaces:** `ContextSettings` 属性 `model_context_window/max_output_tokens/max_user_input_tokens/max_agent_steps/tool_result_max_tokens/rerank_top_k/context_calibration_path` 读取上述未加前缀的 6 个变量及 `CONTEXT_CALIBRATION_PATH:Path|None`。`BudgetProfile` 属性 `prefix_reserve/evidence_per_chunk/summary_reserve/safety_reserve/control_reserve/desired_turns/steady_user_tokens/steady_answer_tokens/steady_tool_tokens/steady_structure_tokens/cjk_tokens_per_char/ascii_chars_per_token/version`，初始值分别为 1350/400/500/500/400/40/256/512/200/96/1/4/ch07-v1；其 fingerprint 为这些实际参数的 hash。`ContextBudget` 字段 `fixed/peak/available/history/layer1/layer2/breakdown/profile_hash`，前 6 个为 int、breakdown 为分项 dict。`estimate_messages(messages:Sequence[AnyMessage], *, profile:BudgetProfile)->int`，`estimate_request(messages, tools:Sequence[dict], *, profile)->int`；`compute_budget(settings:ContextSettings, profile:BudgetProfile, *, actual_fixed:dict[str,int]|None=None)->ContextBudget`，actual_fixed 使用 prefix/evidence/summary 三个键，各项扣 `max(实际,工程预留)`，不能因实际暂小就吃掉为后续预留的空间；`check_window(messages, tools, *, settings, profile, output_tokens:int, remaining_tool_calls:int)->None`，不足抛 `ContextBudgetError`。

- [x] **Step 1:** 写 `test_demo_budget_is_derived`、`test_default_budget_preserves_target_pool`，断言：
  ```python
  assert (demo.fixed, demo.peak, demo.history, demo.layer1, demo.layer2) == (6350, 6000, 5650, 3954, 1695)
  assert (default.available, default.history, default.layer1, default.layer2) == (108258, 42560, 29791, 12768)
  assert compute_budget(window_plus_1000, profile).history == 6650
  ```
  另写 `test_chinese_and_serialized_tools_share_counter`：中文、JSON 参数、tool_calls 都增加计数，不能按 UTF-8 byte=真实 token 或默认 chars/4 漏算 CJK。
- [x] **Step 2:** Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_budget.py tests/ch07/test_tokens.py -q`；新行为 RED，不接受纯夹具错误作为证明。
- [x] **Step 3:** 实现上述签名与整数预算公式。启动可计算工程包，但标明 calibration_status；profile 的 token 参数改动必须让预算包 fingerprint 改变。显式区分 W 与累计 total_model_tokens。预算输入计数先转换公开 OpenAI messages/tools，完整 State 的 ch07/ledger 元数据不作为模型收到的文本计入。
- [x] **Step 4:** 补 `test_one_steady_turn_cannot_fit_raises`、`test_actual_evidence_over_reserve_reduces_history`、`test_parallel_batch_counts_each_tool`、`test_profile_counter_drift_is_rejected`；检查剩余工具结果和输出已预留，单次请求不足抛包含「上下文预算不足」的错误。Run 同一组 GREEN。
- [x] **Step 5:** 追记 Task 1 四项与 RED/GREEN 路径，提交 `feat(ch07): derive context budgets with one token profile`。

### Task 2：权威 DDL、原文 ID 与摘要事务仓储

**Files:** Create `sql/ch07-ddl.sql`、`sql/ch07-layers.sql`、`src/mewhelp/ch07/{types,store,migration}.py`、`scripts/migrate_ch07_schema.py`；Modify `src/mewhelp/db/models.py`、`tests/test_db_ddl_drift.py`；Test `tests/ch07/test_store.py`、`test_migration.py`。

**Interfaces:** ORM `ConversationSummary` 完全对应用户 DDL；`read_conversation(session:Session, *, conversation_id:int, user_id:str)->ConversationSnapshot`；`advance_layer1(session, *, conversation_id, expected_layer1:int, new_layer1:int)->bool`；`append_summary(session, *, job:SummaryJob, from_msg_id:int, upto_msg_id:int, content:str, profile:BudgetProfile)->SummarySegment|None`；`find_ledger_ids(session, *, conversation_id:int, event_keys:Sequence[str])->dict[str,int]`；`migrate_ch07(engine:Engine)->dict`。

- [x] **Step 1:** 写 `test_user_ddl_and_orm_match`：三列 NULL/Text/BIGINT UNSIGNED、两索引精确名称/列序、无额外 FK、两文件 SET NAMES。写 `test_summary_commit_is_atomic`、`test_noncontiguous_ids_stay_conversation_local`：输入本会话 10/14/23、其他会话 11/22，覆盖不能包含其他会话；发生 rollback 后段数/S/summary 均不变。
- [x] **Step 2:** Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_store.py tests/ch07/test_migration.py tests/test_db_ddl_drift.py -q`；确认新契约 RED。
- [x] **Step 3:** 原样保存用户两份 SQL（含 COMMENT/列位置/索引）。迁移先 inspect，再只补缺项，MySQL 的 DDL 不当作可自动整批 rollback；SQLite 测试使用同构 ORM/兼容 ALTER。仓储 flush，调用者拥有 commit；摘要插入/S/最近连续段投影在同一短事务，行锁只用于落盘阶段。
- [x] **Step 4:** 补 `test_half_applied_migration_is_restartable`、`test_wrong_existing_type_stops`、`test_duplicate_seq_never_rewrites_segment`、`test_stale_summary_snapshot_skips`、`test_projection_selects_whole_latest_segments`。`append_summary` 只校验并覆盖 job 的实际子批范围；L 即使已变大也不推进 S 到新 L。Run Step 2 GREEN；MySQL 实迁移及备份留 Task 8，不在测试阶段直接改用户库。
- [x] **Step 5:** 追记 Task 2 与 schema drift/事务证据，提交 `feat(ch07): persist layer anchors and append-only summaries`。

### Task 3：整轮分层投影和固定顺序模型输入

**Files:** Create `src/mewhelp/ch07/projection.py`；Test `tests/ch07/test_projection.py`、`test_prompt_order.py`。

**Interfaces:** `group_committed_turns(full:Sequence[AnyMessage], snapshot:ConversationSnapshot, *, current_turn_id:str)->tuple[HistoryTurn,...]`；`project_history(turns, snapshot, budget:ContextBudget, *, profile:BudgetProfile)->HistoryContext`；`model_messages(history:HistoryContext, *, system:str, question:str, background:dict, current_react:Sequence[AnyMessage]=())->list[AnyMessage]`。history.tokens 包含 `layer1_raw/layer2_projected/summary/total`；新的 L 通过 HistoryContext 返回，持久更新归 Task 5。

- [x] **Step 1:** 写 `test_recent_messages_remain_byte_for_byte`、`test_layer2_preserves_user_and_shortens_assistant`、`test_tools_count_without_ledger_rows`：
  ```python
  assert projected_user.content == original_user.content
  assert projected_answer.content == original_answer.content[:60] + expected_marker
  assert len(projected_tool.content.splitlines()) == 1
  assert full_after == full_before
  ```
  `expected_marker` 固定为 `…[已截短]`。工具标识固定为 `[工具结果 name=... call_id=... object=... status=...]`，字段来自实际调用/状态，object 不存在则省略。
- [x] **Step 2:** Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_projection.py tests/ch07/test_prompt_order.py -q`；确认 RED。
- [x] **Step 3:** 使用已查证 trim_messages 的 last/human/allow_partial=False 与 Task 1 计数器，再向完整轮/完整工具组边界收拢。模型顺序仅一个 system、L2、L1、当前原话、一个背景 HumanMessage、本轮完整 ReAct 对；投影绝不更新 full。
- [x] **Step 4:** 补 `test_current_user_occurs_once`、`test_no_orphan_tool_message_after_trim`、`test_uncommitted_failed_turn_excluded`、`test_snapshot_boundaries_are_inclusive`、`test_identical_static_prefix_across_different_backgrounds`、`test_large_single_turn_degrades_whole`。历史 controller JSON 不当作可见对话。Run Step 2 GREEN。
- [x] **Step 5:** 追记 Task 3 与原文对比/前缀证据，提交 `feat(ch07): project history without mutating full messages`。

### Task 4：事实摘要评估与后台任务生命周期

**Files:** Create `src/mewhelp/ch07/{prompts,summary_model,summary,evaluation}.py`、`eval/ch07/README.md`、`eval/ch07/{summary-calibration,summary-acceptance,references-calibration,references-acceptance,tokens-calibration}.jsonl`、`eval/ch07/freeze.json`；Test `tests/ch07/test_summary_jobs.py`、`test_evaluation_contract.py`。

实际文件命名：README.md；summary-calibration.jsonl 12 条、summary-acceptance.jsonl 12 条、references-calibration.jsonl 8 条、references-acceptance.jsonl 8 条、tokens-calibration.jsonl 16 条。freeze.json 存每文件 SHA256/数量/split，后续不因失败改 acceptance 标签。

**Interfaces:** `SummaryModel.summarize(*, batch:Sequence[AnyMessage], background:str)->SummaryResult`（async Protocol）；`SummaryTaskManager(session_factory, model:SummaryModel, profile:BudgetProfile, *, concurrency:int=2)`，`schedule(job:SummaryJob)->bool`、`aclose(*, timeout_seconds:float=5)->None`。`summary_messages(batch, *, background)->list[AnyMessage]`；`validate_summary(result:SummaryResult, batch)->None`；`freeze_dataset(path:Path)->dict`、`evaluate_part(dataset, outdir, *, part:str, phase:str)->int`。

- [x] **Step 1（Prompt 评估替代 RED）:** 编写并冻结上述标注集。摘要覆盖订单/手机号、商品、未解决诉求、否定、已解决事实、纯闲聊、背景旧摘要污染、多个订单和长工具；输出 30–200 字业务摘要，纯闲聊固定为「本段无需要保留的业务事实。」。先跑初始候选的 calibration 集，保留逐条真实输出/usage 与不通过项；不以断言 Prompt 字符串代替实际评估。
- [x] **Step 2:** 建立 CLI `& $pyCh07 -X utf8 -m mewhelp.ch07.evaluation freeze --dataset eval/ch07` 和 `... evaluate --dataset eval/ch07 --part summary --phase calibration --outdir artifacts/ch07/<run>/summary-calibration-01`。summary 成功标准：所有订单/手机号精确保留、零新增数字事实/批准结果、旧背景不当新批事实、全部明确未解决诉求保留、业务输出长度达标；语义判定逐项标签可审计。只对失败原因修正候选，再存为生产 Prompt。
- [x] **Step 3（控制代码 RED）:** 写 `test_inflight_summary_does_not_block_foreground`、`test_advance_during_model_call_commits_only_snapshot`、`test_failure_leaves_anchors_and_releases_inflight`；Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_summary_jobs.py tests/ch07/test_evaluation_contract.py -q`。
- [x] **Step 4:** 实现后台 manager：捕获 trigger 的区间/新批原文，async 独立模型，不持前台锁；大批按摘要窗口切完整轮，每段只压一次，旧段仅背景。每子批构造 SummaryJob：old_upto 使用前一已提交段的 upto（第一段用触发 S）、layer1_snapshot_id 保持原目标 L、turns 只含本子批；短事务调用 Task 2，失败则停止后续子批，不能继续跨过失败区间。异常 rollback/日志，取消释放标记。同会话任务去重，不立刻无限重试。模型工厂沿用已配置上游、无 tools、温度 0、摘要输出预留 256 token；长度/数字校验失败不推进覆盖。
- [x] **Step 5:** 补去重、竞争 seq、两会话并发、关闭取消、纯闲聊空事实、旧摘要不回炉的控制测试，Run Step 3 GREEN。摘要 acceptance 留 Task 8 对冻结生产 hash 跑一次；本阶段只报告 calibration。追记真实评估与 RED/GREEN，提交 `feat(ch07): summarize new history batches asynchronously`。

### Task 5：State、会话入口和指代/分类贯通

**Files:** Create `src/mewhelp/ch07/{context,provenance}.py`；Modify `src/mewhelp/ch05/{state,service,runtime,workflow,agent,intent}.py`、`src/mewhelp/ch06/{understanding,selection,evaluation,prompts}.py`、`tests/ch05/{test_persistence,conftest}.py`、`tests/ch06/{conftest,test_calibration}.py`；Test `tests/ch07/test_state_context.py`、`test_reference_context.py`、`test_resume_context.py`。

**Interfaces:** `prepare_history(context:WorkflowContext, state:dict)->HistoryContext`（async，读仓储/分层/调度）；`prepare_request_context(context:WorkflowContext, state:dict)->WorkflowContext`（async，返回 dataclasses.replace 副本，含 `request_epoch:str`、`request_history:dict`）；`history_payload`/`history_from_payload` 对应上文。State 新增 `history_ctx:dict`、`history_epoch:str`；WorkflowContext 新增 settings/profile/summary_manager 与可选请求字段。`reference_sources(history:HistoryContext, *, user_id:str, session_factory)->list[dict]`、`verify_reference(*, order_id:str, source_id:str, sources:Sequence[dict])->bool`。

- [x] **Step 1:** 写真实 AsyncSqliteSaver 测试 `test_nodes_add_only_new_raw_messages`、`test_reopen_preserves_full_tools_but_model_uses_projection`、`test_bootstrap_stable_ledger_ids_once`、`test_failed_turn_retained_for_diagnostics_not_replayed`。断言当前用户一次、完整工具正文存在、原文行不重复、未提交 UUID 不被拿作 BIGINT 边界。Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_state_context.py tests/ch07/test_resume_context.py -q` 确认 RED。
- [x] **Step 2:** begin_turn/工具/等待/最终节点分别增量吐消息；完成/等待事务后根据既有 ch06_event_key 查 ledger ID，按原 reducer ID 只补元数据。账本只写本轮 user/可见 assistant，不遍历 agent_messages 重写旧工具。升级旧 checkpoint 时用已提交可见消息顺序与内容核对绑定 ID；映射不唯一则从 MySQL 可见原文建立投影并记录兼容降级，不臆造归属或删工具原文。
- [x] **Step 3:** 普通聊天使用 `{**previous.values, **incoming}`（新 turn_id/question 已覆盖）准备本请求 context；订单恢复使用该中断的 snapshot.values 准备，二者都把 context 副本传入既有 `graph.astream(context=...)`。节点 wrap 在 request_epoch 首次不匹配时，把 prepared_history 合入传给操作的 State 副本并随节点结果增量写回。保留 `Command(resume=...)`、收到后的 `None` 继续和 receipt 重放，不用输入 Command(update=...)，不对活中断 aupdate_state 强改上下文。不同会话并发不修改共享 context 对象。
- [x] **Step 4（Prompt 评估）:** 先跑 references calibration 的旧入口基线，保存最早订单/摘要来源/多订单的失败；修改前置历史输入和 Prompt后重跑有变动的 calibration 样例。两入口共用相同 history_ctx，删 history[-20]/content[:2000] 二次裁剪。source_id 从 MySQL/段区间生成，订单必须有可核对来源与用户归属；可支持唯一明确的「最早」来源，不能放开任意历史订单。语义评价按冻结标签，负例零虚构 ID，正例正确对象/诉求与否定保留。
- [x] **Step 5:** 写 `test_resume_refresh_preserves_interrupt_and_next`、`test_receipt_replay_cannot_overwrite_live_interrupt`、`test_summary_first_order_resolves_among_multiple_orders`、`test_background_summary_cannot_authorize_another_user_order`、`test_classifier_and_understanding_share_snapshot`、`test_mysql_bootstrap_without_checkpoint_does_not_claim_tool_recovery`。更新旧 persistence 测试到「已提交投影不含失败轮」的新契约，不能 RemoveMessage 裁完整历史。Run 上述新三组及 `tests/ch05/test_persistence.py tests/ch06/test_resume_persistence.py tests/ch06/test_ledger.py` GREEN。
- [x] **Step 6:** 新增上下文文件和 token/profile hash 纳入当前 Ch06 指纹；保持冻结 dataset 校验，后续 Task 8 真校准前不使用旧 hash 伪装通过。追记 Task 5 的恢复/指代证据，提交 `feat(ch07): carry full history and context through workflow state`。

### Task 6：实际模型调用、峰值约束和 UTF-8 原样日志

**Files:** Create `src/mewhelp/ch07/observability.py`、`docs/ch07-logging.json`；Modify `src/mewhelp/ch05/{agent,prompts,runtime,evidence}.py`、`src/mewhelp/ch06/{assessment,structured}.py`、`src/mewhelp/main.py`、`.env.example`；Test `tests/ch07/test_model_context.py`、`test_context_logging.py`、`test_runtime_budget.py`。

**Interfaces:** `configure_context_logging(path:Path)->None`；`log_history(ctx:HistoryContext, *, state:dict, request_epoch:str)->None`；`log_model(messages, tools, *, state:dict, purpose:str, model_name:str, profile:BudgetProfile)->dict` 返回公开 OpenAI messages/tools/context 计数，与实际模型接收序列对应；`log_summary_event(phase:str, *, job:SummaryJob, **fields)->None`。使用已查证 `convert_to_openai_messages(include_id=False)` 和 convert_to_openai_tool；诊断消息 ID另放 metadata，不能假称为 HTTP messages 的字段。

- [x] **Step 1:** 捕获 ChatOpenAI 的最终请求 JSON（本地测试 transport、只记录 messages/tools，不记录 header）写 `test_model_ctx_matches_outgoing_payload`、`test_no_variable_system_in_decide_repair_answer_or_assessment`、`test_history_ctx_exists_on_chitchat_turn`。Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_model_context.py tests/ch07/test_context_logging.py tests/ch07/test_runtime_budget.py -q` RED；新增 transport API 使用前补查 Context7 对应文档。
- [x] **Step 2:** 主力请求调用 Task 3 构造器；固定规则合入稳定 system，决策/纠正/订单/证据放背景或控制数据。不同用途允许各自固定工具绑定策略，同用途跨轮稳定；最终正文/JSON纠正不执行新工具。实例化前绑定实际 tools 并用 Task 1 预检 input/output/remaining peak。
- [x] **Step 3:** 新 6 个变量接到实际限制：max_tools=settings.max_agent_steps、max_decisions=max_agent_steps+2（最多一轮纠正与最终控制均计数）、final_max_tokens=settings.max_output_tokens；保留超时与累计成本保护；默认费用上限按用户批准的spec §17推导，显式成本上限优先。整个并行批次先查剩余额度再执行；超限不执行部分批次。结果超过 TOOL_RESULT_MAX_TOKENS 保留 raw State/trace 并有界回复，不送截短结果冒充原文。RERANK_TOP_K 在证据构造时限制完整条目数量，actual evidence 实计；超长完整条款压缩历史，仍装不下则明确拒绝，不能截法规事实。
- [x] **Step 4:** lifespan 配置 log/app.log、做启动一轮自检、创建/关闭 summary manager；启动失败未安装半个 runtime。日志含全部摘要/消息/证据、S/L、条数、分项 token、触发范围、seq 和耗时。handler 幂等、UTF-8、覆盖 ch05/ch06/ch07，不靠 stderr 配置才有文件日志。
- [x] **Step 5:** 补 `test_parallel_calls_cannot_bypass_three_tool_budget`、`test_tool_overflow_keeps_raw_but_prevents_next_model`、`test_oversized_chinese_input_is_explicit`、`test_repair_payload_also_preflighted`、`test_default_app_creates_log_without_extra_cli_flags`、`test_summary_lifecycle_logs_boundaries_and_elapsed`。Run Step 1 GREEN 与相关 `tests/ch05/test_agent.py tests/ch06/test_model_budget.py tests/ch06/test_model_requests.py`。追记 Task 6，提交 `feat(ch07): enforce model windows and log exact contexts`。

### Task 7：只读多会话 API 与前端切换

**Files:** Create `src/mewhelp/ch07/{api,schemas}.py`、`tests/ch07/test_conversation_api.py`、`tests/ch07/page-conversations.js`；Modify `src/mewhelp/ch07/store.py`、`src/mewhelp/main.py`、`src/mewhelp/static/index.html`、`tests/page-smoke.js`。

**Interfaces:** 仓储 `list_user_conversations(session, *, user_id:str)->list[ConversationItem]`、`read_visible_messages(session, *, conversation_id:int, user_id:str)->ConversationMessages`。GET `/api/conversations?user_id=...` 返回 `{conversations:[{id,session_id,created_at,updated_at,first_question,has_summary,summary_count}]}`；GET `/api/conversations/{id}/messages?user_id=...` 返回 `{id,session_id,messages:[{id,role,content,citations}]}`。不创建会话、不触发摘要；404 处理同 spec。

- [x] **Step 1:** 写 `test_list_newest_first_first_question_preview`、`test_messages_remain_original_after_summary`、`test_cross_user_returns_404`、`test_get_has_no_db_or_summary_side_effects`。Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_conversation_api.py -q` RED；预览前 40 字，仅列表可截短，回载全文不截。
- [x] **Step 2:** 实现 DTO、两个 sync GET 与仓储查询，user_id 缺失沿用 demo-user 占位，拒绝空白；列表排序 created_at DESC/id DESC，has_summary 来自真实段表，不因仅有 L 降级就显示已摘要。Run GREEN。
- [x] **Step 3:** 使用 frontend-design 技能复用现有页面风格，加入会话栏/新对话/移动端收起。先在 page-conversations.js 写拒绝迟到 A 覆盖当前 B、忙时禁切、列表异常仍可 send、原文全文渲染、新对话不删除 A 的行为断言，并跑 RED。
- [x] **Step 4:** 实现 `loadConversationList()`、`switchConversation(conversationId)`、`startNewConversation()`、`renderConversationList(items)`；会话切换成功后统一设置 sessionId/history 并调用 restorePending；先拿到全文再替换当前 UI。请求序号/AbortController 防迟到覆盖，发送 busy 同时禁侧栏按钮。存储键按 user/session 隔离，旧用户级缓存只做兼容降级。
- [x] **Step 5:** Run `node tests/ch07/page-conversations.js src/mewhelp/static/index.html` 与 `node tests/page-smoke.js src/mewhelp/static/index.html` GREEN；使用 computer-use/CUA 真浏览器核对新建两会话、切回全文、继续、移动端、摘要标记和断网降级。浏览器可先用本地可控 API 场景，正式 MySQL/SSE 在 Task 8。追记 Task 7，提交 `feat(ch07): browse and resume separate conversations`。

### Task 8：联合标定、当前校准指纹及真实 22 轮验收

**Files:** Modify `src/mewhelp/ch07/evaluation.py`、`eval/ch07/README.md`；Create `scripts/smoke_ch07_acceptance.py`、`tests/ch07/test_acceptance_contract.py`；报告到 `artifacts/ch07/<run>/`，代码改动改变 hash 时只重跑受影响验证。

**Interfaces:** `calibrate_tokens(dataset:Path, outdir:Path)->int` 生成带 estimator/profile/模型/样例 hash 的 `context-profile.json`；CLI `calibrate-tokens --dataset eval/ch07 --outdir ...`。验收 CLI `--base-url URL --profile default|demo --report-dir DIR --user-id ID --session-prefix PREFIX --turns 22`；至少报告 `turns_completed/window_violations/degrades/summary_triggers/summary_segments/first_order_reference_ok/nonblocking_timestamps`。

- [x] **Step 1:** 给验收脚本写 `test_report_cannot_pass_with_only_health_or_mock`、`test_default_report_rejects_any_compression`、`test_demo_requires_cascade_and_first_order`；Run `& $pyCh07 -X utf8 -m pytest tests/ch07/test_acceptance_contract.py -q` RED。成功必须有完整 HTTP/SSE 22 轮、实际 request usage、数据库段/边界和原样日志证据，缺一 complete=false。
- [x] **Step 2:** 核对运行环境与当前供应商能力、模型是否接受原配置；用已有方式备份 conversations/messages/相关业务表到 ignored 的本地备份，记录数量/hash。执行 `& $pyCh07 -X utf8 scripts/migrate_ch07_schema.py`，检查两步列、段表与 utf8mb4 COMMENT，再执行一次证明重跑无新增破坏。保持其他服务进程，不覆盖原 .env。
- [x] **Step 3:** 跑 tokens calibration：16 个冻结中文/混排/JSON/峰值样例，采真实 input usage；以最大低估比及结构差额确定保守边界，CJK 参数与 prefix/证据/summary/稳态/peak 同一 profile 版本验算。起始安全包可保持原参数但须有校验证据；不能单调系数。若需要提高预留导致演示不再是 5650/3954/1695，携报告停下问用户，不篡改真实 usage。
- [x] **Step 4:** 代码已冻结后，用当前上下文版本重新校准 Ch06 必要路由：`& $pyCh07 -X utf8 -m mewhelp.ch06.evaluation calibrate --dataset eval/ch06 --outdir artifacts/ch07/<run>/router-calibration`。接入共用历史时 calibration 样例必须走同一 classifier 构造/计量；生成新的 router.json，保留原 freeze.json。只有模型/语料/检索输入 hash 未变时复用政策校准；确实变动则对应重新校准，不绕校验。正式启动显式设置新路径。
- [x] **Step 5:** 跑 summary/references 的冻结 acceptance 各一次，报告按 Task 4/5 的准确率和零编造准则判定；不按失败重标标签。只有更改 Prompt/输入构造/模型等实际原因时重跑受影响部分并保留旧输出。
- [x] **Step 6:** 实现脚本并 Run Step 1 GREEN。准备默认/演示各一服务实例、独立 checkpoint 路径、相同已迁移 MySQL，单 worker；用 run 标记隔离会话，不改既有服务的配置。PowerShell demo 仅覆盖用户指定 6 个变量，另给校准路径；启动和 health 只作前置，不算验收。
- [x] **Step 7:** 分别运行 `& $pyCh07 -X utf8 scripts/smoke_ch07_acceptance.py --base-url http://127.0.0.1:<port> --profile <default|demo> --report-dir artifacts/ch07/<run>/<profile> --user-id <run-user> --session-prefix <profile-prefix> --turns 22`。用同一冻结对话脚本：首轮明确查订单 1001 与原诉求；中间含真实业务/工具和足够用户原话，末轮第 22 轮问「最开始那个订单后来怎么说」。真实填入的原话有业务含义，不用只堆随机字压预算。
- [x] **Step 8:** 默认 degrades=0、triggers=0、所有请求不过窗；demo 有全部降级/trigger/start/done链路，段表不可变且 S/L 单调，最早订单正确且其段当时实际被注入。保存 SSE 首 token/done 和摘要 start/end 的绝对/单调时间；另用 Task 4 可控慢模型证明不用等待摘要，不能只用偶然很快的摘要下结论。真浏览器打开同一用户默认/demo 会话，全文回载、继续聊、刷新 pending。
- [x] **Step 9:** 追记 Task 8 的实际结果、样例/模型/代码 hash、失败与必要返工；提交脚本/评估契约及脱敏统计 `test(ch07): verify context budgets and asynchronous memory end to end`。未满足实际验收的项明确保留未通过状态，不宣称 finish。

### Task 9：独立审查、必要修复和完结交付

**Files:** Modify `README.md`、`dev-notes/ch07.md`；审查/验证报告到 `artifacts/ch07/<run>/review/`、`verification/`。修复只碰已审查触发路径，不把审查变成无关重构。

**Interfaces:** 最终交付必须包含默认启动、6 变量演示启动、两步迁移命令、实际测试统计、日志查看、dev-notes 路径。报告对原请求的 7 项功能、5 项验收逐一给证据。

- [x] **Step 1:** Run 一次最终确定性套件 `& $pyCh07 -X utf8 -m pytest -q`、`& $pyCh07 -X utf8 -m ruff check src tests scripts`、两个 JS 行为脚本。汇总实际 passed/deselected/failures，不能从旧 Ch06 数字推断通过；已有有效真实 acceptance 不无原因重跑。
- [x] **Step 2:** 使用 requesting-code-review。Native 执行时派一位独立 reviewer 看实施起点到当前 HEAD 的完整 diff，对照 spec/plan/Review Focus（尤其预算、并发覆盖、中断恢复、原文/精简分离、真实日志与验收真实性）。Subagent-driven 则每任务先独立 review，再做一次整体接缝审查。用户已选择 Native，仅一次整体 reviewer。
- [x] **Step 3:** 收到反馈先用 receiving-code-review 核对具体触发场景；确需修复则先写失败回归，再修改、再相关验证；新增代码/Prompt影响真实报告 hash 时按影响范围重跑。追记 code review 结论、取舍与返工，保留原报告。
- [x] **Step 4:** README 写真实可运行的 `$pyCh07`/迁移/默认/demo/smoke/`rg 'model_ctx|history_ctx|summary' log/app.log` 命令，引用已产生 report，不写假想成功数字。演示仅本地服务，保留模型/数据库/checkpoint 的当前有效路径。
- [x] **Step 5:** 使用 verification-before-completion 与 finishing-a-development-branch；按用户当前授权保留分支并交付，不默认 merge/push 或清理别人服务/目录。即时追记 Finish 四项，提交 `docs(ch07): deliver context management demo and verification`。只有功能、真实验收、审查与交付全部完成才标 finish。

## 计划自审、覆盖与执行门槛

| Spec | 负责任务 |
| --- | --- |
| 4 DDL/边界/投影表 | 2 |
| 5 完整历史/稳定 ID/工具不入消息表 | 2、3、5 |
| 6 三层纯投影/规则裁剪 | 3 |
| 7 模型窗口/稳态峰值/联合校准 | 1、6、8 |
| 8 分段异步/原子覆盖/非阻塞 | 4、5、8 |
| 9 顺序/system前缀/指代来源 | 3、5、6 |
| 10 最近连续段/首订单投影仍在 | 2、4、8 |
| 11 每轮原样日志/后台留痕 | 5、6、8 |
| 12 两 GET/多会话/静默降级 | 7、8 |
| 13 TDD或标注评估/真实验收/交付 | 每任务、8、9 |
| 14 Context7/本机接口验证 | 已查证基础、每任务新增接口前补查 |

2026-10-04 计划自审完成：步骤都有断言或准确签名，接口/DTO 在引用前定义，5 个 Review Focus 有归属测试，预算数字与 approved spec 一致，锁/事务没有隐含等待模型，计划没有写成产品完整源码。修正了证据 TopK 接缝的文件清单、分批摘要 old_S 的顺序推进与失败止步、普通请求准备时新 turn_id 的覆盖。结构检查为 9 个连续任务、50 个可检查步骤，预算算式通过；这不是产品测试或用户计划评审通过。文件表中的批量花括号是路径清单表达，不是 PowerShell 命令。

计划评审前的历史记录：当时用户仅批准书面设计，随后已审阅本计划并选择Native（见文件开头）。当时提供的选择为：Native（主代理逐任务执行，末尾一次独立整体审查）或 Subagent-driven（每任务独立实施与审查，末尾整体审查）。本计划推荐 Native：9 项任务依赖同一消息 ID/State/中断接缝，顺序实施减少上下文重复，确定性测试与最后独立审查承担验证。选定后分别调用 executing-plans 或 subagent-driven-development；批准之前不开始实施。

## 当前执行状态（2026-10-05，产品提交5b931bc）

Task1–8已完成；default-05全新连续22轮、demo-03核对12轮并续到22轮通过，两组窗口违例0，默认降级/摘要0、演示降级15/摘要3。真实浏览器切回继续与跨进程刷新pending完成，路由32条与修复后指代8/8当前有效。Task9唯一整体review的3Important已按RED/GREEN关闭；另有用户批准的累计费用修订，新增回归和完整套件865passed/5deselected41.73s通过。README、交付核对及finish留痕已齐备，最终文档提交后交付。2026-10-05恢复原Docker容器后，两组44条原文/摘要与原报告完全一致，当前路由绑定有效；所有依赖healthy。保留ch02-tools分支/worktree、旧失败报告和checkpoint，不merge/push。计划workspace已移出.superpowers到ignored.cache/ch07/native-evidence-20261004，必要ledger/RED/GREEN/审查包归档至verification/native-evidence。
