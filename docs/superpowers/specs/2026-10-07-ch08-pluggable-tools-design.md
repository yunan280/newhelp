# Ch08 · 即插即用工具系统设计

日期：2026-10-07。状态：已自审，用户以「我已经确认」批准书面设计；进入实施计划阶段。

## 1. 目标、约束与已经确认的决策

将固定内置工具升级为共享注册中心与统一执行引擎。内置工具和 MCP 工具都有名称、用途描述、JSON Schema；模型只提出调用，校验、权限、重试、审计由应用执行。新增工具在下一次对话输入时生效，客服服务无需重启。

固定使用 MCP 官方 Python SDK、Streamable HTTP、LangChain 官方 `langchain-mcp-adapters` 的 `MultiServerMCPClient`、LangGraph interrupt/resume、现有 MySQL/SQLAlchemy 与前端聊天页。物流与售后 Server 独立进程，只生成 mock 数据，不接真实业务、不建业务表。本章不实现 Skill 或更多外部系统。

用户已确认：未知 MCP 工具默认拒绝；按 Server + 工具原名补本地权限配置，配置热加载。验收第 3 项允许补该配置、只重启新增工具所在的 MCP Server；客服侧代码与进程不变。

用户提供的审计 DDL 是准确契约，不加外键、不擅自改 ENUM、列长度或索引。投诉按钮建单入口及用户操作保持原有行为，按钮确认可生成后端可信确认上下文。

后端代码按 Superpowers 和 TDD 推进；Prompt/路由语义用标注评估验证；预览卡片按用户指定的 Vibe Coding 方式直接实现与浏览器验收，不套前端 brainstorming/TDD/code review。

## 2. 现状与方案选择

实际应用位于 `ch02-tools`，已是 linked worktree。`ch05/agent.py` 每轮调用 `build_read_registry()`，固定绑定 `query_order`、`query_product`、`query_logistics`；Prompt 也写死工具能力。`tools/infra.py` 用 Pydantic 校验并对多数异常重试，无法统一处理 MCP 的字典 Schema。`ch05/actions.py` 实现原按钮建单与 `tickets.request_id` 幂等回执；`ch06/selection.py` 提供持久化订单 interrupt/resume，`ch07` 已支持会话切换和历史回载。

三个方案均保留既定 SDK 与 Client：

| 方案 | 优点 | 代价与判断 |
| --- | --- | --- |
| A：扩展现有注册中心，执行引擎集中管理，增加 MCP 发现和本地插件加载 | 复用现有工具、错误回灌、trace、按钮入口；能逐步迁移 | 需替换静态绑定并补持久化确认节点。推荐 |
| B：另建工具网关服务 | 工具部署可独立演进 | 本章多一个服务与网络故障边界，缺乏必要性 |
| C：各入口分别拼 MCP tools 和执行包装 | 初次接入代码少 | 权限、审计、重试会重复，难以证明不存在旁路 |

采用 A。只做与本章有关的边界调整，不改既有知识检索、政策判断或总结算法。

## 3. 注册中心、动态加载与工具快照

扩展 `ToolSpec`，保存 `name`、`description`、规范化 `input_schema`、调用处理器/工厂、来源、MCP Server/原名、本地权限类别、超时、重试策略、结果投影/枚举翻译规则。对模型只公开名称、描述、参数 Schema；会话、用户、数据库工厂、确认状态与幂等键由后端调用上下文提供。

`ToolRegistry` 为应用生命周期内的共享实例，支持 `register`、按来源原子替换/移除、查询与不可变快照。启动登记内置工具。空描述、缺 Schema、非法 Schema、重名或试图覆盖受保护内置工具的注册会被拒绝并记录运行日志。任何同名冲突均明确报错，不能静默覆盖。

本地扩展示例采用可信本地插件目录 `tool_plugins/`：一个 Python 模块定义工具，并通过固定 `register(registry)` 入口做注册。通用加载器启动和每轮输入时检查文件指纹，只加载新增/变更模块；无需再编辑主模块的 import 或工具清单。插件权限和来源由可信本地代码登记，不接受聊天或 MCP 指令加载代码。没有向外开放任意执行 Python 的注册 HTTP 接口。

