# Ch07：当前会话的三层上下文管理

日期：2026-10-04。状态：用户回复「已确认」，书面设计获批、brainstorm 定稿。进入实施计划编写；计划仍需单独评审并选择执行方式，产品代码和数据库尚未修改。

## 1. 目标与范围

用户关键原话：「只管当前会话，不跨会话、不存用户画像」「装得下就不压，压缩是成本不是美德」。

完整原文能够回载、继续聊天；模型能借助原文、规则投影和一次性摘要理解本会话早期事实。消息账本和 checkpoint 不被裁剪结果覆盖。验收同时要求普通配置连续至少 20 轮无降级/摘要，以及指定 18000 演示窗口下出现完整的降级、后台摘要与最早订单追问。

技术选择固定：LangGraph State/add_messages/checkpoint、LangChain trim_messages、MySQL conversations/messages/conversation_summaries。沿用已有官方 AsyncSqliteSaver、同步 SQLAlchemy + 独立线程 Session、单 worker。checkpoint 的 SQLite 与业务表的 MySQL 是两种现有职责，不增加另一种 checkpointer、队列产品或长期记忆系统。

只处理同一 conversation_id 的历史。侧栏按 user_id 列会话，选中某一会话后上下文只来自该会话。保持转人工与建工单的独立用户动作及 Ch06 等待订单选择/退款申请行为。历史不建立向量索引，不按主题重要度筛选，不建立用户画像。

## 2. 已验证基线

实际根目录 `C:/Users/27497/projects/mewhelp-wt/ch02-tools`，已有 linked worktree，分支 `ch02-tools`，基线 `0107c00`，开始时工作树干净。现有主入口 `/ch05/chat/stream`、JSON Agent 与 Ch06 恢复共用工作流。

| 接缝 | 已有行为 | Ch07 设计 |
| --- | --- | --- |
| ch05/state.py | messages 已挂 add_messages；模型输入 agent_messages 独立 | 保留 reducer，新增只读上下文快照与边界；节点增量追加原始消息 |
| ch05/service.py/runtime.py | thread_id=session_id；官方 SQLite checkpoint；无状态时从 MySQL bootstrap | 恢复原文与稳定消息 ID，每轮读取 MySQL 最新摘要/边界 |
| ch05/agent.py | 固定 2048 trim；证据/最终控制动态 system | 统一构造上下文；所有可变背景放用户消息后；保留真实 Function Calling |
| ch06/understanding.py | 最近 20 条、每条 2000 字；指代来源须在近期可信实体中 | 使用共享 history_ctx；摘要提供可追溯的订单出处，去掉二次按条数截断 |
| ch05/intent.py | 分类只收消解后的当前句 | 分类和指代消费同一历史快照，避免两个窗口口径 |
| ch05/workflow.py | 工具原文写 messages；checkpoint messages 只追加用户/最终答复 | 本章工具原文只入完整 State/checkpoint；MySQL 原文账本保留用户/客服可见内容 |
| static/index.html | 单会话本地缓存、引用/反馈、订单等待恢复 | 会话侧栏、原文回载、原有卡片恢复；旧缓存仅用于断网降级 |

本机 `.venv-ch03` 只读检查：LangGraph 1.2.12，checkpoint-sqlite 3.1.1，langchain-core 1.6.5，langchain-openai 1.6.6，FastAPI 0.141.1，SQLAlchemy 2.1.1。早期 `.venv` 没装 LangGraph，实施/验证使用 `.venv-ch03`。

## 3. 组织方案与选择

1. **推荐：在现有工作流中接入统一上下文管理模块。** 独立负责预算、边界投影、摘要调度与日志；指代、分类、Agent 共用快照。保留当前图的中断、恢复、流式输出和业务执行逻辑。
2. 全部上下文工作封在单个 Agent 节点：接入较少，但绕过 Agent 的闲聊/投诉轮仍看不到同一历史，后台摘要与待点选恢复难统一。
3. 新建独立上下文子图：仍可使用指定栈，但需要父子 State、checkpoint 命名空间及中断映射，本章没有对应收益。

推荐方案的上下文组件包含 token 估算/预算包、会话仓储、纯投影构造、后台摘要管理器、观测日志五个边界。仓储只做事务与查询，模型摘要通过可替换的 async 接口，投影构造不做模型请求，不更新 State 完整历史。

## 4. 用户提供的 DDL 是权威契约

