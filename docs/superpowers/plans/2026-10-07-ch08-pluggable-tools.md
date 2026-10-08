# Ch08 Pluggable Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 内置与 MCP 工具动态进入同一注册中心，由统一引擎执行，且聊天建单只有在可信前端确认后才写入并留审计。

**Architecture:** 扩展现有 `ToolRegistry/ToolSpec`，应用启动注册内置工具，每轮加载本地插件、权限配置和两个 MCP Server 的工具，冻结该轮快照供路由、模型、预算和执行使用。执行引擎统一处理 Schema、权限、暂时故障、结果和独立审计；写入准备、interrupt、实际执行分别保存 checkpoint。

**Tech Stack:** Python 3.12、FastAPI、同步 SQLAlchemy/MySQL、LangChain Core、LangGraph 1.2.12/AsyncSqliteSaver、MCP 官方 Python SDK 1.x、langchain-mcp-adapters 0.3.2/MultiServerMCPClient、Streamable HTTP、jsonschema、原聊天页。

**Spec:** `docs/superpowers/specs/2026-10-07-ch08-pluggable-tools-design.md`，用户以「我已经确认」批准，定稿提交 `9fa8e8b`。

**状态：** 用户以「Native」批准计划并选择当前会话逐项实施，末尾独立后端整体审查；执行中。

## Global Constraints

- 实际根目录 `C:/Users/27497/projects/mewhelp-wt/ch02-tools`，已有 linked worktree，分支 `ch02-tools`。复用该 checkout，不新建 worktree、不切分支；既有 `dev-notes/ch07.md` 的未提交修改不纳入本章提交。
- 固定 SDK/Client/Streamable HTTP/LangGraph/MySQL，不替换技术；依赖 `langchain-mcp-adapters==0.3.2`、`mcp>=1.28,<2`、`jsonschema>=4.25,<5`，安装后记录并锁定实际兼容版本，不混用 SDK 2.x API。
- 具体新增 API 先 Context7 查官方定义，再核实本机签名。设计阶段已有查询可复用；发现矛盾或不可行携证据询问用户。
- 工具必须有名称、用途描述、JSON Schema；启动注册内置，每轮刷新 MCP/插件和配置；冻结一轮快照。`query_logistics` 只来自物流 MCP，内置清单移除。
- 未知 MCP 默认拒绝，权限只认本地 `(Server, 工具原名)` 配置。热加载授权可用于验收 3，Server annotations/模型文字不能放行；本章外部 MCP 写工具均拒绝。
- 唯一本系统写工具 `create_ticket`：真实用户明确请求才能准备聊天预览，缺描述/类型追问，不编造；执行须可信确认；原投诉按钮确认入口保持用户流程。
- 写工具实际自动重试次数恒为 0；读工具仅暂时网络故障/超时可重试，默认初试 + 2 次，退避 `0.2/0.4` 秒。查询落空、业务/参数/权限/程序错误不重试。
- 原始 DDL 保存 `sql/ch08-ddl.sql`；`SET NAMES utf8mb4`，InnoDB/utf8mb4，无外键，中文状态严格为 `成功/失败/超时/校验拦下/权限拒绝`。不加唯一键/索引，不修改用户列定义。
- 每个逻辑调用一条最终审计，包括拒绝/校验；重试合一条，独立 Session，失败不拦结果或撤销业务。`duration_ms` 含尝试/退避，不含人工等待。
- 模型不可提供用户/会话/session 工厂/确认凭据/幂等键；每调用独立数据库 Session。现有单 worker、会话锁、完整 checkpoint 与模型投影继续使用。
- 图中只有 JSON 兼容目录/预览/回执 payload；共享实例、handler、BaseTool、Session 和授权对象不能存入 checkpoint。正常模型控制输出契约保持。
- 结果投影必要字段、本地枚举中文映射、`ensure_ascii=False`，保留 RAG evidence artifact；真实动态 Schema 计入 Ch07 预算，不能静默删工具越窗。
- 不实现 Skill、不接真实物流/售后、不建 Server 业务表、不加更多业务系统。预览卡片直接 Vibe Coding，不套前端 brainstorming/TDD/code review；后端安全测试不豁免。
- Prompt/数据先冻结标注集再跑真实评估，替代 TDD；代码先有效 RED/GREEN。每任务完成即时记录四项及证据、提交；不在末尾补过程。
- 运行日志/数据库备份/checkpoint/真实消息/凭据本地 ignored；不提交 `.env`、模型、虚拟环境或旧验收原始数据。已通过的真实评估仅在相关改动/失败时重跑。

## Review Focus

