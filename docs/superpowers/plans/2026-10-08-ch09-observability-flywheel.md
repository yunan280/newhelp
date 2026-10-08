# Ch09 可观测性与数据飞轮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用本地 Langfuse 展开客服链路，并完成带证据的问题入池、人工补知识、按意图统计 token 和两轮真实评估。

**Architecture:** 三个入口先可靠提交原话与同轮快照，再由服务内工作器标准化和语义归并。正式置信闸仍位于知识检索后、Agent 前；人工核准复用 ch03 原文和向量发布，观测和评估不改变业务权限。所有后台任务使用独立 Session 和可恢复的持久来源。

**Tech Stack:** 现有 FastAPI、SQLAlchemy/MySQL、LangChain/LangGraph、BGE-M3、Milvus 原生 BM25/hybrid/RRF、bge-reranker-v2-m3；Langfuse 服务端 v4.54.0、Python SDK4.17.0；Windows PowerShell/Task Scheduler。

**Spec:** [2026-10-08-ch09-observability-flywheel-design.md](../specs/2026-10-08-ch09-observability-flywheel-design.md)，用户已于2026-10-08确认书面 spec。

**Status / Execution:** 用户于2026-10-08以「我已确认」批准本计划；Native实施进行中。主代理按 `superpowers:executing-plans` 实现，最后一次独立后端审查；不为每个任务另开实施/审查代理。前端遵守 Vibe Coding 例外。

## Global Constraints

- 固定使用现有 FastAPI、SQLAlchemy/MySQL、LangChain/LangGraph、BGE-M3、Milvus 原生 BM25/hybrid/RRF、bge-reranker-v2-m3，以及开源自部署 Langfuse。
- 实测基线：langgraph1.2.12、langchain-core1.6.5、langchain-openai1.6.6、SQLAlchemy2.1.1、FastAPI0.141.1、pymilvus2.6.17；SDK冲突先出证据并问用户，不能静默升级/换方案。
- LANGFUSE_BASE_URL 显式为本地3039；关闭 TELEMETRY_ENABLED。SDK和查询不能回退云端，凭据只在忽略的本地环境文件。
- 原样保留用户 review_queue/eval_runs DDL、中文 ENUM、InnoDB/utf8mb4；先 CREATE review_queue，再 ALTER 问题池加外键，ON DELETE SET NULL。
- 已批准的额外字段只有 messages.retrieval_snapshot JSON 及问题池反馈入口扩展；不另建反馈/任务表，不按主题微调分类。
- 新落池 retrieved_chunks=NULL 只表示已确定未检索；empty/legacy_partial/unavailable 必须区分，旧行迁移后的 NULL 不能冒称未检索。
- 20 calibration / 40 test 严格隔离，实际生成用 Top5；校准不复用旧 query rewrite 分数，不把80条评估示例导入线上库。
- 主流程保留现有输入/token/耗时预算、Ch06政策合同和Ch08确认/审计权限。写操作的重试策略保持不变。
- Ch08客服9020、MCP9021/9022保留；Ch09客服9030、独立 checkpoint；Langfuse仅回环3039。
- 后端代码 TDD；Prompt/数据用冻结标注样例或评估验证；前端直接 Vibe Coding，不做前端 brainstorm/TDD/code review。
- 每完成阶段/任务即追记 dev-notes/ch09.md 四项；区分真实页面、API/DB、离线测试、历史证据，不重跑无关Ch08模型评估。

## Review Focus

1. 重复问句、切会话后的旧回答和超出 JavaScript 安全整数的 BIGINT：反馈必须指回准确原话，ID以十进制字符串传输。Task 5 的历史/归属测试覆盖。
2. 反馈已提交后重放账本、消息提交失败、waiting预览：不可变证据不冲突，可变反馈不被覆盖，未提交回答不能反馈。Task 2/4/5 覆盖。
3. 队列超过一页、单条毒数据、人工审核与归并并发：不跳候选、不饿死后续、不累加终态行；Task 6/7 的真实MySQL测试覆盖。
4. SDK上下文被后台任务继承、SSE断开、旧确认回执重放：trace不串会话/意图，确认等待不跨请求，重放不得新增写操作。Task 1/4 覆盖。
5. 评估NA/部分故障、跨页usage、同轮重复收尾和配置漂移：不补满分、不伪造完整轮次、不重复用量，趋势不能比较不同口径。Task 8/9 覆盖。

## 文件边界与依赖