用户追加了两步 DDL，拟原样保存为 `sql/ch07-ddl.sql` 与 `sql/ch07-layers.sql`，实施迁移必须检查并应用两步。均有 `SET NAMES utf8mb4`。

- conversations 新增 `summary TEXT NULL`、`summary_upto_msg_id BIGINT UNSIGNED NULL`、`layer1_from_msg_id BIGINT UNSIGNED NULL`，列位置及 COMMENT 按用户原文。
- conversation_summaries：`id BIGINT UNSIGNED AUTO_INCREMENT`、`conversation_id BIGINT UNSIGNED NOT NULL`、`seq INT NOT NULL`、`from_msg_id/upto_msg_id BIGINT UNSIGNED NOT NULL`、`content TEXT NOT NULL`、`created_at DATETIME DEFAULT CURRENT_TIMESTAMP`。
- 保留用户指定的主键、`uk_conv_seq(conversation_id,seq)`、`idx_conv_upto(conversation_id,upto_msg_id)`、InnoDB/utf8mb4。不擅自补另一种外键、额外摘要状态列或替代字段。

**边界按后续 DDL 的闭区间定义**，不是字段名字字面上的「从这条起」：

```text
S = summary_upto_msg_id，L = layer1_from_msg_id，NULL 逻辑上表示 0
id <= S              已由摘要覆盖
S < id <= L          Layer 2（规则投影）
id > L               Layer 1（原样）
保持 0 <= S <= L；全在同一 conversation_id 的已提交消息范围内。
```

L 是最近原文之前的截止 ID。降级完整的一轮时，把 L 移到该轮最后一条已提交消息 ID。数据本身不移动、不改写、不删除。摘要段 `[from_msg_id,upto_msg_id]` 是闭区间，下一段从 S 之后本会话第一条未覆盖消息开始；不能假设全库消息 ID 连续，也不能用 S+1 去造不存在的消息。

`conversation_summaries` 是摘要段的权威记录。`conversations.summary` 是最近若干段按 seq 拼接的读投影，更新它不等于重新摘要。不将摘要伪装成 messages 行。迁移重跑先核对列/类型/索引；部分执行时只补缺项，类型不符明确报错，避免 `ADD COLUMN` 重复失败或吞掉漂移。

## 5. 完整历史、消息 ID 与工具历史

State.messages 保留当前会话原文、真实 AI tool_calls 与成对 ToolMessage；人设/检索背景/内部分类 JSON 不当作对话历史持久追加。每个节点只返回新产生的消息，稳定 ID 让 add_messages 对同 ID 更新且不重复追加。

MySQL messages 保存用户原文与客服可见回答，包括等待订单和恢复回答；本章不新写工具返回正文。当前轮 ReAct 工具原文及工具结构仍保留在完整 State/checkpoint 和已有 trace 中。旧章节已经写入的工具账本行保持历史兼容，GET 原文回载只显示用户/客服可见内容。

原文轮次使用既有幂等事件键关联已提交 MySQL 行 ID；补充消息元数据记录账本 ID、turn_id 和工具所属轮。不会因为 reducer ID 是 UUID 就拿 UUID 与 BIGINT 边界比较。工具没有 MySQL 行：它的层归属继承所属轮的账本边界，不能把没有工具行误判为工具占用为零。完整历史 token 估算纳入实际工具消息。

当前用户消息从本轮开始即增量加入完整 State。构造模型输入时，历史快照明确排除当前 turn_id，再单独添加当前用户原话，防止 current user 双份。当前工具消息按调用次序追加；最终用户可见回复只追加一次。等待/恢复/取消沿用既有幂等规则，处理后完整历史不重复用户问题。

有 checkpoint 时以它保留的完整消息为基础，摘要与锚点每轮从 MySQL 获取；丢失 checkpoint 时从 MySQL 恢复可见原文与摘要，不宣称 MySQL 能恢复从未入该表的工具原文。新服务仍可继续聊天，工具细节再次需要时重新查询。

## 6. Layer 1/2 的纯投影

Layer 1 调用 LangChain trim_messages：`strategy='last'`、`start_on='human'`、`allow_partial=False`，使用同一 CJK token_counter。只选完整轮/完整工具调用组，绝不截断 Layer 1 正文。根据返回的最早保留轮计算新 L，更新边界，不把返回消息列表写回 State.messages。

