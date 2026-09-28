# Ch02 · Function Calling 工具链 — 设计文档

- **日期**:2026-09-28
- **分支**:`ch02-tools`(worktree `../mewhelp-wt/ch02-tools`,从 `ch01-conversation@043f7ea` 开出)
- **状态**:设计已批准,待实现(已于 2026-09-28 按用户提供的 DDL 重写 §6/§7/§12)

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
- **数据库迁移工具(Alembic)** — 理由见 §7
- **SQLAlchemy 异步 / `asyncmy`** — 理由见 §4
- **重排 ch01 的目录结构**(`src/mewhelp/` 保持原样,不引入 `app/`、`core/` 这类新顶层)

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

Python 3.12(本机)· FastAPI 0.141.1 · SQLAlchemy 2.1.1 · PyMySQL · LangChain-core 1.6.5 · langchain-openai 1.6.6 · 模型 `deepseek-chat`(沿用 ch01 的 `LLM_MODEL`,**本章不换上游**)

**依赖变更:无。** `sqlalchemy` / `pymysql` 骨架里已声明但从未使用,本章正好是它们的第一个消费者。`langchain-core` 提供 `@tool`。Docker 只用来跑 MySQL,不需要 Python 侧的新包。

### 为什么是同步 SQLAlchemy + PyMySQL,不是异步异步 `asyncmy`

实测本机 venv:

```
sqlalchemy 2.1.1  ✅ 已装    pymysql 2.2.8  ✅ 已装
asyncmy    ❌ 缺            greenlet ❌ 缺    aiosqlite ❌ 缺
```

`greenlet` 是 SQLAlchemy async 路径的硬前置 —— 缺它连 `create_async_engine` 都导入不了。所以"异步"这条路要 **+3 个新依赖**,与本节的「零新增依赖」直接冲突,换来的是本章并不需要的并发能力(单 worker、单用户演示)。

**代价如实记**:工具内部的 DB 调用是同步的,在 async 编排里会阻塞事件循环。处置见 §9.5(丢线程池),那一节也写明了它的诚实边界。

### 已在本地实测的形状(不是从文档推断的)

> **能用的:**
> - `@tool` / `ToolException` 在 `langchain_core.tools`,不在 `langchain.tools`
> - `AIMessage(content="", tool_calls=[...])` + `ToolMessage(content=..., tool_call_id=...)` 串成的四段账本**形状合法**
> - `convert_to_openai_tool(tool)` 能直接产出发给 DeepSeek 的 JSON —— 测试可直接断言它
>
> **否掉的:**
> - `InjectedToolArg` 在 langchain-core 1.6.5 上**不生效** —— 被标注的参数照样出现在 schema 的 `properties` 与 `required` 里,模型会看见并要求自己编一个值。注入过滤发生在 `langchain.agents` 的 ToolNode 路径上,而本章不用它。**上下文注入改用闭包**,见 §9.2
>
> **DDL 复刻能力(§6.2 详列):** 中文 ENUM 的默认值、`BIGINT UNSIGNED`、`DEFAULT CURRENT_TIMESTAMP`、命名外键、ENGINE/CHARSET/COMMENT 都能逐字复刻;**唯独 `ON UPDATE CURRENT_TIMESTAMP` 不行** —— `server_onupdate` 不落 DDL。这是 §7「DDL 当权威」的硬理由

## 五、架构与目录结构

### 分层延续 ch01 的边界

ch01 定下的规矩是「service 层只说发生了什么,SSE 的封装格式归 api 层管」。本章沿用:**service 层 yield 的是事件对象,不是字符串,也不是 `data:` 帧**。

```
交互层     FastAPI · SSE 流式 · 聊天页(工具徽章)
   ↓
编排层     ch02/service.py  —— 模型定工具 → 执行 → 回灌 → 收敛
   ↓
能力层     tools/registry.py(注册表)
           tools/infra.py(执行管线:校验 / 超时 / 重试 / ToolResult)
           tools/{business,knowledge,ticket}.py(五个工具)
   ↓
数据层     db/{base,engine,models,seed}.py · MySQL(Docker)
```

### 两个出口,一个核心

```
_prepare_turn()          ← 共享:会话身份 → 落 user → 组装上下文 → turn1 定工具 → 执行 → 落 tool
  ├─ stream_agent_turn() 流式,产出事件流   → POST /ch02/chat/stream(前端主入口,SSE)
  └─ run_agent_turn()    非流式,一次性返回  → POST /ch02/agent(程序化 / eval / 测试出口,JSON)
```

两个出口**只在最后收敛那一步不同**(`astream` vs `ainvoke`),前六步共用。`/ch02/agent` 的存在让 eval 与 `curl` 不必解析 SSE。

### 目录结构

