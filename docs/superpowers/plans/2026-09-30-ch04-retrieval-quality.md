# Ch04 Retrieval Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有客服系统交付原生 BM25 混合检索、指定重排、可靠引用与拒答，以及可复跑的四策略评估数字和前端交互。

**Architecture:** MySQL 保持原文权威，新的 Milvus 集合负责 dense/BM25 和前置过滤。Query 理解、检索、证据组织、生成校验与拒答记录组成共享知识核心，聊天、JSON、CLI 和隔离评估调用该核心。保留已有业务工具与会话账本，在知识正文输出前完成校验。

**Tech Stack:** 已有 FastAPI / SQLAlchemy / MySQL / LangChain；PyMilvus 2.6.x 与 Milvus 原生 BM25；BGE-M3；BAAI/bge-reranker-v2-m3；原生 HTML/CSS/JavaScript。

**Spec:** [2026-09-30-ch04-retrieval-quality-design.md](../specs/2026-09-30-ch04-retrieval-quality-design.md)，用户已答复「已确认」。本计划状态：待用户评审及执行方式选择。

## Global Constraints

- 实际项目根目录：`C:\Users\27497\projects\mewhelp-wt\ch02-tools`；复用已有 linked worktree，不清理既有 Ch03 修改。
- 固定 Milvus 2.5 起原生 BM25，`text` 挂 BM25 函数、内置 `chinese` analyzer；不能用 Python rank-bm25 替代。
- BGE-M3 dense 为 1024 维、COSINE；dense / BM25 各 Top-50，Milvus `hybrid_search` + `RRFRanker(k=60)`，融合最多 50，指定 reranker Top-10。
- 新集合 `knowledge_ch04`；保留旧集合，回填包括旧 `done` 行，完成校验及最后变更补齐后才切换。
- `product_category VARCHAR(128) NULL` 与已有 `category` 分开；过滤白名单仅 `category`、`product_category`、`content_type`、`is_key_clause`。
- 只理解当前原话；不做指代消解、多轮改写；同义词只在检索侧扩展，不产生重复入库问法。
- 知识正文校验前不输出；问题池独立事务先提交；服务异常不冒充证据不足；一次处理只入池一次。
- 来源 DTO 字段及 SSE `sources` payload 与 spec §6 完全一致；chunk ID 及池记录 ID 传字符串。
- 相关性分数不是正确概率；生产阈值只用 20 条 calibration 校准，40 条 test 不用于调参。
- 评估 60 条、5 类各 12、每类易/中/难各 4；calibration 每类 1/1/2，test 每类 3/3/2；语料至少 80 chunk，独立数据库/集合。
- 排序消融使用共同生成策略、不启用生产 rerank 阈值；真实生产拒答与入池另外验收。
- 拒答 Faithfulness 为 N/A；指标异常不填高分；报告同时给回答/评分覆盖率、误拒率和错误数。
- 后端代码 TDD；纯 Prompt / 标注数据以真实标注样例验证替代 TDD；前端按 Vibe Coding 直接实现，无前端 brainstorm/TDD/code review 门槛。
- 每个任务前查询所用 API 的 Context7 官方资料并核对本地版本；遇到固定选型矛盾停止相关工作询问用户，不换方案。
- 每个阶段、任务、review 与 finish 即时追加 `dev-notes/ch04.md` 的四项记录；未知结果不预填。

## Review Focus

1. 正文不变而品类变更，或同步中发生更新：旧索引不能标成已发布或成为证据。Task 1/2/4 覆盖。
2. 归一改掉近似型号、数字、单位、否定或必要条件：必须回退原话，且不能读取历史来补全。Task 3 覆盖。
3. 同轮重复 chunk、超过 JS 安全整数的 ID、首尾排列后编号变化：唯一编号映射仍准确，引用不得越界。Task 5/7 覆盖。
4. 自评/引用无效、问题池提交失败、消息账本提交失败：没有猜测正文泄漏，问题池与账本事务边界可观察。Task 5/7 覆盖。
5. 伪造 section_path、路径穿越或文档符号链接越过根目录：来源读取只能返回配置根目录内的 Markdown。Task 6 覆盖。

---

## 文件归属、执行约定

| 归属 | 文件 | 职责 |
| --- | --- | --- |
| Task 1 | `knowledge/store.py`、`knowledge/ingest.py`、`db/models.py`、`db/repository.py`、新 `knowledge/refusals.py`、`sql/ch04-ddl.sql`、新 `scripts/migrate_ch04_schema.py` | 原文快照、商品品类、拒答表、引用持久化、幂等迁移 |
| Task 2 | `knowledge/vectors.py`、`knowledge/sync.py`、`knowledge/cli.py`、新 `knowledge/filters.py` | 原生索引、过滤表达式、增量/全量同步与索引审计 |
| Task 3 | 新 `knowledge/query.py`、新 `knowledge/prompts.py`、`knowledge/cli.py` 对应子命令 | 单轮归一、保护信息、意图路由与检索侧扩展 |
| Task 4 | `knowledge/retrieval.py`、新 `knowledge/reranking.py` | 四种策略的召回、MySQL 复核与指定模型重排 |
| Task 5 | 新 `knowledge/answering.py`、`knowledge/prompts.py`、`knowledge/cli.py` 对应子命令 | 全轮证据编号、首尾组织、结构化生成/校验、唯一拒答路径 |
| Task 6 | 新 `knowledge/sources.py`、`knowledge/api.py`、`main.py` | chunk / 文档来源读取与来源页面路由 |
| Task 7 | `ch02/service.py`、`events.py`、`schemas.py`、`api.py`、`config.py`、`knowledge/answering.py` 工厂、`knowledge/cli.py` search、`tools/knowledge.py`、`ticket.py`、`infra.py` | 在线接入、可信过滤、sources 出口、无正文泄漏、旧工具回归 |
| Task 8 | 新 `knowledge/evaluation/metrics.py`、`dataset.py`，新 `eval/ch04/corpus.jsonl`、`queries.jsonl`、`README.md` | 指标公式、标注校验与冻结样本 |
| Task 9 | 新 `knowledge/evaluation/runner.py`、`judge.py`、`calibration.py`、`__main__.py` | 隔离评测、judge、阈值校准、逐题/分桶报告 |
| Task 10 | `static/index.html`、新 `static/source.html` | 可点引用、原文查看、一次性前端满意度采集 |
| Task 11 | 新 `scripts/smoke_ch04_acceptance.py`、`README.md`、`.env.example`、实际报告 | 真机迁移/切换、四策略数字、演示与交付 |

所有测试/新文件的具体路径在所属任务列出。跨任务只通过下面 Interfaces 中的契约衔接，不另造相似 DTO。

