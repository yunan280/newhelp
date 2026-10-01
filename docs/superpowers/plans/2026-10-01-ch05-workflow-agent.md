# Ch05 Workflow + ReAct Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先演示裸工具循环，再交付七意图四出口、前置知识闸门、可持久化的 LangGraph ReAct 客服及用户自选的人工/工单按钮。

**Architecture:** 一个显式 StateGraph 负责固定分流，Agent 决策与只读工具节点之间循环，最终答复由独立节点真实流式输出。官方 AsyncSqliteSaver 是 Ch05 会话状态来源；MySQL 保留业务账本、问题池和工单。界面只展示建议，用户确认建单后才通过独立 HTTP 接口复用原工单工具。

**Tech Stack:** Python 3.11+、FastAPI 0.141.1 原生 SSE、langchain-core 1.6.5 / langchain-openai 1.6.6、LangGraph 1.2.12、langgraph-checkpoint-sqlite 3.1.1、MySQL/SQLAlchemy 2.1.1、既有 Milvus 2.6.x / BGE-M3 / 原生 BM25 / RRF / reranker、HTML/CSS/JavaScript。

**Spec:** [2026-10-01-ch05-workflow-agent-design.md](../specs/2026-10-01-ch05-workflow-agent-design.md)，用户已以「确认」批准。

## Global Constraints

- 应用工作目录：`C:/Users/27497/projects/mewhelp-wt/ch02-tools`；当前 linked worktree 已满足隔离条件。实现基线 `a2a98a8`，设计批准提交 `e461783`；不操作外层仓库、不覆盖 Ch03 未跟踪文件。
- 固定技术：LangGraph 的图、State 和官方 checkpointer；现有 FastAPI、LangChain 模型适配、MySQL/SQLAlchemy、Milvus 检索；原生 HTML/CSS/JavaScript 页面。
- 官方 `AsyncSqliteSaver`，单进程/单实例；本章不新增 PostgreSQL 或多实例会话锁。不能用内存 saver 冒充持久化。
- 指代消解只返回原问题；意图只能是物流、订单、商品咨询、退款退货、售后、投诉、闲聊。分类 JSON 不得包含模型控制的节点名。
- 商品咨询和退款退货强制先检索、再过 gate、再进 Agent；物流/订单/售后不预检索、不走知识 gate；投诉不进 Agent；分流是代码枚举表。
- 确定的问候/感谢/身份问法本地保守匹配，零模型调用；其他输入先分类，判为闲聊时零 Agent/答复生成调用。
- 业务工具复用 Ch02，检索复用 Ch03/04；主力 Agent 的可执行工具集不含 `create_ticket`，后端不能自动转人工或建单。
- 转人工只在前端模拟；建工单必须单独确认；两个按钮互不绑定，不点击也不阻塞下一轮。
- 不做正式指代消解、正式意图模型、上下文策略升级、MCP 业务接入、知识飞轮发布、Langfuse 接入或真人客服集成。
- 代码走 RED → GREEN → refactor；纯 Prompt/数据先冻结标注，再真实调用保存逐例结果，不写镜像文案测试。
- 每任务开始前核对涉及的新库 API：Context7 resolve/query → 对照实际安装版本/签名 → 动手；技术矛盾必须向用户说明，不自行替换。
- 每个阶段即时追加 `dev-notes/ch05.md` 的四项记录；每任务只提交本任务文件及记录，禁止 `git add .` 吞入既有 Ch03 草稿。

## Review Focus

- 「你好，订单 1001 能退吗」不能因问候前缀或订单号而跳过知识路径；Task 2 的快速匹配代码测试及混合问题真实标注负责。
- 上轮高分证据/投诉建议不能污染下一轮物流或闲聊；Task 5 的跨轮 State 重置、保留历史建议但不重复展示测试负责。
- 同一建议的建单请求重发，或 MySQL 已提交但 checkpoint 记回执失败，只能产生一张工单；Task 6 的持久幂等键及故障注入测试负责。
- 用户忽略建议继续聊天、页面恢复历史、点击人工后再点工单，都不应自动触发其他动作；Task 7 的页面交互测试负责。
- 第二次工具调用必须在第一次结果之后，同轮两工具不能冒充多步；Task 4 的脚本模型测试与 Task 8 的真实轨迹验收负责。

## 文件与接口分工

所有下述相对路径均以应用目录为根。执行者须读 spec 和整份计划，不能只看任务标题。

| 文件 | 责任 |
| --- | --- |
| `src/mewhelp/ch05/bare.py` | 原生 HTTP 调 LLM 的裸循环及 CLI，不导入 LangGraph/预制 Agent |
| `src/mewhelp/ch05/limits.py`, `config.py` | 步数、工具次数、模型用量、截止时间与本章配置 |
| `src/mewhelp/ch05/schemas.py`, `state.py` | 请求/结果/建议/轨迹 DTO、可序列化 State、运行依赖上下文 |
| `src/mewhelp/ch05/intent.py`, `prompts.py` | 七分类、保守闲聊快速匹配、Agent 决策及最终答复 Prompt |
| `src/mewhelp/ch05/evidence.py` | 复用检索、序列化证据、前置 gate 与独立拒答记录 |
| `src/mewhelp/ch05/agent.py` | 只读工具绑定、ReAct 决策/执行/答复节点、停止逻辑 |
| `src/mewhelp/ch05/workflow.py`, `runtime.py`, `service.py` | 图边、官方 checkpointer 生命周期、会话串行化/历史与日志 |
| `src/mewhelp/ch05/api.py`, `events.py` | Ch05 JSON/SSE/确认建单传输边界、工具 call_id 和 round |
| `src/mewhelp/ch05/actions.py` | 验证建议归属并复用 Ch02 工单工具，不实现新业务工具 |
| `src/mewhelp/ch05/evaluation.py`, `eval/ch05/*` | 冻结标注和真实分类/决策 Prompt 评估 |
| `tests/ch05/*`, `tests/page-smoke.js` | 图与业务边界 TDD、独立按钮及 SSE 页面回归 |
| `scripts/smoke_ch05_acceptance.py`, `scripts/migrate_ch05_schema.py` | 真实验收和幂等字段增量迁移 |