1. 预览期间热撤销权限、工具换 Schema、用户另发一条消息：旧授权不可沿用，取消/拒绝审计齐全。Task 5/7。
2. MCP 返回 malicious description/readOnlyHint、异常组或“业务落空”错误块：不用声明授权，不把故障算成功，不盲目重试。Task 3/4/6。
3. 同批次先查询后等待工单，resume/重启/断流重放：查询不重复，工单不重复；旧按钮回执不覆盖新 interrupt。Task 7/8。
4. 配置正在编辑、一个 Server 宕机、并发两用户刷新工具：原子替换、权限失败关闭、独立可用工具不受牵连、不串会话。Task 1/4/6。
5. 嵌套 Schema/$ref、超长中文结果与工具清单、审计 JSON 序列化/连接卡顿：不联网取 Schema，失败可诊断，预算与审计等待有界。Task 1/2/3/6。

## 文件职责与执行约定

路径均相对上述根目录，交付链接用绝对路径。`tools/contracts.py` 定义跨模块 DTO；`registry.py` 管登记与快照；`validation.py` 管 JSON Schema；`plugins.py` 管可信本地模块；`permissions.py/errors.py/formatting.py/engine.py` 分别管授权、分诊、结果、统一执行；`audit.py` 独立审计；原 `infra.py` 保留导出/兼容调用入口并委托引擎。

`ch08/config.py/mcp_client.py/runtime.py` 管本地配置、MCP 发现、每轮共享工具刷新；`mcp_servers/*` 为独立业务进程。`ch08/ticket_intent.py/confirmation.py/schemas.py/api.py` 管用户原话依据、持久化确认与传输。`ch08/migration.py` 管准确 DDL；`evaluation.py` 与 `eval/ch08/*` 管标注和报告；`scripts/*ch08*` 管可复现服务/验收。

公共类型使用 frozen dataclass，字典数据在边界复制。Task 1 定义：

- `ToolResult` 保留旧 `name/args/ok/content/error/elapsed_ms/attempts/artifact`，artifact 类型扩为 `object|None` 以容纳 MCP structured artifact，RAG 消费处仍核对 RetrievalResult；新增默认字段 `status:str`、`retry_count:int`、`source:Literal['builtin','mcp']`、`mcp_server:str|None`、`tool_call_id:str|None`；从 `infra.py` re-export 保持旧导入。
- `ToolCallContext(conversation_id:int|None=None, session_id:str|None=None, user_id:str|None=None, turn_id:str|None=None, tool_call_id:str|None=None, deadline_monotonic:float|None=None, intent_evidence:dict|None=None, authorization:WriteAuthorization|None=None)`。
- `WriteAuthorization(method:Literal['preview','legacy_button'], confirmation_id:str, conversation_id:int, session_id:str, user_id:str, tool_name:str, args_hash:str)` 仅由后端已验证边界产生，不解析模型参数构造。
- `PreparedToolCall(name:str, args:dict, tool_call_id:str, args_hash:str, schema_hash:str, requires_confirmation:bool, result:ToolResult|None=None)`；`result` 非空代表已经终结的拒绝/校验结果，不再执行/重复审计。
- `ToolSpec` 保留旧构造字段次序 `tool/retryable/timeout_seconds/preserve_raw`，新增 `source/mcp_server/remote_name/permission/input_schema/tool_factory/result_fields/enum_labels/model_visible/available`。`permission` 为 readonly/write/deny；factory 类型为 `Callable[[ToolCallContext],BaseTool]|None`，负责每调用上下文注入。注册时提取 name/description、规范化 Schema；原普通读工具默认 readonly，create_ticket 明确 write。
- `ToolSnapshot.specs` 不可变映射、`fingerprint:str`；`tools()->list[BaseTool]` 仅返回 model_visible、available、已允许的工具；`catalog()->list[dict]` 为 JSON 兼容用途/Schema/来源清单；`get(name:str)->ToolSpec|None`。

PowerShell 验证统一：`$pyCh08 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path`，`$runCh08 = 'artifacts/ch08/20261007-01'`。后续遇权限限制按系统机制申请，不换解释器规避。每任务的测试输出保存在 `$runCh08/process-evidence/`。新增接口缺失可作为首轮 RED，依赖未装/无效夹具/语法错不能冒充行为 RED。

### Task 1：规范注册、Schema 与本地插件热加载

**Files:** Create `src/mewhelp/tools/contracts.py`、`validation.py`、`plugins.py`、`tests/ch08/{__init__,conftest,test_registry,test_validation,test_plugins}.py`；Modify `tools/registry.py`、`infra.py`（仅 DTO re-export）、`pyproject.toml`；Create `requirements-ch08.lock.txt`；Test 另含 `tests/test_tool_registry.py`。