Layer 2 每个用户消息原字保留；客服可见答复保留前 60 个 Unicode 字符，增加明确的省略标识；工具结果变为一行工具名称/调用标识/对象 ID/状态，不保留长结果。工具标识从实际调用生成，不用 LLM 编写，不臆测业务事实。原文和配对结构保留在完整历史，投影是每次渲染得到的副本。

分层按整轮和工具配对组，避免半个 assistant tool_calls 或孤立 ToolMessage。历史工具不需要再执行。单个超大历史轮无法进入 Layer 1 时整轮降级，不能为保住「一轮」偷偷截原文；启动自检针对标定的一轮稳态占用，并在运行时记录超大轮的特殊结果。

Layer 2 触发用**规则投影后的实际 token 占用**，不按 messages 表行数，也不按累计二十轮这种计数触发。Layer 1 原始用量纳入工具，Layer 2 投影纳入工具的一行标识。日志同时写出两种占用，能解释大结果为何促成原文层降级。

## 7. 预算公式及可审查的初始标定方案

用户答复：「没有现成公式，请给出可审查的标定方案」。以下数字是本章拟采用的**工程预留和标定起点**，不是已经完成的真实 token 测量。

区分模型单次上下文窗口 W 与原 `CH05_TOTAL_MODEL_TOKENS=64000` 的一轮多次调用累计成本，两个限制各自校验。64000 不能用作 W。

```text
固定开销 F = prefix + evidence_reserve + summary_reserve + output_reserve + safety
本轮峰值 P = max_user_input + tool_observations_peak + control_and_framing
实际可分配 A = max(0, W - F - P)
期望历史 D = desired_turns * steady_turn_tokens
历史 H = min(D, A)
Layer 1 = max(0, floor(7 * H / 10) - 1)
Layer 2 = floor(3 * H / 10)
未分配的 1 token 作为历史段拼接舍入余量；使用整数运算，不依赖 float 误差。
```

初始预算包：

| 组成 | 起始值/公式 | 校准与检查方式 |
| --- | --- | --- |
| 固定人设/红线/稳定工具 schema | 1350 token 预留 | 每种主模型调用前缀实际计数；超出则重算预算，不能伪造 prefix 长度 |
| 检索证据 | RERANK_TOP_K × 400 token | 400 是每条起始估计；实际证据原文另计，较长时缩小可用历史，不能拿省略后的条款冒充全文 |
| 摘要投影 | 500 token | 逐段完整注入；超过时按最新连续段组成投影，不改摘要段原文 |
| 输出 | MAX_OUTPUT_TOKENS | 在实际模型参数和预留两处使用同一个值；流式 length 收尾沿用既有处理 |
| 安全余量 | 500 token | 加入估算误差、角色/模板偏差；真实 usage 校准后复核 |
| 当前用户输入 | MAX_USER_INPUT_TOKENS | 输入按同一估算器校验；超限明确返回输入过长，不截用户原话 |
| 本轮工具观测峰值 | MAX_AGENT_STEPS × TOOL_RESULT_MAX_TOKENS | 一个 step 按一次工具执行计；并行批次每个调用分别占一个名额，总数受同一上限约束 |
| 控制与格式峰值 | 400 token | 累积工具参数、决策帧和纠正额外输入的起始预留；实际计数超过时重算，而不隐含允许超窗 |
| 期望历史 | 40 × 1064 = 42560 token | 每轮起始稳态：用户 256 + 回答 512 + 常见工具文本 200 + 结构 96；用标注多轮样例校准 |

按用户指定演示变量：

```text
W=18000，output=2000，user=2000，steps=3，tool_result=1200，rerank=5
F=1350 + 5*400 + 500 + 2000 + 500 = 6350
P=2000 + 3*1200 + 400 = 6000
A=18000 - 6350 - 6000 = 5650
H=min(42560,5650)=5650
Layer 1=floor(5650*7/10)-1=3954
Layer 2=floor(5650*3/10)=1695
```

这是可核对的配置推导，不将 H 直接硬编码成 5650。默认软件窗口拟保守设为 128000，输出/输入/步数/工具/检索初始默认采用 4096/4096/4/1200/10，则 A=108258，H=42560，Layer 1=29791，Layer 2=12768。是否能留 20 个**实际演示轮**，最终以原始请求计数和真实会话证据证明，不把平均稳态称为所有最大输入的保证。