- PowerShell 在项目根执行：`$pyCh04 = (Resolve-Path '.venv-ch03/Scripts/python.exe').Path`；后文 `& $pyCh04` 均使用该解释器。先记录实际依赖版本，不因 Context7 最新示例自动升级。
- 执行前用 `using-git-worktrees` 确认复用，并保存受影响文件的执行前基线、`git status` 和 diff 到仓库外备份目录；运行默认回归建立基线，不把旧章已有失败归咎于新实现或忽略它。
- 当前许多 Ch03 文件尚未跟踪。首次跟踪被本任务实际改动的文件时，在 dev-notes 明列包含的既有基线；其余 Ch03 文件不顺带提交。已跟踪文件按 hunk 检查暂存差异。
- 每任务最后先追加 dev-notes，再只暂存该任务列出的文件/片段及记录，执行 `git diff --cached --check`，检查 diff 后提交；禁止 `git add .` / `git add -A`。
- 每个后端任务的红灯来自所声明行为缺失，随后最小实现转绿；Prompt/数据与前端例外按各任务声明执行。

### Task 1: 原文快照、商品品类与持久化边界

**Files:** Modify `src/mewhelp/knowledge/store.py`、`src/mewhelp/knowledge/ingest.py`、`src/mewhelp/db/models.py`、`src/mewhelp/db/repository.py`；Create `src/mewhelp/knowledge/refusals.py`、`sql/ch04-ddl.sql`、`scripts/migrate_ch04_schema.py`；Test `tests/test_ch04_storage.py`、`tests/test_ch04_migration.py`，扩展 `tests/test_knowledge_store.py`、`tests/test_knowledge_ingest.py`、`tests/test_db_repository.py`、`tests/test_db_ddl_drift.py`。

**Interfaces:** Consumes `KnowledgeDraft`、`put_chunk(session: Session, draft: KnowledgeDraft) -> KnowledgeChunk`、`TurnMessage`、`append_messages(session: Session, *, conversation_id: int, rows: list[TurnMessage]) -> None`。Produces `KnowledgeDraft.product_category: str | None = None`（追加字段，保持既有位置参数），冻结 `ChunkSnapshot(id: int, text: str, questions: str, answer: str, section_path: str | None, category: str, product_category: str | None, content_type: str | None, is_key_clause: bool, content_hash: str)`，`snapshot_chunk(row: KnowledgeChunk) -> ChunkSnapshot`；`ReasonCode = Literal['no_evidence','low_relevance','insufficient_evidence','invalid_generation','invalid_citation','unsupported_context_size']`；`RefusalInput(original_question: str, source_conversation_id: int | None, entry_point: Literal['chat_stream','agent','cli'], trigger_stage: Literal['retrieval','generation'], reason_code: ReasonCode, reason: str)`；`PoolCommitError(RuntimeError)`；`record_refusal(session_factory: Callable[[], Session], item: RefusalInput) -> str`。`Message` / `TurnMessage` 增加 `citations: list[dict] | None = None`。

- [ ] 写失败测试；SQLite 工厂沿用现有测试模式，构造真实 ORM 行，固定以下断言：
  ```python
  # test_product_category_change_invalidates_snapshot
  assert changed.id == original_id
  assert changed.vectorize_status == "pending" and changed.vector_id is None
  assert snapshot_chunk(changed).content_hash != previous_hash
  # test_refusal_uses_original_question_and_independent_commit
  assert saved.original_question == "HX-210 没到账，今天肯定能到吗？"
  assert saved.source_conversation_id == conversation_id
  # test_final_message_keeps_source_snapshot
  assert final_message.citations[0]["chunk_id"] == "9007199254740993"
  ```
  同组覆盖 unchanged 幂等、空 product_category、header 去除后入库一次、不猜挖掘品类、在线无会话拒绝记录、CLI 可空会话、UTC 时间和强制 commit 异常向调用者传播。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_storage.py tests/test_ch04_migration.py -q`，期望新行为缺失而 FAIL，不能将无关导入错误当红灯证据。
- [ ] 在上述文件实现快照和存储：hash 为固定字段顺序的 UTF-8 canonical JSON SHA-256，覆盖正文、过滤元数据与章节路径；`text` 继续用原有 `embedding_text`。元数据变化也 pending。文档仅解析开头 `<!-- product-category: ... -->`，移除标记后切分。低置信度 ORM 使用命名外键及稳定入口/阶段值；独立 Session commit 返回主键字符串，失败包装为 PoolCommitError 并保留原始异常链。引用随最终消息同事务写入。
- [ ] 实现 `migrate_ch04(engine: Engine) -> None` 于迁移脚本：应用增量 DDL，先 inspect 已有列/表，正确结构重跑无操作，结构不符报错；不改 Ch02/Ch03 权威 DDL。漂移测试比较旧 DDL 加 Ch04 增量后的结构。记录 MySQL DDL 隐式提交、迁移前备份命令及所用 Context7 SQLAlchemy/Pydantic 接口依据。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_storage.py tests/test_ch04_migration.py tests/test_knowledge_store.py tests/test_knowledge_ingest.py tests/test_db_repository.py tests/test_db_ddl_drift.py -q`，期望全 PASS；检查摘要改变、事务失败和迁移重跑均被实际触发。
- [ ] 追加记录并提交本任务列出的文件/片段，message `feat(ch04): add metadata snapshots and refusal persistence`。

### Task 2: Milvus 原生 BM25、可信过滤与可恢复回填

**Files:** Modify `src/mewhelp/knowledge/vectors.py`、`src/mewhelp/knowledge/sync.py`、`src/mewhelp/knowledge/cli.py`；Create `src/mewhelp/knowledge/filters.py`；Test `tests/test_ch04_vectors.py`、`tests/test_ch04_filters.py`、`tests/test_knowledge_sync.py`。

**Interfaces:** Consumes Task 1 `ChunkSnapshot` / `snapshot_chunk`。Produces `SearchFilters(category: str | None = None, product_category: str | None = None, content_type: str | None = None, is_key_clause: bool | None = None)`（拒绝未知字段），`compile_filter(filters: SearchFilters) -> tuple[str, dict]`；`SearchHit(id: int, content_hash: str)`；`Strategy = Literal['dense','bm25','hybrid','hybrid_rerank']`；`HybridMilvusIndex.ensure_collection() -> None`、`upsert(snapshot: ChunkSnapshot, vector: list[float]) -> None`、`search(strategy: Strategy, *, vector: list[float] | None, bm25_query: str, filters: SearchFilters) -> list[SearchHit]`、`delete(ids: list[int]) -> None`、`audit(snapshots: list[ChunkSnapshot]) -> list[dict]`。`MilvusSettings.connect_hybrid(*, collection: str | None = None) -> HybridMilvusIndex`。`sync_pending(session_factory: Callable[[], Session], embed: Callable[[list[str]], list[list[float]]], vectors: HybridMilvusIndex, *, limit: int = 100, row_ids: list[int] | None = None) -> int`；`reindex_all(session_factory: Callable[[], Session], embed: Callable[[list[str]], list[list[float]]], index: HybridMilvusIndex, *, batch_size: int = 100) -> int`。

