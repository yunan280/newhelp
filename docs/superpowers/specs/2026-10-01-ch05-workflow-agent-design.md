# Ch05 · 确定性 Workflow 与核心 ReAct Agent

日期：2026-10-01。状态：用户以「确认」批准书面设计及下述两个边界；brainstorm 已定稿，实施计划须另行评审。

## 1. 目标与固定约束

将现有客服应用的单轮工具调用升级为「Workflow 决定路径、Agent 决定业务工具调用」的结构。先保留一个不依赖 Agent 编排框架的裸循环演示，再用 LangGraph 重构。验收以用户的五条场景及下面的边界验证为准。

固定技术：LangGraph 的图、State 和官方 checkpointer；现有 FastAPI、LangChain 模型适配、MySQL/SQLAlchemy、Milvus 检索；原生 HTML/CSS/JavaScript 页面。业务能力沿用 Ch02 工具，检索沿用 Ch03/04 实现。发现技术方案走不通先向用户说明，不替换技术。

本章不做正式指代消解、正式意图模型、上下文策略升级、MCP 业务接入、知识飞轮发布或真人客服集成。低置信度问题只复用现有问题池记录，不发布为知识。

## 2. 已核查的应用基线

真正的应用仓库是 `C:/Users/27497/projects/mewhelp-wt/ch02-tools`，已处于 linked worktree；分支 `ch02-tools`，探索时 HEAD 为 `a2a98a8`。现有 Ch03 未跟踪文档和脚本不属于本次改动。

- `ch02/service.py`：业务路径至多执行一轮工具，随后不绑定工具收敛；知识路径走 Ch04 有据生成。
- `tools/business.py`：`query_order`、`query_logistics`、`query_product`，仍为可复现的演示数据。
- `tools/knowledge.py`：`query_faq` 返回检索内容及 typed artifact，不生成最终答案。
- `tools/ticket.py`：`create_ticket` 真写 MySQL，但还会写「已转人工」状态，必须解除该副作用。
- `knowledge/retrieval.py`：`retrieve_evidence` 返回 `RetrievalResult`，复用原生 BM25/dense/RRF/rerank 和 MySQL 权威原文检查。
- `knowledge/answering.py`：已有重排校准加载、来源 DTO、上下文预算、拒答文案；原有 `answer_question` 同时生成并校验答案，不直接作为本章的检索节点。
- `knowledge/refusals.py`：独立事务写 `low_confidence_questions`，可用于本章 gate 的弱证据记录。
- `static/index.html`：已有 SSE、工具徽章、来源和反馈；原转人工按钮目前只是发送「我要转人工」，需改为用户点击后的本地模拟。

当前 `.venv-ch03`：FastAPI 0.141.1、SQLAlchemy 2.1.1、langchain-core 1.6.5、langchain-openai 1.6.6、PyMilvus 2.6.17；未安装 LangGraph 或 checkpointer 扩展。实施前按 Context7 文档核对实际安装版本和签名，再记录锁定的兼容版本，不把索引中的示例直接视为本机可运行代码。

## 3. 实现组织的三个选择

1. **推荐：一个显式 StateGraph，Agent 决策与工具执行构成可回边的节点。** 所有关键步骤在同一 State、checkpointer 和日志轨迹中可见。模块按分类、检索闸门、Agent、传输分开，不把全部实现塞入一个文件。
2. 外层 Workflow 加独立 Agent 子图。也符合选型，但本章需要额外处理子图状态映射和流式命名空间，收益不足。
3. 外层 Workflow 的 Agent 节点内放一个完整 while 循环。代码短，但节点内部中间步骤不能自然取得独立 checkpoint，不利于本章展示图中的 ReAct。

裸循环演示仍保留以作教学对照；正式网页入口使用方案 1。

## 4. 流程与七意图四出口

```text
START → begin_turn → resolve_reference → classify_intent → route_intent
  知识 → retrieve_knowledge → confidence_gate
                               ├─ pass → agent_decide
                               └─ weak → fallback → log_turn → END
  业务 → agent_decide
  投诉 → complaint_reply → log_turn → END
  闲聊 → chitchat_reply → log_turn → END

agent_decide ── tool_calls → execute_tools → agent_decide
             ├─ answer/clarify → stream_answer → log_turn → END
             └─ budget/limit → bounded_reply → log_turn → END
```