软件窗口是显式配置的上限，必须不大于当前供应商实际窗口；不能凭 `deepseek-chat` 旧别名猜能力或自动改共享模型配置。主力/前置模型若窗口不同，按实际使用模型分别做调用前校验。

**本设计对 steps 的明确提案**：MAX_AGENT_STEPS 管整轮工具执行次数，批次可以并行但不能逃出总额度；收尾决策和回答单独预留。原 CH05 的 max_decisions/max_tools 不得仍隐含允许 8 次工具结果进入上述 3 次观测的预算。纠正调用计入现有累计调用账本，也对实际输入做单次窗口检查。映射在实施计划中写明并测试，不能只新增 env 而不接入运行限制。

TOOL_RESULT_MAX_TOKENS 是单次结果可送模型的上限。现有工具输出短，通常不触发。发现更大结果时保留 checkpoint 原始输出，明确标记超限并走有界回复；不得把 Layer 1 的原文悄悄截成 1200 token 后仍宣称「未压缩」。

启动先输出完整预算分解，检查 H 与 Layer 1 能容纳至少一个标定的完整稳态轮。失败产生 ERROR「上下文预算不足」并阻止错误配置接收聊天请求。当前轮实际固定内容超过预留时，在该轮模型调用前用实际占用重算可分配预算并记录差额，不等到上游拒绝才发现。

**联合校准**：起始 CJK 1 字=1 token，ASCII 约 4 字符=1 token，另计角色和 JSON/schema 结构。冻结标注样例覆盖纯中文、英文混排、订单号/手机号、长答复、并行工具和真实工具 JSON。记录供应商 input/output usage、实际 messages/tools 序列化长度、估算差值；以高分位误差校准 CJK 系数与结构预留，随后用同一新估算器重算 prefix/证据/summary/稳态/峰值预算包。估算器与预算包绑定同一版本/hash，不能只改单字系数或只调 H。

演示预算包的起始数字须通过上述标定和请求总量安全检查。若真实测量证明该开销组合不能安全支持 5650，携带分项报告向用户确认修订，不为了凑数降低实际结果或隐藏误差。

## 8. 后台分段摘要

每轮上下文准备读取 S/L，先按 Layer 1 实际预算移动 L；Layer 2 投影实际用量超过其预算时记录 trigger，并创建独立 async 任务。同一会话只允许一个进行中的摘要任务；不同会话共享小型并发额度。任务由 lifespan 管理，使用自己创建的 Session，不使用前台请求的 Session。

触发时固定 `(old_S, snapshot_L, from_id, upto_id)`，只摘要 `old_S < id <= snapshot_L` 的完整新批次。**后台任务不会读取一个后来变大的 L 然后越过本次输入范围更新 S。** 模型调用期间不持有前台 session 锁或 MySQL 行锁，不 await 摘要来得到当前回复。

摘要输入使用新批次的原始用户/客服原文及实际工具事实；先按摘要模型窗口分完整轮分批，防止摘要任务自己溢出。旧摘要仅作为背景，明确排除在本次抽取范围外；不能把「旧摘要+新对话」合并改写成另一版摘要。seq 连续递增，一段覆盖一个完整区间，每个区间仅在摘要成功并落盘后被标为覆盖。

模型输出目标 30–200 中文字、仅事实与诉求：问过商品、原始订单号/手机号、明确请求、仍未解决问题。不留寒暄、不补没有出现的状态、金额、批准结果。确定性检查数字和标识必须来自本批原始消息或明确的本批工具结果；来源不能是旧摘要模型猜测。语义忠实度由预先冻结的正/反例真实评估，不用几条正则声称完全杜绝幻觉。无可保留业务事实的批次也形成一条明确的无业务事实记录，避免无限重复同一闲聊区间。

模型完成后开短事务锁 conversations：重查 S 等于 old_S，区间归属正确，计算下一个 seq，插入不可变摘要段；S 更新到本次实际最后覆盖 ID，summary 按新的段落重建最近连续段投影，L 保持前台可能已经推进的值。摘要插入、S 更新和投影更新在同一事务提交。`uk_conv_seq` 加行锁防重复；竞争或范围已覆盖时 skip，失败 rollback，绝不先推进 S 后写摘要。

前台读取已提交摘要的新快照，后台不异步改 graph State 以免覆盖正在进行的用户轮。失败保留未摘要的 Layer 2，下次有新上下文准备且仍超预算时再触发，不开无限立即重试。任务退出释放进行中标记；关机给有界收尾时间，未提交任务取消并记录，下次启动/下一轮按未推进的锚点恢复处理。

