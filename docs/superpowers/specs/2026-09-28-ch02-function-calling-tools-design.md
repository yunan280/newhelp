# Ch02 · Function Calling 工具链 — 设计文档

- **日期**:2026-09-28
- **分支**:`ch02-tools`(worktree `../mewhelp-wt/ch02-tools`,从 `ch01-conversation@043f7ea` 开出)
- **状态**:设计已批准,待实现

## 一、目标

给第 1 章那个 SSE 流式聊天页**装上查数据的能力**:用户问一句,模型自己决定调哪个工具,工具结果回灌后模型继续作答;聊天页的气泡上能看见这一轮调了哪个工具。

包含五件事:

1. **项目基建** — FastAPI + SQLAlchemy 的分层骨架;Docker 起 MySQL;四张表 + 测试数据
2. **五个业务工具** — 用 LangChain `@tool` 定义,模型自主选择
3. **工具基础设施** — 注册管理、参数 Schema 校验、执行错误处理、超时重试;工具结果回灌给模型
4. **接进现有聊天入口** — 工具链长在聊天本身,用户问一句就走「模型定工具 → 执行 → 回灌收敛」;最终回答仍逐 token 流式吐出,工具执行那段先推状态帧;聊天记录落库;气泡上显示工具轨迹徽章
5. **单轮收敛** — 模型调一次工具就收

## 二、非目标(本章明确不做)

- **多轮自动循环的 Agent Loop**(不做 `create_agent`,不做 LangGraph 编排)
- **向量检索 / RAG**(ch03 的事)
- 工具权限控制、工具级限流与熔断
- 真实电商 / 物流系统对接(三个查询工具的内部数据是随机的)
- 会话身份的鉴权体系(`user_id` 是占位,见 §6)
- **数据库迁移工具(Alembic)** — 本章用 `Base.metadata.create_all()`,理由见 §7

### 一条要说明的偏差:ch01 spec 留下的前向指针不兑现了

ch01 的设计文档第四节写着:「`langchain` 与 `langgraph` 留在 `agent` extra,等 ch02 需要 `create_agent` 时再进」。

**这一章不需要 `create_agent`** —— 单轮收敛用两次模型调用手工编排就够,`create_agent` 是多轮 Agent Loop 的入口,属于本章非目标。因此:

- `langchain` 元包**始终不进**依赖,`agent` extra 原样留着
- `@tool` 装饰器与 `ToolException` 都在 **`langchain_core.tools`** 里(已在本机 1.6.5 上实测),`langchain-core>=1.6` 已经在主依赖中
- **本章零新增依赖**

## 三、验收标准

| # | 标准 | 怎么验 |
|---|---|---|
| ① | 浏览器打开聊天页,问「订单 1001 的物流到哪了」,能看到模型选中工具(气泡带工具徽章)并按返回结果作答 | 页面手动验 + SSE 帧序断言 |
| ② | 问「退货政策是什么」,`query_faq` 查得到并作答 | 同上 |
| ③ | 换个说法问「邮费是多少」,确认关键词查表查不出来 | **预期结果就是漏召回**,记下来留给 ch03 升级。见 §8 |

## 四、技术栈与依赖

Python 3.12(本机)· FastAPI 0.141.1 · SQLAlchemy 2.1.1 · PyMySQL 1.2.3 · LangChain-core 1.6.5 · langchain-openai 1.6.6 · 模型 `deepseek-chat`

**依赖变更:无。** `sqlalchemy` / `pymysql` 骨架里已声明但从未使用,本章正好是它们的第一个消费者。`langchain-core` 提供 `@tool`。Docker 只用来跑 MySQL,不需要 Python 侧的新包。

> 已在本地实测的三个形状(不是从文档推断的):
> - `@tool` / `ToolException` 在 `langchain_core.tools`,不在 `langchain.tools`
> - `AIMessage(content="", tool_calls=[...])` + `ToolMessage(content=..., tool_call_id=...)` 串成的四段账本**形状合法**
> - `convert_to_openai_tool(tool)` 能直接产出发给 DeepSeek 的 JSON —— 测试可直接断言它
>
> 以及一条**否掉的**形状:`InjectedToolArg` 在 langchain-core 1.6.5 上**不生效** —— 被标注的参数照样出现在 schema 的 `properties` 与 `required` 里,模型会看见并要求自己编一个值。注入过滤发生在 `langchain.agents` 的 ToolNode 路径上,而本章不用它。**上下文注入改用闭包**,见 §9.2。