运行依赖使用 `WorkflowContext`，包含 session 工厂、模型工厂、已有 RAG runtime 工厂、`AgentLimits`；不能放进 checkpoint。`WorkflowState.messages` 只累积已完成的 user/assistant 历史；活跃本轮使用独立 `agent_messages`。

固定默认值：4 次 Agent 决策、8 次工具调用、单次最终输出 1024 tokens、决策输出 256 tokens、分类输出 128 tokens、整轮模型总量 64000 tokens、整轮截止 180 秒、单次模型请求 30 秒且 `max_retries=0`。分类/决策/答复均计入；缺真实 usage 时用 UTF-8 字节加消息/工具 schema 开销作保守估算并标记 estimated。沿用历史预算 `HISTORY_TOKEN_BUDGET=2048`；沿用显式 `RAG_CONTEXT_BUDGET`，不混淆字节预算与供应商 token 上限。

建议固定文案：闲聊「您好，我是客服小猫，可以帮您查询商品、订单、物流或售后问题。」；投诉「很抱歉给您带来不好的体验。您可以选择转人工或创建工单，我们会协助您处理。」；转人工完成「已转接人工客服」，随后「您好，我是客服小猫，请问有什么可以帮您的」。知识不足复用 Ch04 `REFUSAL_MESSAGE`。

开始执行时在每个新 PowerShell 终端初始化下面的变量（任务中的命令依赖它们）。若 run 目录已有结果，使用新后缀，不覆盖旧 attempts：

```powershell
Set-Location C:/Users/27497/projects/mewhelp-wt/ch02-tools
$pyCh05 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
$env:PYTHONUTF8 = '1'
$runCh05 = 'ch05_20261001_01'
```

### Task 1: 裸 Agent 循环与有界执行

**Files:** Create `src/mewhelp/ch05/__init__.py`（不提前导入图）、`bare.py`, `limits.py`, `config.py`; Test `tests/ch05/test_bare.py`, `test_limits.py`。

**Interfaces:**
- Consumes: `build_business_tools() -> list[BaseTool]`、`ToolRegistry.run(name: str, args: dict) -> ToolResult`、现有模型配置。
- Produces: `AgentLimits`（上述固定默认值）、`TokenUsage(input_tokens: int, output_tokens: int, estimated: bool)`、`BudgetExceeded`；`estimate_call_tokens(messages: list[dict], tools: list[dict]) -> int`；`reserve_call(used: int, input_bound: int, output_limit: int, final_reserve: int, limits: AgentLimits) -> None`，额度不足抛 `BudgetExceeded`。
- Produces: `Ch05Settings`（`config.py`）：`env_prefix='CH05_'`，`checkpoint_path: Path = Path('data/ch05/checkpoints.sqlite3')`，limits 各字段取上述默认值；不读取/复制 API 密钥到本章新配置。`BareResult(answer: str, model_calls: int, tool_trace: list[dict], usage: TokenUsage, stop_reason: str)`。
- Produces: `async bare_loop(question: str, *, complete: Callable[[list[dict], list[dict], int], Awaitable[dict]], registry: ToolRegistry, limits: AgentLimits) -> BareResult`；`async complete_http(client: httpx.AsyncClient, messages: list[dict], tools: list[dict], max_tokens: int) -> dict` 返回原 Chat Completions JSON（`choices[0].message`、`usage`、`model`），不混用 Responses API 的工具形状。

- [ ] **Step 1: 冻结循环断言，写 RED 测试。** 脚本响应依次为 query_order 调用、根据上次结果的 query_logistics 调用、正文。断言三次模型请求、第二次请求有与第一 call_id 对应的 tool 消息、工具顺序严格为订单→物流；无工具正文一轮结束；未知工具/错误作为 tool 观察回灌。限制测试断言第五次决策/第九次工具不能执行，预算不足连请求都不发，缺 usage 的估算不为零，重复同参结果无进展有停止原因。

  本任务测试文件自带 `scripted_completion`（async callable，`requests` 保存参数快照）和 `read_registry` fixtures；首个调用 id 固定 `order-1`。承重用例：

  ```python
  async def test_bare_feeds_order_observation_into_next_call(scripted_completion, read_registry):
      result = await bare_loop('先查订单1001，再查物流', complete=scripted_completion,
                               registry=read_registry, limits=AgentLimits())
      assert [item['name'] for item in result.tool_trace] == ['query_order', 'query_logistics']
      assert result.model_calls == 3
      assert scripted_completion.requests[1][0][-1]['tool_call_id'] == 'order-1'
  ```
- [ ] **Step 2: Run RED。** `& $pyCh05 -X utf8 -m pytest tests/ch05/test_bare.py tests/ch05/test_limits.py -q`；预期因循环/预算能力尚不存在失败，保留实际输出。
- [ ] **Step 3: 最小实现。** 先通过 Context7 核对 HTTPX POST/timeout/JSON 与工具协议。HTTPX 直接调用既有 OpenAI 兼容 endpoint，使用原工具的 name/description/args_schema 组装 tools，不用 LangGraph、`create_agent` 或 `create_react_agent`。保持现有 `deepseek-chat` 请求别名，按当前官方 DeepSeek 接口显式传 `max_tokens` 和 `thinking={'type':'disabled'}`，不依赖默认 thinking 吃完小输出额度；请求/响应模型名都记录。只绑定三个原有业务读工具；裸循环控制流只使用普通 Python。正常无 tool_calls 就返回正文，超限给固定有界回复，不额外调用模型。
- [ ] **Step 4: Run GREEN 与一次裸循环演示。** 同一局部 pytest 全绿；执行 `& $pyCh05 -X utf8 -m mewhelp.ch05.bare --question '订单 1001 的物流到哪了'` 保存实际工具/用量输出到 `artifacts/ch05/<run>/bare.json`，不写业务库。
- [ ] **Step 5: 记录并提交。** 追加 Task 1 四项过程及真实演示结果；显式 add 本任务文件和 `dev-notes/ch05.md`，提交 `feat(ch05): demonstrate bounded bare tool loop`。尚不安装图依赖，以保留“先手写、再用图”的顺序。