摘要进行中仍保留未覆盖的 Layer 2 投影，不能假装它已经被覆盖。前台始终检查整个实际请求加剩余 ReAct 峰值和输出预留是否可装入窗口；Layer 2 预算是触发阈值，允许它短期占用历史总池里原文层未占满的空间。整个 H 都无法容纳时提供明确的上下文不足有界回复，不等待后台，也不静默删用户事实。慢摘要评估要特意覆盖这一分支。

## 9. 模型上下文顺序与缓存边界

主模型使用以下顺序：

```text
1. 一个稳定 system（人设、红线、控制/回答固定规则）；稳定 tools 参数
2. Layer 2 规则投影，按原会话次序
3. Layer 1 最近原文，按原会话次序
4. 当前用户原话，只出现一次
5. 一条 HumanMessage 背景数据：本会话摘要投影 + 本轮检索证据 + 已解析当前问法/实际业务数据
6. 本轮 ReAct 新增的 assistant tool_calls / tool messages 成对顺序
```

摘要、证据、决策、订单事实、最终生成控制值属于可变数据，不能独立追加 system。稳定规则覆盖普通控制、纠正与最终正文；阶段信息放背景/控制数据。工具定义仍使用真实 bind_tools/schema，不把它们改成自然语言伪工具。

顺序验收检查实际 ChatOpenAI 请求 messages 与 tools，固定前缀和工具 schema hash 跨轮不变；日志不能只打内部准备对象。前缀缓存命中率另受供应商影响，应用只能保证输入组织，不以 mock 的命中率代替实际缓存证明。

指代/分类使用同一 token 管理后的 history_ctx，包含摘要行、Layer 2 与 Layer 1，原句仍保留供消解校验。去掉 understanding 对 history 的最近 20 条/2000 字二次裁剪。摘要来源标识从段的 `[from_id,upto_id]` 与原始账本/State 实际出处建立，不让摘要正文自由生成可受信任的 message ID。

当前用户显式订单仍优先；最早/上次等指代需要引用实际可见摘要或窗口的可验证来源。多个历史订单时，不能因为全部实体不唯一就拒绝一个已有明确「最开始」出处的引用；也不能允许模型自由挑一个订单。保留用户归属核验、否定/数字/条件保护和不明确时澄清。摘要不作为商品/物流最新状态或退款资格证据，确定对象后仍查工具/政策。

## 10. 摘要投影的有限窗口

按用户后续 DDL，summary 是「最近几段梗概拼成的投影」。从最新 seq 倒序选择能整体放入 500 token 预留的连续段，再正序拼接；一个段不能半截注入，不回炉改写旧段。全部段仍存 conversation_summaries 可审计，不按语义重要度另选段。

这意味着有限窗口不能保证无限久以前的每个事实永远送进模型。20+ 轮演示的首个订单摘要必须仍在投影中，验收记录当时注入的完整段和覆盖区间；若实际演示在追问前已产生过多段使首段退出，先调整摘要/窗口预算并重新审查，不伪称记忆成功。无限会话保证需要新的选择规则，本章按用户要求不加入。

## 11. 观测日志

建立 UTF-8 的 `log/app.log` 文件 handler，同时保留开发终端日志。不得依赖只有 mewhelp.ch05 的现有 stderr logger 配置使 Ch07 消息消失。

- `history_ctx`：每轮前置阶段必打，含 session/conversation/turn ID、S/L、摘要全文及段 ID、规则投影和原文窗口逐条 role/id/content、窗口条数、分项估算及预算。指代/分类实际使用它的同一版本；闲聊/投诉/兜底也有这一条。
- `model_ctx`：每次主力 Agent/最终回答/纠正/资格判断调用前打实际 messages、完整背景摘要与证据、绑定 tools/schema hash、窗口条数、history/总输入/预留 tokens、模型/调用用途。正文不能省略成字符数或只打印摘要预览。
- 降级：明确输出「层1 降级 X→Y」，X/Y 为变更前后边界，并有实际用量与预算。
- `summary trigger/start/done/skip/fail`：session、seq/目标段、old_S、snapshot_L、from/upto、实际 Layer 2 tokens/阈值、原因、排队/模型/落库/总耗时；完成输出「summary done 第N段」。