## 五、架构与目录结构

### 分层延续 ch01 的边界

ch01 定下的规矩是「service 层只说发生了什么,SSE 的封装格式归 api 层管」。本章沿用:**service 层 yield 的是事件对象,不是字符串,也不是 `data:` 帧**。

```
交互层     FastAPI · SSE 流式 · 聊天页(工具徽章)
   ↓
编排层     ch02/service.py  —— 模型定工具 → 执行 → 回灌 → 收敛
   ↓
能力层     tools/registry.py(注册/校验/超时/重试/错误兜底)
           tools/{business,knowledge,ticket}.py(五个工具)
           memory.py(ch01 的会话历史,本章一行不改)
   ↓
数据层     db/{base,engine,models,seed}.py · MySQL(Docker)
```

### 目录结构

```
src/mewhelp/
├── config.py               # 不动。新增的 MYSQL_* 读取走 db/engine.py
├── llm.py                  # 不动
├── memory.py               # 不动(ch01 的 SessionStore 仍是上下文源)
├── main.py                 # 改:挂 ch02 路由
├── static/index.html       # 改:气泡里的工具轨迹徽章(Vibe Coding,不评审)
├── db/                     # 新增 —— 跨章节共用
│   ├── __init__.py
│   ├── base.py             # Base(DeclarativeBase) + type_annotation_map
│   ├── engine.py           # create_engine / SessionLocal / get_session
│   ├── models.py           # Faq / Conversation / Message / Ticket
│   └── seed.py             # 幂等灌种子数据
├── tools/                  # 新增
│   ├── __init__.py
│   ├── registry.py         # ToolRegistry + 执行管线 + ToolResult
│   ├── business.py         # query_order / query_product / query_logistics
│   ├── knowledge.py        # query_faq
│   └── ticket.py           # create_ticket
└── ch02/                   # 新增
    ├── __init__.py
    ├── api.py              # POST /ch02/chat/stream
    ├── events.py           # TokenEvent / ToolEvent / DoneEvent
    ├── service.py          # stream_chat_with_tools
    └── prompts.py          # 客服 System Prompt(带工具使用规则)

docker-compose.yml          # 新增,仓库根
tests/
├── test_db_models.py
├── test_db_seed.py
├── test_tool_registry.py
├── test_tool_business.py
├── test_tool_knowledge.py
├── test_tool_ticket.py
├── test_ch02_events.py
├── test_ch02_service_tools.py
├── test_ch02_api_chat.py
└── eval/
    ├── tool_routing_cases.jsonl     # 新增:工具选择评估集
    └── test_tool_routing_eval.py    # 新增:标记 eval,真调上游
```

**ch01 的东西一行不改**:`ch01/*`、`memory.py`、`llm.py`、`config.py` 全部原样。ch01 的 103 条测试继续守着 ch01 —— 这是选方案 A 换来的东西。

## 六、数据层:四张表

### 表结构

```
faq
  id          INT PK AUTO_INCREMENT
  question    VARCHAR(500)  NOT NULL
  answer      TEXT          NOT NULL
  category    VARCHAR(64)   NOT NULL
  created_at  DATETIME      NOT NULL

conversations
  id          INT PK AUTO_INCREMENT
  session_id  VARCHAR(64)   NOT NULL UNIQUE   -- 就是聊天页那个 session_id
  user_id     VARCHAR(64)   NOT NULL          -- 占位,本章恒为 demo-user
  status      VARCHAR(32)   NOT NULL          -- open | pending_human
  created_at  DATETIME      NOT NULL

messages
  id              INT PK AUTO_INCREMENT
  conversation_id INT  NOT NULL FK(conversations.id)
  role            VARCHAR(16) NOT NULL        -- CHECK role IN ('user','assistant','tool')
  content         TEXT  NOT NULL DEFAULT ''
  tool_calls      JSON  NULL                  -- 模型发起的工具调用申请 [{name,args,id}]
  tool_call_id    VARCHAR(64) NULL            -- role=tool 时对应哪次申请
  created_at      DATETIME NOT NULL

tickets
  ticket_no       VARCHAR(32) PK              -- 工单号即主键,不是自增代理键
  conversation_id INT  NOT NULL FK(conversations.id)
  description     TEXT  NOT NULL
  ticket_type     VARCHAR(32) NOT NULL
  status          VARCHAR(32) NOT NULL        -- open
  created_at      DATETIME NOT NULL
```