### Task 2: 七意图、State 契约及 Prompt 标注

**Files:** Create `schemas.py`, `state.py`, `intent.py`, `prompts.py`, `evaluation.py`（先实现分类评估）、`eval/ch05/intents.jsonl`, `eval/ch05/agent-decisions.jsonl`; Modify `pyproject.toml`, `.env.example`; Test `tests/ch05/test_intent_contract.py`, `test_state.py`, `test_evaluation_contract.py`。

**Interfaces:**
- Produces: `Intent = Literal['物流','订单','商品咨询','退款退货','售后','投诉','闲聊']`，`Route = Literal['knowledge','business','complaint','chitchat']`；`ClassificationResult(intent: Intent, usage: TokenUsage, origin: Literal['local','llm'])`；`route_intent(intent: Intent) -> Route`，`resolve_reference(text: str) -> str`，`match_chitchat(text: str) -> bool`，`async classify_intent(text: str, *, model: BaseChatModel) -> ClassificationResult`。
- Produces: `AgentDecision(reply_mode: Literal['answer','clarify'], suggested_actions: list[Literal['handoff','create_ticket']], ticket_type: Literal['售后','投诉','咨询'] | None)`；`ActionOffer(offer_id: str, turn_id: str, actions: list[str], description: str, ticket_type: str)`；`ToolTrace(call_id: str, round: int, name: str, args: dict, ok: bool, content: str, error: str | None, elapsed_ms: int)`。
- Produces: `TurnRequest` 复用公开的 Ch02 `AgentRequest` 校验契约；`TurnResult` 的字段名固定 `session_id`, `conversation_id`, `resumed`, `answer`, `sources`, `refused`, `low_confidence_question_id`, `intent`, `route`, `actions`, `offer`, `tool_trace: list[ToolTrace]`, `node_trace: list[str]`, `usage: TokenUsage`, `calls: dict[str,int]`, `stop_reason`, `ledger_error`。calls 固定 classifier/decision/answer 三键；本地闲聊均为 0。
- Produces: `WorkflowState`（messages 使用 `add_messages`；字段名固定 question/resolved_question、session_id/user_id/conversation_id/resumed、turn_id、intent/route、evidence/gate、agent_messages/pending_tool_calls、tool_trace、decision_count/tool_count/usage/calls、answer/actions、offers、node_trace/stop_reason/ledger_error/started_at）；`WorkflowContext(session_factory, model_factory, rag_factory, limits)` 注入运行依赖，model_factory 使用 get_ch05_model，rag_factory 使用已有 get_rag_runtime。State 的 DTO 用 model_dump 字典保存，依赖仅在 context 中。
- Produces: `async evaluate_prompts(dataset_dir: Path, outdir: Path, *, part: Literal['intents','decisions','all']) -> int`。本任务实现 intents 分支及 CLI；后续 Task 4 实现 decisions，Task 8 汇总两个分支。逐例保存 expected/actual/raw/origin、模型请求及响应名、usage、passed；错误结果非零退出，规则快路径明确标零调用。
- Produces: `get_ch05_model(output_tokens: int, *, temperature: float = 0.0, streaming: bool = False) -> BaseChatModel` 放 `config.py`，仍调用原 `get_chat_model`，不替换模型适配器或提供商。当前 DeepSeek 通过 `extra_body={'max_tokens': output_tokens, 'thinking': {'type':'disabled'}}` 传供应商字段，不同时传会被 SDK 改名的顶层 max_tokens；`timeout=30,max_retries=0,use_responses_api=False`，仅流式调用启用 stream_usage。

- [ ] **Step 1: 写确定性契约 RED。** 参数化断言七值对应 spec 四出口；透传包括原始空格和代词不改变；`你好`/`谢谢！`/`你是谁` 快速命中，`你好，订单1001能退吗` 不命中；非法 JSON/越界意图必须报分类错误；伪造节点名不得控制边。State 序列化不含 Session/模型/闭包，DTO 不接受未知动作。HTTPX MockTransport 捕获模型实际请求，断言供应商收到 max_tokens=128、thinking disabled、非流式不带 stream_options，证明限额不是只写在 Python 参数里。

  ```python
  def test_mixed_refund_is_not_a_local_greeting():
      assert match_chitchat('你好，订单1001能退吗') is False
      assert route_intent('退款退货') == 'knowledge'
      assert route_intent('物流') == 'business'
      assert resolve_reference(' 那它呢？ ') == ' 那它呢？ '
  ```