```
src/mewhelp/
├── config.py               # 不动。新增的 MYSQL_* 读取走 db/engine.py
├── llm.py                  # 不动
├── memory.py               # 不动(trim_history 仍被复用;SessionStore 不再是上下文源,见 §11)
├── main.py                 # 改:挂 ch02 路由
├── static/index.html       # 改:请求体、tool 帧解析、工具徽章(Vibe Coding,不评审)
├── db/                     # 新增 —— 跨章节共用
│   ├── __init__.py
│   ├── base.py             # Base(DeclarativeBase) + type_annotation_map
│   ├── engine.py           # engine / SessionLocal / get_session(MYSQL_* 在这里读)
│   ├── models.py           # Faq / Conversation / Message / Ticket
│   └── seed.py             # 幂等灌种子数据
├── tools/                  # 新增
│   ├── __init__.py
│   ├── registry.py         # ToolRegistry:names() / tools() / get(name)
│   ├── infra.py            # 执行管线 + ToolResult
│   ├── business.py         # query_order / query_product / query_logistics
│   ├── knowledge.py        # query_faq
│   └── ticket.py           # create_ticket
└── ch02/                   # 新增
    ├── __init__.py
    ├── api.py              # POST /ch02/chat/stream(SSE)+ POST /ch02/agent(JSON)
    ├── events.py           # TokenEvent / ToolEvent / DoneEvent
    ├── service.py          # _prepare_turn + stream_agent_turn + run_agent_turn
    └── prompts.py          # AGENT_SYSTEM(客服 + 工具使用规则)

sql/ch02-ddl.sql            # 新增:权威建表语句(自带 SET NAMES utf8mb4)
docker-compose.yml          # 新增:仓库根
tests/
├── test_db_models.py
├── test_db_ddl_drift.py    # 新增:模型 ↔ ch02-ddl.sql 的漂移断言(见 §7)
├── test_db_seed.py
├── test_tool_infra.py
├── test_tool_business.py
├── test_tool_knowledge.py
├── test_tool_ticket.py
├── test_ch02_events.py
├── test_ch02_service_tools.py
├── test_ch02_api_chat.py
├── test_ch02_api_agent.py
└── eval/
    ├── tool_routing_cases.jsonl     # 新增:工具选择评估集(24 条)
    └── test_tool_routing_eval.py    # 新增:标记 eval,真调上游
```

**ch01 的东西一行不改**:`ch01/*`、`memory.py`、`llm.py`、`config.py` 全部原样。ch01 的 103 条测试继续守着 ch01 —— 这是选方案 A 换来的东西。

**路由前缀沿用 ch01 的规矩**(`APIRouter(prefix="/ch02")`),不引入 `/api/*` 这种新风格,免得同一份代码里两种命名并存。

## 六、数据层:四张表

### 6.1 表结构(以 `sql/ch02-ddl.sql` 为权威)

```
conversations
  id          BIGINT UNSIGNED PK AUTO_INCREMENT      COMMENT '会话主键'
  session_id  VARCHAR(64)  NOT NULL UNIQUE           ← 唯一一处偏离;理由见 6.3
  user_id     VARCHAR(64)  NOT NULL                  COMMENT '用户标识'
  status      ENUM('进行中','已转人工','已结束') NOT NULL DEFAULT '进行中'
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
  KEY idx_user_id (user_id)

messages
  id              BIGINT UNSIGNED PK AUTO_INCREMENT
  conversation_id BIGINT UNSIGNED NOT NULL
  role            ENUM('user','assistant','tool') NOT NULL
  content         TEXT NULL        -- assistant 纯工具调用时为空
  tool_calls      JSON NULL        -- [{name, args, id}]
  tool_call_id    VARCHAR(64) NULL
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  KEY idx_conversation_id (conversation_id)
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)

faq
  id          BIGINT UNSIGNED PK AUTO_INCREMENT
  question    VARCHAR(512) NOT NULL
  answer      TEXT         NOT NULL
  category    VARCHAR(64)  NOT NULL
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
  KEY idx_category (category)

tickets
  ticket_no       VARCHAR(32) PK              -- 工单号即主键,不是自增代理键
  conversation_id BIGINT UNSIGNED NOT NULL
  description     TEXT NOT NULL
  ticket_type     ENUM('售后','投诉','咨询') NOT NULL
  status          ENUM('待处理','已处理') NOT NULL DEFAULT '待处理'
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  KEY idx_conversation_id (conversation_id)
  CONSTRAINT fk_tickets_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
```

全库 `ENGINE=InnoDB` / `CHARSET=utf8mb4`。建表顺序:先 `conversations`,再依赖它的 `messages` / `tickets`。

### 6.2 ORM 映射到 DDL 的三个坑(均已在 2.1.1 上实测)

**坑一:中文 ENUM 默认存成员名,不存值。** SQLAlchemy 的 `Enum` 默认序列化 Python 枚举的**成员名**。不处理的话库里会变成 `ConversationStatus.ongoing` 而不是 `进行中` —— 与 ch01 撞过的 `f"{intent}"` 得到 `AfterSalesIntent.refund` 是同一类。

```python
class ConvStatus(enum.Enum):
    ongoing = "进行中"; human = "已转人工"; closed = "已结束"

status: Mapped[ConvStatus] = mapped_column(
    Enum(ConvStatus, values_callable=lambda e: [m.value for m in e]),
    nullable=False,
    server_default=text("'进行中'"),      # 注意:SQL 字面量要自带引号
)
```
实测 MySQL 侧产出 `status ENUM('进行中','已转人工','已结束') NOT NULL DEFAULT '进行中'` —— **与 DDL 逐字一致**。SQLite 侧落库原值 `进行中`,读回也是 `进行中`。

**坑二:`BIGINT` 主键在 SQLite 上不自增。** SQLite 只把**类型名恰好为 `INTEGER`** 的列当 rowid 别名,`BIGINT` 拿不到自增。用 variant 抹平:

```python
id: Mapped[int] = mapped_column(
    mysql.BIGINT(unsigned=True).with_variant(Integer, "sqlite"),
    primary_key=True, autoincrement=True,
)
```
实测:MySQL 侧 `BIGINT UNSIGNED`,SQLite 侧两次插入拿到 `id = 1, 2`。

**坑三:`Mapped[str]` 默认映射到无长度的 `String`,MySQL 的 `VARCHAR` 不接受。** 在 `Base` 上配:

```python
class Base(DeclarativeBase):
    type_annotation_map = {
        str: String(255),          # 需要更长的地方显式 mapped_column(String(N)) 覆盖
        datetime.datetime: DateTime,
    }
```

### 6.3 `session_id` 是本章**唯一**一处偏离你 DDL 的地方

`conversations.session_id VARCHAR(64) NOT NULL UNIQUE`,用途只有一个:**聊天页手上那个不透明 session_id → 会话行的映射**。

你的 DDL 里没有这一列,于是本来只有两条路:让自增 `id` 自己当身份,或者加这一列。**选加这一列,理由不是审美,是可枚举性**:

`conversations.id` 是 1、2、3。若拿它当身份,**猜一个存在的 id 不会被任何校验挡住** —— 猜中就是别人的会话。改成"不存在就 404"只挡得住**不存在**的 id,挡不住**存在**的那些,所以那个补丁治不了这个病。用 uuid4 当身份则这条路直接堵死 —— 而 ch01 的 `session` 帧本来就是服务端生成的 uuid4,契约一个字都不用改。

**换来的三个好处:**
1. 身份不可猜,别人的会话拿不到
2. ch01 的 SSE 契约原样保留(`session` 帧恒为首帧,客户端只需多带一个 `user_id`)
3. 加了它之后,DB 回放成了干净的选项,**顺手消掉了「内存 store 与 MySQL 两个真相」那道缝**(见 §11)

**代价**:`sql/ch02-ddl.sql` 比你给的版本多一列。

### 6.4 三处判断

**`user_id` 是占位,本章恒为 `"demo-user"`。** 项目里没有登录,聊天页也没有身份。`ChatRequest` 收一个可选 `user_id`,缺省即 `demo-user`,并在文件里写明这是占位。**不造假鉴权**:给每个浏览器发匿名 id 存 `localStorage` 看着更像回事,但那会让"同一用户换个浏览器就换了个人",是在假装有一套账号体系。真实身份属于后续章节。

**`status` 的三态:本章只用前两态。** 首轮建壳写 `进行中`;某一轮触发了 `create_ticket` 时置 `已转人工`(这是 `create_ticket` 在本章唯一自然的落点 —— 一个工单被建出来,却没有任何地方记得"这个会话交给人工了",那个字段就只是个装饰)。`已结束` **本章没有任何路径会写入**,如实记录,不假装有闭环。

**`tickets.ticket_no` 是主键**,格式 `T` + `YYYYMMDD` + 3 位日序号(例 `T20260928001`)。生成方式是"查当天已有条数 + 1",**并发下会撞主键** —— 撞了就捕获 `IntegrityError` 递增重试(上限 5 次),仍失败则让 `create_ticket` 返回 `ok=False` 的结构化错误。上限写进 §16。

## 七、Docker、建表方式与种子数据

### `docker-compose.yml`(仓库根)

MySQL 8.0,**容器 3306 → 宿主 3306**,`MYSQL_DATABASE=mewhelp`,账号密码取自 `.env` 的 `MYSQL_*`,命名卷持久化,带 `healthcheck`(`mysqladmin ping`)。

### 建表方式:`sql/ch02-ddl.sql` 是权威,不用 `create_all` 建 MySQL 表

不是偏好,**是实测存在一道缝**:已在本机验证,ORM 能逐字复刻你 DDL 的绝大多数要素,但

| 你的 DDL 要素 | ORM 能否复刻 |
|---|---|
| `BIGINT UNSIGNED` | ✅ |
| `ENUM(...) DEFAULT '进行中'`(中文值) | ✅ 逐字一致 |
| `DEFAULT CURRENT_TIMESTAMP` | ✅ |
| `CONSTRAINT fk_... FOREIGN KEY(...)` | ✅ |
| `ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='...'` | ✅ |
| `KEY idx_user_id (user_id)` | ⚠️ 能建,但**不内联进 `CREATE TABLE`**,要单独编译 |
| **`ON UPDATE CURRENT_TIMESTAMP`** | ❌ **`server_onupdate` 不落 DDL** |
| SQLite 侧自增 / 中文 ENUM 原值 | ✅ id = 1,2;原值 `进行中` |

最后一行决定了这件事:让 `create_all` 建 MySQL 表,`updated_at` 就**永远不会自动更新** —— 而那是你写进 DDL 的东西。既然你的 `.sql` 已经写好了,让它当真相比让 ORM 去近似它更诚实。

**因此分工是:**

- **MySQL(演示)**:`sql/ch02-ddl.sql` 建表。它**自带 `SET NAMES utf8mb4`** —— docker 官方 mysql 镜像的 client 默认字符集可能是 latin1,不加这一句,中文 ENUM 值与种子数据会被 **double-encode 存成乱码**
- **SQLite(测试)**:`Base.metadata.create_all()`,内存库,不需要 Docker
- **两者的漂移由 `test_db_ddl_drift.py` 守住**(见 §15.1)

**为什么不上 Alembic**:Alembic 解决的是"表结构在生产环境上要可演进、可回滚"。本章没有生产环境,表结构在一个 `.sql` 文件里,改 schema 就重建一次库。引入 Alembic 要多一个依赖、一个 `alembic/` 目录、一套 revision 流程,而它守护的风险(线上迁移失败)在这一章**不存在**。

**代价说清楚**:表结构一旦有数据就改不动了,只能删卷重建。等这个项目真的有了要保留的数据,再补 Alembic —— 那时也才有真实的迁移可写。

### 种子数据:`db/seed.py`(Python,不是 `.sql`)