**Interfaces:** `ToolRegistry(specs:Mapping[str,ToolSpec]|None=None, *, engine:ToolExecutionEngine|None=None)`；`register(spec:ToolSpec)->None`；`replace_source(source:str, specs:Sequence[ToolSpec])->None`，source 为所有者键如 `mcp:logistics`/`plugin:echo`，区别于 ToolSpec 的 builtin/mcp 枚举；`snapshot()->ToolSnapshot`；旧 names/tools/get/run/run_all 保留，run/run_all 新增 keyword-only `context:ToolCallContext|None=None`，每个 tool_call 的 id 注入对应上下文。`normalize_schema(tool:BaseTool, schema:dict|None=None, *, forbid_extra:bool=False)->dict`；`validate_arguments(schema:dict,args:object)->list[dict]`；`PluginLoader(directory:Path).refresh(registry:ToolRegistry)->dict`，每个插件只实现 `register(registry)`。

- [x] **Step 1:** 复用/补查 Context7 与发行版接口，安装新增依赖到 `.venv-ch03`，`pip check` 成功后锁定实际新增包及必要传递依赖版本，不升级无关组件。
- [x] **Step 2:** 写 `test_register_appears_without_rebuilding_registry`、`test_snapshot_stays_fixed_after_registration`、`test_duplicate_cannot_override_create_ticket`。写 Schema 用例覆盖字典/Pydantic、嵌套 required、enum、上下界、额外参数、远程 `$ref` 不取网。核心断言：
  ```python
  registry.register(ToolSpec(tool=echo))
  assert 'echo' in registry.snapshot().specs
  assert validate_arguments(integer_schema, {'limit': '2'})
  assert not validate_arguments(integer_schema, {'limit': 2})
  ```
- [x] **Step 3:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_registry.py tests/ch08/test_validation.py tests/ch08/test_plugins.py -q`，记录有效 RED。
- [x] **Step 4:** 实现上述接口与 DTO；Schema 必须是 object、定义合法且只含可解析本地引用。插件先载入临时注册表，整模块成功后原子替换，失败不留下半张工具表；未变文件不重执行，移除文件撤销其工具。
- [x] **Step 5:** 补 `test_partial_plugin_load_does_not_mutate_snapshot`、`test_concurrent_refresh_keeps_complete_catalog`，临时目录新增一个只有注册入口的模块后工具可见。Run 同组及旧 registry 读用例 GREEN。
- [x] **Step 6:** 追记四项和版本/RED/GREEN路径，提交 `feat(ch08): add dynamic registry and strict tool schemas`。

### Task 2：准确审计 DDL、迁移与独立记录器

**Files:** Create `sql/ch08-ddl.sql`、`src/mewhelp/ch08/{__init__,migration}.py`、`tools/audit.py`、`scripts/migrate_ch08_schema.py`、`tests/ch08/test_audit.py`、`test_migration.py`；Modify `db/models.py`、`tests/test_db_ddl_drift.py`。

**Interfaces:** `migrate_ch08(engine:Engine)->dict`；`ToolAuditLog` 映射用户全部列/default/COMMENT/3 个索引，无 FK。`ToolAuditWriter(session_factory:Callable, *, timeout_seconds:float=1.0)`；`async record(result:ToolResult, context:ToolCallContext)->None`，包含失败隔离、有限等待、正常回执重放去重；`existing_call(conversation_id:int|None, tool_call_id:str|None)->bool` 为独立 Session 查询。

- [x] **Step 1:** 写 `test_audit_accepts_orphan_conversation_id`、`test_audit_enum_values_match_supplied_ddl`、`test_migration_checks_existing_table_without_rebuild`、`test_audit_failure_does_not_change_tool_result`、`test_audit_timeout_has_bounded_wait`。断言表列逐项与用户 DDL 一致，`foreign_keys==[]`，未知会话 ID 可写。
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_audit.py tests/ch08/test_migration.py tests/test_db_ddl_drift.py -q`，有效 RED。
- [x] **Step 3:** 原样保存用户 SQL，ORM 复用既有中文 ENUM/SQLite variant 模式；mysql BIGINT/TINYINT/INT unsigned、索引/default/COMMENT 都核对。迁移仅新增缺表，已存在不兼容时明确停止；记录器在独立线程/Session写入，任何审计错误只记运行日志。
- [x] **Step 4:** 补 `test_status_retry_duration_and_unicode_summary_are_saved`、`test_bad_json_or_oversized_error_is_safely_recorded`、`test_replayed_terminal_call_does_not_add_audit`。审计失败后成功工具仍成功，已有 tickets 事务不回滚。Run 同组 GREEN。
- [x] **Step 5:** 追记四项和证据；提交 `feat(ch08): persist best effort tool audits`。真实 MySQL 建表与 SHOW CREATE 放 Task 10，不把 SQLite 当 utf8mb4 实证。

### Task 3：权限、重试分诊、结果格式化与统一引擎

**Files:** Create `tools/{permissions,errors,formatting,engine}.py`、`tests/ch08/{test_engine,test_permissions,test_formatting}.py`；Modify `tools/registry.py`、`infra.py`、`tests/test_tool_infra.py`（更新过期 extra-ignore/任意异常重试预期）。