- [ ] **Step 2: Run RED。** `& $pyCh05 -X utf8 -m pytest tests/ch05/test_intent_contract.py tests/ch05/test_state.py -q`；预期失败于新契约缺失。
- [ ] **Step 3: 安装并核对 API，再实现代码契约。** `agent` extra 改为精确 `langgraph==1.2.12`、`langgraph-checkpoint-sqlite==3.1.1`，移除未使用的完整 langchain 依赖；安装 `uv pip install --python .venv-ch03/Scripts/python.exe -e '.[agent,rag,dev]'`。记录实际版本及依赖变化（dry-run 显示 websockets 17.1→16.1.1）。Context7 核对 StateGraph/context_schema/Runtime/add_messages 与 ChatOpenAI 签名，实际导入验证后实现，失败则停下说明，不换版本/API蒙混。
- [ ] **Step 4: 为 Prompt 冻结标注，再写 Prompt并验证。** 意图集 28 条、七类各 4 条，含商品价格先知识、退款订单混合、投诉和缺订单号；决策集 12 条，覆盖完整证据、缺必要参数、人工/工单单选/双选/不选、无承诺到账/送达/审批。先写期望，不根据输出改标签。分类用简单 Prompt 输出 `{"intent":"..."}` JSON，经 schema 校验；规则表固定。给评估器注入错误分类及服务异常证明非零退出，再运行 `& $pyCh05 -X utf8 -m mewhelp.ch05.evaluation --dataset eval/ch05 --part intents --outdir artifacts/ch05/$runCh05/intents`，要求 28/28；本地匹配的样例另记零调用，其余真实调用。失败保留 attempts、改 Prompt 后按冻结标签复跑，不写 Prompt 字符串单测。
- [ ] **Step 5: Run GREEN、留痕与提交。** 上述契约测试和分类评估通过，API 版本核验保存 `artifacts/ch05/<run>/runtime-versions.json`；配置项 `CH05_CHECKPOINT_PATH=data/ch05/checkpoints.sqlite3` 及 limits 默认值写 `.env.example`，不覆盖私人 `.env`。提交 `feat(ch05): define intent routing and workflow state`。

### Task 3: 强制检索与前置知识闸门

**Files:** Create `evidence.py`; Test `tests/ch05/test_evidence.py`。

**Interfaces:**
- Consumes: `get_rag_runtime(session_factory, calibration_path=...) -> RagRuntime`、`retrieve_evidence(runtime.retrieval, query, filters) -> RetrievalResult`、`source_dtos(evidence)`、`record_refusal(factory, RefusalInput) -> str`。
- Produces: `EvidenceEnvelope(sources: list[SourceDTO], scores: list[float | None], threshold: float, context_budget: int, unsupported_reason: str | None)`，`GateDecision(passed: bool, reason_code: str | None, reason: str, top_score: float | None)`。
- Produces: `async retrieve_knowledge(question: str, *, rag: RagRuntime, filters: SearchFilters) -> EvidenceEnvelope`；`evaluate_gate(evidence: EvidenceEnvelope, *, prompt_bytes: int) -> GateDecision`；`async persist_refusal(context: WorkflowContext, state: WorkflowState, gate: GateDecision) -> str`。

- [ ] **Step 1: 写 RED。** 有效分数达到 0.5 放行，0.49 拒绝，空证据拒绝；NaN/缺分数/非法校准是配置错误；超上下文拒绝。断言拒绝前独立提交原问题及 `trigger_stage='retrieval'`，入池失败不伪称完成。模拟 Milvus 故障断言是服务错误，不是低置信度兜底。

  `weak_evidence` 是本测试文件 fixture：一个有效 SourceDTO，scores=[0.49]、threshold=0.5、context_budget=32000。

  ```python
  def test_gate_rejects_score_below_threshold(weak_evidence):
      decision = evaluate_gate(weak_evidence, prompt_bytes=300)
      assert decision.passed is False
      assert decision.reason_code == 'low_relevance'
  ```
- [ ] **Step 2: Run RED。** `& $pyCh05 -X utf8 -m pytest tests/ch05/test_evidence.py -q`，预期新 gate 不存在/行为未实现。
- [ ] **Step 3: 实现接口。** Context7 核对 PyMilvus 检索与 SQLAlchemy 独立事务，继续复用现有检索器。构造透传 `QueryUnderstanding(question, question, question, 'knowledge', ['ch05_passthrough'])`，不增加第二套意图分类或正式问题改写。gate 比较 reranker 分数与现有校准阈值，不能调用 `answer_question`/`runtime.generate` 或使用 RRF 分数代替。证据保存为 DTO 字典，不 checkpoint 检索客户端。
- [ ] **Step 4: Run GREEN 与来源回归。** 上述局部测试全绿；`& $pyCh05 -X utf8 -m pytest tests/test_ch04_retrieval.py tests/test_ch04_sources.py tests/test_ch04_calibration.py -q` 通过，证明复用边界未破坏。
- [ ] **Step 5: 记录与提交。** 记录实际 RED/GREEN 及无额外生成调用，提交 `feat(ch05): gate knowledge before agent execution`。

### Task 4: 核心 ReAct 节点与真实流式答复

**Files:** Create `agent.py`; Modify `prompts.py`（纯 Prompt 部分仍走标注评估）、`evaluation.py`（增加 decisions 分支）; Test `tests/ch05/test_agent.py`, `test_evaluation_contract.py`。

**Interfaces:**
- Consumes: Tasks 1–3 的 limits、State/Decision/ToolTrace、EvidenceEnvelope；原 `build_business_tools()`、`ToolRegistry`。
- Produces: `build_read_registry() -> ToolRegistry`（仅 query_order/query_product/query_logistics）；`async decide_agent(state: WorkflowState, context: WorkflowContext) -> dict`；`async execute_agent_tools(state: WorkflowState, context: WorkflowContext, emit: Callable[[dict], None]) -> dict`；`async stream_answer(state: WorkflowState, context: WorkflowContext, emit: Callable[[dict], None]) -> dict`；`next_agent_step(state: WorkflowState) -> Literal['execute_tools','stream_answer','bounded_reply']`。

- [ ] **Step 1: 写 RED。** ScriptedModel 的三次决策依次为 query_order、读取其返回后 query_logistics、answer 控制 JSON；FinalModel 分三块产出正文。断言至少两轮工具、call_id/round 配对、决策文本不进入 token、最终正文逐块产生。简单物流只调用一次工具；缺订单号直接 clarify；一/两/零建议只作为 metadata，任何分支工单数为零。未知/伪造 create_ticket 只能作为失败观察，不执行写工具。分别断言步数、工具数、重复同参无进展、额度不足/取消不再调用模型。

  ```python
  def test_agent_cannot_execute_ticket_writes():
      registry = build_read_registry()
      assert set(registry.names()) == {'query_order', 'query_product', 'query_logistics'}
      assert registry.get('create_ticket') is None
  ```