日志是用户要求的原样上下文证据。只写实际消息和模型输入，不写 Authorization、数据库密码或 API key。log/app.log 保持本地运行产物，不提交真实用户原文到 Git。回归中检查配置加载后确实生成文件；日志内容与捕获的实际请求严格匹配。

## 12. 多会话页面与只读 API

`GET /api/conversations?user_id=...` 返回当前用户全部会话：id/session_id、created_at/updated_at、首个用户问题预览、has_summary/summary_count。按 created_at DESC、id DESC 排序，预览以首问为准，不用最后回复代替。GET 不创建会话或触发摘要。

`GET /api/conversations/{id}/messages?user_id=...` 核对会话归属，不存在/归属不符为 404；返回 id/session_id 和按消息 ID 顺序排列的用户/客服原文及已有 citations/必要回载元数据。历史是否摘要不影响返回原文。沿用本课程已有 user_id 身份约定，不把可传 user_id 称为登录鉴权。

左侧会话栏有「新对话」、首问预览和已摘要标记。新对话只清空当前页选中会话与草稿，第一次发言由既有聊天 API 创建会话；旧会话继续存在。切回旧会话先获取完整原文，再更新 session_id 和 UI，随后调用现有 pending 恢复接口回载待选订单/申请状态。

聊天输出过程中禁止切会话/新建，避免 SSE 帧写入另一个会话；加载切换使用请求序号/取消机制，迟到响应不能覆盖新选中会话。读取失败保留当前聊天与本地缓存，侧栏失败不弹全屏错误、不影响发送。会话建立/一轮完成后刷新列表。移动端可收起侧栏，继续复用现有原生页面和渲染器。

## 13. 验证、校准与交付

可单测逻辑先 RED 再 GREEN：预算算式/校准配置耦合、整轮边界、无原文污染、工具 ID 归属、事务/并发快照、消息 reducer 幂等、迁移两步/部分重跑、GET 归属与只读、实际请求/log 对齐。使用确定性可控的慢摘要模型证明用户 done 不 await 摘要完成。

Prompt/标注数据按用户要求替换 TDD：先冻结新 Ch07 标注样例与评价规则，跑真实摘要/指代/分类样例，再调整 Prompt。包括订单/手机号精确保留、明确否定、不虚构已退款/签收、多个订单「最早」与「上次」区别、闲聊丢弃、旧摘要不重复抽取、新批次事实来源、长工具输入、后台失败后重试。

上下文改动会使 Ch06 的 understanding/intent/calibration 指纹失效；接入时需生成反映当前代码的新校准证据，保留旧标签与旧报告，不关闭哈希核验、不把旧成功报告时间改成本次。运行与改动相关的必要校准和回归；同样输入/模型/代码已经有有效证据时复用，不无理由反复付费。

实际验收：

1. 同一用户两个新会话，不互相借历史；旧会话完整原文回载后继续，刷新恢复待点选。
2. 默认包至少 22 轮普通业务对话，所有窗口检查通过，降级与 summary trigger 都为 0。
3. 演示 6 个变量严格采用用户值，预算分解为 5650/3954/1695；至少 22 轮含足够用户/客服/工具占用，出现层1降级、Layer 2 超预算 trigger、后台 start/done、summary 新行与边界推进。最后追问「最开始那个订单后来怎么说」，验证订单号、原诉求和实际重新查询结果。
4. 模型请求原样日志、真实 usage、MySQL 摘要行/消息原文、SSE 首 token/done 与摘要任务时间戳共同证明；启动健康或全 mock 不替代这一组证据。
5. 浏览器验证新建、切换、历史全文、继续聊、已摘要标记、侧栏读取失败及迟到响应。保留当前页面引用/反馈/业务卡片功能。

最终交付 README 中的默认启动/演示环境变量/两步迁移/验收/日志搜索命令，确定性与真实评估结果，独立 code review 与必要修复结果，`dev-notes/ch07.md`。每阶段当时追记用户原话、产出、纠偏、返工，不收尾补写伪阶段。

## 14. 官方接口查证

本次先通过 Context7 MCP 查询再比对本机接口：