- [ ] 写失败测试，客户端 spy 只用于验证请求契约，不充当验收数字：
  ```python
  # test_hybrid_uses_two_filtered_native_requests
  assert [request.limit for request in captured_reqs] == [50, 50]
  assert captured_ranker.dict()["params"]["k"] == 60 and captured_limit == 50
  assert captured_fields == ["id", "content_hash"]
  # test_concurrent_metadata_change_is_not_marked_done
  assert row.vectorize_status == "pending"
  # test_full_reindex_includes_previously_done_rows
  assert set(written_ids) == {pending_id, done_id}
  ```
  覆盖原生 BM25 Function / chinese schema、同名不兼容集合拒绝、NULL 品类严格排除、值含引号/反斜杠不扩大过滤、1024 维验证、UTF-8 字节溢出失败、部分 upsert 后重跑及 tombstone 排除。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_vectors.py tests/test_ch04_filters.py tests/test_knowledge_sync.py -q`，期望新契约 FAIL。
- [ ] 查询 Context7 Milvus schema、nullable scalar、BM25 index、`AnnSearchRequest` 参数化 expr 与 `hybrid_search`，核对本地 2.6.17 源码。实现显式 id/vector/text/sparse/四过滤字段/hash schema；text 上 BM25 函数，sparse 的 BM25 索引。VARCHAR 字节上限：text 65535、category 1020、product_category 512、content_type 128、hash 64；拒绝超长而不裁剪。确保 schema/索引兼容检查和实际加载就绪，不仅检查集合存在。
- [ ] 在 `filters.py` 实现 whitelist 和参数化表达式；仅在本地接口已核实支持时使用 expr_params。若不支持，用 JSON 字面量转义值、字段名保持白名单，不接受任意表达式。两个 AnnSearchRequest 共用同一过滤条件；纯策略只发所属请求，hybrid_rerank 在索引层与 hybrid 相同。
- [ ] 在 `sync.py` 用完整快照做并发回填检查；全量按主键分页并可重跑，不将全量重建转换为删除原集合。CLI 增加 `reindex --collection knowledge_ch04`、`audit-index --collection knowledge_ch04`，审计 missing/extra/hash/metadata 差异；现有 `sync`、指定行发布和删除补偿使用同一 snapshot writer。旧 dense 类保留，生产配置切换留给 Task 11。
- [ ] 运行上一条 pytest 命令，期望全 PASS；记录真实 Milvus 集成仍待 Task 11，不能用 spy 结果声称原生检索已验收。
- [ ] 追加记录并提交上述文件/片段，message `feat(ch04): index native BM25 with filtered hybrid retrieval`。

### Task 3: 单轮 Query 理解与保护信息

**Files:** Create `src/mewhelp/knowledge/query.py`、`src/mewhelp/knowledge/prompts.py`、`eval/ch04/query-understanding-samples.jsonl`；Modify `src/mewhelp/knowledge/cli.py` 的 check-query 子命令；Test `tests/test_ch04_query.py`。

**Interfaces:** Consumes 现有 `get_structured_model(schema, *, include_raw=False, **kwargs)`。Produces `QueryUnderstanding(original: str, canonical: str, bm25_query: str, route: Literal['knowledge','business','greeting'], diagnostics: list[str])`；`validate_normalization(original: str, raw: dict) -> QueryUnderstanding`；`async understand_query(question: str, *, model: Any | None = None) -> QueryUnderstanding`。不接收 history 参数；模型依赖可注入。

- [ ] 为校验代码写失败测试：
  ```python
  # test_lost_model_or_negation_falls_back
  result = validate_normalization("HX-210 不支持 65W 吗？", raw_rewrite_to_HX_210S)
  assert result.canonical == "HX-210 不支持 65W 吗？"
  assert result.route == "knowledge" and result.diagnostics
  # test_synonyms_only_expand_bm25
  assert result.canonical == "退换货规则是什么？"
  assert "换新" in result.bm25_query
  ```
  测试近似型号、数值/单位、否定与条件丢失、额外字段/错类型、空输出回退、模型调用异常传播、事实意图不确定进 knowledge；spy 确认只有本轮原话送模型。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_query.py -q`，期望校验/接口缺失 FAIL。
- [ ] 实现保护信息校验与 BM25 query 构造。原话由服务器保留，模型不得改写 original；从原话提取型号/数值单位及否定、限制条件的保护片段，缺失则回退整句。结构化扩展最多 5 个短语、每个最多 32 字符，去重后仅加入一条 BM25 查询；无入库调用。使用 `include_raw=True` 检查解析结果，保留既有供应商与 function_calling 接入。
- [ ] 编写 Query Prompt 与至少 10 个标注样例（型号/口语/同义词/多条件/问候/业务工具/模糊事实）；这是 Prompt/数据步骤，以当前模型真实运行样例验证替代 TDD。保存每例输入、原输出、保护校验后的结果及判定；型号/条件损坏均须回退，业务订单与问候须正确路由。失败改 Prompt 或保护校验后重跑失败样例，不用测试替身冒充 Prompt 验证。
- [ ] 运行上述 pytest 为 PASS；运行 `& $pyCh04 -m mewhelp.knowledge.cli check-query --samples eval/ch04/query-understanding-samples.jsonl`（本任务在 cli.py 增加该子命令）得到逐例结果、失败数 0；真实服务不可用时明确阻塞，不能把样例阶段标完成。
- [ ] 追加记录并提交本任务文件及 cli.py 的该子命令片段，message `feat(ch04): normalize single-turn queries without losing constraints`。

### Task 4: 权威证据复核、四策略与指定重排

**Files:** Modify `src/mewhelp/knowledge/retrieval.py`；Create `src/mewhelp/knowledge/reranking.py`；Test `tests/test_ch04_retrieval.py`、`tests/test_ch04_reranking.py`。