`role` 用 `CheckConstraint` 落到 DDL 上,不只是应用层约定 —— SQLite 与 MySQL 8 都支持 CHECK。

### 三处判断

**`user_id` 是占位,本章恒为 `"demo-user"`。** 项目里没有登录,聊天页也没有身份。`ChatRequest` 收一个可选 `user_id`,缺省即 `demo-user`,并在本文件里写明这是占位。**不造假鉴权**:给每个浏览器发匿名 id 存在 `sessionStorage` 看着更像回事,但那会让"同一用户换个浏览器就换了个人",是在假装有一套账号体系。真实身份属于后续章节。

**`conversations.status` 有 `pending_human` 流转。** 首轮建壳写 `open`;当某一轮触发了 `create_ticket`,把该会话置为 `pending_human`(已转人工)。这是 `create_ticket` 在本章唯一自然的落点 —— 一个工单被建出来,却没有任何地方记得"这个会话交给人工了",那个字段就只是个装饰。

**`tickets.ticket_no` 是主键**,格式 `T` + `YYYYMMDD` + 3 位日序号(例 `T20260928001`)。生成方式是"查当天已有条数 + 1",**并发下会撞主键** —— 撞了就捕获 `IntegrityError` 递增重试(上限 5 次),仍失败则让 `create_ticket` 返回一个 `ok=False` 的结构化错误。这个上限写进 §16 已知局限。

### 类型标注与 MySQL 的长度要求

`Base` 上配 `type_annotation_map`,给 `str` 一个带长度的 MySQL variant —— MySQL 的 `VARCHAR` 不接受无长度声明,而 `Mapped[str]` 默认映射到无长度的 `String`。这是 SQLAlchemy 2.x 官方文档给的写法:

```python
class Base(DeclarativeBase):
    type_annotation_map = {
        # Mapped[str] 默认映射到无长度的 String,而 MySQL 的 VARCHAR 不接受无长度声明。
        # 给一个 255 的默认长度,需要更长的地方显式写 mapped_column(String(N)) 覆盖。
        str: String(255).with_variant(VARCHAR(255), "mysql"),
        datetime.datetime: DateTime,
    }
```

需要更长的地方(`faq.question` 500、`messages.content` 用 `Text`)显式写 `mapped_column` 覆盖。

## 七、Docker 与种子数据

### `docker-compose.yml`(仓库根)

MySQL 8.0,**容器 3306 → 宿主 3306**,`MYSQL_DATABASE=mewhelp`,账号密码取自 `.env` 的 `MYSQL_*`,命名卷持久化,带 `healthcheck`(`mysqladmin ping`)。

### 建表方式:不做 Alembic

`db/seed.py` 同时负责建表与灌数据:先 `Base.metadata.create_all(engine)`,再按主键 upsert 种子行,整个脚本**幂等,可重复跑**。

**为什么不上 Alembic**:Alembic 解决的是"表结构在生产环境上要可演进、可回滚"。本章没有生产环境,表结构在四个文件里,改 schema 就重建一次库。引入 Alembic 要多一个依赖、一个 `alembic/` 目录、一套 revision 流程,而它守护的风险(线上迁移失败)在这一章**不存在**。

**代价说清楚**:表结构一旦有数据就改不动了,只能删库重建。等这个项目真的有了要保留的数据,再补 Alembic —— 那时也才有真实的迁移可写。

### 种子数据

**`faq` 12 条**,分类覆盖:退换货 / 物流 / 支付 / 发票 / 商品 / 售后。内容见 §8(运费那条的措辞是设计的一部分)。

**`conversations` / `messages` / `tickets` 灌各 1~2 条样例**,便于直接开表看结构;其余由聊天过程产生。

## 八、「邮费是多少」的漏召回必须是设计出来的

这是验收 ③,也是全章最容易蒙混过去的一条。如果种子数据**碰巧没有**邮费条目,漏召回是运气不是设计 —— ch03 的向量检索就失去了一个可信的对照基线。

**所以把它钉死:**