**理由是它要能在两个方言上跑** —— SQLite 测试与 MySQL 演示共用同一份种子,`SET NAMES` 那类 client 侧问题对 Python 写库不适用(连接串里带 `charset=utf8mb4` 即可)。所以:**DDL 用 `.sql`(因为 CLI 执行的编码坑真实存在),种子用 Python(因为它要在 SQLite 上被测试直接断言)。**

- **`faq` 12 条**,分类覆盖:退换货 / 物流 / 支付 / 发票 / 商品 / 售后。内容见 §8(运费那条的措辞是设计的一部分)
- **`conversations` / `messages` / `tickets` 各灌 1~2 条样例**,便于直接开表看结构;其余由聊天过程产生
- 脚本**幂等,可重复跑**(按主键 upsert)

## 八、「邮费是多少」的漏召回必须是设计出来的

这是验收 ③,也是全章最容易蒙混过去的一条。如果种子数据**碰巧没有**邮费条目,漏召回是运气不是设计 —— ch03 的向量检索就失去了一个可信的对照基线。

**所以把它钉死:**

1. `faq` 里**有**一条讲运送费用的知识,但措辞是 **「运费怎么计算」**,该行全文**不含**「邮费」这个子串。
2. `query_faq(keyword)` 做 `LIKE '%keyword%'` 扫 `question` / `answer` / `category`。用户问「邮费是多少」,模型抽出的关键词是「邮费」→ **0 行命中**。
3. 于是漏召回发生在**同义词**这一层,而不是"我们忘了写这条知识"。这正是向量检索要解决的问题,ch03 拿它当基线才有意义。
4. 验收 ② 走**同一段代码**:「退货政策是什么」→ 关键词「退货」→ 命中 → 有答案。

**同一个工具,一问就中、一问就漏** —— 这才说明漏的是检索能力,不是工具坏了。

### 配套:System Prompt 必须禁止模型凭知识直答

否则模型可能用参数化知识直接答出邮费,验收 ③ 观察到的就变成"模型没调工具",而不是"查表查不出来" —— 那是另一件事,不能混为一谈。

`ch02/prompts.py` 的 System Prompt 因此必须包含:**政策类问题(退换货、运费、发票、保修、时效)一律先调 `query_faq`,不许用自己的知识直接回答;工具说没有就如实告诉用户没查到。**

## 九、工具基础设施

### 9.1 注册表

`ToolRegistry`(`registry.py`)持有 `dict[str, BaseTool]` 与每个工具的元信息,提供 `names()` / `tools()`(给 `bind_tools`)/ `get(name)`。

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

### 9.3 执行管线(`infra.py`)

| 步骤 | 失败时 |
|---|---|
| 1. 按 `name` 查工具 | 模型编了个不存在的工具 → 结构化错误结果,**不抛** |
| 2. 按 `tool.args_schema` 显式校验参数 | pydantic 报错翻成人话 → 结构化错误结果,**不重试** |
| 3. `asyncio.wait_for(tool.ainvoke(args), timeout=3.0)` | 超时 → 进第 4 步 |
| 4. 重试(**)仅对读类工具**) | 最多 **3 次尝试**(1 初试 + 2 重试),退避 0.2s / 0.4s;每次尝试独立计时 |
| 5. 仍失败 | 结构化错误结果回灌给模型 → **不抛**,让模型据此组织回答 |

**三条不重试的判断:**

- **参数校验失败不重试。** 参数是模型生成的,同一个坏参数重试三次只会白烧三倍时间 —— 重试对"输入错了"这个成因无效。校验失败应当立刻回灌,让模型自己改口径重问(那是下一轮的事,本章不做)。
- **只重试超时与瞬时异常,不重试语义性失败。** 比如"订单不存在",重试三次还是同一个答案。
- **写类工具(`create_ticket`)一律不重试。** 没有幂等设施,超时重试会**重复建单** —— 用户投诉一次,工单出来两张。这条与上一条是独立的:即使成因是瞬时异常也不重试。(`ticket_no` 撞主键后的**递增重试**是另一回事,那是工具内部换一个号再试,不是盲目重放。)

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

**代价,如实记**:`asyncio.wait_for` 超时**杀不掉已经在跑的那个线程** —— 超时只是让我们不再等它,那个线程会自己跑完。本章的工具都是"一次小查询或一次小插入",可以被放弃的代价是有界的。但这是一个真实存在的缝,写进 §16,不要在评审时被当成"超时已经做对了"。

## 十、五个工具

| 工具 | 模型可见签名 | 实现 |
|---|---|---|
| `query_order` | `order_id: str` | 内部生成:商品名、下单时间、金额、订单状态 |
| `query_product` | `product_name: str` | 内部生成:价格、库存、规格 |
| `query_logistics` | `order_id: str` | 内部生成:承运商、运单号、轨迹节点列表 |
| `query_faq` | `keyword: str` | `LIKE '%keyword%'` 扫 `faq` 的 `question`/`answer`/`category` |
| `create_ticket` | `description: str`, `ticket_type: Literal['售后','投诉','咨询']` | 写 `tickets` 表,返回工单号;会话置 `已转人工` |

- 前三个**不接真实接口、不建表**,在工具内部生成 —— 按需求写死。
- **随机源由入参决定**:`random.Random(f"{tool_name}:{arg}")` 起种子。**同一个 `order_id` 永远给出同一份物流** —— 验收 ① 可复现、可截图,eval 也能断言内容。真实随机留给"换个订单号"这个维度。
- `query_faq` **命中 0 行时返回一句明确的"知识库没有这一条"**,不是空串。空串回灌给模型,模型分不清"查了没有"和"工具坏了",容易编答案。这是本章唯一那个刻意漏召回的出口,它的措辞决定了模型会不会老实说"没查到"。
- `create_ticket` 的 `ticket_type` 用 `Literal`,取值集与 §6 的 `tickets.ticket_type` 严格对应(3 值),约束模型别乱填。