- [ ] **Step 2: Run RED。** `& $pyCh05 -X utf8 -m pytest tests/ch05/test_agent.py -q`；保存缺失循环、工具白名单或流式边界的失败证据。
- [ ] **Step 3: 实现节点。** 使用已核对的 `.bind_tools(...).ainvoke(...)` 决策，正常 tool_calls 经旧执行器回灌 ToolMessage；无工具时解析 AgentDecision。最终节点使用不绑定工具的 `.astream()`，仅发送正文，累积 chunk 的真实 usage，不泄漏控制 JSON/隐藏推理。预算预留最终输入/输出额度；达到限制直接固定回复和可选建议。每工具 start/end 带 call_id 与 round；相关调用跨决策轮，独立读可同轮并行。
- [ ] **Step 4: Run GREEN 与实际 Prompt 评估。** agent + bare + limits 三组局部测试通过，未为了过测试把业务工具新增或塞进 if/else 写死模拟 Agent。`evaluation.py` 增加 decisions 分支，注入错动作/缺必要追问证明验证器会失败，再运行 `& $pyCh05 -X utf8 -m mewhelp.ch05.evaluation --dataset eval/ch05 --part decisions --outdir artifacts/ch05/$runCh05/decisions`，要求 12/12，保存真实决策/最终答复。失败按冻结标签改 Prompt并留痕，不等到系统收尾才验证本任务 Prompt。
- [ ] **Step 5: 记录与提交。** 保存两轮真实测试轨迹与预算停止原因，提交 `feat(ch05): implement bounded react nodes and answer streaming`。

### Task 5: 单图编排、官方 checkpointer、日志和 HTTP

**Files:** Create `workflow.py`, `runtime.py`, `service.py`, `api.py`, `events.py`, `tests/ch05/conftest.py`; Modify `src/mewhelp/main.py`; Test `tests/ch05/test_workflow.py`, `test_persistence.py`, `test_api.py`。

**Interfaces:**
- Produces: `build_workflow(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`，节点包装器从 `Runtime[WorkflowContext].context` 取依赖并用 `get_stream_writer()` 发事件；Task 4 函数仍可直接隔离测试。
- Produces: `WorkflowRuntime(graph: CompiledStateGraph, context: WorkflowContext, locks: SessionStore)`；`asynccontextmanager open_runtime(session_factory, *, settings: Ch05Settings, model_factory: Callable[..., BaseChatModel]) -> AsyncIterator[WorkflowRuntime]`。
- Produces: `async stream_turn(runtime: WorkflowRuntime, request: TurnRequest, *, entry_point: Literal['chat_stream','agent']) -> AsyncIterator[dict]`；`async run_turn(runtime: WorkflowRuntime, request: TurnRequest) -> TurnResult`；`get_workflow_runtime(request: Request) -> WorkflowRuntime`。
- Produces: POST `/ch05/chat/stream`、POST `/ch05/agent`；事件 `session`, `node`, `tool`, `sources`, `token`, `actions`, `done`, `error`。JSON 收集同一执行流，不重复跑一次图。

- [ ] **Step 1: 写 RED。** 对七意图注入分类/模型替身，断言节点顺序与 spec 图一致。弱知识没有 Agent 及答案调用；业务没有 retrieve/gate；投诉只固定安抚+两个建议；常见闲聊总调用 0、分类所得闲聊仅分类 1。强知识 sources 早于首 token。上轮知识→下轮物流、投诉→闲聊不残留 evidence/current actions；旧 offers 只用于按钮验证，不自动重新展示。消息账本每轮保持 tool_calls 与结果配对。

  `workflow_runtime` 与 `knowledge_request` 在新 `tests/ch05/conftest.py` 定义，使用强证据和脚本模型，无外部服务。

  ```python
  async def test_workflow_gates_knowledge_before_agent(workflow_runtime, knowledge_request):
      result = await run_turn(workflow_runtime, knowledge_request)
      order = result.node_trace
      assert order.index('retrieve_knowledge') < order.index('confidence_gate')
      assert order.index('confidence_gate') < order.index('agent_decide')
      assert result.refused is False
  ```
- [ ] **Step 2: 写恢复/边界 RED 并运行。** 文件 AsyncSqliteSaver 关闭重开后同 thread_id 保留完成历史，不同 thread_id 隔离；同 session 并发必须串行；失败轮不进入下一轮历史。初次 checkpoint 为空时允许一次性从现有 MySQL `load_replay_messages` 引导旧会话，之后不重复导入。JSON/SSE 最终数据一致、分类/检索异常无 done、取消无完成标记、账本失败有明确日志。Run `& $pyCh05 -X utf8 -m pytest tests/ch05/test_workflow.py tests/ch05/test_persistence.py tests/ch05/test_api.py -q`，保存 RED。
- [ ] **Step 3: 实现完整图与 runtime。** 官方 `AsyncSqliteSaver.from_conn_string(str(path))` 用在 lifespan async context；路径创建后编译一次，RAG 按知识路径惰性加载。每轮 START→begin→透传→分类→固定路由；Agent/tool 回边与所有正常出口的 log_turn 均有显式边。按 `session_id` 提供 `configurable.thread_id`，新输入重置本轮字段；完成后才向 messages reducer 追加 user/final assistant。异常由外层记录，基础设施错误不改成低置信度。`offers` 按 offer_id 保存已完成回复中的建议，不持有待执行图 interrupt。
- [ ] **Step 4: 完成传输并 Run GREEN。** FastAPI 延用 `fastapi.sse.EventSourceResponse/ServerSentEvent`，包含源 DTO、call_id/round、动作建议/实际 stop_reason；首次事件 session，actions 在成功最终答复完成后发，done 在 log_turn 和最终 checkpoint 完成后发。新 lifespan 不在启动时连接 Milvus/加载权重或查询 MySQL，旧接口回归不依赖未运行的外部服务。局部测试全绿，再跑 `& $pyCh05 -X utf8 -m pytest tests/test_ch02_api_agent.py tests/test_ch02_api_chat.py tests/test_ch04_chat.py -q`。
- [ ] **Step 5: 记录与提交。** 保存文件重开/实际 State 恢复证据和 HTTP 契约结果，提交 `feat(ch05): persist deterministic workflow and expose chat api`。