**Interfaces:** Consumes Task 1 snapshot、Task 2 index/filters/Strategy、Task 3 QueryUnderstanding，现有 `embed_texts(texts: list[str]) -> list[list[float]]`。Produces `RankedChunk(chunk: ChunkSnapshot, score: float | None)`；`RetrievalResult(candidates: list[ChunkSnapshot], final: list[RankedChunk])`；`RetrievalRuntime(session_factory: Callable[[], Session], embed: Callable[[list[str]], list[list[float]]], index: HybridMilvusIndex, rerank: Callable[[str, list[ChunkSnapshot]], list[RankedChunk]])`；`retrieve_evidence(runtime: RetrievalRuntime, query: QueryUnderstanding, filters: SearchFilters, *, strategy: Strategy = 'hybrid_rerank') -> RetrievalResult`；`rerank_chunks(question: str, chunks: list[ChunkSnapshot]) -> list[RankedChunk]`（全部候选的排序，调用者截 Top-10）；`reranker_metadata() -> dict`（model_id、revision、max_length、score_transform）；`UnsupportedContextError`（输入超过已核实预算）。

- [ ] 写失败测试：
  ```python
  # test_stale_or_wrong_category_hit_never_becomes_evidence
  assert [item.id for item in result.candidates] == [valid_id]
  # test_reranker_moves_relevant_chunk_into_final_ten
  assert len(result.final) == 10 and result.final[0].chunk.id == formerly_rank_20_id
  # test_dense_and_bm25_ablation_do_not_call_reranker
  assert rerank_calls == 0
  ```
  覆盖 missing/pending/tombstone/hash mismatch/过滤变化剔除、不使用索引正文、空结果、同分主键升序、非 1024 向量、一次缓存加载、并发首次加载、模型错误传播及超长 pair 在推理前失败。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_retrieval.py tests/test_ch04_reranking.py -q`，期望新接口 FAIL。
- [ ] 在 retrieval.py 实现各策略候选取回与 MySQL snapshot 复核；dense 仅嵌入 canonical，BM25 不额外计算 dense；hybrid 共享两路各 50。候选保持检索排名，复核后最多 50；纯三策略取前 10，第四策略指定模型重排取 10；不通过扩大 TopK 绕过用户的固定值。
- [ ] 查询 Context7 CrossEncoder 并核对 Sentence Transformers 3.4.1 的 `default_activation_function` / `activation_fct`；在 reranking.py 使用固定 `BAAI/bge-reranker-v2-m3`，缓存及首次加载锁，统一显式 sigmoid 分数。按已下载模型 config/tokenizer 得出实际可支持输入长度，推理前不截断地检查每个 query/chunk pair；记录 revision、长度、分数变换，加载/推理失败不降级模型。
- [ ] 运行上述 pytest，期望全 PASS；给检索返回保留 candidates/final 两份列表，后续指标不得把精排 10 条冒充候选 50 条。
- [ ] 追加记录并提交本任务文件，message `feat(ch04): rerank verified hybrid evidence with bge v2 m3`。

### Task 5: 有据生成、统一引用与唯一拒答路径

**Files:** Create `src/mewhelp/knowledge/answering.py`、`eval/ch04/answering-samples.jsonl`；Modify `src/mewhelp/knowledge/prompts.py`、`src/mewhelp/knowledge/cli.py` 的 check-answer 子命令；Test `tests/test_ch04_answering.py`。

**Interfaces:** Consumes Task 1 `record_refusal` / RefusalInput / PoolCommitError、Task 3 QueryUnderstanding、Task 4 RetrievalRuntime / RetrievalResult / UnsupportedContextError / `reranker_metadata`。Produces `SourceDTO(number: int, chunk_id: str, questions: str, answer: str, section_path: str, category: str, product_category: str | None, content_hash: str, source_url: str)`；`AnswerAssessment(answerable: bool, reason: str, answer: str, citation_numbers: list[int])`；`QuestionContext(original_question: str, conversation_id: int | None, entry_point: Literal['chat_stream','agent','cli'])`；`AnswerResult(answer: str, sources: list[SourceDTO], refused: bool, low_confidence_question_id: str | None, retrieval: RetrievalResult)`；`RagRuntime(retrieval: RetrievalRuntime, generate: Callable[[list[BaseMessage]], Awaitable[AnswerAssessment | None]], session_factory: Callable[[], Session], relevance_threshold: float, context_budget: int)`；`async generate_assessment(messages: list[BaseMessage], *, model: Any | None = None) -> AnswerAssessment | None`；`load_relevance_threshold(path: Path, expected_model_metadata: dict) -> float`；`layout_indices(count: int) -> list[int]`；`async answer_question(runtime: RagRuntime, query: QueryUnderstanding, *, filters: SearchFilters, context: QuestionContext, strategy: Strategy = 'hybrid_rerank', apply_relevance_gate: bool = True, record_pool: bool = True, evidence: RetrievalResult | None = None) -> AnswerResult`。evidence 仅接受本轮可信核心的复核结果，省去工具路径重复检索；None 时内部检索。

- [ ] 写失败测试：
  ```python
  def test_evidence_layout_preserves_rank_numbers():
      assert layout_indices(10) == [1, 3, 5, 7, 9, 10, 8, 6, 4, 2]
      assert layout_indices(3) == [1, 3, 2]
  # test_invalid_citation_refuses_and_records_once
  assert result.refused and "猜测的答案" not in result.answer
  assert recorder_calls == 1 and saved.reason_code == "invalid_citation"
  # test_pool_failure_does_not_return_successful_refusal
  with pytest.raises(PoolCommitError):
      await answer_question(runtime, query, filters=filters, context=context)
  ```
  覆盖重复 chunk 只编号一次、BIGINT 字符串、空召回/低相关性/自评不足/解析失败/空答案/编号声明不一致、无引用事实、长证据超预算、服务故障不入池及来源 URL `/kb/source/{id}`。非文档缺章节展示真实类型/主题/问题路径。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_answering.py -q`，期望新接口 FAIL。