## 十一、编排:单轮收敛

```
_prepare_turn
① 会话身份:读 conversations(按 session_id)
     没带 session_id → 新建会话 + 新 uuid,resumed=false
     带了且查到    → 用它,resumed=true
     带了但查不到  → 按这个 session_id 建,resumed=false(见 §12.3)
② 组装上下文:按 conversation_id 读 messages → 回放过滤(见下)→ trim
     [AGENT_SYSTEM] + [跨轮历史] + [本轮 user]
③ turn1:model.bind_tools(5 个).astream(messages),**边流边吐 token**
④ 若这一轮带 tool_calls:
     → 推 tool 帧(phase=start)
     → 执行(走 §9.3 管线,一轮内多个 tool_call 并行 asyncio.gather)
     → 推 tool 帧(phase=end,带 ok / elapsed_ms / attempts)
     → 回灌:[...messages, ai(带 tool_calls), *tools(带 tool_call_id)]
     → 收敛:model.astream(...),**不 bind_tools**,继续逐 token 吐
⑤ 无 tool_calls → 直接收敛(turn1 的正文就是最终答案)
⑥ 落库(§13)→ done
```

### 「单轮」的确切含义:一次模型调用里的**所有** tool_calls 都执行

需求写的是「模型调一次工具就收敛」。**这里的「一次」指的是一轮模型调用,不是「恰好一个调用」** —— 模型完全可能在一个回合里同时发出两个 `tool_calls`(OpenAI 协议下它们是并列的,`AIMessage.tool_calls` 是个列表)。

所以钉死:

- **这一轮里所有的 `tool_calls` 全部执行**,每个的结果各回灌一条 `ToolMessage`
- 每个工具都推自己的一对 `tool` 帧(start / end)
- 然后收敛

**不做的**:不因为"只允许调一次"就丢掉第二个调用 —— 那就等于模型说的话被静默截断了,而用户看不到任何痕迹。也不做"执行完第一个发现够了就跳过其余" —— 那需要一个判断"够了"的规则,而那个规则本身就是 Agent Loop 的雏形。

评估集里有一条专门量这个(一次问句里同时涉及订单与物流),数字如实记。

### 收敛那一步不 bind_tools,是"只做单轮"的结构性保证

模型**没有工具可调**,收敛不是靠嘱咐,是靠它调不到。这比在 prompt 里写"只准调一次"可靠得多 —— 后者是一句可以被忽略的话,前者是一个不存在的接口。

### 第 ③ 步的正文处理:边收边吐(有代价的选择)

模型调工具时**通常只吐工具调用、不吐正文**,但不是永远如此。三种处理:

| 方案 | 做法 | 代价 |
|---|---|---|
| (a) | 第 1 次调用不流式,拿到结果再决定 | 不需要工具的普通问答**就不流式了** —— 而那是**多数轮次**,ch01 的流式白做 |
| (b) | 第 1 次调用的正文先攒着,看清有没有工具调用再决定 | 同上,只是延后到生成完 |
| (c) ⭐ | **正文照常逐 token 流**;若后面跟了工具调用,推 tool 帧、接着吐最终答案 | 模型既说前言又调工具时,**前言与最终答案同框** |

**选 (c)。** 收益是**两条路都保住流式**,而那正是 ch01 的立身之本。真实情况下模型调工具时正文为空,(c) 与 (a) 在验收 ①② 上表现完全一致;差别只在"模型说了句『让我查一下』再调工具"这个情形 —— 而那恰恰是**人类客服也会有的说法**,同框并不难看。

**不做文本缓冲的理由**:缓冲意味着"逐 token 流式"这条路在首个 token 上让位于一个可能根本不发生的分支,把确定性收益(每次都卡)换成一个不确定的损失(偶尔同框)。

代价要落到前端:徽章可能在正文已经吐了几个 token 之后才到,**插入位置在正文容器之上**,不能重置已经渲染的文本(§14)。

### 跨轮上下文:回放过滤

**只回放 `role=user` 与「`role=assistant` 且 `content` 非空且 `tool_calls` 为空」的消息**(每轮的提问与最终回答)。带 `tool_calls` 的 assistant(常夹带 preamble)与 `tool` 消息**不跨轮回放**,避免续接时模型看到自己上一轮的半截前言。**但全部消息仍完整落库**,备追溯。

### 最终回答为空,仍按 ch01 的失败处理

若收敛后的正文 `strip()` 之后为空,抛 **`EmptyCompletionError`**(复用 ch01 那个异常类),api 层翻成 `error` 帧 + `code="empty_completion"`,**并且不落库** —— 与 §13「只在整轮成功之后写」一致。

理由与 ch01 完全相同:空回答会作为一条真正的空 assistant 消息永久重放。**注意与工具调用轮的区分**:turn1 的正文**通常就是空的**,那**不是**失败 —— 判空的只有收敛后的最终回答。这是本章新引入的一个容易搞反的地方:同一段代码里,turn1 的空正文合法,收敛那一步的空正文非法。

## 十二、接口契约

### 12.1 POST /ch02/chat/stream(SSE,前端主入口)

```jsonc
{
  "session_id": "…",     // 可选,不传则服务端生成并从 session 帧返回。空白一律 422(ch01 同一条规则)
  "user_id":    "…",     // 可选,缺省 "demo-user"。见 §6.4,本章是占位
  "message":    "…"      // 必填,空白(含零宽字符)一律 422
}
```