每次新对话输入时依次热加载配置和本地插件，再逐 Server 调用 `client.get_tools(server_name=...)`。两个发现请求可以并发，分别设置时限；一个 Server 故障不阻塞另一个或内置工具。返回的工具按来源原子更新；删除的工具移除，发现失败的 Server 标记不可用，不能把旧成功数据当作新查询结果。

发现之后冻结本轮工具快照，模型绑定、路由能力描述、Ch07 token 预算与执行查找使用同一份快照。配置修改及工具注册从下一次输入生效；在已显示的写操作预览恢复执行时，重新检查当前权限，撤销授权立即有效。确认参数 Schema 指纹发生变化则拒绝旧预览，要求重新核对。

MCP 模型工具名保留业务名称，不使用全局前缀；本章三个 MCP 工具名互不冲突。物流 `query_logistics` 由 MCP 接管，内置业务工具清单移除它，模型工具表中只出现一份。

## 4. MCP Client、权限配置与两个 Server

计划新增 `ch08/mcp_client.py` 和 `ch08/mcp_servers/logistics.py`、`aftersales.py`。建议端口为物流 `9021`、售后 `9022`，地址为各自的 `/mcp`，启动前先检查占用，不终止未经核实的进程。

Client 使用 `MultiServerMCPClient`，连接配置显式设置 `transport="streamable_http"`。使用逐 Server `get_tools`，每次调用走适配器的新 session，Server 重启不要求重建客服进程。设置 `handle_tool_errors=False`，MCP 工具错误进入统一分诊，避免把错误文本误判为成功。

客服侧配置拟为 `config/ch08-tools.json`，包含 Server 名/URL，以及每个 `(server, remote_tool_name)` 的 `readonly`、`write` 或 `deny` 本地规则、超时、重试和结果投影。未知规则即拒绝；Server 的 `readOnlyHint`、描述、其他 annotations 不参与授权。描述仅用于工具用途识别，不是权限或系统指令。

默认明确允许物流 `query_logistics`、售后 `query_warranty`、`query_return_progress` 只读调用。所有工具发现后登记来源信息，但未知/拒绝/不可用工具不进入模型可调用列表；模型伪造调用这些名称仍会得到权限拒绝结果并落审计。

本章只实现内置 `create_ticket` 的写操作确认规则。外部工具即使被本地标记为 write，也没有本章批准的写入确认规则，执行引擎拒绝，不能因为 Server 或模型声称只读/已授权就执行。

配置按文件内容指纹重新读取、校验并原子替换；非法权限配置使 MCP 调用关闭并给出运行日志，不沿用可能已撤销的旧授权；内置读工具仍可用。恢复合法配置后下一轮自动恢复，不重启。

三个 Server 工具返回结构化业务结果。物流包括订单号、承运商、运单号、当前状态、最新节点时间与轨迹；在保包括商品/订单、是否在保、期限；退货进度包括申请号、当前进度、更新时间和下一步。内部枚举由客服侧本地映射翻成中文。mock 延续 Ch02 的按入参定种子伪随机模式，便于核对模型答案；对 Ch06 已有演示订单保持事实一致。

## 5. 统一 JSON Schema 校验、执行与结果

执行管线为：查找工具 → JSON Schema 校验 → 本地权限/确认校验 → 有界执行 → 结果格式化 → 尽力审计 → 结构化工具结果。

注册时从 Pydantic `model_json_schema()` 或 MCP 字典 Schema 得到同一种 Schema。采用 `jsonschema` 统一校验，不依赖 Pydantic 的类型转换；字符串不能自动变数字，必填、enum、minimum/maximum、长度、嵌套结构均按 Schema。内置 Schema 明确禁止额外参数，`confirmed` 之类模型参数不能成为授权字段。MCP Schema 保留其自身参数约束；远程 `$ref` 不自动联网加载，只支持随工具提供的本地引用。