1. `faq` 里**有**一条讲运送费用的知识,但措辞是 **「运费怎么计算」**,该行全文**不含**「邮费」也**不含**「费用」这两个子串。
2. `query_faq(keyword)` 做 `LIKE '%keyword%'` 扫 `question` / `answer` / `category`。用户问「邮费是多少」,模型抽出的关键词是「邮费」→ **0 行命中**。
3. 于是漏召回发生在**同义词**这一层,而不是"我们忘了写这条知识"。这正是向量检索要解决的问题,ch03 拿它当基线才有意义。
4. 验收 ② 走**同一段代码**:「退货政策是什么」→ 关键词「退货」→ 命中 → 有答案。

**同一个工具,一问就中、一问就漏** —— 这才说明漏的是检索能力,不是工具坏了。

### 配套:System Prompt 必须禁止模型凭知识直答

否则模型可能用参数化知识直接答出邮费,验收 ③ 观察到的就变成"模型没调工具",而不是"查表查不出来" —— 那是另一件事,不能混为一谈。

`ch02/prompts.py` 的 System Prompt 因此必须包含:**政策类问题(退换货、运费、发票、保修、时效)一律先调 `query_faq`,不许用自己的知识直接回答;工具说没有就如实告诉用户没查到。**

## 九、工具基础设施

### 9.1 注册表

`ToolRegistry` 持有 `dict[str, BaseTool]` 与每个工具的元信息,提供 `names()` / `tools()`(给 `bind_tools`)/ `get(name)`。

### 9.2 上下文注入用闭包

**不用 `InjectedToolArg`**(§4 已说明它在 1.6.5 上不生效)。改用工厂函数:

```python
def build_registry(db: Session, conversation_id: int) -> ToolRegistry:
    """返回 5 个已绑定当前请求上下文的工具。"""
    @tool
    def query_faq(keyword: str) -> str:
        """按关键词查常见问题。keyword 是用户问题里的核心词。"""
        ...  # 闭包里直接用 db
    ...
```

**模型只看见业务参数**,`db` 与 `conversation_id` **根本不在函数签名里**,因此不可能出现在 schema 里、更不可能被模型幻觉出来。已在本地验证:`convert_to_openai_tool` 产出的 JSON 里 `properties` 只有业务参数。

### 9.3 执行管线

| 步骤 | 失败时 |
|---|---|
| 1. 按 `name` 查工具 | 模型编了个不存在的工具 → 结构化错误结果,**不抛** |
| 2. 按 `tool.args_schema` 显式校验参数 | pydantic 报错翻成人话 → 结构化错误结果,**不重试** |
| 3. `asyncio.wait_for(tool.ainvoke(args), timeout=3.0)` | 超时 → 进第 4 步 |
| 4. 重试 | 最多 **3 次尝试**(1 初试 + 2 重试),退避 0.2s / 0.4s;每次尝试独立计时 |
| 5. 仍失败 | 结构化错误结果回灌给模型 → **不抛**,让模型据此组织回答 |

**两条不重试的判断:**

- **参数校验失败不重试。** 参数是模型生成的,同一个坏参数重试三次只会白烧三倍时间 —— 重试对"输入错了"这个成因无效。校验失败应当立刻回灌,让模型自己改口径重问(那是下一轮的事,本章不做)。
- **只重试超时与瞬时异常,不重试语义性失败。** 比如"订单不存在",重试三次还是同一个答案。

### 9.4 统一返回

```python
@dataclass(frozen=True)
class ToolResult:
    name: str
    args: dict
    ok: bool
    content: str          # 回灌给模型的内容;ok=False 时是一段给模型看的失败说明
    error: str | None     # 机器可判的失败原因
    elapsed_ms: int
    attempts: int
```

`ok=False` 时 `content` 同样**回灌给模型** —— 这是"执行错误处理"这条需求的落点:工具坏了,模型该知道,并据此告诉用户,而不是整轮 500。

### 9.5 阻塞调用与超时的诚实边界

DB 工具内部是同步 SQLAlchemy,在 async 的编排里会阻塞事件循环。处置:**DB 操作经 `anyio.to_thread.run_sync` 丢到线程池**(`anyio` 随 FastAPI/Starlette 一起来,零新依赖)。