**Interfaces:** `ToolExecutionEngine(audit:ToolAuditWriter, *, sleep=asyncio.sleep)`；`async prepare(snapshot:ToolSnapshot,name:str,args:dict,context:ToolCallContext)->PreparedToolCall`；`async execute(snapshot:ToolSnapshot,name:str,args:dict,context:ToolCallContext)->ToolResult`；`async reject(prepared:PreparedToolCall,context:ToolCallContext,reason:str)->ToolResult`。prepare 可返回最终失败或需要确认，绝不写业务；execute 总是校验/鉴权，未确认 write 拒绝。`permission_error(spec,args,context)->str|None`；`classify_failure(exc:BaseException)->tuple[str,bool]`；`format_result(spec:ToolSpec,raw:object)->tuple[bool,str,str|None,object|None]`。

- [x] **Step 1:** 写参数拦下/未知权限/缺确认/伪造 confirmed/no retry write/空结果不重试/ValueError不重试/瞬时故障重试/审计失败成功不受影响。关键断言：
  ```python
  assert (denied.status, denied.attempts, denied.retry_count) == ('权限拒绝', 0, 0)
  assert (read_timeout.status, read_timeout.attempts, read_timeout.retry_count) == ('超时', 3, 2)
  assert (write_timeout.status, write_timeout.attempts, write_timeout.retry_count) == ('超时', 1, 0)
  assert writer.calls == 1
  ```
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_engine.py tests/ch08/test_permissions.py tests/ch08/test_formatting.py -q`，有效 RED。
- [x] **Step 3:** 实现 JSON Schema→权限→单次时限→有限退避→结果→独立审计；retry 0.2/0.4 秒、最多 3 次；所有 write 强制 1 次。识别网络异常及 429/502/503/504，MCP ToolException/not_found/非暂时错误不重试。不吞 GraphInterrupt/CancelledError：必要清理/尽力审计后继续传播；await 用户确认不在 execute 内发生。
- [x] **Step 4:** 测异常组混合叶子、deadline 在退避前耗尽、业务落空、MCP内容块/artifact、枚举翻译、长中文截断与真实工单号保留。断言 duration 包含所有尝试且单次审计；同步 write 超时仍不第二次调用。Run 新组与更新旧 infra/registry GREEN。
- [x] **Step 5:** 旧 `execute_tool`/registry.run 委托同引擎，不能独自绕过权限；兼容入口至少识别 create_ticket 的 write 属性。追记四项并提交 `feat(ch08): unify tool execution permissions and retries`。

### Task 4：本地权限热加载、MCP 动态发现和独立 Server

**Files:** Create `ch08/{config,mcp_client,runtime}.py`、`ch08/mcp_servers/{__init__,logistics,aftersales,mock_data}.py`、`config/ch08-tools.json`、`tests/ch08/{test_mcp_discovery,test_mcp_servers,test_config}.py`；Modify `tools/business.py`、`tests/test_tool_business.py`。

**Interfaces:** `ToolSystemSettings(config_path:Path,plugin_dir:Path)`；`read_tool_config(path:Path)->dict`；`MCPToolProvider(client:MultiServerMCPClient, *, discovery_timeout_seconds:float=3.0).discover(server_name:str)->list[ToolSpec]`；`Ch08ToolRuntime(registry:ToolRegistry,engine:ToolExecutionEngine,settings:ToolSystemSettings,session_factory:Callable)`；`async refresh()->ToolSnapshot`；`async aclose()->None`。Server `build_server(*,host:str='127.0.0.1',port:int)->FastMCP`、`main()->None`，支持 `--host/--port`，物流 9021/售后 9022 默认；mock 返回 `{'outcome':'success'|'not_found','data':dict|None,'message':str}`。

- [x] **Step 1:** 写逐 Server 发现/本地授权/新增与删除工具/Server失联隔离/配置半写 fail-closed/恶意 annotations 不放行。断言未授权发现工具登记但不进入 snapshot.tools，直接调用得到拒绝审计；写类 MCP 永不执行。
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_mcp_discovery.py tests/ch08/test_mcp_servers.py tests/ch08/test_config.py -q`，有效 RED。
- [x] **Step 3:** 使用既定 Client、`transport='streamable_http'`、`get_tools(server_name=...)`、`handle_tool_errors=False`；不把 Client 用作 async context manager。配置 content fingerprint、原子权限更新；逐 Server 并发发现，异常独立收集，安全注册重名检查。
- [x] **Step 4:** 用官方 FastMCP 定义 `query_logistics(order_id:str)`、`query_warranty(order_id:str)`、`query_return_progress(return_id:str)`，Schema 保证非空入参；Streamable HTTP stateless/json_response，数据由入参定种子，不导入数据库模块。物流与演示订单事实对齐；只读规则包含结果字段和枚举映射。内置 business 清单移除 query_logistics。
- [x] **Step 5:** 补 `test_removed_mcp_tool_does_not_survive_refresh`、`test_restart_discovery_returns_new_tool_without_new_client`、`test_config_recovery_reenables_tools` 和 mock字段/中文映射测试；Run 同组及旧 business tests GREEN。真实独立进程证明留 Task 10。
- [x] **Step 6:** 追记四项并提交 `feat(ch08): discover tools from business MCP servers`。