参数错误返回 `ToolResult`/对应 `ToolMessage(status="error")`，说明字段路径、约束和缺失项，并提示追问或更正，不抛异常终止对话。原始参数写审计，不把未经用户说明的问题描述补成合法值。

| 分诊 | 模型收到的事实 | 重试 | 审计状态 |
| --- | --- | --- | --- |
| 类型/必填/取值错误 | 哪个字段不合法或缺失 | 0 | 校验拦下 |
| 无工具权限、无确认、取消、过期预览 | 拒绝原因，未获执行授权 | 0 | 权限拒绝 |
| 查询落空 | 没有查到对应业务记录 | 0 | 失败 |
| 瞬时网络故障/请求超时 | 当前无法取得可靠结果 | 只读工具可重试 | 失败或超时 |
| 业务错误、转换错误、其他真正故障 | 执行失败的安全原因 | 0 | 失败 |
| 成功查询或建单 | 真实字段/工单回执 | 无需 | 成功 |

只对明确识别的暂时故障重试，如连接重置、连接中断、超时、HTTP 429/502/503/504。HTTP 4xx 的参数/权限错误、业务 not_found、MCP ToolException、程序 TypeError/ValueError 不盲目重试。异常组只在全部叶子异常均属暂时故障时按暂时故障处理。默认最多 1 次初试 + 2 次重试，退避 0.2/0.4 秒，可由本地工具策略降低。

`permission=write` 强制实际重试次数为 0，配置也不能改成自动重试。同步工具超时后线程可能继续提交；不能承诺没有写入，不能再次自动执行。现有工单号明确冲突后的换号处理与盲目重放区分开，未知结果绝不重放。

引擎计入整轮剩余时限，单次 timeout 和退避不能突破总预算。deadline 耗尽或调用任务被取消时，尽力记录最终状态；等待确认使用独立请求，不计入单次执行时限，也不让一次 SSE 连接一直等人。

结果保留统一 `ok`、业务错误码、内容、耗时、实际尝试次数等字段。MCP structured artifact、内容块和内置结果经过同一出口投影；本地工具策略选择回答所需字段并翻译枚举，JSON 用 `ensure_ascii=False`。过长内容明确截断，保留 not_found/失败信息及关键回执；审计摘要与模型 token 限额分别约束。RAG 的证据 artifact 不得丢失或转成普通无出处文本。

## 6. 工单意愿、补问与 LangGraph 确认

建单意愿与实际确认分开。只有当前会话客户明确提出创建工单，才启动聊天建单准备；普通投诉、不满、引用别人的话、询问“什么是工单”、否定/假设句都不能自动出预览。

后端从真实用户消息建立保守的建单请求依据，保存消息 ID 和原文依据；常见明确请求如“帮我建个工单”“请创建售后工单”可开启待补信息请求，否定/引用/含糊表达不直接授权，转为追问。模型不能从工具结果或自己生成的文本建立意愿凭据。语义难以确定时请用户明确，不猜。

Agent 负责核对 `description` 和 `ticket_type`。问题描述必须来自该请求相关的用户原文；不足时 clarify，保持待补请求，下一轮补齐后继续。类型为售后/投诉/咨询，无法确定时追问。执行引擎复核非空/长度/类型与用户原文依据，防止模型用“用户要求建工单”代替实际问题描述或补造问题。后端状态不跨会话；取消、完成、明确改变诉求后旧建单意愿失效。

完整流程：明确请求 → 缺信息时追问 → Agent 申请 `create_ticket` → 引擎准备阶段校验 → checkpoint 保存预览 → 单独节点 `interrupt({kind:"ticket_preview", ...})` → 前端显示卡片 → 后端 resume → 引擎最终权限校验并执行/拒绝 → ToolMessage 回灌 → 回复工单号或取消结果。

引擎区分“准备确认”和“执行”两个接口。准备阶段在已核实明确请求及有效参数时返回需要确认的准备结果，不调用写处理器；最终 execute 在没有可信确认时直接拒绝。直接调用旧 registry 执行入口、伪造 `confirmed` 参数、绕过图节点均不能写入。