| 意图 JSON 中的值 | 固定出口 | 检索与 Agent 规则 |
| --- | --- | --- |
| 商品咨询 | 知识 | 强制检索，过 gate 才能进 Agent；不能由商品 mock 工具跳过知识证据 |
| 退款退货 | 知识 | 先检索政策并过 gate；Agent 可再查订单等业务数据 |
| 物流 | 业务 | 不预检索、不走知识 gate，Agent 自己调工具 |
| 订单 | 业务 | 同上 |
| 售后 | 业务 | 同上；缺必要信息则追问，可建议人工或工单 |
| 投诉 | 投诉 | 固定安抚话术加两个独立建议；不进 Agent、不自动动作 |
| 闲聊 | 闲聊 | 固定回复；不进入主力 Agent |

`route_intent` 是枚举到出口的代码表，不让模型输出节点名或下一跳。指代消解只返回原问题。分类是简单 Prompt + 七值 schema，输出 JSON；非法 JSON、越界意图及上游失败走明确错误，不静默当作闲聊。

**已确认的调用计数口径**：确定的问候/感谢/身份问法加本地保守快速匹配，命中时完全零模型调用；其他输入只做一次分类，若判为闲聊则零 Agent/答复生成调用。用户已批准这一口径；有限规则不声称覆盖所有开放式闲聊。

混合政策与业务的问题归知识出口，例如「订单 1001 能不能退货、按什么政策」。分类 Prompt 明确这一优先级，用事先标注样例验证，防止以订单号存在为由绕过 gate。

## 5. State 与持久化

State 贯穿会话身份、历史消息、本轮原问题/透传问题、意图/出口、证据快照/分数/阈值、gate 结果、Agent 消息与工具轨迹、步骤/token 计数、最终正文、建议选项、停止原因及节点轨迹。只保存可序列化数据；不保存 Session、模型实例、工具闭包、检索客户端或锁。

`thread_id` 使用稳定的会话身份，不能每轮换成新 UUID。运行依赖通过图运行上下文或工厂注入。会话历史与活跃本轮工具往返分开；每轮开始重置 evidence、gate、actions、工具轨迹和计数，不能继承上一轮的高分证据或投诉建议。历史送模型时继续使用现有最终用户/助手消息裁剪规则，不引入摘要或长期记忆。

官方 checkpointer 是 Ch05 会话状态来源。现有 MySQL messages 保留为业务账本和调试轨迹，不在每轮把同一段历史同时从 MySQL 与 checkpoint 拼接两遍。同 session 的聊天及建单操作串行化；不同 session 可独立进行。必须验证进程重启后用原 thread_id 继续对话、不同 thread_id 隔离。

**已确认的持久化边界**：官方 `AsyncSqliteSaver`，单进程/单实例，文件持久化，依赖 `langgraph-checkpoint-sqlite`；在 FastAPI lifespan 中管理连接和已编译图。这是生产架构骨架的本地实现，不能宣传为多实例生产部署能力。业务表仍用 MySQL；本章不新增 PostgreSQL 或多实例会话锁。

不能用内存 saver 冒充持久化，不能自行写一个 MySQL saver 或换社区 saver。内存 saver 仅用于不涉及重启的隔离单测。

## 6. 检索与前置置信度闸

知识节点调用 Ch04 的检索能力，使用当前可信问题及已有过滤接口。不调用包含最终生成的 `answer_question`，以免在进入主力 Agent 前已经生成另一份答复。

gate 使用 Ch04 校准的 reranker 分数：有效且已发布的证据不能为空；分数须存在且有限；最高重排分数须达到对应模型/语料校准阈值。上下文不能超出显式预算，不能为了放行静默截掉必要证据。不能把 RRF 融合分数当重排分数与原阈值比较，也不能把相关性分数称为答案正确概率。

弱证据先在独立事务记录原问题、会话、`trigger_stage=retrieval`、入口及原因，再输出已有兜底话术；Agent 决策与答案模型的调用数均为零。入池失败返回明确服务错误，不能声称已经记下问题。Milvus/MySQL/模型服务故障与弱证据是不同结果，不能伪装成正常拒答。

通过 gate 后，将证据快照及来源编号传给 Agent，并在首个答复 token 之前发 sources。只验证前置 gate，不增加答完以后阻断用户已见内容的置信度流程；不得把本章简单阈值包装为完整的证据覆盖判断。来源显示继续复用现有页面与原文接口。