### Task 6: 独立确认建单、解除旧副作用和持久防重

**Files:** Create `actions.py`, `sql/ch05-ddl.sql`, `scripts/migrate_ch05_schema.py`; Modify `api.py`, `schemas.py`, `src/mewhelp/tools/ticket.py`, `src/mewhelp/db/models.py`, `src/mewhelp/db/repository.py`, `sql/ch02-ddl.sql`, `tests/test_tool_ticket.py`, `tests/test_db_ddl_drift.py`; Test `tests/ch05/test_actions.py`, `test_ticket_migration.py`。

**Interfaces:**
- Produces: `TicketRequest(session_id: str, user_id: str | None, offer_id: str, confirmed: Literal[True], description: str, ticket_type: Literal['售后','投诉','咨询'])`；`TicketReceipt(ticket_no: str, ticket_type: str, replayed: bool)`；POST `/ch05/tickets`，不提供后端 handoff 路由。
- Produces: `async create_confirmed_ticket(runtime: WorkflowRuntime, request: TicketRequest) -> TicketReceipt`；沿用 session 锁，核对 checkpoint 的 offer/会话/动作及已有会话归属，拒绝伪造 offer 或跨会话操作。
- Modifies: `build_ticket_tools(session_factory, conversation_id, *, request_id: str | None = None) -> list[BaseTool]`；`insert_ticket(..., request_id: str | None = None) -> None`。实际 tool 的外部签名仍是 description/ticket_type，不暴露 request_id/conversation_id 给模型；不新增业务工具。
- Produces: `find_ticket_by_request_id(session: Session, *, request_id: str) -> Ticket | None` 放在现有 `db/repository.py`，原工具与确认入口共用；比较实际行的 conversation_id/description/ticket_type，不能只按请求键给另一会话回执。
- Produces: `migrate_ch05(engine: Engine) -> None`；tickets 增加可空 `request_id VARCHAR(64)` 与唯一索引 `uk_tickets_request_id`。原章调用留 NULL；Ch05 使用服务端建议 UUID 作为稳定键，同一个 offer 的重发取得同一工单。

- [ ] **Step 1: 写业务 RED。** 原 `create_ticket` 写一行但 conversations.status 保持 ongoing，回执不声称已转人工；替换旧断言 human 的测试，仍保留合法类型/闭包注入/回滚。未确认、未知 offer、错会话不调用工具；合法确认才新增一行；同 offer 同参数重发返回同 ticket_no，修改参数则 409，不能把重复 key 当新建成功。

  替换现有 `tests/test_tool_ticket.py` 的状态用例，沿用其已存在的 create_ticket/session_factory fixtures：

  ```python
  async def test_ticket_creation_preserves_conversation_status(create_ticket, session_factory):
      await create_ticket.ainvoke({'description': '要投诉', 'ticket_type': '投诉'})
      with session_factory() as session:
          assert session.get(Conversation, 1).status is ConvStatus.ongoing
          assert len(session.scalars(select(Ticket)).all()) == 1
  ```
- [ ] **Step 2: 写迁移/故障 RED 并运行。** SQLite 测试原表升级两次不丢行，request_id 类型/nullable/唯一约束不匹配就拒绝。模拟工具已提交、checkpoint 回执写入失败，再发同请求仍只有一行；模拟并发同 key，唯一约束兜底且查回同一会话工单。Run `& $pyCh05 -X utf8 -m pytest tests/ch05/test_actions.py tests/ch05/test_ticket_migration.py tests/test_tool_ticket.py -q`，保存 RED。
- [ ] **Step 3: 修正原工具并实现确认入口。** 删 `set_conversation_status(...human)` 及转交人工回执。加入 request_id 的查询/唯一约束处理仍在旧工具中，不能在新接口另写一套建单逻辑；成功回执由数据库中对应 key 的实际行确认，不能只把旧工具返回的「失败」字符串误认成功。写工具仍 `retryable=False`。DB 提交与 checkpoint 不原子，但 DB 唯一键保证重复建单被阻止；不承诺通用 exactly-once。
- [ ] **Step 4: 增量迁移与 GREEN。** Context7 查 SQLAlchemy inspect/事务/唯一冲突接口后实现可重入迁移；更新新库初始 DDL 和 ORM，旧库先按既有约定备份再增量 ALTER，不重建/seed 业务库。运行动作/迁移测试及 `tests/test_db_ddl_drift.py`；实际 MySQL 迁移连续两次成功，已有 tickets 行数/会话状态不改变。
- [ ] **Step 5: 记录与提交。** 明确该 nullable 字段是本章防重的新增表结构，工具业务功能仍复用 Ch02。记录前后实际行数与两次迁移输出，提交 `feat(ch05): separate confirmed tickets from simulated handoff`。

### Task 7: 原生聊天页的两个独立按钮和确认交互

**Files:** Modify `src/mewhelp/static/index.html`, `tests/page-smoke.js`。

**Interfaces:** Consumes Task 5 的 SSE、Task 6 的 TicketRequest/TicketReceipt；页面 send 改接 `/ch05/chat/stream`，按钮由 actions/offer 元数据渲染，不分析正文关键词执行动作。