| 文件/区域 | 责任 |
| --- | --- |
| src/mewhelp/ch09/config.py、contracts.py、runtime.py | 配置与跨模块DTO；聚合生命周期/工作器，业务逻辑不堆到 main |
| ch09/observability.py、costs.py | 请求及子观测、Langfuse读取与意图用量汇总 |
| ch09/migration.py、db/models.py、knowledge/refusals.py | 权威DDL与ORM镜像、问题池增量字段及独立提交 |
| ch09/snapshots.py、feedback.py | 不可变证据、历史恢复、回答身份与事务反馈 |
| ch09/confidence.py、calibration.py | 单调置信公式、冻结20题候选选择与配置核验 |
| ch09/generation.py | 主工作流的单次充分性判断与知识答案，复用Ch04合同 |
| ch09/normalization.py、dedup.py、locks.py、flywheel.py | 模型标准化、分页语义查重、连接级锁、归并计数与后台恢复 |
| ch09/reviews.py | 人工审核、稳定知识键和向量发布补偿 |
| ch09/evaluation.py、evaluation_jobs.py | 当前正式路径的隔离逐题评估、持久运行状态与同轮防重 |
| ch09/api.py | 参数/DTO校验、HTTP错误映射和分页；调用服务接口，不实现模型流程 |
| scripts/*ch09*、infra/langfuse/、sql/ch09.sql | 本地部署/迁移/启动/定时/演示命令；私有配置不进Git |
| static/index.html、static/review.html、static/ch09-stats.html | Vibe页面与后端合同对接 |
| tests/ch09/、eval/ch09/、docs/ch09-*.md、artifacts/ch09/ | 真边界测试、标注样例、交付和本次证据；原始运行产物忽略 |

依赖顺序：1观测 → 2迁移/快照 → 3置信 → 4主流程落池 → 5反馈 → 6归并 → 7审核 → 8评估 → 9统计/定时 → 10页面 → 11整体验收/审查。Task 9只消费已完成任务的接口。本章是同一客服闭环，共享轮次、快照、配置与发布集合，使用一个计划。

所有示例命令均是**实施时待运行的命令**，不是当前测试结果。进入实施后使用 `superpowers:using-git-worktrees`：先检查本任务附件并复用合适的隔离工作树；否则以含本计划的本地提交创建 `codex/ch09-observability-flywheel`。在新树中保留必要Ch08预览HTML/脚本的快照及hash，排除其他章节笔记。只读导出原 `.venv-ch03` 的实际依赖清单并核对版本，建立独立 `.venv-ch09`、以原栈为基线安装SDK；`requirements-ch08.lock.txt`只锁了工具增量，不能冒充完整环境锁。不改运行中的旧venv。下文相对路径和命令均以最终Ch09树为cwd；任务开始时登记实际 `<run>`（如 `20261008-native`），`<date>`取执行日Asia/Shanghai日期。设置 `$ch09Python = (Resolve-Path ./.venv-ch09/Scripts/python.exe).Path`；下文 `python` 是该路径的简写，执行时用 `& $ch09Python`，不用系统Python。

通用收尾：每个后端任务保存 red/green 结果到 `artifacts/ch09/<run>/execution/`，立即追加四项开发记录，然后只提交该任务文件和笔记。Prompt样例留输入、期望、实际输出、失败理由和模型/Prompt hash；前端记录实际视觉修改和页面证据，不制造TDD记录。

### Task 1: 本地 Langfuse 和统一请求观测

**Files**
- Create: `src/mewhelp/ch09/__init__.py`, `config.py`, `contracts.py`, `observability.py`, `runtime.py`; `infra/langfuse/compose.yml`; `scripts/init_ch09_langfuse.py`, `scripts/start_ch09_langfuse.ps1`; `docs/ch09-sdk-reference.md`, `requirements-ch09.lock.txt`。
- Modify: `pyproject.toml` observability extra，`.env.example`, `.gitignore`, `main.py:27` lifespan，`ch05/runtime.py:30` open_runtime，`ch05/workflow.py:443` build_workflow，`ch05/state.py` trace关联字段，`ch05/service.py:45`，`ch06/selection.py:105`，`ch08/confirmation.py:270`，`ch01/service.py`，`ch02/service.py`，`knowledge/answering.py` 的图外调用边界，`tools/engine.py`。
- Test: `tests/ch09/conftest.py`, `test_config.py`, `test_observability.py`, `test_tool_observations.py`。

**Interfaces**
- Consumes: 现有 `WorkflowRuntime`, `SessionFactory = Callable[[], Session]`, `ToolResult`, `ToolCallContext`；CallbackHandler来自官方SDK。
- Produces: `Ch09Settings`（enabled、base_url、confidence_path、artifact_dir等）；`RequestTraceContext(session_id, user_id, conversation_id, turn_id, entry_point, trace_kind, origin_trace_id)`；`ObservationRuntime.request(ctx: RequestTraceContext, *, input: dict) -> ContextManager[RequestObservation]`。
- `RequestObservation`提供 trace_id、`set_intent(intent: str) -> None`、`finish(*, status: str, output: dict) -> None`；`ObservationRuntime.observe(name: str, *, input: dict, kind: str='span') -> ContextManager`、`flush() -> None`、`shutdown() -> None`；disabled为安全空实现。
- `build_workflow(checkpointer, *, callbacks=())` 编译后只固定一次 callbacks；`open_runtime(..., observation_runtime=None)` 传入同一实例。图外模型显式复用该回调；图内模型继承，不能二次挂造成双计。
- `Ch09Runtime`持有observation_runtime以及后续可选flywheel/eval_jobs；`open_ch09_runtime(factory: SessionFactory, *, settings: Ch09Settings) -> AsyncContextManager[Ch09Runtime]`。`attach_workflow(runtime: WorkflowRuntime) -> Awaitable[None]`在主图可用后启动需要其模型/检索资源的工作器；初始化阶段不存在的功能保持None，不提前加载未完成模块。

- [x] **1. 接口与兼容检查。** 使用已有Context7库ID `/langfuse/langfuse-python`、`/langfuse/langfuse-docs` 核对4.17.0的上下文、CallbackHandler和v2 cursor读取；保存官方出处及精确签名到 `docs/ch09-sdk-reference.md`。以UTF-8保存原venv依赖清单、剔除指向旧树的editable条目，以当前新树安装mewhelp；完整约束下安装锁定4.17.0并 `python -m pip check`。若需变化的旧依赖与固定栈冲突，先出解析证据问用户，不用未锁的 `pip install .[observability]` 升级整个框架。
- [x] **2. 写失败测试。** 测试 `test_enabled_cloud_url_is_rejected`；`test_one_callback_parallel_roots_and_late_intent` 断言两会话trace_id不同、最终intent各自正确、handler只初始化一次；`test_background_trace_is_detached`；`test_disconnect_and_resume_close_distinct_roots` 断言waiting和resume不同trace_id、同turn/origin关联；`test_tool_denial_and_timeout_are_observed` 断言prepare拒绝/execute超时都有参数、status、retry_count和耗时，业务结果不因观测异常变化。

```python
with pytest.raises(ValueError):
    Ch09Settings(enabled=True, base_url='https://cloud.langfuse.com')
assert left.trace_id != right.trace_id
assert callback_factory.call_count == 1
```
- [x] **3. 验证红。** `& ./.venv-ch09/Scripts/python.exe -X utf8 -m pytest tests/ch09/test_config.py tests/ch09/test_observability.py tests/ch09/test_tool_observations.py -q`；预期新接口不存在或合同断言失败，排除无关导入/环境错误。
- [x] **4. 实现观测边界与部署。** 用已核对的 `start_as_current_observation`、`propagate_attributes` 和请求自己的root handle，分类后更新root metadata；所有node/工具子观测保留真实输入输出，不依赖共享last_trace_id。临时导出故障记录后继续客服。SSE和resume在请求上下文内消费完生成器并收尾；旧回执重放创建只读请求观测，不触发写工具。后台根上下文与客服分离。生成初始化私有文件 `.env.ch09.langfuse`，不打印密钥；Compose锁定web/worker版本及所有依赖digest，6服务与3039回环。
- [x] **5. 验证绿与真实部署。** 重跑第3步；执行 `powershell -NoProfile -File scripts/start_ch09_langfuse.ps1`，保存docker compose健康/实际image版本/本地项目API可读证据。依赖共存资源检查不足则如实阻塞，不换云端；本任务不宣称已通过完整链路验收。
- [x] **6. 记录与提交。** 生成 `requirements-ch09.lock.txt`（无密钥），追记Task 1结果；`git commit -m 'feat(ch09): add self-hosted request observability'`，提交范围仅本任务及笔记。

### Task 2: 权威迁移、问题池扩展和不可变快照

**Files**
- Create: `sql/ch09.sql`, `scripts/migrate_ch09_schema.py`, `src/mewhelp/ch09/migration.py`, `snapshots.py`。
- Modify: `ch09/contracts.py`快照DTO，`db/models.py`, `db/repository.py:45` TurnMessage/append_messages_once，`knowledge/refusals.py:31`及RefusalInput/record_refusal；`pyproject.toml`注册mysql测试marker。
- Test: `tests/ch09/test_migration.py`, `test_mysql_schema.py`, `test_snapshots.py`, `test_ledger_snapshot.py`。

**Interfaces**
- Produces: ORM `ReviewQueue`、`EvalRun` 放入现有 `db/models.py`，避免SQLAlchemy metadata漏注册；LowConfidenceQuestion新增两列，Message新增retrieval_snapshot。
- `migrate_ch09(engine: Engine) -> dict`；`RetrievedChunk`完整字段见spec §5.1；`EvidenceSnapshot(schema_version=1, state, chunks, query, filters, top_k, confidence)`。`MessageSnapshot`含schema_version、`retrieved_chunks: EvidenceSnapshot|None`、turn_id/source_user_event_key/source_user_message_id/intent/retrieval_performed/retrieval_events/pool_id/trace_id/answer_status/feedback_lcq_id。answer_status明确区分完成和waiting/error，历史加载不能给preview生成可反馈ID；不是从snapshot=NULL推断最终回答。
- `snapshot_result(result: RetrievalResult, *, query: str, filters: SearchFilters, top_k: int=5, confidence: dict|None=None) -> EvidenceSnapshot`；`immutable_message_snapshot(snapshot: dict|None) -> dict|None`用于重放比较，排除feedback_lcq_id与请求trace_id，但包含原话身份、完成状态和证据。trace_id保持首次成功写入值，重放新请求通过root origin/replayed元数据关联，不覆盖原消息溯源。
- `RefusalInput`追加默认 `retrieved_chunks: dict|None=None`；旧调用不改签名位置。独立record_refusal提交JSON并返回原str ID。

- [x] **1. 写失败测试。** 原DDL逐列核验与重复迁移；同名不兼容ENUM/长度/外键必须报错。`test_feedback_mutation_does_not_break_ledger_replay`：保存快照→加feedback_lcq_id→从新trace重放原消息，assert只一条、feedback和首次trace仍在；修改immutable chunk则冲突。`test_snapshot_preserves_original_rank_text_score`：Top5完整中文原文/分数/hash，JSON中文不转义。`test_legacy_null_is_not_no_retrieval`区分旧值与确知未检索。

```python
assert replayed.id == original.id
assert replayed.retrieval_snapshot['feedback_lcq_id'] == '9'
assert answer_row_count == 1
```
- [x] **2. 验证红。** `python -X utf8 -m pytest tests/ch09/test_migration.py tests/ch09/test_snapshots.py tests/ch09/test_ledger_snapshot.py -q`（下文python均指 `.venv-ch09/Scripts/python.exe`）。
- [x] **3. 实现DDL和快照。** sql/ch09.sql保留用户DDL/注释，加已批准补充；迁移检查实际结构、SET NAMES及UTC连接时间。ORM与MySQL无符号/JSON/中文ENUM一致，旧enum值保留；全JSON对象赋值。只比较immutable快照，反馈更新不被原TurnMessage覆盖；新增池字段失败继续遵守PoolCommitError。
- [x] **4. 真实MySQL验证。** 测试专用数据库名必须以 `mewhelp_ch09_test_` 开头，不指向客服库；fixture使用忽略配置，不打印URL。`python -m pytest tests/ch09/test_mysql_schema.py -m mysql -q`：SHOW CREATE、默认值/索引、中文值实存读取、删除review后matched_review_id为NULL、两次迁移不丢数据；缺真实DB时此专门命令失败而非跳过伪通过。
- [x] **5. 验证绿与迁移应用。** 重跑第2步和第4步；备份本次目标结构证据后 `python -X utf8 scripts/migrate_ch09_schema.py --report artifacts/ch09/<run>/schema.json`。仅兼容增量变更，不删除旧数据或重建现有表。
- [x] **6. 记录与提交。** 即时追记字段边界、MySQL证据和重放结果；`git commit -m 'feat(ch09): persist evidence and flywheel schema'`。

### Task 3: 正式置信公式与20题冻结校准

**Files**
- Create: `src/mewhelp/ch09/confidence.py`, `calibration.py`, `tests/ch09/test_confidence.py`, `test_calibration_contract.py`。
- Modify: `ch05/evidence.py`, `ch05/state.py`, `ch05/runtime.py`（正式profile加载及共享原始检索接口）；保留旧Ch04 CLI阈值合同。
- Evaluation: 原 `eval/ch04/{corpus,queries,freeze}.json*`；本次 `artifacts/ch09/<run>/confidence/`，不修改标注。

**Interfaces**
- `ConfidenceProfile(top_k, effective_cutoff, weights, threshold, model_metadata, dataset_hashes, retrieval_config_hash)`，weights固定为 `(w_s,w_n,w_g)` 三元组；`EvidenceConfidence(passed, value, top1, gap, effective_count, missing_top2, reason_code, reason)`。
- `score_evidence(scores: Sequence[float], *, profile: ConfidenceProfile) -> EvidenceConfidence`；`load_confidence_profile(path: Path, *, expected: dict) -> ConfidenceProfile`。
- `retrieve_current_evidence(question: str, *, rag: RagRuntime, filters: SearchFilters) -> RetrievalResult`：现有原问题passthrough路径的共享接口，无二次改写。`retrieve_knowledge`调用它构造现有Envelope，并保留Top5原始ChunkSnapshot用于生成/快照；评估直接消费原Top10/候选Top50。
- `calibrate_current_path(*, dataset: Path, workdir: Path, run_id: str, retrieval_runtime: RetrievalRuntime) -> Path`（async）：只取20 calibration，建立隔离SQLite/集合，产物绑定冻结语料与当前模型/config，不绑定线上库hash。

- [ ] **1. 写公式失败测试。** 权重非负、和1、s1/gap/n(c)/5按spec公式；一条证据gap0且missing_top2=True；空证据拒绝；NaN/Inf/None/越界、未按rank排序报配置故障；profile模型/TopK/config不一致拒绝加载，线上普通FAQ新增不使profile失效。

```python
# profile: TopK=5, cutoff=.6, weights=(.5,.25,.25), threshold=.5
result = score_evidence([.8, .6], profile=profile)
assert result.value == pytest.approx(.5 * .8 + .25 * 2 / 5 + .25 * .2)
assert score_evidence([], profile=profile).passed is False
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_confidence.py tests/ch09/test_calibration_contract.py -q`。这里只测运算/隔离合同，不拿假的高分证明真实门控效果。
- [ ] **3. 实现公式与搜索。** 权重步长0.25、w_s≥0.5；cutoff来自实测评分，threshold含全拒候选；按误放、误拒、较高阈值、固定参数顺序选择。函数保留原Top10/候选和真实Top5原文，不因prompt布局重排分数。耗时预算/无证据/上下文超限继续独立处理。正式profile通过CH09配置单独加载；旧RagRuntime构造所需RAG_CALIBRATION_PATH按旧格式保留，不把confidence新文件塞给旧load_relevance_threshold解析器；主力闸不使用该旧阈值。
- [ ] **4. 数据验证代替Prompt TDD。** 先 `python -m mewhelp.knowledge.evaluation.dataset --validate eval/ch04`。`python -m mewhelp.ch09.calibration --dataset eval/ch04 --workdir artifacts/ch09/<run>/confidence --run-id ch09_cal_<date>` 实跑20题原路径，保存全部候选/误放误拒/特征/召回/模型revision/hash。40 test留给Task 8，不能用于选参数；旧calibration-scores不复用。若全拒或真实语料不够支撑则公开结果，闭环不得假称通过。
- [ ] **5. 验证绿。** 重跑第2步；正式profile重载校验通过，修改test标签不会改变calibration候选选择；报告注明这是校准证据，不是两轮评估。
- [ ] **6. 记录与提交。** 记实际参数、错误及产物路径；`git commit -m 'feat(ch09): calibrate formal evidence confidence'`。

### Task 4: 主力生成充分性、三入口证据基础和持久回答ID

**Files**
- Create: `src/mewhelp/ch09/generation.py`, `tests/ch09/test_workflow_confidence.py`, `test_generation_boundary.py`, `test_turn_snapshot.py`。
- Modify: `ch05/workflow.py:54/227/235/317/344/397`, `ch05/agent.py:293`, `ch05/state.py`, `ch05/schemas.py` TurnResult，`ch05/service.py`；`knowledge/answering.py`拒答快照和充分性接口；`ch05/evidence.py` 的retrieve_policy及workflow.policy_node采集政策快照，保留Ch06政策行为。
- Evaluation: `eval/ch09/generation-samples.jsonl`, `src/mewhelp/ch09/prompt_eval.py`（后续扩展同一评估入口），`tests/ch09/test_prompt_dataset.py`。

**Interfaces**
- Consumes: Task 1 observation、Task 2 Snapshot/RefusalInput、Task 3 score/profile/原始检索。
- `KnowledgeAnswerOutcome(result: AnswerResult, usage: TokenUsage, raw_usage: dict|None)`；`generate_knowledge_answer(state: dict, context: WorkflowContext, emit: Callable, *, record_pool: bool=True) -> dict`（async）返回现有answer/refused/pool/usage/calls/stop_reason状态更新；Task 8用隔离context和record_pool=False消费同一接口。
- 所有线上路径把实际retrieval snapshot传入拒答；知识生成复用AnswerAssessment充分性/引用检查，已经formal_gate通过的evidence只禁用旧阈值，不能重新检索。评估复用同一充分性执行函数，使用 `record_pool=False`。
- `TurnResult`增加 `answer_message_id: str|None`、`feedback_status: Literal['none','down']`；最终账本提交成功才赋ID，waiting/失败默认None；开始新轮重置快照、pool、trace关联但不删历史。

- [ ] **1. 写失败测试。** 知识闸拦下不调用Agent、pool含当轮Top5；formal通过但旧阈值更高不得二次拦截；模型answerable=False先提交generation池再兜底，错误引用拒答且未发正文；知识充分性只调用一次模型并计真实usage；闲聊/订单后继轮retrieval_performed=False不带旧片段；ledger失败/preview无可反馈ID；相同旧回执重放tickets数不变。

```python
assert actual['refused'] is True
assert actual['answer'] == REFUSAL_MESSAGE
assert actual['low_confidence_question_id'] == str(generation_pool.id)
assert '未经验证的草稿' not in emitted_text
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_workflow_confidence.py tests/ch09/test_generation_boundary.py tests/ch09/test_turn_snapshot.py -q`。
- [ ] **3. 实现工作流。** begin_turn清新轮检索；retrieve/policy分别采事件快照；gate保留预算检查后用正式profile；知识最终一次结构生成后校验引用再发token，业务/追问仍原合同。保留Ch07完整历史/模型投影和Ch08ToolMessage成对；不可变snapshot在消息事务中随最终回答提交，log_node从真实ids返回answer_message_id。模型预算包含schema和真实提示，不把估算当Langfuse用量。
- [ ] **4. 真实标注验证。** 冻结至少12条充分/不充分/引用错误及型号、否定、数值条件样例，expected来自原文而非模型输出；`python -m mewhelp.ch09.prompt_eval --suite generation --samples eval/ch09/generation-samples.jsonl --output artifacts/ch09/<run>/generation.jsonl`。保留实际模型、prompt、raw输出和断言；数据问题先修标注明确留痕，不能看结果改正确答案。前端正文形态仍按Vibe实现。
- [ ] **5. 验证绿及受影响回归。** 重跑第2步；`python -m pytest tests/ch05 tests/ch06 tests/ch07 tests/ch08/test_ticket_history.py tests/ch08/test_ticket_resume.py tests/ch08/test_ticket_restart.py -q`。确认新结构化生成不会损坏已确认的上下文/权限/只读回执。
- [ ] **5a. 核对并更新必要路由校准。** `ch06.evaluation.runtime_hash`包含整个ch05/schemas.py，新响应字段会使旧router产物失效。所有受指纹覆盖的代码改动完成后，`python -m mewhelp.ch08.evaluation calibrate-router --dataset eval/ch08 --outdir artifacts/ch09/<run>/router` 实际跑16条独立calibration，验证新hash并保存原产物；不手改成功产物hash。后续若修改覆盖文件，只对确实失效的校准重做并记录原因，不能重复旧26条验收充数；启动用该新路径。
- [ ] **6. 记录与提交。** 记检索/生成拒答和ID合同证据；`git commit -m 'feat(ch09): connect main workflow to evidence flywheel'`。

### Task 5: 后端负反馈与历史轮次恢复

**Files**
- Create: `src/mewhelp/ch09/feedback.py`, `api.py`; `tests/ch09/test_feedback.py`, `test_mysql_feedback.py`, `test_legacy_snapshot.py`。
- Modify: `ch09/snapshots.py`, `ch07/schemas.py`, `ch07/store.py:123` read_visible_messages，`main.py` router注册。

**Interfaces**
- `FeedbackRequest(user_id: str, session_id: str, answer_message_id: str, choice: Literal['down'])`；`FeedbackReceipt(pool_id: str, answer_message_id: str, replayed: bool)`。
- `submit_negative_feedback(factory: SessionFactory, request: FeedbackRequest, *, recover: Callable) -> FeedbackReceipt`（async）；`recover_answer_snapshot(message_id: int, *, factory: SessionFactory, checkpoint_reader: Callable) -> MessageSnapshot`（async）。checkpoint异步读取留在原事件循环；旧轮恢复先完成再进入短反馈事务，锁行后重查归属/最终状态。必须对应明确turn/ledger绑定，无依据标unavailable；确认为旧waiting则拒绝反馈。
- `POST /api/ch09/feedback`；VisibleMessage增加同名answer_message_id/feedback_status；历史ID不截断为JS浮点，旧客户端新增字段不破坏原响应。

- [ ] **1. 写失败测试。** `test_old_answer_bigint_and_repeated_question_find_exact_user` 使用大于2**53的字符串ID及同一会话两次同问，断言反馈原问题/快照指向指定回答；跨用户/跨session/工具消息/preview拒绝；`test_retry_returns_same_pool_id`重复和失败重试只一条；事务任一步失败同时回滚。老snapshot只从同轮历史回捞，旧引用无分为legacy_partial，检索空/未检索/未知四态区分，不查询现在线上知识。

```python
assert retry.pool_id == first.pool_id
assert retry.replayed is True
assert feedback_pool_count == 1
assert saved.original_question == selected_turn_question
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_feedback.py tests/ch09/test_legacy_snapshot.py -q`。
- [ ] **3. 实现反馈与恢复。** 校验归属后锁最终assistant行，查绑定user消息，创建feedback池和整对象反馈标记同事务；reason_code=user_feedback、entry/stage=feedback。已有池ID直接返回；恢复明确引用/评分不可用时如实标部分/不可恢复，不猜“最近用户消息”。GET历史带持久ID/已反馈状态，不能泄露另一会话快照。
- [ ] **4. 真实并发验证。** `python -m pytest tests/ch09/test_mysql_feedback.py -m mysql -q`：独立连接同时点同一回答，assert pool1条/同ID，反馈后账本重放不清标记。用Task 2专用DB；专门命令缺DB即失败。
- [ ] **5. 验证绿。** 重跑第2/4步及 `tests/ch07/test_conversation_api.py`。本阶段只验证API/DB，页面点击在Task 10/11。
- [ ] **6. 记录与提交。** 记旧轮恢复边界/缺分状态及ID证据；`git commit -m 'feat(ch09): persist idempotent negative feedback'`。

### Task 6: 标准化、分页语义归并与恢复工作器

**Files**
- Create: `src/mewhelp/ch09/normalization.py`, `dedup.py`, `locks.py`, `flywheel.py`; `tests/ch09/test_flywheel.py`, `test_mysql_flywheel.py`。
- Modify: `ch09/runtime.py`, `api.py`, `prompt_eval.py`；新增 `eval/ch09/normalization-samples.jsonl`, `dedup-samples.jsonl`。

**Interfaces**
- `NormalizedGap(question: str, suggested_answer: str)`；`DedupDecision(matched_ids: list[str], reason: str)`，返回ID只允许本页候选。
- `normalize_gap(original: str, *, snapshot: EvidenceSnapshot|None, model: Runnable) -> NormalizedGap`（async）；`find_equivalent(question: str, *, candidates: Sequence[tuple[str,str]], model: Runnable) -> DedupDecision`（async）。
- `named_lock(engine: Engine, *, scope: str, key: str='', wait_seconds: int=0) -> ContextManager[bool]`：专用autocommit连接持锁、finally释放；锁名hash后≤64字符，无长事务。
- `process_gap(pool_id: int, *, factory: SessionFactory, engine: Engine, normalize: Callable, dedup: Callable) -> str`（async，返回review ID）；`FlywheelWorker.start() -> None`, `wake() -> None`, `retry(pool_id: int) -> None`, `status() -> dict`, `aclose() -> Awaitable[None]`。
- 管理诊断 `GET /api/ch09/flywheel/status`（持久待处理数+本次尝试状态）；`POST /api/ch09/flywheel/retry/{pool_id}` 只重新安排原行，不新增池事件。

- [ ] **1. 写失败测试。** `test_match_in_second_page_and_smallest_id`：候选>8，第二页才有同义，选择最早匹配ID；`test_invalid_id_never_updates_queue`；`test_poison_row_does_not_starve_next_gap`；3次自动尝试后停、人工retry可恢复；`test_replay_does_not_increment`；`test_review_becomes_terminal_during_dedup`重新查重而非累加终态；shutdown取消模型等待不丢原话。

候选ID同时包含字符串'9'和'10'时，最早ID是9，不能按字符串字典序误选10；SQLite测试注入假的连接锁，真正锁竞争只由MySQL测试证明。

```python
assert returned_review_id == second_page_oldest_match_id
assert target.occurrence_count == 2  # 两个不同入口事件，每个只归并一次
assert replay_target.occurrence_count == target.occurrence_count
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_flywheel.py -q`。
- [ ] **3. 实现处理与恢复。** 名称锁控制多进程；短Session读取原话和全部待审分页，模型调用时无长事务。页大小≤8且预算不足缩小，所有页都比较；匹配取最早ID。短事务锁池/目标重查状态，再insert或atomic increment+matched_review_id同提交。临时失败退避最多3次/服务运行，schema/越界保留未处理等人工重试；状态诊断不冒称持久任务历史。lifespan扫描历史积压并唤醒新事件，后台独立root记录prompt/理由/消耗。
- [ ] **4. Prompt标注验证。** 至少12条标准化、16对语义查重样例，含型号/数字/否定/条件变化、跨页同义、无法确定不合并，草稿无证据明确待补；`python -m mewhelp.ch09.prompt_eval --suite flywheel --samples eval/ch09 --output artifacts/ch09/<run>/flywheel-prompts.jsonl`，要求逐条期望合同满足，保存真实输出/错误。候选答案不进入提示以免误把答案相似当问题同义。
- [ ] **5. 真实事务与绿。** `python -m pytest tests/ch09/test_mysql_flywheel.py -m mysql -q`：两个独立worker/连接同义输入只一review且count2；在归并与审核竞争时不写终态；故障注入事务回滚后重跑只增一次。重跑第2步，服务重启未匹配行仍能处理；未处理/失败从诊断API可见。
- [ ] **6. 记录与提交。** 记录样例、真实计数及恢复；`git commit -m 'feat(ch09): normalize and merge durable knowledge gaps'`。

### Task 7: 人工审核、幂等知识写入与发布重试

**Files**
- Create: `src/mewhelp/ch09/reviews.py`, `tests/ch09/test_reviews.py`, `test_mysql_reviews.py`, `test_review_publication.py`。
- Modify: `ch09/api.py`；复用 `knowledge/store.py` put_chunk、`knowledge/sync.py` sync_pending和 `knowledge/vectors.py` 当前集合。

**Interfaces**
- `ApproveReviewRequest(approved_answer: str, category: str='客服补充FAQ', product_category: str|None=None)`；`ReviewPublication(review_id: str, review_status: str, knowledge_id: str|None, publication_status: str|None, sync_error: str|None)`。
- `approve_review(factory: SessionFactory, review_id: int, request: ApproveReviewRequest, *, publish: Callable[[list[int]],None], verify_published: Callable[[int],bool]) -> ReviewPublication`；`reject_review(factory, review_id: int) -> ReviewPublication`；`retry_publication(factory, review_id: int, *, publish, verify_published) -> ReviewPublication`。
- `GET /api/ch09/reviews?status=&page=&page_size=&sort=`，默认待审、次数降序+id稳定排序；列表及详情返回 `{items,total,page,page_size}`；详情原话独立分页。`GET /api/ch09/reviews/{id}`、`POST .../{id}/approve|reject|publish`；来源知识键固定 `ch09-review:<id>`。

- [ ] **1. 写失败测试。** approved_answer空/仅空格422；同参数确认两次KnowledgeChunk1条，同ID；变更已核准答案409；驳回不建知识。`test_vector_failure_preserves_approval_and_pending` 断言review通过且原文保留、publication pending、sync_error非空；重试published无需再建知识。不同category/product参数同样是冲突边界；详情列出全部原话/入口/评分/恢复状态，不只显示最近一个。

```python
assert failed_publication.review_status == '通过'
assert failed_publication.publication_status == 'pending'
assert failed_publication.sync_error
assert retried.knowledge_id == failed_publication.knowledge_id
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_reviews.py tests/ch09/test_review_publication.py -q`。
- [ ] **3. 实现审核。** 事务锁review；待审时用KnowledgeDraft稳定source_key、content_type=faq写原文和核准答案，原子提交后sync_pending。核准后答案/分类必须与原知识一致才能幂等；驳回终态不可反转。发布验证MySQL done和当前Milvus可见，失败如实pending，retry只发布该row_id；不新增Agent工具或修改政策hash。
- [ ] **4. 真实并发与接口验证。** `python -m pytest tests/ch09/test_mysql_reviews.py -m mysql -q`：同时审批只一知识、worker不归并通过行；同问检索到该知识ID为发布成功的真实证据。API/DB验证不记作点击页面验收。
- [ ] **5. 验证绿。** 重跑第2/4步；`python -m pytest tests/test_knowledge_store.py tests/test_knowledge_sync.py -q`，原入库/补偿路径保持兼容。
- [ ] **6. 记录与提交。** 记审批/发布两阶段实际状态；`git commit -m 'feat(ch09): publish approved review answers safely'`。

### Task 8: 当前正式路径的隔离评估与 eval_runs

**Files**
- Create: `src/mewhelp/ch09/evaluation.py`, `evaluation_jobs.py`; `tests/ch09/test_evaluation.py`, `test_eval_jobs.py`, `test_mysql_eval_runs.py`。
- Modify: `ch09/runtime.py`, `api.py`；按最小公共边界复用 `knowledge/evaluation/{dataset,metrics,judge,runner}.py`，保留旧四策略CLI。

**Interfaces**
- `EvaluationRequest(run_id: str, triggered_by: Literal['手动','定时']='手动', resume: bool=False)`；`EvaluationJob(run_id, status, processed, dataset_size, artifact_dir, error)`；run_id匹配 `[a-z][a-z0-9_]{0,63}`，必须拒绝路径穿越。
- `evaluate_current_path(*, dataset: Path, workdir: Path, run_id: str, profile: ConfidenceProfile, resume: bool=False) -> dict`（async）：隔离SQLite与 `ch04_eval_<run_id>`，40 test，返回summary。
- `EvalJobManager.submit(request: EvaluationRequest) -> EvaluationJob`（async）、`get(run_id: str) -> EvaluationJob`、`aclose() -> Awaitable[None]`；`persist_eval_run(factory, *, request: EvaluationRequest, summary: dict, engine: Engine) -> str`。
- `POST /api/ch09/evaluations` 返回202和状态URL；`GET /api/ch09/evaluations/{run_id}`；`GET /api/ch09/eval-runs?page=&page_size=`按created_at/id排序。metrics保留 `candidate_recall50/candidate_mrr50/final_recall5/final_recall10/final_mrr10/faithfulness`、各 `*_N`、覆盖率/错误和 `_meta`，前端不得换名丢口径。

- [ ] **1. 写失败测试。** `test_production_collection_and_path_escape_rejected`；`test_eval_uses_raw_question_gate_and_generation_top5` 断言共享原问题接口、原Top10算指标/Top5生成、正式闸实际生效、record_pool=False；NA不是1、无GT和未召回0不同、judge失败计错误；`test_partial_run_has_no_eval_row`；`test_repeated_completion_returns_same_row`；`test_resume_rejects_model_or_prompt_drift`。

```python
assert partial_processed == 39 and completed_eval_rows == 0
assert first_eval_id == repeated_completion_id
assert summary['dataset_size'] == 40
assert summary['faithfulness'] is None  # 拒答/无声明的NA样例
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_evaluation.py tests/ch09/test_eval_jobs.py -q`。假模型只用于运行控制/指标边界，不能充当两轮真实评估。
- [ ] **3. 实现隔离runner。** 冻结hash验证、独立DB/集合、全部40题逐题终态保存；调用Task 3原检索，使用Task 4同一知识生成合同，构造隔离WorkflowContext/预算与state，不运行工具/不写线上池。reuse BGE/精排模型实例及原文编码缓存，不复用新run的检索/生成/judge答案。保留原Ch04指标定义/GT和逐声明判定。
- [ ] **4. 实现任务恢复/持久登记。** `manifest.json`/`cases.jsonl`原子更新，记录dataset/model/prompt/profile/config hash、开始/结束/错误及resume；全局评估命名锁限制只有一模型任务。每个run收尾另用不同scope的按run命名锁短事务查JSON `_meta.run_id` 防重，避免在持全局锁时再次争用同名锁。全部40题处理完（含错误终态）才建一行；有错误/全部NA状态明确且客户端非零，未完成不建行。回收只限本次任务资源，不清空生产集合。
- [ ] **5. 验证绿与真实登记并发。** 重跑第2步；`python -m pytest tests/ch09/test_mysql_eval_runs.py -m mysql -q`：独立连接重复收尾只有1行，metrics中文/NA保留、不同run各自独立；任务中止后同配置显式resume可恢复，变更配置拒绝。真实40题两轮统一留Task 11，不提前重复跑。
- [ ] **6. 记录与提交。** 记隔离边界/同轮防重及未完成状态；`git commit -m 'feat(ch09): persist isolated production-path evaluations'`。

### Task 9: 意图token汇总、趋势和定时命令

**Files**
- Create: `src/mewhelp/ch09/costs.py`, `scripts/run_ch09.ps1`, `scripts/run_ch09_evaluation.ps1`, `scripts/register_ch09_evaluation.ps1`; `tests/ch09/test_costs.py`, `test_trends.py`。
- Modify: `ch09/api.py`, `config.py`, `runtime.py`；新增 `docs/ch09-demo.md` 初版命令。

**Interfaces**
- `read_token_costs(*, client, from_time: datetime, to_time: datetime) -> dict`（async），只访问本地Langfuse；`GET /api/ch09/token-costs?from=&to=`返回UTC [from,to)、最后读取时间、每intent的request_count/input_tokens/output_tokens/total_tokens/unknown_usage_count/usage_coverage/per_request_mean及可选模型细分。unknown_usage_count以缺用量的generation计数，usage_coverage以generation有效条数/总条数计算；一条generation的input/output不完整就标未知。统计只累加实测部分，该intent用量不完整时per_request_mean=null，不把未知当0；明确无模型调用的请求tokens=0而非未知。
- `eval_trend(rows: Sequence[EvalRun]) -> list[dict]`；相邻同数据/config/指标分母口径才给变化，NA为null，不同hash标 `comparable=False`。
- PowerShell启动默认 `-Port 9030`，载入忽略的本地Ch09/Langfuse环境、独立checkpoint及Task 3正式profile，复用MCP9021/9022配置；评估脚本参数 `-RunId`, `-TriggeredBy 手动|定时`, `-Resume`, `-BaseUrl http://127.0.0.1:9030`。

- [ ] **1. 写失败测试。** `test_all_cursor_pages_and_only_generation_usage` 跨2页根和2页子观测，父聚合不再加、观察ID重复去重、真实generation每次重试独立计；先分类耗时归最终intent，后台任务不混入chat；缺usage记unknown，实测0仍有效；导出/查询失败返回错误。`test_window_is_half_open_and_timezone_explicit`；`test_na_and_config_drift_not_compared_as_drop`。

```python
# 跨页chat样例实测input合计8、output4，另有1条usage缺失和后台generation
assert sum(item['total_tokens'] for item in report['items']) == 12
assert sum(item['unknown_usage_count'] for item in report['items']) == 1
assert incomplete_intent['per_request_mean'] is None
```
- [ ] **2. 验证红。** `python -m pytest tests/ch09/test_costs.py tests/ch09/test_trends.py -q`。
- [ ] **3. 实现查询与趋势。** Context7已核对v2 `api.observations.get_many`支持cursor、is_root_observation、trace_id、时间窗；按SDK4.17.0精确fields/metadata/usage类型实现Task 1参考，完整读根/生成子观测，按root最终intent归属。不要照搬旧v2/v3 trace-only示例；引用失效需先查Context7。只能对真实tokens求和，不回填预算估算。金额无真实价格不展示。
- [ ] **4. 实现命令与定时。** 启动先验证9030占用归属、DB SELECT1、MySQL结构、Milvus集合及本地Langfuse项目，不以healthz代替依赖验证。Task Scheduler独立 `MewHelp-Ch09-Evaluation` 默认Sunday04:00 Asia/Shanghai、可配置，保留Ch03任务；调用服务提交/轮询，故障exit非零。宿主时区不同则明确转换/拒绝隐式本地解释。只注册计划要求的本地任务，不创建Codex提醒。
- [ ] **5. 验证绿与调度状态。** 重跑第2步；`powershell -NoProfile -File scripts/register_ch09_evaluation.ps1 -At 04:00`；保存TaskName/Enabled/NextRunTime/时区，检查action调用正确脚本/环境。不额外手动触发第三轮充当“已日历执行”；本次两轮真实执行在Task 11。
- [ ] **6. 记录与提交。** 初版演示写实际配置文件/命令与统计字段，不写尚未验证的成功数字；`git commit -m 'feat(ch09): report intent usage and schedule evaluations'`。

### Task 10: 前端 Vibe Coding 对接

**Files**
- Modify: `src/mewhelp/static/index.html`（保留继承的Ch08工单预览）。
- Create: `src/mewhelp/static/review.html`, `ch09-stats.html`；`main.py`增加 `/review` 和 `/ch09/stats` 页面路由；`docs/ch09-demo.md`补页面入口。

**Interfaces**
- 消费Task 4/5持久回答ID和feedback状态，Task 6积压/重试，Task 7分页/审核/发布，Task 8/9任务/趋势/token接口。
- 页面不把AI草稿自动变成核准答案，点击通过时提交操作人当前明确填写的approved_answer；按钮文案区分已核准/待发布/已发布。

- [ ] **1. 直接做聊天反馈。** done后保存str ID；👎成功才已反馈、失败可重试；刷新/切会话恢复后端状态。waiting/未提交/模拟答复禁用持久反馈；👍保持本地采集。回复刷新与Ch08预览卡继续工作。
- [ ] **2. 直接做管理页面。** 次数排序/分页、标准问题/示例草稿、展开原话与入口/完整片段/评分/历史恢复状态；可编辑核准答案并通过/驳回；pending发布重试、后台未处理与错误重试可见。原文/模型输出按文本渲染，不当作HTML。
- [ ] **3. 直接做统计页面。** 意图token表带UTC窗口/本地显示、覆盖率/未知用量，按total排序；评估趋势有坐标/指标名/分母/时间、下降差值明显，不同配置标不可直接比较；运行中/失败真实展示。
- [ ] **4. 页面实际查看与反馈修改。** 使用获准的浏览器查看9030页面和3039 Langfuse，检查确认/驳回/重试/刷新与中文长文本。记录截图及真实点击；DOM/API检查单列。权限被拒绝则保留该项待用户手动结果，不能另换浏览器/地址绕过。此步骤是功能与效果检查，不加前端TDD或独立code review。
- [ ] **5. 记录与提交。** 即时记录页面产物、用户视觉纠偏、问题返工；`git commit -m 'feat(ch09): connect feedback review and metrics pages'`。

### Task 11: 六项真实验收、后端审查和交付

**Files**
- Create: `scripts/smoke_ch09_acceptance.py`, `docs/ch09-final-report.md`；修改 `docs/ch09-demo.md`, `dev-notes/ch09.md`；证据 `artifacts/ch09/<run>/acceptance/`。
- 若发现bug：先按 `superpowers:systematic-debugging` 找根因，再写所属后端失败测试修复；Prompt用失败样例验证，前端直接改，分别归回相应任务。

- [ ] **1. 运行一次最终所需回归。** `python -X utf8 -m pytest -m 'not eval and not mysql' -q`；随后 `python -m pytest tests/ch09 -m mysql -q`（真实专用DB）。`python -m ruff check src/mewhelp/ch09 tests/ch09 scripts/migrate_ch09_schema.py` 和 `python -m pip check`。保存实际pass/skip/fail；旧项目已有lint问题单列，不修无关文件。检查红绿日志，不能只用全绿结果声称TDD。
- [ ] **2. 启动与trace证据。** 用Task 9命令启动9030并核对原9020仍可用；请求知识/业务MCP/拒答/确认/取消/并发，各自保存实际trace ID与origin关联，确认prompt、工具调用/拒绝/耗时、原文检索、真实usage完整。Langfuse UI实际展开与服务端observations证据分别保留；缺任何必要信息不写“任意请求完整通过”。
- [ ] **3. 完整飞轮。** 问明确标识的演示业务未知问题，保存真实兜底→pool→matched_review_id→review；管理页看原话/当轮片段，人工提交有依据的核准答案，验证MySQL+Milvuspublished；同问再真实命中新知识并正确引用回答。演示数据与真实政策区分，禁止导入Ch04评估语料；无核心代码修改/无服务重启。
- [ ] **4. 负反馈与统计。** 对旧轮回答点👎并刷新重试、切会话，保存同一原问题/快照/唯一pool/语义归并次数；另验明确未检索的闲聊/业务为NULL。至少两类intent的真实generation usage对照统计，覆盖率/分页/时间窗口与未知用量如实显示。
- [ ] **5. 两轮真实40题评估。** 顺序运行：`powershell -NoProfile -File scripts/run_ch09_evaluation.ps1 -RunId ch09_<date>_r01 -TriggeredBy 手动`，随后新 `ch09_<date>_r02`。每轮40 test都实际检索/生成/判定，保存cases/summary/manifest、真实耗时/模型消耗、两条eval_runs及同口径趋势；不是跑两次历史report导入。存在错误/全部NA按实际状态报告并修根因，不擅自重复整轮；新改动确实影响口径时才再跑并记录原因。
- [ ] **6. 独立后端审查。** 使用 `superpowers:requesting-code-review` 派一个全新、最强可用审查代理，以隔离树基线到HEAD审查后端/脚本/Prompt/数据合同、事务/并发/原栈兼容及spec对照；不审前端视觉代码。按 `superpowers:receiving-code-review` 核实结论，修正高风险问题并做受影响验证，追记带路径和触发场景的审查结论。
- [ ] **7. Finish与最终交付。** 按 `superpowers:verification-before-completion` 和 `superpowers:finishing-a-development-branch`，确认必要验证有实际证据。交付树路径/分支/提交、可复制部署/启动/页面/评估命令、实际测试数字、校准产物、两轮指标/分母/趋势、Langfuse请求链接、真实页面状态和dev-notes路径；保留未满足验收，不把API通过写成UI通过。无用户授权不合并/推送；独立树保留供测试，不主动停服务或删除运行产物。

## 计划自查与交接

Spec映射：§4→Task1/9/11，§5→Task2/5，§6→Task3/4，§7→Task4/5，§8→Task6，§9→Task7，§10→Task8/9/11，§11→Task10，§12→各任务验证及Task11；固定选型/隔离/留痕适用于全部任务。六项验收均由Task11真实场景验证，离线与API证据分开。

自查时核对：新增模块和签名的生产/消费一致；每个红绿/数据验证步骤都有具体命令和期望合同；Review Focus各有所属任务；路径按当前repo清单验证；正文只给接口、断言和必要算法，不代写完整程序。计划自查通过不等于用户计划审批或功能验收。

用户审阅此计划后，继续采用已选Native并调用 `superpowers:executing-plans`；在此之前不执行以上步骤、不安装产品依赖、不迁移DB、不创建外部项目。