## 7. 裸循环、ReAct 与流式

裸循环先写成可运行 CLI：调用配置的 OpenAI 兼容 LLM → 检查返回的 tool_calls → 通过已有工具执行器执行 → 按 call_id 追加 tool 消息 → 再调用 LLM；无 tool_calls 时结束并返回正文。循环编排不用 LangGraph 或预制 Agent 工厂；不新增订单/物流等工具。保留工具开始/结束、轮次、用量与停止原因以便教学演示。

图版使用同样的业务工具和工具错误契约。简单问题只执行一次工具，复杂问题允许根据前一步结果选择下一步；多个互不依赖的读调用可以同轮执行，依赖调用必须回到 Agent 再决策。缺订单号等必要信息时进入追问答复，不编造参数。

为保证真正的最终答案流式且不把工具调用前言当作已确认答案，建议决策与答复分开：决策轮绑定只读工具并缓冲正文；无待执行工具时产出 answer/clarify 和建议选项的控制 JSON；独立答复节点不绑定工具，用 `.astream()` 把最终正文逐块交给用户。该设计会额外使用一次最终答复调用，其 token 必须计入预算，不伪称为零成本。

停止条件包括正常回答、追问、最大决策轮数、最大工具次数、重复同参工具调用无进展、token/上下文预算耗尽、总时限和客户端取消。建议默认最多 4 轮决策、8 次工具、最终输出上限 1024 tokens；具体整轮 token 默认值在计划中依据实际供应商配置核验后定值。调用前预估并预留最终答复额度，调用后累计真实 usage；供应商不给 usage 时标记估计值，不能记成 0。达到上限输出清楚的兜底及可选建议，不继续调用 LLM。

只推送模型正文与工具摘要，不推送隐藏推理内容。通过 LangGraph custom stream 转换为已有 SSE 事件；分类输出、内部控制 JSON 和工具参数碎片不直接混进答案。

## 8. 人工与工单两个独立动作

建议是结构化元数据，固定枚举 `handoff`、`create_ticket`，可为空、一个或两个。投诉必有两个；Agent 可建议其中任意一个或两个。建议不是命令，也不挂一个等待用户决定的图 interrupt。用户不点按钮继续发消息时，下一轮正常从 begin_turn 开始。

正式 Agent 工具白名单不包含 `create_ticket`；执行器再次检查白名单，模型伪造该工具名也不能写库。转人工不提供后端执行路径。本章不新增业务工具，只新增确认后调用原工单工具的 HTTP 入口。

前端两个按钮分别绑定处理器：

- 点「转人工」后确认；仅在前端展示「已转接人工客服」及「您好，我是客服小猫，请问有什么可以帮您的」。不请求工单接口，不写 tickets，不把会话在数据库标为真人接管，也不禁用继续与普通 Workflow 对话。
- 点「建工单」后单独确认问题描述及类型，确认才 POST 工单接口；后端从会话解析 conversation_id 后调用 Ch02 `create_ticket`，成功返回实际工单号。取消不写库。一个按钮的完成状态不影响另一个。

修改原 `create_ticket`：仍使用原函数、表及类型，删除写人工状态的语句及「已转交人工」回执。对应 Ch02 回归测试改为验证建单后会话状态仍不变；这项有意的旧语义变化必须同步文档，不能以兼容旧副作用为由违背本章要求。

点击中禁用当前按钮，不自动重试写请求。若设计要提供重试/跨标签页防重，需给 tickets 加持久唯一请求键并在同一事务保证幂等；不能只靠内存标记或 checkpoint 声称跨崩溃 exactly-once。该增强的范围与迁移在实施计划评审时明确，不能暗中扩展业务工具。

历史记录可保存按钮建议与成功结果；恢复页面不能自动执行动作，不能从助手文本匹配「工单」就发请求。

## 9. 传输、错误与日志

新增 `/ch05/chat/stream` 与 `/ch05/agent`，共用一个图核心；网页接 Ch05。旧章节端点继续可用，不把旧的生成后检查链路混入 Ch05。

SSE 继续支持 session/tool/sources/token/done/error，增加 actions 和可观测的节点轨迹事件；工具事件带 call_id 与 round，确保同名工具多轮调用配对。JSON 出口返回同样的最终答案、来源、建议、完整工具轨迹、token 计数、停止原因及节点顺序。`done` 只在图正常结束后发出；中途错误/取消不能伪造完成。