- [ ] **Step 1: 扩页面冒烟为 RED。** 注入投诉 SSE，断言只出现两个独立按钮，渲染/历史恢复不 fetch tickets；都不点继续 send 发普通 chat。人工确认仅增加指定状态和客服小猫问候、HTTP 建单次数 0；取消人工无变化。工单取消无请求，确认只一次 POST 且 confirmed=true；人工完成后工单仍可点，工单完成后人工仍可点。双击只一个在途请求，页面恢复不自动执行。同工具跨轮按 call_id 对应徽章，不再按名字误配。

  页面测试内扩展现有 VM/DOM 替身为 `runActionScenario({click: string, confirm: boolean}) -> Promise<{ticketRequests: number, logText: string, ticketButtonDisabled: boolean}>`，实际执行 HTML 脚本和点击处理器，不能直接伪造结果。

  ```javascript
  const observed = await runActionScenario({click: 'handoff', confirm: true});
  assert.equal(observed.ticketRequests, 0);
  assert.ok(observed.logText.includes('已转接人工客服'));
  assert.ok(observed.logText.includes('您好，我是客服小猫，请问有什么可以帮您的'));
  assert.equal(observed.ticketButtonDisabled, false);
  ```
- [ ] **Step 2: Run RED。** `node tests/page-smoke.js src/mewhelp/static/index.html`；保存动作未独立/原 human 按钮仍发送消息导致的实际失败。
- [ ] **Step 3: 实现页面。** 沿用样式、气泡、来源、反馈和 storage，增加建议区及两个处理器。人工按钮不再 `submit('我要转人工')`；仅本地确认和显示。工单用独立确认表单（描述、类型、取消/确认），确认后请求，成功显示实际工单号；网络失败不自动重发，稳定 offer_id 保留给用户主动重试。各按钮只锁自己的状态，不用动作阻塞普通对话；流式过程中仍遵守发送互斥。历史只恢复显示及建议，禁止副作用。
- [ ] **Step 4: Run GREEN 与浏览器核验。** Node 冒烟全绿；真实页面分别验证不点、取消、人工、工单、两种点击顺序和继续聊天。用可用浏览器工具时先读取 computer-use 技能；如浏览器策略阻断，诚实保留自动冒烟与后端证据并说明，不把协议替身截图称为真实页面。
- [ ] **Step 5: 记录与提交。** 记录实际 UI 验证方法/结果，提交 `feat(ch05): add independent handoff and ticket confirmations`。

### Task 8: 冻结 Prompt 评估、真实验收和演示命令

**Files:** Create `eval/ch05/README.md`, `scripts/smoke_ch05_acceptance.py`; Modify `evaluation.py`（汇总/证据校验）、`README.md`; Test `tests/ch05/test_evaluation_contract.py`, `test_acceptance_contract.py`。

**Interfaces:** 沿用 Task 2 的 `evaluate_prompts(..., part=...)`；`run_acceptance(base_url: str, *, report_dir: Path, session_prefix: str, session_factory: Callable[[], Session]) -> int`。失败/服务错误均非零退出，保存原始输出、解析结果、期望、实际模型名、用量和失败原因。

验证脚本的纯校验接口 `check_case(payload: dict, expected: dict) -> list[str]` 返回违反验收条件的具体原因，供 HTTP 验收与其测试共用；顺序工具期望字段为 `sequential_tools`。

- [ ] **Step 1: 对验证器写 RED。** 伪造错误分类/缺轨迹/服务错误必须导致非零退出；两同轮工具不能通过复杂场景；点击人工不得以工单行数上升为通过。Run `& $pyCh05 -X utf8 -m pytest tests/ch05/test_evaluation_contract.py tests/ch05/test_acceptance_contract.py -q`，证明确实识别失败，不能先生成结果再放宽标签。

  ```python
  def test_same_round_tools_cannot_pass_sequential_acceptance():
      payload = {'tool_trace': [{'name': 'query_order', 'round': 1},
                               {'name': 'query_logistics', 'round': 1}]}
      assert check_case(payload, {'sequential_tools': ['query_order', 'query_logistics']})
  ```
- [ ] **Step 2: 实现验证器，Run GREEN。** Prompt 评估读 Task 2 冻结的 28+12 样例，校验枚举、动作选择、必要追问、工具选择和禁止业务承诺，不以措辞逐字一致判定语义。真实验收使用专属 session_prefix；新增的演示 ticket 是明确确认后的测试工单，报告记录创建前后计数，不改原知识库/旧章节评估集合。JSON/SSE 同一场景分别保存节点顺序及工具 call_id/round。
- [ ] **Step 3: 汇总/补跑实际 Prompt 评估。** Task 2/4 已完成的分类 28/28、决策 12/12 结果须与最终 Prompt、样例和模型配置 hash 对应，服务错误 0；没有变化则直接汇总，不机械重烧模型调用。若后续改了 Prompt/输入构造/配置或留下未解失败，运行 `& $pyCh05 -X utf8 -m mewhelp.ch05.evaluation --dataset eval/ch05 --part all --outdir artifacts/ch05/$runCh05/prompts`。失败保留 attempts，按同一冻结标签返工，不修改期望迎合输出。
- [ ] **Step 4: 跑五场景和边界真实验收。** 问政策/未知政策、订单1001物流、投诉并分别确认/取消两个动作、常见闲聊、复杂「请先查询订单1001的商品和下单时间，再查询物流最新节点，比较这两个时间」。复杂场景须 query_order 在前、query_logistics 在下一决策轮且实际读取前次结果；弱政策池记录已提交且 Agent=0。验证多轮状态隔离和关闭重开 SQLite 后同 session 恢复；不得拿旧 Ch04 报告替代本次结果。
- [ ] **Step 5: 全量验证、文档与提交。** `& $pyCh05 -X utf8 -m pytest -q`；`& $pyCh05 -X utf8 -m ruff check src tests scripts/migrate_ch05_schema.py scripts/smoke_ch05_acceptance.py`；Node 冒烟一次。只有新增修改、失败或未解问题才重复扩大测试。README 给出以下命令、实际结果和单实例边界；及时留痕，提交 `test(ch05): verify workflow prompts and acceptance paths`。