- [ ] 实现全轮去重编号与交替首尾排列，布局算法是奇数名次升序后偶数名次降序；编号先于布局产生。所有正文都来自 snapshot，完整知识上下文不经 2000 字符截断。预算计入 Prompt / 问题 / 证据，超预算映射 unsupported_context_size；不靠裁剪通过预算。
- [ ] 实现一次结构化生成、自评/引用校验和统一拒答出口。无证据或低最高 rerank 分数在生成前拒答；模型返回 false 或无效结构/引用在生成后拒答。所有正常拒答仅在这一出口调用 record_refusal 一次，提交完成才创建可返回结果。provider/index 异常原样抛出；超长 pair 的 UnsupportedContextError 进入统一 unsupported_context_size 拒答，空 retrieval 仍有明确结果。评估显式 `apply_relevance_gate=False, record_pool=False`，绝不偷偷追加 reranker。阈值读取器验证 JSON 的 threshold、model_metadata、corpus_hash、query_hash 与有限数值，metadata 必须和当前模型一致；尚未校准不得用猜测门槛。
- [ ] 编写 System/生成 Prompt，明列禁止到账/送达/审批承诺、虚构政策参数、来源指令注入及把分数当概率。以至少 10 条标注样例真实调用验证完整条件、自评不足、近似型号、证据冲突、退款到账承诺、文档注入、正确引用；保留逐例判定。这一步按用户要求用样例验证代替 Prompt TDD。
- [ ] 运行上述 pytest 为 PASS，并运行 `& $pyCh04 -m mewhelp.knowledge.cli check-answer --samples eval/ch04/answering-samples.jsonl`（本任务在 cli.py 增加该子命令），输出逐例通过且失败数 0；同时用生成输出故意含无效编号证明校验拒答。
- [ ] 追加记录并提交本任务文件及 cli.py 对应片段，message `feat(ch04): validate cited answers and persist explicit refusals`。

### Task 6: 来源原文接口与文档定位边界

**Files:** Create `src/mewhelp/knowledge/sources.py`；Modify `src/mewhelp/knowledge/api.py`、`src/mewhelp/main.py`；Test `tests/test_ch04_sources.py`、`tests/test_kb_api.py`、`tests/test_main.py`。页面内容由 Task 10 负责。

**Interfaces:** Consumes Task 1 snapshot、Task 5 SourceDTO。Produces `read_published_chunk(session_factory: Callable[[], Session], chunk_id: int) -> ChunkSnapshot | None`；`DocumentSource(filename: str, markdown: str, section_path: str)`；`read_document_source(chunk: ChunkSnapshot, root: Path) -> DocumentSource | None`；`KbRuntime` 追加 `docs_root: Path`（默认配置根目录，可注入）；GET `/api/kb/chunks/{chunk_id}` 返回当前 snapshot 的来源字段（chunk_id 字符串、不含回答内的 number）；GET `/api/kb/chunks/{chunk_id}/document` 返回 DocumentSource；GET `/kb/source/{chunk_id}` 提供 Task 10 的 source.html。配置文档根目录用 `KNOWLEDGE_DOCS_ROOT`，默认 `knowledge-docs`。

- [ ] 写失败测试：
  ```python
  # test_source_chunk_id_is_a_string
  assert response.json()["chunk_id"] == "9007199254740993"
  # test_document_path_outside_root_is_rejected
  assert read_document_source(traversal_chunk, root) is None
  assert read_document_source(symlink_outside_chunk, root) is None
  # test_unpublished_source_is_not_exposed
  assert response.status_code == 404
  ```
  覆盖合法 corpus hash/相对 Markdown 文件及章节、伪造 hash、绝对路径、`..`、Windows 大小写路径、非 .md、missing/deleting/pending、FAQ 无文档入口和不接收任意 file 参数。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_sources.py tests/test_kb_api.py -q`，期望新路由/边界 FAIL。
- [ ] 查询 Context7 FastAPI 路由/依赖、SQLAlchemy 读取接口；实现 root 规范化/hash 与入库规则一致、resolve 后 containment 检查、类型和 corpus 前缀验证，再读取 UTF-8 原文。只接受主键定位，错误来源返回 404 而不回显磁盘路径。人工录入请求/响应增加可选 product_category，旧条目仍可读。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_sources.py tests/test_kb_api.py tests/test_main.py -q`，期望全 PASS；source 页面路由可在 Task 10 增加页面后完成实际页面验证，不以空页面代替完成。
- [ ] 追加记录并提交本任务文件，message `feat(ch04): expose authoritative chunk and guarded document sources`。

### Task 7: 聊天/JSON 接入与知识正文输出门槛

**Files:** Modify `src/mewhelp/ch02/service.py`、`src/mewhelp/ch02/events.py`、`src/mewhelp/ch02/schemas.py`、`src/mewhelp/ch02/api.py`、`src/mewhelp/config.py`、`src/mewhelp/knowledge/answering.py` 的工厂、`src/mewhelp/knowledge/cli.py` 的 search、`src/mewhelp/tools/knowledge.py`、`src/mewhelp/tools/ticket.py`、`src/mewhelp/tools/infra.py`；Test `tests/test_ch04_chat.py`，扩展 `tests/test_ch02_service_prepare.py`、`tests/test_ch02_service_run.py`、`tests/test_ch02_service_stream.py`、`tests/test_ch02_api_agent.py`、`tests/test_ch02_api_chat.py`、`tests/test_tool_knowledge.py`、`tests/test_tool_infra.py`、`tests/test_tool_ticket.py`、`tests/test_config.py`。

**Interfaces:** Consumes Task 2 SearchFilters、Task 3 `understand_query`、Task 5 `answer_question` / AnswerResult。Produces `SourcesEvent(sources: list[SourceDTO], refused: bool, low_confidence_question_id: str | None)`；`AgentTurnResult` 增加同名三字段；`stream_agent_turn(session_factory: Callable[[], Session], *, session_id: str | None, user_id: str, message: str, filters: SearchFilters | None = None) -> AsyncIterator[AgentEvent]`、`async run_agent_turn` 同参返回 AgentTurnResult。`build_turn_rows(prepared: PreparedTurn, *, answer: str, citations: list[dict] | None = None) -> list[TurnMessage]`、`persist_turn(session_factory: Callable[[], Session], prepared: PreparedTurn, *, answer: str, citations: list[dict] | None = None) -> None`。`build_knowledge_tools(session_factory: Callable[[], Session], *, embed=embed_texts, vectors=None, context: QuestionContext | None = None, filters: SearchFilters | None = None, rag_runtime: RagRuntime | None = None, query: QueryUnderstanding | None = None) -> list[BaseTool]` 保持模型可见 schema 仅 `keyword: str`。`ToolResult` 追加 `artifact: RetrievalResult | None = None`。

运行时工厂归本任务：`get_rag_runtime(session_factory: Callable[[], Session], *, calibration_path: Path, collection: str | None = None) -> RagRuntime` 放在 answering.py（列入本任务修改范围）；组装 Task 2/4/5 依赖并调用 Task 5 阈值读取器，不在业务工具或 HTTP 复制构造。配置 `RAG_CALIBRATION_PATH` 为可空路径，知识分支缺失时明确运行错误；`RAG_CONTEXT_BUDGET` 必须为正整数并在部署时设置经过核实的预算；非知识分支不触发模型加载或阈值读取。用同样的可注入工厂支持隔离 HTTP 验收。