**代价,如实记**:`asyncio.wait_for` 超时**杀不掉已经在跑的那个线程** —— 超时只是让我们不再等它,那个线程会自己跑完。本章的工具都是"一次小查询或一次小插入",可以被放弃的代价是有界的。但这是一个真实存在的缝,写进 §16 已知局限,不要在评审时被当成"超时已经做对了"。

## 十、五个工具

| 工具 | 模型可见签名 | 实现 |
|---|---|---|
| `query_order` | `order_id: str` | 内部随机生成:商品名、下单时间、金额、订单状态 |
| `query_product` | `product_name: str` | 内部随机生成:价格、库存、规格 |
| `query_logistics` | `order_id: str` | 内部随机生成:承运商、运单号、轨迹节点列表 |
| `query_faq` | `keyword: str` | `LIKE '%keyword%'` 扫 `faq` 的 `question`/`answer`/`category` |
| `create_ticket` | `description: str`, `ticket_type: Literal[...]` | 写 `tickets` 表,返回工单号 |

- 前三个**不接真实接口、不建表**,在工具内部随机生成 —— 按需求写死。
- **随机源可注入**:工厂函数收一个 `random.Random`。测试里钉种子即可断言具体输出;演示里不钉就是随机的。
- `query_faq` **命中 0 行时返回一句明确的"知识库没有这一条"**,不是空串。空串回灌给模型,模型分不清"查了没有"和"工具坏了",容易编答案。这是本章唯一那个刻意漏召回的出口,它的措辞决定了模型会不会老实说"没查到"。
- `create_ticket` 的 `ticket_type` 用 `Literal`,取值集与 §6 的 `tickets.ticket_type` 对应,约束模型别乱填。

## 十一、编排:单轮收敛

```
① 读历史(内存 store,一行不改)→ 裁剪 → 拼 prompt + bind_tools(5 个)
② 第 1 次调用:astream,边流边吐 token
③ 若这一轮带 tool_calls:
     → 推 tool 帧(phase=start)          ← "工具执行那一段先推个状态帧"
     → 执行(走 §9.3 管线)
     → 推 tool 帧(phase=end,带 ok / elapsed_ms / attempts)
     → 回灌:[...history, human, ai(带 tool_calls), tool(带 tool_call_id)]
④ 第 2 次调用:astream,继续吐 token —— 这一次 **不 bind_tools**
⑤ 落库(§13)→ done
```

### 「单轮」的确切含义:一次模型调用里的**所有** tool_calls 都执行

需求写的是「模型调一次工具就收敛」。**这里的「一次」指的是一轮模型调用,不是「恰好一个调用」** —— 模型完全可能在一个回合里同时发出两个 `tool_calls`(OpenAI 协议下它们是并列的,`AIMessage.tool_calls` 是个列表)。

所以钉死:

- **这一轮里所有的 `tool_calls` 全部执行**,每个的结果各回灌一条 `ToolMessage`
- 每个工具都推自己的一对 `tool` 帧(start / end)
- 然后进入第 ④ 步,收敛

**不做的**:不因为"只允许调一次"就丢掉第二个调用 —— 那就等于模型说的话被静默截断了,而用户看不到任何痕迹。也不做"执行完第一个发现够了就跳过其余" —— 那需要一个判断"够了"的规则,而那个规则本身就是 Agent Loop 的雏形。

评估集里有一条专门量这个(一次问句里同时涉及订单与物流),数字如实记。

### 最终回答为空,仍按 ch01 的失败处理

若第 ④ 步收敛后的正文 `strip()` 之后为空,抛 **`EmptyCompletionError`**(复用 ch01 那个异常类),api 层翻成 `error` 帧 + `code="empty_completion"`,**并且不落库** —— 与 §13「只在整轮成功之后写」一致。

理由与 ch01 完全相同:空回答会作为一条真正的空 assistant 消息永久重放。**注意与工具调用轮的区分**:工具调用轮的第 1 次调用正文**通常就是空的**,那**不是**失败 —— 判空的只有第 ④ 步的最终回答。这是本章新引入的一个容易搞反的地方:同一段代码里,第 1 次调用的空正文合法,第 2 次调用的空正文非法。

### 第 ④ 步不 bind_tools 是"只做单轮"的结构性保证