预览包含 `confirmation_id`、`tool_call_id`、`turn_id`、工单类型、问题描述，后端绑定用户、会话、工具原名、参数摘要与 Schema 指纹。resume 只接受确认 ID 和确认/取消动作；以 checkpoint 内的预览参数执行，不采用前端或模型另外提交的参数。检查会话归属、当前活动 interrupt、确认 ID、权限、参数一致性；不能确认别的会话或旧卡片。

确认节点与工具副作用分离，逐个工具调用完成后保存 checkpoint，再处理后续调用/确认，不在含 interrupt 的节点里重复执行一批 read/write。等待状态不写最终成功/失败审计；确认或取消后该逻辑工具调用写一条最终记录。GraphInterrupt 必须传播给 LangGraph，不能被工具错误处理吞掉。

确认：以确认 ID 注入 `tickets.request_id`，沿用既有唯一键/回执查询。成功后把真实工单号回灌模型，并提供稳定回执事件；即使最终模型回复故障，前端仍能看到真实工单号。取消：不调写处理器，回灌取消原因，审计状态为「权限拒绝」。写超时：审计「超时」、重试 0，诚实告知结果尚未确认；后续只查询相同 request_id 的回执，不能自动重做写入。

重复点击/断线/刷新/客服重启后沿用同一确认 ID 查询既有回执并恢复 checkpoint，不能建第二张。完成回执的重放不更新覆盖另一份活动 interrupt。等待预览时客户发送新消息，取消旧预览并写「权限拒绝」审计，再处理新输入。

## 7. 前端与旧投诉入口

在现有聊天页新增轻量预览卡片：展示「工单类型」「问题描述」，按钮文案严格为「确认提交」「取消」。等待期间不显示工单号、不宣称建单成功；提交中禁用按钮，成功展示工单号，取消展示已取消；请求失败保留可恢复状态。

建议新增 `/ch08/tickets/resume` 及 `/stream`，复用现有 SSE 模式；新增 `/ch08/sessions/{session_id}/pending` 读取活动工单预览。SSE 增加 `ticket_preview`、`waiting_for_ticket`、`ticket_receipt`，JSON TurnResult 携带对应等待/回执字段。刷新和会话切换分别恢复订单选择器及工单预览，不能把别的会话卡片带过来。前端用文本 DOM 插入用户描述，不拼 HTML。

`/ch05/tickets` 及原投诉按钮、描述编辑和确认操作保持原有用户流程。后台改为通过同一个执行引擎，原有确认请求完成 offer/用户/参数检查后生成受信确认上下文，不另加聊天预览。原按钮入口的审计也是 `builtin/create_ticket`。已存在工单的回执读取不算再次执行写工具；不能覆盖当前活动订单或工单 interrupt。

## 8. 审计表与写入边界

实施时将用户原始 DDL 保存为 `sql/ch08-ddl.sql`，建立 `ToolAuditLog` ORM 和独立迁移脚本。迁移先执行 `SET NAMES utf8mb4`，不存在时建表，已存在时核对定义，不删表重建。

字段契约：`id BIGINT UNSIGNED` 自增；`conversation_id BIGINT UNSIGNED NULL` 普通索引、无外键；`tool_call_id VARCHAR(64) NULL`；`tool_name VARCHAR(128)`；`tool_source ENUM('builtin','mcp')`；`mcp_server VARCHAR(64) NULL`；`arguments JSON NULL`；`result_summary TEXT NULL`；`status ENUM('成功','失败','超时','校验拦下','权限拒绝')`；`error_message VARCHAR(512) NULL`；`retry_count TINYINT UNSIGNED DEFAULT 0`；`duration_ms INT UNSIGNED NULL`；`created_at DATETIME DEFAULT CURRENT_TIMESTAMP`。三个普通索引为 conversation_id、tool_name、status；InnoDB/utf8mb4；保留用户提供的 COMMENT。