拟交付 PowerShell 演示（执行时以真实跑通结果修订）：

```powershell
Set-Location C:/Users/27497/projects/mewhelp-wt/ch02-tools
$pyCh05 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
$env:PYTHONUTF8 = '1'
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
$runCh05 = 'ch05_20261001_01'
uv pip install --python $pyCh05 -e '.[agent,rag,dev]'
docker compose up -d mysql
docker compose -f milvus-compose.yml up -d
# 首次升级先按 README 备份现有库，再运行可重入迁移
& $pyCh05 -X utf8 scripts/migrate_ch05_schema.py
& $pyCh05 -X utf8 -m mewhelp.ch05.bare --question '订单 1001 的物流到哪了'
& $pyCh05 -X utf8 -m uvicorn mewhelp.main:app --host 127.0.0.1 --port 8000
# 另开同目录终端，浏览器打开 http://127.0.0.1:8000/
& $pyCh05 -X utf8 scripts/smoke_ch05_acceptance.py --base-url http://127.0.0.1:8000 --report-dir artifacts/ch05/$runCh05 --session-prefix $runCh05
```

不擅自占用/终止已有服务；若 8000 已监听先核验 PID/命令行，验收可用独立 8005 和独立 checkpoint 文件。只在完成新代码验收后按用户授权更新实际演示服务，避免旧进程提供旧代码却被当成新验收。

### Task 9: 整体 code review、修正与 finish

**Files:** `artifacts/ch05/<run>/review.md`, `review-resolution.md`, `verification-results.json`, `dev-notes/ch05.md`，及评审实际发现涉及的文件。

**Interfaces:** Review 范围为 `a2a98a8..HEAD` 中本章实际改动，包括前端、迁移、真实证据；不将旧章报告或设计文字当实现事实。

- [ ] **Step 1: 发起与执行方式一致的评审。** Native：全部任务由主代理实施，完成后按 requesting-code-review 派一个新鲜上下文 reviewer 做整分支审查。Subagent-driven：每任务按对应技能完成 implementer/spec/code gates，最后整体审查。实施前读所选执行技能，不能依据旧 dev-notes 的 Native/Vibe 记录擅自豁免本章流程。
- [ ] **Step 2: 记录 code review 结论。** 保存文件/行号/触发条件及 severity；立即追加四项过程。有效问题先复现，再最小修正，纯 Prompt 用冻结样例、代码用相应回归；无根据的建议说明理由，不盲改固定选型。记录修正与新检查，不为节约步骤把重要问题留到交付后。
- [ ] **Step 3: 最终验证。** 按 verification-before-completion 跑与最终修改相称的检查，确认实际退出状态及真实证据；更新最终报告/README，提交已验证的修正和记录。全量套件已通过且评审未修改代码时，不机械重复一套无新增信息的测试。
- [ ] **Step 4: Finish。** 按 finishing-a-development-branch 检查分支/worktree状态，保留用户原文件及私密运行数据；即时记录 finish 四项。交付功能演示命令、离线测试/真实 Prompt/五场景结果、完整 dev-notes 路径及明确单实例限制。推送/合并/删除分支等集成动作按用户已有授权和 finish 技能执行，不能因功能完成自行改变主仓库。

## Context7 与本机接口核验

本阶段已查 StateGraph/context_schema/Runtime/get_stream_writer/add_messages、官方 AsyncSqliteSaver async with/from_conn_string、HTTPX POST/JSON/MockTransport、Chat Completions 的 tools/tool_calls/usage 和 ChatOpenAI 参数。实际 langchain-openai 1.6.6 会把顶层 max_tokens 改名为 max_completion_tokens；当前 [DeepSeek 官方接口](https://api-docs.deepseek.com/api/create-chat-completion) 使用 max_tokens，并默认 thinking enabled。经 Context7 核对 [ChatOpenAI extra_body](https://reference.langchain.com/python/langchain-openai/chat_models/base) 后，非联网 MockTransport 探针确认 extra_body 能把 max_tokens=128/thinking disabled 放入实际请求且不产生 max_completion_tokens。Ch05 明确采用这个传参路径，不改供应商/请求别名；这只是 SDK 请求形状验证，不能冒充真实模型验收。`request_timeout`（timeout 别名）、`max_retries`、`stream_usage` 及 `bind_tools` 签名已核对。Context7 索引示例与 dry-run 的 1.2.12 不同，执行时还需对照安装包实际签名。

## 自审与执行交接

本计划的 spec 覆盖：裸循环 Task 1；意图/透传 Task 2；前置检索闸 Task 3；ReAct/流式/停止 Task 4；State/checkpointer/日志 Task 5；独立确认建单 Task 6；两个按钮 Task 7；真实评估和五验收 Task 8；review/finish Task 9。接口均从所属任务定义处导入，纯 Prompt 不采用字符串单测。

计划新增的 `tickets.request_id` 属于 spec 已允许在计划评审明确的防重迁移：仅 nullable 唯一字段及原工具闭包注入，不新建业务工具或新存储。SQLite/checkpointer 仍是用户批准的单实例范围；没有多实例能力声明。

**推荐执行方式：Native。** 九项任务共享 State/事件/建议/预算契约，按依赖顺序由当前代理实现可减少交接成本；最后再独立评审整个分支。当前只完成计划与自审，待用户评审计划并选择 Native 或 Subagent-driven 后才能安装依赖/写产品代码。