- [ ] 写失败测试：
  ```python
  # test_knowledge_body_waits_for_verified_sources
  assert event_names == ["session", "tool", "tool", "sources", "token", "done"]
  assert "未经校验的前言" not in emitted_text
  # test_refusal_pool_survives_message_ledger_failure
  assert pool_row_count == 1 and sources_event.refused is True
  # test_trusted_filters_cannot_be_changed_by_tool_arguments
  assert observed_filters.product_category == "耳机"
  ```
  覆盖 RAG 不调用普通无证据生成、模型未选 query_faq 也不能旁路知识事实、原话与工具 keyword 不同仍记录原话、同轮多次 query_faq 去重编号、无效引用不输出原答案、池失败走 error 无 done、服务错误无池记录、引用/最终回答同事务、超过 2000 字符证据保全、问候逐块流式和业务工具原功能。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_chat.py -q`，期望新协议/输出边界 FAIL。
- [ ] 在 service.py 共用会话身份/锁与 Query 理解后路由：knowledge 直接调用共享核心，发送 query_faq 工具进度，不让普通 turn1/收敛生成知识正文；greeting 保留流式；business 使用已有业务工具。business 的事实 preamble 先缓冲，若出现 query_faq 或未获得业务工具证据则回到当前原话的知识核心；通过可信业务分支后才交付正文。没有第二轮 Agent 工具循环、没有历史 query 改写。
- [ ] 将 query_faq 适配到共享 retrieve_evidence 并注入可信 QuestionContext/query/filters；模型 keyword 不能替代原始问题或扩大过滤。同轮工具闭包缓存原话检索结果，返回 RetrievalResult artifact，不在单个工具里生成/编号/入池；最终仅一次 answer_question 使用该 evidence，避免多次 query_faq 重复入池。infra 保全 artifact 的完整证据，其他工具字符限制不变。CLI search 改为 understand_query → answer_question，entry_point=cli；支持四个结构化过滤参数，不再用旧工具文字作为最终回答。
- [ ] 查询 Context7 FastAPI 原生 SSE / LangChain tool artifact 并核对本地接口；API 请求加入可选 filters，JSON 增加 sources/refused/池ID；SSE 最终 token 前发送 sources，其 payload 仅 spec 的三字段。完整知识答案校验后交付 token，无模拟打字 sleep；错误仍走已有 error。SourcesEvent 明确成帧，不落入 else/done 分支。更新旧测试中与新知识输出边界冲突的断言，保留问候/订单/工单/锁/错误回归。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_chat.py tests/test_ch02_service_prepare.py tests/test_ch02_service_run.py tests/test_ch02_service_stream.py tests/test_ch02_api_agent.py tests/test_ch02_api_chat.py tests/test_tool_knowledge.py tests/test_tool_infra.py tests/test_tool_ticket.py -q`，期望全 PASS，来源与引用快照序列化保持 BIGINT 字符串。
- [ ] 追加记录并提交本任务文件/片段，message `feat(ch04): gate knowledge responses and stream citation sources`。

### Task 8: 指标计算与冻结标注集

**Files:** Create `src/mewhelp/knowledge/evaluation/__init__.py`、`src/mewhelp/knowledge/evaluation/metrics.py`、`src/mewhelp/knowledge/evaluation/dataset.py`、`eval/ch04/corpus.jsonl`、`eval/ch04/queries.jsonl`、`eval/ch04/README.md`；Test `tests/test_ch04_metrics.py`、`tests/test_ch04_dataset.py`。

**Interfaces:** Consumes Task 1 `source_id(source_key: str) -> int`、Task 2 SearchFilters。Produces `recall_at_k(ranked: list[int], relevant: set[int], k: int) -> float | None`、`reciprocal_rank_at_k(ranked: list[int], relevant: set[int], k: int) -> float | None`、`faithfulness(supported: list[bool]) -> float | None`；`EvalCase(id: str, question: str, query_type: str, difficulty: str, split: str, filters: SearchFilters, relevant_chunk_ids: set[int], reference_answer: str, key_facts: list[str], should_refuse: bool, label_basis: str)`；`load_dataset(corpus_path: Path, queries_path: Path) -> tuple[list[KnowledgeDraft], list[EvalCase]]`。JSONL 的相关 ID 用字符串，读入后严格转 int。