模型**没有工具可调**,收敛不是靠嘱咐,是靠它调不到。这比在 prompt 里写"只准调一次"可靠得多 —— 后者是一句可以被忽略的话,前者是一个不存在的接口。

### 第 ② 步的正文处理:边收边吐(有代价的选择)

模型调工具时**通常只吐工具调用、不吐正文**,但不是永远如此。三种处理:

| 方案 | 做法 | 代价 |
|---|---|---|
| (a) | 第 1 次调用不流式,拿到结果再决定 | 不需要工具的普通问答**就不流式了**,ch01 的流式白做 |
| (b) | 第 1 次调用的正文先攒着,看清有没有工具调用再决定 | 普通问答要等整个生成完才出字,流式同样白做 |
| (c) ⭐ | **正文照常逐 token 流**;若后面跟了工具调用,推 tool 帧、接着吐最终答案 | 模型既说前言又调工具时,**前言与最终答案同框**,读起来像两段话 |

**选 (c)。** 收益是**两条路都保住流式**,而那正是 ch01 的立身之本。真实情况下模型调工具时正文为空,(c) 与 (a) 在验收 ①② 上表现完全一致;差别只在"模型说了句『让我查一下』再调工具"这个情形 —— 而那恰恰是**人类客服也会有的说法**,同框并不难看。

**不做文本缓冲的理由**:缓冲意味着"逐 token 流式"这条路在首个 token 上让位于一个可能根本不发生的分支,把确定性收益(每次都卡)换成一个不确定的损失(偶尔同框)。

## 十二、SSE 事件契约

ch02 的 `/ch02/chat/stream` 在 ch01 契约上**新增一个 `tool` 帧**,其余完全一致:

| 事件 | 载荷 | 说明 |
|---|---|---|
| `session` | `{session_id}` | **仍是第一个**,契约不变 |
| `tool` | `{name, args, phase: "start"｜"end", ok?, elapsed_ms?, attempts?}` | **新增**。`phase=start` 时只有 `name`/`args`;`phase=end` 时补齐 `ok`/`elapsed_ms`/`attempts` |
| `token` | `{text}` | 不变 |
| `done` | `{finish_reason}` | 不变(仍恒为 `"stop"`,ch01 的如实声明继续有效) |
| `error` | `{message, code}` | 不变,`code` 沿用 `empty_completion` / `upstream_error` |

**向后兼容**:老页面不认识 `tool` 事件会直接忽略它,不会坏。新页面拿它画徽章。

**ch01 的路由 `/ch01/chat/stream` 保留不动**,聊天页改为指向 `/ch02/chat/stream`。

### 请求体

```jsonc
POST /ch02/chat/stream
{
  "session_id": "…",     // 可选,不传则服务端生成并从 session 帧返回。空白一律 422(ch01 同一条规则)
  "user_id":    "…",     // 可选,缺省 "demo-user"。见 §6,本章是占位
  "message":    "…"      // 必填,空白(含零宽字符)一律 422
}
```

`user_id` 的校验沿用 ch01 的 `_reject_blank` 规则:`extra="forbid"`、present-but-blank 拒掉、非空白原样放行不 strip。

### session_id → conversation 的映射

`conversations.session_id` 上是 UNIQUE。每一轮:

1. 按 `session_id` 查 `conversations`
2. 查到 → 用它,并把 `user_id` 保持原样(**不覆盖** —— 同一 session 换了 user_id 是异常,但本章不做鉴权,静默保留首轮那个)
3. 查不到 → 新建一行,`status='open'`,记下 `user_id`

这个"查到就用、查不到就建"要能并发安全:两个并发请求同时建同一个 `session_id` 会撞 UNIQUE。处置与 §6 的工单号一致 —— 捕获 `IntegrityError` 后重查一次。**但注意**:ch01 的 `store.lock(session_id)` 已经把同一 session 的整轮串行化了,所以这条路径在本章的多 worker 场景之外基本走不到。仍要写对,因为锁是进程内的。

## 十三、落库

### 只在整轮成功之后写

与 ch01「失败的一轮不写进会话历史」保持同一条规则 —— 半截的账本比没有账本更难查。

一轮成功且调了工具时,写 **4 条** `messages`:

```
role=user       content=用户那句话
role=assistant  content=模型前言(通常为空)   tool_calls=[{name,args,id}]
role=tool       content=工具返回原文           tool_call_id=<对应 id>
role=assistant  content=最终回答
```