### Task 5：用户原话依据、补问状态与可信授权

**Files:** Create `ch08/{ticket_intent,schemas,confirmation}.py`、`tests/ch08/{test_ticket_intent,test_confirmation_rules}.py`；Modify `tools/ticket.py`（调用上下文 factory），不更改原 tickets schema。

**Interfaces:** `ticket_request_patch(question:str,message_id:str,previous:dict|None)->dict` 返回当前原话依据/待补请求/撤销状态；`validate_ticket_draft(args:dict,evidence:dict)->list[str]` 核对描述来自相关用户原文、实际问题非空、类型合法；`make_ticket_preview(prepared:PreparedToolCall,context:ToolCallContext)->dict` 创建 JSON preview；`verify_ticket_confirmation(state:dict,request:TicketResumeRequest,snapshot:ToolSnapshot)->WriteAuthorization` 校验归属/活动预览/args与Schema指纹。`TicketResumeRequest(session_id,user_id,confirmation_id,action:Literal['confirm','cancel'])` 严禁 description/type/confirmed 等额外字段。

- [x] **Step 1:** 写常见明确请求、缺实际问题、用户下一轮补描述、否定/假设/引用/普通投诉、不确定句追问、撤销后旧依据无效。写模型虚构描述与跨用户授权拒绝：
  ```python
  assert validate_ticket_draft({'description':'键盘坏了','ticket_type':'售后'}, only_request_evidence)
  assert not validate_ticket_draft({'description':'键盘坏了','ticket_type':'售后'}, real_problem_evidence)
  assert forged_call_context.authorization is None
  ```
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_ticket_intent.py tests/ch08/test_confirmation_rules.py -q`，有效 RED。
- [x] **Step 3:** 保守识别当前用户肯定请求，排除引用/否定/假设，不能信 assistant/tool 文本；待补请求绑定原用户 message IDs，仅相关补充可用。类型缺失/无足够依据交给 Agent clarify；描述取真实原文或其连续问题片段，不写语义臆测补全。
- [x] **Step 4:** 后端 verifier 绑定 user/session/conversation/tool_call/args_hash/schema_hash/current permission；没有活动 preview 不发凭据。ticket factory 从 context 注入 conversation_id 与 confirmation_id=request_id，每调用独立 Session。补权限热撤销/参数更改/Schema更改/多轮切换意愿测试，Run 同组 GREEN。
- [x] **Step 5:** 追记四项并提交 `feat(ch08): verify ticket intent and confirmation authority`。

### Task 6：共享生命周期、动态 Agent、路由和 Ch07 预算

**Files:** Modify `ch05/{runtime,state,agent,workflow,intent,schemas,prompts}.py`、`ch06/{config,prompts}.py`、`ch07/{context,budget}.py`、`main.py`；Create `tests/ch08/{test_agent_catalog,test_routing_contract,test_dynamic_budget}.py`、`eval/ch08/{routing,tool-behavior}.jsonl`、`eval/ch08/router/{intents,calibration}.jsonl`。Prompt 语义验证见 Task 10；本任务不写镜像 Prompt 字面量测试。

**Interfaces:** `WorkflowContext` 新增 `tool_runtime:Ch08ToolRuntime|None`、`tool_snapshot:ToolSnapshot|None`；`WorkflowState` 新增 JSON `tool_catalog/tool_catalog_hash/ticket_request/tool_queue/tool_cursor/tool_results/ticket_preview/ticket_status/ticket_confirmation_receipts`。`IntentOutput/ClassificationResult` 新增可选 `matched_tool:str|None=None`，只可匹配快照中名字，不代表权限，confidence 未达实测阈值时该字段也置空。`route_intent(intent,scope='general',*,matched_tool:str|None=None,snapshot:ToolSnapshot|None=None)->Route`：有效工具事实请求走 business，其余沿原路由；正式退款/资格/政策不被进度查询挤掉。`measured_prefix(profile:BudgetProfile, tools:Sequence[dict]|None=None)->int` 接收真实工具Schema；每轮提供snapshot转换结果，启动缺snapshot时仍遵守profile工程预留。`Ch06Settings.router_dataset_path:Path` 默认原 `eval/ch06`，Ch08启动明确设为 `eval/ch08/router`。

- [x] **Step 1:** 冻结本章路由/工具语义标签（不据模型结果改标签）。写真实 graph + fake provider 集成测试：新增插件能力到达 Agent、warranty/return progress 走 MCP工具、refund policy 仍走既有路径、普通投诉仍原路径、明确建单/待补问题走主力 Agent；验证不是只测 registry 可见。
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_agent_catalog.py tests/ch08/test_routing_contract.py tests/ch08/test_dynamic_budget.py -q`，有效 RED。
- [x] **Step 3:** open_runtime/lifespan 创建一个共享工具中心与引擎并关闭资源。`prepare_request_context` 先刷新/固定 snapshot，再计算真实 prefix；其 replace 保留 summary_manager。begin_turn 初始化目录 JSON、意愿与队列状态；主力 Agent bind与执行都用该 snapshot，移除静态 registry 列表。
- [x] **Step 4:** 路由背景只把工具描述作为不可信用途数据；匹配名校验本地名单、低置信不强分。更新 Prompt 的固定能力限制、create_ticket 补问/确认规则，保留控制JSON、查询依赖顺序、拒编造；扩展原分类校准 hash 对稳定 Prompt/DTO代码计量，动态 catalog 是运行输入，不能要求每注册一个工具就重新校准才能用。
- [x] **Step 5:** 写全快照 Schema计预算/超长中文清单明确 budget拒绝/中途注册下一轮可见/两用户 handler不串状态测试。测 query_order→query_logistics 按顺序、RAG artifact不丢。Run 新组及受影响 ch05/ch06/ch07 控制代码 tests GREEN。
- [x] **Step 6:** 追记四项和标签freeze状态，提交 `feat(ch08): bind agents and budgets to live tool catalogs`。