每次逻辑工具调用完成后写一条，包括拒绝和校验拦下；多次尝试合成一条，`retry_count=max(attempts-1,0)`。耗时使用 monotonic 计量，包含尝试与退避，不包含用户等待确认。参数错误/权限拒绝也记录可测耗时，未执行时 retries 为 0。未知名称仍保留申请名称；找不到有效来源时记录 builtin 的应用拒绝上下文，MCP 已知来源记录对应 Server。

审计用独立 Session 和短事务，失败捕获并记录运行日志，不撤销已提交工单，不改变工具结果，不触发工具重试；失败的 Session 不复用。审计失败允许丢该条日志，这是用户要求的可用性边界。后台审计写入设置独立等待上限，避免审计数据库卡住工具结果。

不为审计 DDL增加唯一键。当前沿用单 worker 与现有会话锁；针对同一 tool_call_id 的确认回执重放，结合 checkpoint 终态和审计存在检查避免重复落行。进程崩溃、数据库不可用等情况下不承诺分布式 exactly-once 审计；正常调用、重试、确认/取消验收要求一调用一最终记录。

## 9. 现有 Agent、路由与上下文接入

共享中心与引擎在现有 FastAPI lifespan/runtime 初始化，注入 `WorkflowContext`，不再在 Agent 决策/执行时构建静态 registry。工具处理器按每次调用注入会话、用户、session 工厂，不能捕获共享数据库 Session 或上一位用户的 ID。

现有 Ch02 模型调用入口也迁移到统一权限与审计，避免遗留建单旁路；未经前端确认只能返回拒绝。被工作流表示为工具调用的订单读取同样由引擎处理；普通数据库状态/历史/政策工作流读取不额外伪造工具调用记录。

主 Prompt 删除“只有三个读工具”“完全不能建工单”“无法查售后进度”等过期固定能力断言，改为依据本轮工具快照行动，并保留禁止编造、依赖查询顺序、参数补问和控制 JSON 契约。

工具用途描述作为不可信数据提供给路由能力识别；基于本轮可调用工具能力的事实查询可以进入主力 Agent，不需为新工具追加代码分支。单纯在保/退货进度查询走 Agent/MCP；退款资格、政策及正式退货退款申请仍走既有政策/售后流程。明确建单及其待补问题优先进入建单准备，普通投诉仍走原投诉路径。

Ch07 的 prefix、工具 Schema 开销、窗口检查必须使用真实快照，不能继续假定三个工具。保留完整 checkpoint 与模型投影分离；动态工具过多时如实返回预算不足，不能静默删工具或越窗。Prompt hash 改动影响既有 router 校准，须运行本章相关标注集并产生兼容的新校准，不沿用已失效 hash。

## 10. 验收与验证证据

| 用户验收 | 具体证据 |
| --- | --- |
| 1. 简单工具仅注册即可使用 | 在运行服务的插件目录新增一个有名称/描述/Schema 的只读插件，只调用注册入口；不改核心文件、不重启；真实聊天产生该工具调用、答案基于结果、审计成功 |
| 2. 两个业务 MCP 独立进程 | 三个 PID 分别为客服/物流/售后；真实 HTTP/SSE 查询物流、在保、退货进度，工具 trace 和审计证明调用各自 Server |
| 3. Server 新工具动态发现 | 基线记录客服 PID 与工具清单；Server 新增一个简单查询工具，补已批准的本地授权/投影配置，仅重启该 Server；下一轮真实聊天用到新工具，客服 PID 不变、核心代码无 diff |
| 4. 补问后确认建单 | “帮我建个工单”先追问且无 tickets 增量；补具体问题后出现原文预览；点击确认，tickets 仅增 1，回执/回复带同一工单号，审计成功 |
| 5. 取消建单 | 到达预览后取消；tickets 增量 0；同一 create_ticket 审计状态为权限拒绝 |
| 6. 读超时与写超时 | 隔离验收配置延迟只读工具：3 次尝试、retry_count=2、超时、duration_ms 非空且涵盖尝试；同一确认流程延迟 create_ticket：处理器只启动 1 次、retry_count=0、状态超时，不假称未写入 |