没调工具的一轮写 2 条。`conversations` 首轮建壳(`status=open`);本轮触发 `create_ticket` 时置为 `pending_human`。

### 落库失败不推翻已经答完的那一轮

这是本方案唯一的缝:ch01 的内存 store 与 MySQL 是两个真相。处置是 —— **写库失败只记日志,不影响响应**。

理由:token 已经逐字吐给用户了,这时候因为落库失败补一个 `error` 帧,是在**骗人** —— 用户明明看到了完整回答,却被告知这一轮失败了。

**代价,要有意识地接受**:账本可能缺行,而且是静默缺行。缓解是日志留痕 + §16 把它写成已知局限。**这是本章明确接受的设计代价,不是疏忽。**

## 十四、聊天页改造(Vibe Coding,不评审)

`static/index.html` 的改造**走 Vibe Coding**:用户描述效果,直接改,不套 brainstorm / TDD / code review。

本章要加的是**工具轨迹徽章**:气泡里显示这一轮调了哪个工具、成功还是失败、耗时。

- 收到 `tool` 帧(`phase=start`)→ 在气泡里挂一个徽章,显示工具名(如「🔧 查物流」)
- 收到 `tool` 帧(`phase=end`)→ 补齐状态(成功 / 失败、耗时)
- **徽章不能打断流式正文**:徽章是气泡里的独立元素,`textContent` 累积正文的写法得让位(徽章的 DOM 节点会被 `textContent = ''` 抹掉)

最后一条是本页已知的坑:现有代码用 `reply.textContent = ''` 清掉等待动画、用 `+=` 累积正文 —— **`textContent` 会清掉所有子节点**。徽章要活下来,正文得换一个独立的容器节点。这条在改动时会被真实撞到。

## 十五、测试策略与验证方式

按需求:**非可单测的产出把 TDD 换成标注样例 / 评估集验证**。

### 15.1 照常走 TDD 的部分(纯逻辑,SQLite 内存库,不需要 Docker)

| 文件 | 覆盖 |
|---|---|
| `test_db_models.py` | 四张表可建;`role` 的 CHECK 真的拒非法值;`tool_calls` JSON 往返 |
| `test_db_seed.py` | 幂等:连跑两次行数不变;`faq` 条数与分类覆盖;运费那条**确实不含**「邮费」「费用」 |
| `test_tool_registry.py` | 未知工具名;参数校验失败**不重试**;超时重试;退避;尝试次数上限;`ToolResult` 各字段 |
| `test_tool_business.py` | 钉种子后输出可断言;三个工具都不碰数据库 |
| `test_tool_knowledge.py` | LIKE 命中;命中 0 行返回明确文案(不是空串);「邮费」查不到 / 「退货」查得到 |
| `test_tool_ticket.py` | 工单号格式;落库;`IntegrityError` 重试递增;`pending_human` 流转 |
| `test_ch02_events.py` | 事件对象形状 |
| `test_ch02_service_tools.py` | 调工具 / 不调工具两条路的**帧序列**;第 2 次调用**没有绑定工具**;**一轮内两个 tool_calls 都被执行**且各推一对帧;第 1 次调用的空正文**合法**、第 2 次调用的空正文**抛 `EmptyCompletionError`**;`conversations` 的查到就用 / 查不到就建 |
| `test_ch02_api_chat.py` | `tool` 帧字段;老客户端忽略未知事件不坏;`user_id` 的空白拒绝与缺省值 |

### 15.2 换成评估集的部分(模型选哪个工具,不可单测)

`tests/eval/tool_routing_cases.jsonl` 共 **24 条**,每条标 `(question, expected_tool | null, note)`,通过 `pytest -m eval` 对真实 DeepSeek 跑:

| # | 类别 | 条数 | 例 | 期望 |
|---|---|---|---|---|
| 1 | 订单 / 物流 / 商品 → 对应工具 | 6 | 「订单 1001 的物流到哪了」 | `query_logistics` |
| 2 | 政策类 → FAQ | 5 | 「退货政策是什么」 | `query_faq` |
| 3 | **同义词漏召回(设计好的)** | 2 | 「邮费是多少」 | `query_faq` **调了、但返回 0 行** ← 验收 ③ |
| 4 | 要人工 → 建单 | 4 | 「我要投诉,转人工」 | `create_ticket` |
| 5 | **不该调工具** | 4 | 「你好」「谢谢」 | `null` |
| 6 | **不该凭知识直答** | 3 | 「发票怎么开」 | `query_faq`(而不是直接答) |
| | **合计** | **24** | | |