### Task 7：持久化 interrupt、确认/取消 API 与恢复

**Files:** Create `ch08/api.py`、`tests/ch08/{test_ticket_graph,test_ticket_resume,test_ticket_api,test_ticket_restart}.py`；Modify `ch08/{confirmation,schemas}.py`、`ch05/{workflow,service,schemas}.py`、`ch06/selection.py`（取消/归属共存入口）、`main.py`。

**Interfaces:** `async prepare_ticket_node(state,context,emit)->dict`；`async await_ticket_node(state,context,emit)->dict`（仅 interrupt 与 resume值接收，无工具副作用）；`async execute_confirmed_ticket_node(state,context,emit)->dict`；`active_ticket_preview(snapshot)->dict|None`；`async pending_ticket(runtime,session_id,user_id)->TurnResult|None`；`async stream_ticket_resume(runtime,request)->AsyncIterator[dict]`；`async resume_ticket(runtime,request)->TurnResult`；`async cancel_ticket_locked(runtime,config,reason)->None`。

- [x] **Step 1:** 写真实 AsyncSqliteSaver graph测试：缺描述追问、预览前无tickets、confirm增1、cancel增0且权限拒绝审计、跨用户/旧卡片拒绝、重复resume同号。测试 `status='waiting_for_ticket'`、SSE `ticket_preview/waiting_for_ticket/ticket_receipt`，resume仅收 id/action。
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_ticket_graph.py tests/ch08/test_ticket_resume.py tests/ch08/test_ticket_api.py tests/ch08/test_ticket_restart.py -q`，有效 RED。
- [x] **Step 3:** 拆工具批次成可持久化单调用节点，以 tool_cursor/tool_results 推进，每次完成写 checkpoint，之后才到独立 interrupt 节点。等待预览持久化可见流水并结束当前 HTTP/SSE。resume前锁会话/校验归属/current interrupt；确认放行或取消拒绝，结果回灌模型。
- [x] **Step 4:** 新增 `/ch08/tickets/resume`、`/ch08/tickets/resume/stream`、`/ch08/sessions/{session_id}/pending`，沿用现有 JSON/SSE 错误事件模式。写请求deadline从 resume 重置，人工等待不算执行耗时；成功发稳定 ticket_receipt 再生成回答，模型失败仍可取工单号。
- [x] **Step 5:** 测 read→interrupt→restart→resume 的 read次数不增加、重复提交与断流同request_id只建1、写超时仅1次且回执只查询、权限热撤销和另发新消息取消旧preview、旧receipt重放不覆盖活动interrupt。Run 新组及原 selection/restart tests GREEN。
- [x] **Step 6:** 追记四项并提交 `feat(ch08): require persisted ticket preview confirmation`。

### Task 8：旧投诉按钮、Ch02 和工作流工具调用迁移

**Files:** Modify `ch05/actions.py`、`ch02/{service,api,prompts}.py`、`tools/ticket.py`、`ch05/workflow.py`；Create `tests/ch08/{test_legacy_boundaries,test_workflow_audit}.py`；Modify 受合同变化影响的 `tests/test_ch02_*`、`tests/test_tool_ticket.py`、`tests/ch05/test_actions.py`。

**Interfaces:** 保持 `/ch05/tickets` 请求/回执和 `create_confirmed_ticket(runtime,request)`；新增 `legacy_button_authorization(state:dict,request:TicketRequest)->WriteAuthorization`，只在真实offer/owner/confirmed=true/参数校验后发凭据。`ch02` 编排新增 keyword-only `tool_runtime:Ch08ToolRuntime|None=None`，生产API注入 lifespan实例；无确认的 legacy模型调用只能拒绝。`load_order(order_id)` 登记为 model_visible=false 内置工具，由 context.user_id核对归属，供固定工作流通过引擎调用。

- [x] **Step 1:** 写 old按钮 confirmed建单/重放同号/伪造拒绝/成功审计，legacy Ch02模型直接create拒绝/校验失败回灌/正常MCP查询审计，workflow load_order也经引擎与审计。拒绝同样检查 tickets增量0。
- [x] **Step 2:** Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_legacy_boundaries.py tests/ch08/test_workflow_audit.py tests/ch05/test_actions.py -q`，有效 RED。
- [x] **Step 3:** 旧后台走共享引擎，保留原按钮编辑/确认与真实回执检索；不能在旧receipt重放时用 aupdate_state覆盖新interrupt。Ch02 API不另造未审计注册表，单轮工具全部经引擎，ToolMessage保持call_id与失败状态。
- [x] **Step 4:** 更新只针对已改变行为的旧测试预期：旧logistics builtin列表、extra参数默默丢弃、任意异常重试、模型隐式create不再有效；原工单编号/中文类型/状态独立性等仍验证。只迁移实际工具调用，不把历史/政策/普通DB读取伪造为工具审计。
- [x] **Step 5:** Run 新组、Ch02 API/service相关 tests、old工具tests和ch05动作tests GREEN；用 `rg` 搜索生产 `.ainvoke/execute_tool/registry.run`，逐个核对无模型执行旁路。追记四项并提交 `refactor(ch08): enforce engine boundaries for legacy tools`。