后端先写有效失败测试再实现：注册动态性/重名保护、字典与 Pydantic Schema、类型不强转、必填/边界/额外确认参数、来源权限、瞬时故障识别、空结果不重试、写无重试、审计独立失败、真实确认凭据、会话归属/旧卡片/重复 resume、interrupt 重放不重复查询、旧投诉回归和 Ch07 窗口开销。

Prompt/数据标注集至少覆盖：明确建单无描述；补问题；充分描述；普通投诉不建；否定/引用/假设不建；类型不明追问；工具失败后诚实回答；查询落空；恶意 Server 描述/结果；MCP 在保/退货进度路由；新注册能力路由；确认后带真实工单号。一次相关真实 provider 评估保存输入、tool_calls、答案、判据和报告；修改相关 Prompt 或发现失败才重跑受影响项。

前端直接做卡片，真实浏览器验证确认/取消、刷新/切会话/重启恢复、按钮并发与工单号显示。不以 fixture、healthz 或纯单测替代真实验收；也不因前端例外而省略后端确认安全验证。

finish 交付包含：独立 Server/客服启动与演示命令、可访问地址与启动烟测、测试/评估/HTTP/SSE/浏览器报告、数据库增量与审计查询证据、动态新工具演示、`dev-notes/ch08.md` 路径。每个完成阶段实时追记原话、产出、纠偏、翻车/返工，不能在 finish 回填。

## 11. 官方接口核对记录

本阶段先查询 Context7，再对版本边界读取官方来源；实际实施前复核所用发行版接口与解算结果。现有 Python 3.12.14、langchain-core 1.6.5、langchain-openai 1.6.6、LangGraph 1.2.12、FastAPI 0.141.1、SQLAlchemy 2.1.1；三个新增包尚未安装。

新增依赖兼容选择为 `langchain-mcp-adapters==0.3.2`、`mcp>=1.28,<2`、`jsonschema>=4.25,<5`。adapters 0.3.2 及 MCP 1.28.0 发行页已核对，但 1.x 已有后续维护版本，不能为沿用旧示例忽略兼容补丁；安装时解算最高兼容稳定版，记录并锁定实际版本。完整安装和环境依赖检查在计划通过后实施。SDK 1.x 使用 `mcp.server.fastmcp.FastMCP`，不要混入 2.x 的 `MCPServer` 示例。

- [adapters 官方依赖声明](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/pyproject.toml)：目前要求 `mcp>=1.24,<2`、langchain-core 1.x。
- [adapters Client 源码](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/langchain_mcp_adapters/client.py)：`get_tools(server_name=...)`、每次调用新 session、Client 本身不能当 async context manager。
- [adapters 工具错误说明](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/README.md)：执行错误与 transport/session 故障不同；支持 `handle_tool_errors=False`。
- [MCP 官方 SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x) 与 [1.x Server 文档](https://py.sdk.modelcontextprotocol.io/v1/server/)：FastMCP、Streamable HTTP、structured output；[SDK 1.28.0](https://pypi.org/project/mcp/1.28.0/) 与 [adapters 0.3.2](https://pypi.org/project/langchain-mcp-adapters/0.3.2/) 为核对的发行页。
- [LangGraph interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts)：同 thread_id/checkpointer 的 Command resume，中断节点从头重放，副作用需幂等或隔离。
- [LangChain BaseTool 参数 Schema](https://reference.langchain.com/python/langchain-core/tools/base/BaseTool/args_schema)：支持 Pydantic 类型或 JSON Schema 字典；保留 ToolMessage artifact。
- [jsonschema 校验](https://python-jsonschema.readthedocs.io/en/stable/validate/)：Schema 检查与 iter_errors；不做参数类型强转。
- [SQLAlchemy MySQL dialect](https://docs.sqlalchemy.org/en/20/dialects/mysql.html) 和 [Enum](https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.Enum)：InnoDB/utf8mb4、unsigned 类型、JSON、ENUM values_callable；实施时再对本机 2.1.1 接口核对。
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)：lifespan 初始化与清理共享运行时。