`user_id` 的校验沿用 ch01 的 `_reject_blank` 规则:`extra="forbid"`、present-but-blank 拒掉、非空白原样放行不 strip。

| 事件 | 载荷 | 说明 |
|---|---|---|
| `session` | `{session_id, resumed}` | **仍是第一个**,ch01 契约 + 新增 `resumed`(见 12.3) |
| `tool` | `{name, args, phase: "start"｜"end", ok?, elapsed_ms?, attempts?}` | **新增**。`phase=start` 时只有 `name`/`args`;`phase=end` 时补齐 `ok`/`elapsed_ms`/`attempts` |
| `token` | `{text}` | **不变**(注意:ch01 叫 `token`,不是 `delta`) |
| `done` | `{finish_reason}` | 不变(仍恒为 `"stop"`,ch01 的如实声明继续有效) |
| `error` | `{message, code}` | 不变,`code` 沿用 `empty_completion` / `upstream_error` |

**向后兼容**:老页面不认识 `tool` 事件会直接忽略它,不会坏。新页面拿它画徽章。

**ch01 的路由 `/ch01/chat/stream` 保留不动**,聊天页改为指向 `/ch02/chat/stream`。

### 12.2 POST /ch02/agent(JSON,程序化 / eval / 测试出口)

```jsonc
// 请求:与 12.1 同形
{ "session_id": "…", "user_id": "…", "message": "订单 1001 的物流到哪了" }

// 响应
{
  "session_id": "…",
  "conversation_id": 12,
  "answer": "您的包裹已到达…",
  "tool_calls":   [{"name": "query_logistics", "args": {"order_id": "1001"}, "id": "call_1"}],
  "tool_results": [{"name": "query_logistics", "ok": true, "content": "…", "elapsed_ms": 3, "attempts": 1}]
}
```

存在的理由:**`curl` 一眼看到模型选了哪个工具**,eval 与单测不必解析 SSE。它与 12.1 共用 `_prepare_turn`,不重复实现。

### 12.3 session_id → conversation 的映射

`conversations.session_id` 上是 UNIQUE。每轮的处置:

| 请求里的 session_id | 处置 | `resumed` |
|---|---|---|
| 没带 | 生成新 uuid4,建会话,`status='进行中'` | `false` |
| 带了,查到 | 用它。`user_id` 保持原样(**不覆盖** —— 同一 session 换了 user_id 是异常,但本章不做鉴权,静默保留首轮那个) | `true` |
| 带了,查不到 | **按这个 session_id 建** | `false` |

**为什么第三条不返回 404。** 身份是不可猜的 uuid4,所以"带着一个我们没见过的 id"只有两种可能:本地存的会话已过期/库被重建,或者……没有第二种。按它建,客户端手里的 id 继续有效,只是历史是空的;返回 404 反而会把客户端卡在一个它无法自救的状态(它没有别的 id 可用)。

**但"静默"不行** —— 用户会以为自己还在原来的对话里。所以 `session` 帧带 `resumed`,前端在 `resumed=false` 时可以提示"这是一段新对话"。**这是本设计里唯一一处把不可见的上下文丢失变可见的地方**,三行代码,值得。

这个"查到就用、查不到就建"要能并发安全:两个并发请求同时建同一个 `session_id` 会撞 UNIQUE。处置与 §6.4 的工单号一致 —— 捕获 `IntegrityError` 后重查一次。

### 12.4 错误处理分层

| 场景 | `/ch02/chat/stream` | `/ch02/agent` |
|---|---|---|
| 工具执行失败 | 基础设施捕获 → 回灌 → 模型正常作答,`tool` 帧照发(`ok=false`) | 200,`tool_results[].ok=false` |
| 请求体校验失败 | 422(未进流) | 422 |
| 上游 LLM 失败 | `error` 帧(流已发 200,错误只能走帧) | 502 |
| 最终回答为空 | `error` 帧 `code=empty_completion` | 502,同上 |
| **落库失败** | **只记日志,不打扰用户**(见 §13) | 只记日志 |

## 十三、落库

### 只在整轮成功之后写

与 ch01「失败的一轮不写进会话历史」保持同一条规则 —— 半截的账本比没有账本更难查。**turn1 挂了、工具挂了但模型没答出来,都不写 `user` 行。**

一轮成功且调了工具时,写 **4 条** `messages`:

```
role=user       content=用户那句话
role=assistant  content=模型前言(通常为空)   tool_calls=[{name,args,id}]
role=tool       content=工具返回原文           tool_call_id=<对应 id>
role=assistant  content=最终回答
```

没调工具的一轮写 2 条。`conversations` 首轮建壳(`status='进行中'`);本轮触发 `create_ticket` 时置 `已转人工`。

### 落库失败不推翻已经答完的那一轮

处置:**写库失败只记日志,不影响响应**。

理由:token 已经逐字吐给用户了,这时候因为落库失败补一个 `error` 帧,是在**骗人** —— 用户明明看到了完整回答,却被告知这一轮失败了。

**代价,要有意识地接受**:账本可能缺行,而且是静默缺行。缓解是日志留痕 + §16 把它写成已知局限。**这是本章明确接受的设计代价,不是疏忽。**

## 十四、聊天页改造(Vibe Coding,不评审)

`static/index.html` 的改造**走 Vibe Coding**:用户描述效果,直接改,不套 brainstorm / TDD / code review。

本章要加的是**工具轨迹徽章**:气泡里显示这一轮调了哪个工具、成功还是失败、耗时。