log_turn 为每个正常出口的公共结束节点，记 session/turn、意图/出口、节点顺序、gate 分数及原因、每次工具名/参数摘要/耗时、轮数、用量和停止原因。异常路径由外层记录失败边界。使用现有 logging，不接 Langfuse；记录必要的问题内容与业务轨迹，不输出密钥或完整模型隐藏推理。

checkpointer 与 MySQL 账本不是同一个事务；本章不得承诺两者分布式原子提交。完整回答后的账本写失败必须留下可检查的错误日志与状态；checkpoint 的权威历史不能依赖一条可能缺失的账本行。

## 10. 验证与五条验收

代码任务走 TDD：先运行能证明行为缺失的测试，再实现最小逻辑，最后重构并回归。纯 Prompt/标注数据任务先冻结人工期望，再调用实际配置模型保存逐例结果；不写只比较 Prompt 字符串的镜像测试。

| 验收 | 必须保存的证据 |
| --- | --- |
| 政策问题 | 日志出现 retrieve_knowledge → confidence_gate → Agent；弱证据时没有 Agent/token 答案调用且问题已提交 |
| 订单 1001 物流 | 业务出口无预检索/gate，真实模型自己选择 query_logistics，答案依据工具返回 |
| 我要投诉 | Agent 调用 0；两个独立按钮；两种点击、两种取消、都不点继续消息均验证；仅确认工单导致 tickets 增加 |
| 闲聊 | 固定话术；按用户确认的调用计数口径验证分类与生成计数 |
| 先订单再物流的复杂问题 | 至少两轮决策/工具往返，第二次调用确实在第一次工具结果之后，不能把同轮两个并行调用算成多步 ReAct |

补充边界：七意图完整映射；非法 JSON；混合政策/订单路由；预算与无进展终止；工具失败/未知工具；幻觉 create_ticket 被拒；跨轮状态重置；同会话并发；重启恢复；JSON/SSE 一致；来源早于 token；不点击无副作用。

运行现有全量 pytest/Ruff，复用页面 Node 冒烟并补动作测试；必要时用浏览器验证真实页面。真实 LLM/检索/工单验收在隔离会话与数据中进行，保留模型结果与 tickets 计数；不能拿替身测试或既有 Ch04 数字冒充 Ch05 实测。

## 11. 阶段流程与交付

书面 spec 和持久化/闲聊计数口径已获用户批准，进入 writing-plans。计划列明文件责任、TDD/Prompt 评估、Context7 核验、验收命令及执行方式；计划评审通过后实施。

每完成 brainstorm 定稿、计划评审、每项任务、code review、finish，立即追加 `dev-notes/ch05.md` 四项：用户关键原话、关键产出、用户拒绝/纠偏、翻车/返工。最终交付给出从实际应用根目录运行的功能演示命令、真实测试/评估数字和 dev-notes 路径；不把待批准草案称作功能完成。

## 12. Context7 查询依据

已通过 Context7 MCP resolve/query 查询以下官方资料；实施时还要核对实际版本及用到的新 API。

- LangGraph：[Graph API/quickstart](https://docs.langchain.com/oss/python/langgraph/quickstart)、[streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[checkpointer libraries](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[StateGraph.compile reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/compile)。官方 SQLite/Postgres saver 是单独安装的扩展，async 图应使用对应 async saver。
- LangChain：[ChatOpenAI reference](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI)、[LangChain reference](https://reference.langchain.com/python)。保持与现有消息/工具协议兼容，不自行升级为预制 Agent API。
- FastAPI：[lifespan](https://fastapi.tiangolo.com/advanced/events)、[原生 SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events)。应用已使用 `fastapi.sse.EventSourceResponse` 和 `ServerSentEvent`，本章延续该接口。
- SQLAlchemy：[Session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)、[transaction control](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)。Context7 索引为 2.0，而本机是 2.1.1；需实际签名和事务回归确认，不把文档版本假装成安装版本。
- PyMilvus：[hybrid_search API](https://github.com/milvus-io/pymilvus/blob/master/_autodocs/api-reference/async-milvus-client.md)、[rankers API](https://github.com/milvus-io/pymilvus/blob/master/_autodocs/api-reference/rankers.md)。本章通过现有 Ch04 检索器复用这些能力，不直接重写 SDK 检索。