### Task 9：前端工单预览卡片（Vibe Coding 例外）

**Files:** Modify `src/mewhelp/static/index.html`；Create `tests/ch08/page-ticket-preview.js`（验收脚本，不做前端TDD）。

**Interfaces:** 处理 Task 7 的三种 SSE事件和pending API；卡片仅展示后端preview内容，按钮严格为「确认提交」「取消」，发送 `{session_id,user_id,confirmation_id,action}`。保留原投诉按钮与订单选择器。

- [ ] **Step 1:** 直接制作卡片与提交/取消/已取消/工单号状态，用 textContent插入描述；提交中防重复点击，失败保留卡片，确认成功有稳定回执；沿现有聊天视觉直接调整。
- [ ] **Step 2:** 接入刷新/会话切换/客服重启后的pending恢复与迟到响应过滤，不将别的session卡片复用，不将用户描述当HTML。原投诉按钮代码只做必要兼容，用户流程不变。
- [ ] **Step 3:** 用现有 page-smoke模式补浏览器验收脚本，后端允许真流程后实际click confirm/cancel并查DB结果；无前端测试先行、无前端独立code review。用户描述效果时直接改并复验受影响场景。
- [ ] **Step 4:** 追记四项及浏览器报告路径；视觉待反馈时保持未提交，反馈认可或进入finish统一提交 `feat(ch08): show ticket preview confirmation cards`。

### Task 10：真实评估、六项验收与交付

**Files:** Create `ch08/evaluation.py`、`eval/ch08/freeze.json`、`eval/ch08/router/freeze.json`、`scripts/{run_ch08,run_ch08_mcp}.ps1`、`smoke_ch08_acceptance.py`、`docs/ch08-demo.md`、`tests/ch08/test_acceptance_contract.py`；Modify Task 6 已建立的本章标注集、`.gitignore`（本章本地运行原始产物）、`ch06/evaluation.py`（校准数据路径桥接/动态capability标签支持）、`ch05/intent.py`（按settings验证dataset引用）。

**Interfaces:** `python -m mewhelp.ch08.evaluation freeze|calibrate-router|run --dataset eval/ch08 --outdir <path> [--calibration <router.json>]`；freeze覆盖标注与split/hash，失败exit1；calibrate-router复用既有测量/threshold算法产生完整 RouterCalibration；`run_ch08.ps1 -Port 9020 -RouterCalibration <path> -ToolConfig config/ch08-tools.json`（沿已存在 context/policy profile）；`run_ch08_mcp.ps1 -Server logistics|aftersales -Port 9021|9022`；`smoke_ch08_acceptance.py --base-url http://127.0.0.1:9020 --outdir <path>` 输出summary/results与必要DB增量/审计证据。脚本不得自行关闭未知进程。