另有 1 条**不计入 24** 的:一轮里同时涉及订单与物流的问句,用来量 §11 的「一轮内多个 tool_calls 都执行」—— 它的期望是一个**集合**(`{query_order, query_logistics}` 的任意非空子集),不是单个工具名,所以单独记,不混进命中率分母。

**评估集只报数字、不设门槛。** 沿用 ch01 对诊断量的处理 —— 本章的质量目标是"能看见工具被选中",不是"命中率 ≥ X"。定一个门槛会诱导为门槛调参,那是 ch01 已经栽过的形状。数字如实记进 `dev-notes/ch02.md`。

### 15.3 三条验收的真机跑法

Docker 起来、种子灌好、服务起了之后,按验收标准 ①②③ 逐条跑,输出原文落进报告。

## 十六、风险与已知局限

| # | 风险 / 局限 | 处置 |
|---|---|---|
| 1 | **`deepseek-chat` + `bind_tools` 是否真返回 `tool_calls`** —— ch01 已证同型号的 function calling 走得通(`with_structured_output(method="function_calling")`),但 `bind_tools` 这条**未实测** | 评估集第一条就验它。**不成立则停下来问用户**,不自行换方案 |
| 2 | Docker 守护进程未启动;`MySQL80` 服务占着 3306 | 真机演示前需用户启 Docker + 停服务。`MySQL80` 开机自启,重启后会回来抢端口 —— **改服务启动类型是系统级动作,先问用户** |
| 3 | 落库失败被静默吞掉(§13) | 已在 §13 明确为接受的设计代价 + 日志留痕 |
| 4 | SQLite 与 MySQL 的方言缝 | MySQL 的 `utf8mb4_0900_ai_ci` 让 `LIKE` 大小写不敏感,SQLite 对非 ASCII 敏感 —— **中文无大小写,本章语料上不产生可观察差异**。另加一条 MySQL 真机冒烟(不在默认套件里) |
| 5 | 工单号并发冲突 | 撞主键后递增重试,上限 5 次,仍失败则返回 `ok=False`。真实并发下的上限是理论上的 |
| 6 | **超时杀不掉线程**(§9.5) | 已如实记录。可以被放弃的代价有界(一次小查询 / 小插入) |
| 7 | `messages.tool_calls` 是 JSON 列,**MySQL 与 SQLite 的 JSON 语义不同**(SQLite 存 TEXT) | 本章只做整体读写、不做 JSON 路径查询,差异不显现。将来要按工具名检索就得先处理这条 |
| 8 | `faq` 的 `LIKE '%kw%'` **不可能走索引** | 这是本章刻意要展示的朴素检索 —— 全表扫描是 12 行规模下的正确答案。ch03 换向量检索时,这条是升级动机的一部分 |

## 十七、交付物

1. **功能演示命令** —— 起 Docker / 灌种子 / 起服务 / 打开聊天页 的完整命令序列,写进 `README.md`
2. **测试结果** —— 默认套件全绿 + `pytest -m eval` 的工具选择数字
3. **`dev-notes/ch02.md`** —— 边做边记,按阶段边界增量追加
4. **`docs/decisions.md`** 补本章决策;**`NOTES.md`** 补踩过的坑与已知局限

## 十八、完成标准

| 标准 | 判据 |
|---|---|
| 默认套件全绿 | `pytest` 全通过,零回归(ch01 的 103 条继续绿) |
| 评估集跑过并报数 | `pytest -m eval` 有输出,数字记进 dev-notes |
| 三条验收真跑通 | 验收 ①②③ 逐条手工跑,输出原文落盘;③ 是**预期漏召回**,如实记录 |
| 聊天页能看到工具徽章 | 由用户确认(与 ch01 同一条规矩:浏览器渲染那一步归用户) |
| `dev-notes/ch02.md` 各阶段边界都已追加 | 边做边记,不补写 |
| MySQL 真机冒烟通过 | 建表 + 种子 + 一轮调工具的对话,在真 MySQL 上跑通 |