- LangGraph `/websites/langchain_oss_python_langgraph`：[State/reducer](https://docs.langchain.com/oss/python/langgraph/graph-api)、[持久化](https://docs.langchain.com/oss/python/langgraph/persistence)。本机确认 AsyncSqliteSaver.from_conn_string(str) 是 async context manager；沿用已有 astream/durability/context，不照搬文档更新示例改变运行协议。
- LangChain `/websites/reference_langchain`：[trim_messages](https://reference.langchain.com/python/langchain-core/messages/utils/trim_messages)、[bind_tools](https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI/bind_tools)。本机确认 token_counter callable、allow_partial/start_on 签名。
- SQLAlchemy `/websites/sqlalchemy_en_20`：[事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)、[Session 并发边界](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)、[with_for_update](https://docs.sqlalchemy.org/en/20/core/selectable.html)。本机 2.1.1，实施只用现场可核对接口。
- FastAPI `/websites/fastapi_tiangolo`：[lifespan](https://fastapi.tiangolo.com/advanced/events/)、[后台任务与独立资源](https://fastapi.tiangolo.com/tutorial/background-tasks/)、[GET 查询参数](https://fastapi.tiangolo.com/tutorial/query-params-str-validations/)。长摘要使用 lifespan 管理的任务，不持有请求依赖 Session。
- 模型窗口需与实际供应商核实。[DeepSeek 官方当前模型说明](https://api-docs.deepseek.com/quick_start/pricing/)与[模型元数据](https://api-docs.deepseek.com/api/list-models/)提供能力信息，但不能将现有网关的旧别名自动当作官方当前模型；本设计不更换共享 LLM_MODEL。

本章未修改 Milvus/Langfuse 接口；如后续计划确实涉及具体接口，须再先查 Context7，不能因这里列了框架名就视为已核对其所有用法。

## 15. 本次自审与用户审核点

**2026-10-04 实测修订，用户已批准**：「接受安全标定后的 5300 / 3709 / 1590（推荐）」。初始 5650 工程包在冻结混排/工具样例中最高低估 277 token，失败原始报告保留于 artifacts/ch07/20261004-native/tokens-calibration-01。联合 v2 包改为中文 1.2 token/字、ASCII 3 字/token、工具模板额外 208、前缀预留 1700，其余预算参数保持原定义；F=6700、P=6000、H=5300、L1=3709、L2=1590。默认 H=42560、L1=29791、L2=12768。后续演示验收以本修订为准。17 条真实测量复核见 tokens-calibration-02（输入/模型/prompt/tools 未变，复用原 usage，新增模型调用 0，附 source/reuse audit）。这是整包修订，没有只改中文系数或掩盖第一次失败。

自审已统一用户 DDL 的闭区间、summary 投影与段表权威关系、工具不入 messages 而仍参与完整 State 计量、异步快照不越界、可变内容不进 system、startup 窗口与累计成本两种预算。

用户回复「已确认」批准了书面设计，包括第 7 节初始预算包及 MAX_AGENT_STEPS 按工具总调用数计、第 10 节最近连续段的有限摘要投影、checkpoint 保留工具而 MySQL 回载只含可见原文。5650 已有公开算式，真实校准尚未执行，不声称验收通过。

本次已进入 Superpowers writing-plans；计划另行评审并确定执行方式。实施前不应用 DDL、不安装依赖、不写产品代码。

## 16. 用户批准的摘要长度修订

2026-10-04 用户明确「接受 30–200 字（推荐）」。短批次 46/49 字实测事实完整但被旧 50 字下限拒绝；新业务摘要范围 30–200，保留号码、零编造与冻结语义样例检查。纯闲聊固定哨兵不受业务长度下限限制。旧校准失败报告保留，不重标冻结样例。

## 17. 用户批准的累计费用上限修订

2026-10-04 用户明确「接受按窗口和调用次数推导成本上限（推荐）」。default-04第22轮当前累计40769 token，窗口尚有容量，但旧64000累计上限无法再预留决策和携完整历史的最终答复。失败报告保留；这是多次调用的费用约束，不是单次上下文窗口超限。

默认累计上限 C=W×N，N=D+1+2×(3+分类模型数)，D=MAX_AGENT_STEPS+2。三个结构化用途为指代/扩展/评估，各最多一次修复；分类 primary最多2次，cascade最多4次；D包括Agent决策及修复，另1次最终答复。primary默认N15、C1920000，演示N14、C252000；这是最坏费用保护，实际调用仍受窗口、输出、步数和超时各自约束。显式CH05_TOTAL_MODEL_TOKENS（含.env）仍优先，管理员可保留64000并接受长会话成本兜底；示例环境不再默认设置它。历史5300/3709/1590及摘要策略不变，旧章独立AgentLimits默认也不变。