- [ ] **Step 1:** 写验收runner的failure合同测试，缺call_id/来源/时间/DB增量、未覆盖任一验收项、模型错误假称成功时report失败，不用hardcoded成功计数。Run `& $pyCh08 -X utf8 -m pytest tests/ch08/test_acceptance_contract.py -q` RED，最小实现后GREEN。
- [ ] **Step 2:** Prompt/数据采用标注评估：完成不少于设计列出的12类场景，含否定/引用/类型不明、描述不足、普通投诉、query not_found/失败、恶意MCP描述、在保/进度、动态能力、新工单号；calibration与acceptance split隔离。根目录freeze覆盖本章新集合，router子目录使用既有Ch06 manifest格式和intents/calibration文件，不能拿不兼容manifest喂旧verify_dataset。校准messages携带样例tool_catalog，运行时按router_dataset_path核对。freeze后运行真实router校准与相关工具/路由评估，保存cases/requests/tool_calls/checks/report；只重跑受影响失败项，不改冻结标签凑通过。
- [ ] **Step 3:** 运行 `& $pyCh08 -X utf8 -m pytest -q`、`& $pyCh08 -m ruff check src tests scripts` 及相关前端脚本；默认pytest不误触发全历史模型eval。只有相关新改动/失败才重复检查；确认兼容依赖 `pip check`。
- [ ] **Step 4:** 检查端口/三个进程/现有Docker依赖；真实MySQL `SELECT 1`、建表迁移与 `SHOW CREATE TABLE tool_audit_logs` 检查中文ENUM/无FK/索引/charset。开启两个独立Server和客服，页面/health及真实HTTP/SSE均可访问；默认9020客服、9021物流、9022售后与旧9017/9018分开，不覆盖旧checkpoint。
- [ ] **Step 5:** 执行六项验收并保留证据。动态builtin：新增插件只有注册；动态MCP：新增Server工具+本地授权，只重启该Server，前后客服PID与核心hash相同。三个业务查询要有各Server来源审计；补问/确认/cancel真实浏览器点击，tickets delta为1/0，实际工单号与回执一致。
- [ ] **Step 6:** 在隔离验收配置注入有界async延迟，读3次最终超时/retry2，write在真实已确认调用仅执行1次/超时/retry0，测耗时、审计、诚实回答；不在默认运行代码开放故障控制给模型。同步写超时另由后端测试证明线程可能继续完成和只查回执，不自动重写。
- [ ] **Step 7:** 再完成refresh/切会话/客服重启pending恢复、旧投诉按钮真实回归。交付docs记录准确启动/演示命令、可达地址、实际测试计数、六项报告路径与DB查询；追记本任务四项并提交 `test(ch08): verify live tools and confirmed ticket flows`。

## 整体评审与 finish

- [x] 使用 requesting-code-review 做独立后端整体审查，输入批准spec、plan、固定基线 `9fa8e8b` 与实际diff；重点按 Review Focus/六项需求找具体触发bug。前端卡片遵守用户例外，不交给该code review；真实浏览器验收仍保留。
- [x] 对有效反馈使用 receiving-code-review 修复，补相关回归并验证；即刻记 code review结论/修复证据到ch08笔记。不把所有tests再重复跑作流程仪式。
- [ ] 使用 verification-before-completion核对最终证据和工作区状态，随后 finishing-a-development-branch；仅在功能与必需验收已完成时声称完成。部署/push/PR按当时已有授权处理，不凭本章实施请求自动推远程。
- [ ] finish追记四项；交付 `docs/ch08-demo.md` 中的真实演示命令、测试/评估/验收结果、`dev-notes/ch08.md`、剩余限制和提交状态。不得以计划文件或healthz代替功能交付。

## 计划自审结论

设计 §1–4 → Task 1/4；§5 → Task 1/3；§6 → Task 5/7；§7 → Task 8/9；§8 → Task 2/3；§9 → Task 6/8；§10 → Task 10与整体评审/finish；§11 → 全局Context7/版本规则。六项用户验收均有Task 10真实证据路径，全部Review Focus均有对应任务测试。

公共DTO/签名由Task 1固定，后续只消费同名接口；授权/预览由Task 5提供、图/API由Task 7提供、前端由Task 9消费。移除静态能力时同步处理路由与校准hash，但新增工具目录作为运行输入，不要求新工具触发重新校准/改核心代码。用户已以「Native」批准并实施。Task 1–8已完成；Task 9实现/DOM通过，真实页面结果待用户反馈；Task 10真实评估26/26与六项HTTP/DB验收通过，独立审查6项修复通过，追加消息配对修复14项通过。路由校准05完成16条；中断后的完整回归966通过、5排除；最新Ruff通过。最终报告已交付，当前Docker启动错误阻止客服恢复，整体finish仍以实际页面验收和受影响HTTP复验为条件。