- 请求体 `{session_id, message}` → `{session_id, user_id, message}`;`user_id` 用 `localStorage` 里的稳定标识
- 收到 `tool` 帧(`phase=start`)→ 在气泡里挂一个徽章,显示工具名(如「🔧 查物流」)
- 收到 `tool` 帧(`phase=end`)→ 补齐状态(成功 / 失败、耗时)
- 收到 `session` 帧 `resumed=false` 且本地已有历史 → 提示「这是一段新对话」
- **徽章不能打断流式正文**:徽章是气泡里的独立元素,`textContent` 累积正文的写法得让位(徽章的 DOM 节点会被 `textContent = ''` 抹掉)

最后一条是本页已知的坑:现有代码用 `reply.textContent = ''` 清掉等待动画、用 `+=` 累积正文 —— **`textContent` 会清掉所有子节点**([index.html:385](src/mewhelp/static/index.html#L385))。徽章要活下来,正文得换一个独立的容器节点。这条在改动时会被真实撞到。

**待定增项(超出需求原文,可随时砍):** 最终回答的轻量 markdown 渲染(先转义再渲染,支持加粗/列表/代码块/链接)。它让模型输出的列表能看,但给一个 Vibe Coding 的页面增了约百行 JS 和一处注入面。**不采纳入 spec 基线,做的时候问用户。**

## 十五、测试策略与验证方式

按需求:**非可单测的产出把 TDD 换成标注样例 / 评估集验证**。

### 15.1 照常走 TDD 的部分(纯逻辑,SQLite 内存库,不需要 Docker)

| 文件 | 覆盖 |
|---|---|
| `test_db_models.py` | 四张表可建;`role` 的 ENUM 在 SQLite 上落成 CHECK 并真的拒非法值;中文 ENUM 存的是值不是成员名;`tool_calls` JSON 往返 |
| `test_db_ddl_drift.py` | **漂移守门**:把模型编译成 MySQL 方言,断言与你 `ch02-ddl.sql` 对齐的关键要素 —— 列名与类型、`BIGINT UNSIGNED`、中文 ENUM 取值与默认值、索引名(`idx_*` 单独编译,不内联)、命名外键。**`ON UPDATE CURRENT_TIMESTAMP` 只断言在 `.sql` 文件里存在**(ORM 产不出,见 §7) |
| `test_db_seed.py` | 幂等:连跑两次行数不变;`faq` 条数与分类覆盖;运费那条**确实不含**「邮费」 |
| `test_tool_infra.py` | 未知工具名;参数校验失败**不重试**;超时重试;退避;**写类工具不重试**;尝试次数上限;`ToolResult` 各字段 |
| `test_tool_business.py` | **同入参 → 同输出**(钉住 §10 的可复现性);换入参 → 不同输出;三个工具都不碰数据库 |
| `test_tool_knowledge.py` | LIKE 命中;命中 0 行返回明确文案(不是空串);「邮费」查不到 / 「退货」查得到 |
| `test_tool_ticket.py` | 工单号格式;落库;`IntegrityError` 重试递增;`已转人工` 流转 |
| `test_ch02_events.py` | 事件对象形状 |
| `test_ch02_service_tools.py` | 调工具 / 不调工具两条路的**帧序列**;收敛那一步**没有绑定工具**;**一轮内两个 tool_calls 都被执行**且各推一对帧;**turn1 的空正文合法、收敛的空正文抛 `EmptyCompletionError`**;会话查到就用 / 查不到就建 / 没带就新建(含 `resumed` 三态);**回放过滤**:带 `tool_calls` 的 assistant 与 `tool` 消息不跨轮回放 |
| `test_ch02_api_chat.py` | `tool` 帧字段;老客户端忽略未知事件不坏;`user_id` 的空白拒绝与缺省值 |
| `test_ch02_api_agent.py` | JSON 出口的 `tool_calls` / `tool_results` 形状;422 / 502 |

### 15.2 换成评估集的部分(模型选哪个工具,不可单测)

`tests/eval/tool_routing_cases.jsonl` 共 **24 条**,每条标 `(question, expected_tool | null, note)`,通过 `pytest -m eval` 对真实上游跑 —— **走 `/ch02/agent` 或 `run_agent_turn`,不解析 SSE**:

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

Docker 起来、DDL 执行、种子灌好、服务起了之后,按验收标准 ①②③ 逐条跑,输出原文落进报告。**① 因为 §10 的定种子设计,`订单 1001` 每次给同一份物流,可以稳定截图。**

## 十六、风险与已知局限

| # | 风险 / 局限 | 处置 |
|---|---|---|
| 1 | **`deepseek-chat` + `bind_tools` 是否真返回 `tool_calls`** —— ch01 已证同型号的 function calling 走得通(`with_structured_output(method="function_calling")`),但 `bind_tools` 这条**未实测** | 第一个开发任务就是真实 tool-calling 冒烟。**不成立则停下来问用户**,不自行换方案 |
| 2 | Docker 守护进程未启动;`MySQL80` 服务占着 3306 | 真机演示前需用户启 Docker + 停服务。`MySQL80` 开机自启,重启后会回来抢端口 —— **改服务启动类型是系统级动作,先问用户** |
| 3 | 落库失败被静默吞掉(§13) | 已在 §13 明确为接受的设计代价 + 日志留痕 |
| 4 | **SQLite 与 MySQL 的方言缝** | MySQL 的 `utf8mb4_0900_ai_ci` 让 `LIKE` 大小写不敏感,SQLite 对非 ASCII 敏感 —— **中文无大小写,本章语料上不产生可观察差异**。DDL 漂移由 §15.1 的守门测试管;另加一条 MySQL 真机冒烟(不在默认套件里) |
| 5 | 工单号并发冲突 | 撞主键后递增重试,上限 5 次,仍失败则返回 `ok=False`。真实并发下的上限是理论上的 |
| 6 | **超时杀不掉线程**(§9.5) | 已如实记录。可以被放弃的代价有界(一次小查询 / 小插入) |
| 7 | `messages.tool_calls` 是 JSON 列,**MySQL 与 SQLite 的 JSON 语义不同**(SQLite 存 TEXT) | 本章只做整体读写、不做 JSON 路径查询,差异不显现。将来要按工具名检索就得先处理这条 |
| 8 | `faq` 的 `LIKE '%kw%'` **不可能走索引** | 这是本章刻意要展示的朴素检索 —— 全表扫描是 12 行规模下的正确答案。ch03 换向量检索时,这条是升级动机的一部分 |
| 9 | **`conversations.session_id` 是你 DDL 之外的一列**(§6.3) | 已获用户批准。代价是 `sql/ch02-ddl.sql` 与原始 DDL 不逐字一致,`test_db_ddl_drift.py` 以本 spec 为准而非以原始 DDL 为准 |
| 10 | `conversations.status='已结束'` 本章无路径写入(§6.4) | 如实记录,不假装有闭环 |

## 十七、交付物

1. **功能演示命令** —— 起 Docker / 执行 DDL / 灌种子 / 起服务 / 打开聊天页 的完整命令序列,写进 `README.md`(含 Git Bash 与 PowerShell 两版)
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
| MySQL 真机冒烟通过 | 执行 `sql/ch02-ddl.sql` + 种子 + 一轮调工具的对话,在真 MySQL 上跑通 |

## 十九、决策记录

| 决策 | 选择 | 理由 / 考虑过的替代 |
|---|---|---|
| 工具链落点 | 长在前端在用的聊天入口 | 能力要落在用户实际使用的入口上;只做独立程序化接口,用户视角里等于没交付 |
| 编排结构 | 共享 `_prepare_turn` + `stream_agent_turn` / `run_agent_turn` 两出口 | 只在收敛那步不同,不重复实现 |
| 流式与工具 | turn1 也 `astream`,收敛同样 `astream`(方案 c) | 保住"每条路都流式";替代方案 a/b 让**多数轮次**丢掉打字机 |
| 单轮保证 | 收敛那步不 `bind_tools` | 结构性保证,不是 prompt 嘱咐 |
| 上下文源 | **DB 回放**(按 `conversation_id` 读 `messages`)+ 回放过滤 | 加了 `session_id` 后 DB 回放变干净,且消掉了「内存 store 与 MySQL 两个真相」那道缝。**替代方案**:沿用 ch01 的 `SessionStore` 当上下文源(用户曾选过),代价是进程重启即丢 + 两个真相 |
| 会话身份 | `session_id`(uuid4,加列存 DB) | 不可猜,挡住"猜 id 读别人的会话";ch01 契约不变。**替代方案**:用自增 `id` 当身份(可枚举) |
| `session_id` 查不到 | 按它新建 + `resumed=false` | 客户端自救;三行代码把不可见的上下文丢失变可见。**替代方案**:404(会把客户端卡死) |
| 表结构来源 | `sql/ch02-ddl.sql` 权威,`create_all` 只用于 SQLite | **实测**:ORM 产不出 `ON UPDATE CURRENT_TIMESTAMP`;让 ORM 近似它,`updated_at` 就永不自动更新 |
| 种子位置 | `db/seed.py`(Python) | 要在 SQLite 测试与 MySQL 演示两个方言上跑,且测试要能直接断言"运费那条不含邮费" |
| 中文 ENUM | `Enum(..., values_callable=...)` | 默认存成员名,库里会变成 `ConvStatus.ongoing`。与 ch01 `f"{intent}"` 是同一类坑 |
| 上下文注入 | 闭包(`build_registry(db, conversation_id)`) | `InjectedToolArg` **在 1.6.5 上实测不生效**,参数会泄进 schema 的 `required` |
| 写类工具重试 | 一律不重试 | 无幂等设施,超时重试会重复建单 |
| mock 数据 | 由入参定随机种子 | 同一 `order_id` 永远同一份物流 → 验收可复现、可截图、eval 可断言 |
| 数据层 | 同步 SQLAlchemy + PyMySQL | **实测** `greenlet`/`asyncmy`/`aiosqlite` 全缺 —— 异步要 +3 依赖,换本章不需要的并发 |
| 路由前缀 | `/ch02/*`,沿用 `APIRouter(prefix=...)` | 与 ch01 同风格,不在同一份代码里并存两种命名 |
| SSE 帧名 | `session` / `tool` / `token` / `done` / `error` | ch01 实际帧名是 `token` 不是 `delta`,也没有 `[DONE]` 哨兵 —— 以代码为准 |
| 目录结构 | `src/mewhelp/{db,tools,ch02}/` | 不引入 `app/`、`core/` 这类新顶层,ch01 一行不改 |
| `/ch02/agent` | 保留为 JSON 出口 | `curl` 一眼看工具轨迹;eval 与单测不必解析 SSE |
| 前端改造 | Vibe Coding | 用户工作要求 1:聊天页改造是 Superpowers 流程例外 |
| `SessionStore` / 旧 prompt | 弃用但不删文件 | 删除是额外风险,留着无害(用户拍板) |
| 建表字符集 | `ch02-ddl.sql` 自带 `SET NAMES utf8mb4` | docker mysql client 默认 latin1 会把中文 ENUM 值 double-encode 成乱码 |