- [ ] 指标/数据校验代码写失败测试：
  ```python
  def test_metrics_use_actual_relevant_sets():
      assert recall_at_k([9, 2, 3], {2, 3}, 2) == 0.5
      assert reciprocal_rank_at_k([9, 2, 3], {2, 3}, 3) == 0.5
      assert recall_at_k([9], {2}, 50) == 0.0
      assert recall_at_k([9], set(), 50) is None
      assert faithfulness([True, False, True]) == pytest.approx(2 / 3)
      assert faithfulness([]) is None
  ```
  校验测试明确拒绝相关 ID 不在 corpus、重复 case/source ID、未知类型/难度/split、无答案问题却有 GT、计数或划分不符合 60/20/40、重复检索 ID 虚增 Recall。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_metrics.py tests/test_ch04_dataset.py -q`，期望代码缺失 FAIL。
- [ ] 实现指标与数据校验：无 GT 的检索指标 None，有 GT 未命中 0，MRR 只算首个相关块倒数排名；拒答无事实声明 Faithfulness None。相同 ID 去重不能提高分母/分子。校验 corpus 至少 80、每类 12 且每难度 4、20/40 固定划分与类别难度交叉计数。
- [ ] 人工编写明确为示例业务的至少 80 个 chunk 和 60 个问题，保持完整可核验原文，不生成生产商品事实；相近型号、同义词/口语、多条件、跨品类与无答案干扰均覆盖。采用稳定 `ch04-eval:<chunk-key>` 生成 ID，逐例由原文标相关块/关键事实/标注依据，冻结后不按检索结果倒填 GT。
- [ ] 数据步骤以标注验证替代 TDD：`& $pyCh04 -m mewhelp.knowledge.evaluation.dataset --validate eval/ch04` 输出 corpus≥80、cases=60、calibration=20、test=40、相关 ID/结构检查通过；逐条人工核查型号/数值/过滤必要条件与原文事实一致，保存标注审核清单及 SHA-256，结构校验不代替事实审核。运行本任务 pytest 命令为 PASS；不在此宣称四策略数字已经完成。
- [ ] 追加记录并提交本任务文件，message `feat(ch04): define retrieval metrics and graded ground truth dataset`。

### Task 9: 真实四策略评估、Faithfulness judge 与校准

**Files:** Create `src/mewhelp/knowledge/evaluation/runner.py`、`src/mewhelp/knowledge/evaluation/judge.py`、`src/mewhelp/knowledge/evaluation/calibration.py`、`src/mewhelp/knowledge/evaluation/__main__.py`；Test `tests/test_ch04_eval_runner.py`、`tests/test_ch04_calibration.py`、`tests/test_ch04_judge.py`；Modify `eval/ch04/README.md`。

**Interfaces:** Consumes Tasks 1–5 的共享核心及 Task 8 EvalCase/metrics。Produces `JudgeClaim(text: str, supported: bool, evidence_numbers: list[int], rationale: str)`、`JudgeResult(claims: list[JudgeClaim], score: float | None, error: str | None)`；`async judge_answer(question: str, answer: str, sources: list[SourceDTO], *, model: Any | None = None) -> JudgeResult`；`CalibrationResult(threshold: float, model_metadata: dict, corpus_hash: str, query_hash: str, false_allow: int, false_refuse: int)`；`calibrate_threshold(cases: list[EvalCase], top_scores: dict[str, float | None], model_metadata: dict, *, corpus_hash: str, query_hash: str) -> CalibrationResult`；`async run_comparison(corpus: list[KnowledgeDraft], cases: list[EvalCase], *, workdir: Path, collection: str, run_id: str) -> Path`，返回 Markdown 报告路径。

- [ ] 为 runner/judge/校准代码写失败测试：
  ```python
  # test_ablations_share_query_but_do_not_share_rerank_gate
  assert strategies == ["dense", "bm25", "hybrid", "hybrid_rerank"]
  assert normalize_calls == len(cases)
  assert production_gate_calls == 0 and online_pool_writes == 0
  # test_test_split_is_rejected_for_threshold_tuning
  with pytest.raises(ValueError):
      calibrate_threshold(test_cases, scores, metadata, corpus_hash="c", query_hash="q")
  # test_judge_parse_failure_remains_an_error
  assert result.score is None and result.error
  ```
  覆盖空答案拒答 N/A、整体/type/difficulty/交叉桶的 N、缺少评分的覆盖率、召回未命中 0、真实错误不当拒答、未冻结运行清单不能标成功、模型版本/分数变换不符时拒绝加载阈值，以及 run_id 复跑不改生产集合。
- [ ] 运行 `& $pyCh04 -m pytest tests/test_ch04_eval_runner.py tests/test_ch04_calibration.py tests/test_ch04_judge.py -q`，期望新代码 FAIL。
- [ ] 实现隔离 runner：原文在 workdir 下独立 SQLite 文件，Milvus 集合名强制 `ch04_eval_<run_id>` 且不允许生产名称；同样 ingest/sync、实际 BGE 向量、实际 native BM25/hybrid/rerank。每题归一只调用一次并缓存，四策略共享 query/过滤/生成自评；只跑 test 40 条作正式对比，20 条 calibration 单独生成分数/校准产物。禁止评估写在线池。
- [ ] 实现阈值候选扫描：候选为有限观察分数以及 `math.nextafter(max_score, math.inf)` 的全部拒绝阈值；空证据永不放行。先最小化无答案误放，再最大化可答放行数，仍并列取更高阈值。输出可答覆盖/误拒与无答案误放，0 误放导致全拒也须如实报告；不靠 test 优化。calibration JSON 符合 Task 5 读取器字段/metadata 契约，没有校准产物不使用主观默认门槛。
- [ ] 写独立 judge Prompt，通过当前模型 temperature=0 结构化拆解事实声明、逐条判断是否被实际 sources 支持；保存声明、支持编号/理由及解析异常。参考答案用于正确性/标注分析，Faithfulness 的支持判定只看实际提供上下文；拒答无事实声明不计高分。Prompt 用标注支持/无支持/部分支持/无事实的样例真实验证，不以测试 fake 替代。
- [ ] 实现 CLI 的 prepare/calibrate/compare 子命令，三者均接受 `--dataset`、`--workdir`、`--run-id`。例如 `& $pyCh04 -m mewhelp.knowledge.evaluation compare --dataset eval/ch04 --workdir artifacts/ch04/ch04_20260930_01 --run-id ch04_20260930_01`；先以同样参数跑 prepare/calibrate。compare 输出逐题 `cases.jsonl`、`summary.json`、`report.md`，包括 candidate Recall@50/MRR@50、final Recall@5/10/MRR@10、Faithfulness、回答/有效评分覆盖率、正确拒答/误拒/错误数、桶 N、耗时、失败样例、语料/问题/Prompt hash、实际依赖/模型/TopK/RRF/过滤/阈值口径。报告明确消融不启用生产阈值。
- [ ] 运行代码 pytest 为 PASS；在 Task 11 就绪真实依赖后跑 prepare/calibrate/compare，不用 fake 分数填交付报告。追加记录并提交本任务代码/README，message `feat(ch04): run isolated four-strategy evaluations and calibration`。

### Task 10: 前端 Vibe Coding——引用与一次性反馈

**Files:** Modify `src/mewhelp/static/index.html`；Create `src/mewhelp/static/source.html`。不增加前端 TDD 或 code review 流程。

**Interfaces:** Consumes Task 7 SSE sources payload 与 Task 6 来源接口；前端本地反馈 payload 固定 `{answer_id, session_id, choice: 'up'|'down', created_at}`，不调用后端。

- [ ] 直接修改现有气泡渲染：sources 事件先保存本轮快照，正文里的已映射 `[N]` 用 DOM 元素呈现可点击编号；未知编号保留普通文本。来源弹层显示原文快照、章节路径、分类/品类和原文入口；本地聊天历史一并保存 sources/answer_id/反馈状态，读取旧历史兼容缺字段。来源/答案都用 textContent 或安全 DOM 构建，不将知识原文当 HTML。
- [ ] 直接实现 source.html：从路径取 chunk ID，加载当前 chunk JSON，文档来源再加载 document JSON，展示完整原文并定位章节；与引用快照 hash 不同在聊天弹层说明当前内容已更新，历史引用仍展示快照。FAQ 等非文档展示真实知识来源路径；404/读取失败清楚提示。
- [ ] 在完成回答左下角加入 👍/👎；点击即点亮所选、显示「已反馈」、同时禁用两按钮。拒答也显示反馈，error/中断不挂完整回答反馈。answer_id 由前端生成并随本地聊天记录保存；记录 payload 和已锁状态，存储失败当前页面仍锁定；不发网络反馈请求。
- [ ] 用实际浏览器验证：点击 `[1]` 看快照/路径、跳文档原文与章节、来源更新提示、BIGINT 不变、恶意 HTML 显示文本、两反馈选项各测一次、第二次无法改选、刷新后已反馈状态保留、存储不可用仍锁、拒答可反馈、SSE error 不显示反馈。保存实际观察与截图路径，效果不符直接调整后再验证。
- [ ] 追加记录并只提交这两个前端文件及记录，message `feat(ch04): add clickable citations and locked local feedback`；后续后端 code review 明确排除本任务前端文件。

### Task 11: 真实迁移、验收数字、演示与收尾

**Files:** Create `scripts/smoke_ch04_acceptance.py`、`tests/test_ch04_acceptance_script.py`；Modify `README.md`、`.env.example`；生成 `artifacts/ch04/<run_id>/` 下报告与校准 JSON（只提交确认不含密钥/个人数据的结果）；所有阶段即时更新 `dev-notes/ch04.md`。

**Interfaces:** Consumes 全部已完成能力；验收脚本 `main(base_url: str, *, report_dir: Path, session_factory: Callable[[], Session], demo_docs: Path | None = None) -> int`，通过真实 HTTP 及对应原文/问题池数据库验证，失败非 0；默认验证生产 MySQL，CLI `--ledger-db` 显式指向隔离实例的 acceptance.sqlite；demo_docs 的示例数据只能进入隔离验收实例，不混入真实商品知识。

该脚本另提供 `serve_acceptance(*, workdir: Path, collection: str, port: int = 8001) -> None`：独立进程使用现有 FastAPI app，依赖覆盖将会话/问题池/原文指向 workdir 的独立 acceptance.sqlite（开启外键），知识核心指向独立真实 `ch04_eval_acceptance_<run_id>` 与冻结语料；文档跳转另外入库隔离样例目录中的 Markdown，使用真实 ingest/sync，不能改动正式对比的语料/集合。不改生产 `.env` 或在线工厂，校准 JSON 与真实模型共用。浏览器及具体型号 HTTP 演示可用此隔离实例，生产 8000 另外验证已有真实知识与未知问题入池；报告标明测试入口及数据库边界。

- [ ] 为验收脚本的断言/错误处理写 `tests/test_ch04_acceptance_script.py` 的失败用例：缺 sources、citation ID 非字符串、无答案未入池、池原因/会话错误都非 0；运行该测试见 FAIL 后实现脚本，再见 PASS。HTTP 验收输入/证据由冻结样例决定，不能临时造“肯定会通过”的输出。
- [ ] 用 `verification-before-completion` 执行默认回归 `& $pyCh04 -m pytest -q`，及 `& $pyCh04 -m ruff check src tests scripts/smoke_ch04_acceptance.py scripts/migrate_ch04_schema.py`；保留实际结果。新失败先触发 systematic-debugging，确定原因后修复并重跑对应检查，不预报通过。
- [ ] 检查 Docker 当前状态，必要时后台启动已安装 Docker Desktop；执行 `docker compose up -d mysql` 与 `docker compose -f milvus-compose.yml up -d`，确认实际 MySQL/Milvus 版本和健康。下载/加载指定 BGE 模型并记录资源、revision/参数；服务或模型不可用就报告实际阻塞，不替换成 Lite/BM25 Python/其他 reranker。验收服务器使用前查询 Context7 Uvicorn/FastAPI 的启动和依赖覆盖接口。
- [ ] 备份真实 MySQL 后执行 `& $pyCh04 scripts/migrate_ch04_schema.py`；`& $pyCh04 -m mewhelp.knowledge.cli reindex --collection knowledge_ch04`，完成全量回填；`audit-index` 输出 missing/extra/hash/metadata 差异均 0。查近似型号/品类样例证明原生 BM25 能辨别，真实语料缺少型号时在隔离验收实例使用标注示例，报告区分两者。
- [ ] 运行真实 `prepare`、`calibrate`、`compare` CLI，保存模型实际计算的 40×4 逐题结果及报告。核对四策略都有数字、空 GT/N/A 分母正确、五类与难度桶 N 正确、有 judge 错误时显示覆盖率；如实记录结果，不能预设 hybrid_rerank 一定优于其他策略。
- [ ] 切换前短暂停止知识发布/挖掘入口，补最后 pending/deleting 并审计；设置 `MILVUS_COLLECTION=knowledge_ch04`、`RAG_CALIBRATION_PATH=<实际校准JSON>`、文档根目录/实际模型预算，重启新版应用后恢复发布。README 给新旧应用/集合成对回退命令，使用执行前保存的旧代码基线，不删除旧集合。`.env.example` 只公开配置键与示例，不输出实际密钥。
- [ ] 运行 `& $pyCh04 scripts/smoke_ch04_acceptance.py --base-url http://127.0.0.1:8000 --report-dir artifacts/ch04/<run_id>` 验证线上真实知识/未知问题；再对 8001 加 `--ledger-db <workdir>/acceptance.sqlite` 验证具体型号问答及原生 BM25 命中、带品类过滤、JSON/SSE 引用对应原文、缺知识明确拒答且池记录原话/会话/入口/原因/时间、负面承诺和生成自评不足。检查 sources 在知识正文前、异常无猜测泄漏；前端验证引用/反馈见 Task 10。
- [ ] 依据用户选择的执行方式完成后端/评估 code review（排除 Task 10 前端文件），应用 `requesting-code-review`；收到建议先按 `receiving-code-review` 验证再处理，逐条记录结论/返工。复跑受影响测试，最后运行默认回归与实际验收；不重复无变化的昂贵全评估，若检索/Prompt/语料/模型改变则新 run_id 重跑报告。
- [ ] README 写可复制的启动、迁移、回填、查询、四策略评估和 HTTP 演示命令及报告位置；追加任务完成/review 结论，检查暂存范围后提交本任务文件/结果，message `docs(ch04): deliver verified retrieval evaluation and demo commands`。
- [ ] 触发 `finishing-a-development-branch`，结合用户已有集成授权决定保留/合并/PR；不得把功能完成等同自动推送。仅在确实完成后追加 finish 四项记录，最终交付功能演示命令、实际测试结果、报告路径与 `dev-notes/ch04.md`。

## 计划自审与交接

执行前由本会话 inline 自审，禁止把计划自审委派给子代理：逐节对照 spec 覆盖、检查步骤是否可操作、核对跨任务签名、确认五项 Review Focus 各有测试、比较计划/spec 篇幅。最终自审结论与发现的修正即时写 dev-notes。

用户审核本计划并选择执行方式后才开始实现：Subagent-driven 每任务新实现者/评审者并整体验收；Native 在本会话逐任务实现，最后独立后端评审。用户对 spec 的确认不替代此计划评审；前端例外继续按原要求直接进行，不增加前端审批。
