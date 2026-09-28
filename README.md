# MewHelp

电商 AI 智能客服 · Agent 应用开发实战

> 基于课程项目,做工程化改造。选题、架构决策和改造点见 [docs/decisions.md](docs/decisions.md)。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 / 框架 | Python 3.11+ · FastAPI |
| Agent 编排 | LangGraph |
| 检索 | Milvus · BGE-M3 · BM25 · RRF · Rerank |
| 存储 | MySQL · SQLAlchemy |
| 可观测 | Langfuse |
| 部署 | Docker Compose |

## 本地开发

```bash
# 1. 环境
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
# .venv\Scripts\activate           # Windows CMD/PowerShell

# 2. 依赖(测试依赖 pytest / pytest-asyncio / ruff / pyyaml 在 [dev] extra 里,
#    只装 `pip install -e .` 是跑不了测试的)
pip install -e ".[dev]"

# 3. 配置密钥(.env 不会进版本库)
cp .env.example .env
# 然后编辑 .env 填入真实值

# 4. 起依赖服务
docker compose up -d

# 5. 跑起来
uvicorn mewhelp.main:app --reload
```

## 验收演示(Ch01)

先起服务:

```bash
uvicorn mewhelp.main:app --reload
```

**必须在仓库根目录启动。** `mewhelp.config.Settings` 的 `env_file=".env"` 是相对**当前工作目录**
解析的:换到别的目录跑就读不到 `.env`,启动时报 `openai_api_key` 缺失。那是工作目录的问题,
不是代码的问题 —— 解决方式是回到仓库根目录,不是往代码里塞密钥。

以下命令用 **Git Bash**。PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名,
要改用 `curl.exe` 并把 JSON 写成 here-string。

> **中文 body 在本机 Git Bash 里传不进去 —— 下面三条命令会直接报 400。**
> ① 流式对话、② 多轮上下文、③ 售后描述结构化,三条都带中文 body,粘进本机 Git Bash 会拿到
> `HTTP 400` 和 `{"detail":"There was an error parsing the body"}`。
> **这不是服务端的问题**(同一份 body 存成 UTF-8 文件发过去一切正常),是**命令形式**的问题:
> Git Bash 把参数按系统 ANSI 代码页(936/GBK)转给 `curl` 了。可直接跑通的 here-doc 写法在
> 本节末尾的「中文 body 在 Git Bash 里传不进去(本机实测)」。
> 下面三条命令**保持原样不改** —— 它们在不经过这层转换的 UTF-8 Git Bash 上是正确的。

### ① 流式对话

```bash
curl -N -X POST http://127.0.0.1:8000/ch01/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"你们一般几点发货?"}'
```

`-N` 必须有,否则 curl 会缓冲,看不出逐 token。

### ② 多轮上下文(同一个 session_id)

```bash
curl -N -X POST http://127.0.0.1:8000/ch01/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"我上周买的跑鞋到现在还没发货"}'

curl -N -X POST http://127.0.0.1:8000/ch01/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"那我还要等多久?"}'
```

### ③ 售后描述结构化

```bash
curl -X POST http://127.0.0.1:8000/ch01/extract \
  -H "Content-Type: application/json" \
  -d '{"description":"订单 20240915001,我买的鞋尺码不对想换大一码,能直接换吗?"}'
```

### 浏览器里看

打开 <http://127.0.0.1:8000/> 是聊天页。

### 中文 body 在 Git Bash 里传不进去(本机实测)

上面三条命令里的中文 body 会被 Git Bash 转坏。Git Bash 把命令行参数交给
`/mingw64/bin/curl`(原生 Windows 程序)时按**系统 ANSI 代码页**(本机 936/GBK)转换,
中文变成 GBK 字节,服务端按 UTF-8 解析,直接返回 **HTTP 400**:

```
{"detail":"There was an error parsing the body"}
```

这不是服务端的问题 —— 同样的 JSON 存成 UTF-8 文件用 `-d @body.json` 发就没问题。
要让中文照原样留在命令里,把 body 从**标准输入**喂进去(不经参数转换):

```bash
curl -N -X POST http://127.0.0.1:8000/ch01/chat/stream \
  -H "Content-Type: application/json" --data-binary @- <<'JSON'
{"message":"你们一般几点发货?"}
JSON
```

三条命令都能这么改:把 `-d '{...}'` 换成 `--data-binary @-` 加 here-doc。

## 验收演示(Ch02 · Function Calling 工具链)

第 2 章给客服加上了**工具调用**:用户提问后由模型自己决定调哪个工具,工具结果回灌给模型,
再收敛成答复。五个工具:`query_order` / `query_product` / `query_logistics`(内部造数,不连真实 API)、
`query_faq`(查 `faq` 表)、`create_ticket`(写 `tickets` 表)。**单轮**:调一次工具就收敛,不做多轮 Agent Loop。

### 0. 起依赖服务

`faq` / `conversations` / `messages` / `tickets` 四张表在 MySQL 里,`query_faq` 和 `create_ticket` 要用。

```bash
# 本机 3306 常被 Windows 自带的 MySQL 服务占着 —— 先停掉它,再起容器
powershell -Command "Stop-Service MySQL80"        # 需要管理员权限的终端
docker compose up -d && docker compose ps

# 建表 + 灌种子(faq 表里的问答条目)
docker compose exec -T mysql mysql -uroot -p"$MYSQL_PASSWORD" mewhelp < sql/ch02-ddl.sql
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 .venv/Scripts/python.exe -m mewhelp.db.seed
```

### 1. 起服务

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 .venv/Scripts/python.exe -m uvicorn mewhelp.main:app --reload
```

同样**必须在仓库根目录启动**(理由见 Ch01 一节)。

### 2. 浏览器里看工具轨迹

打开 <http://127.0.0.1:8000/>,逐条问:

| 问 | 该看到 |
| --- | --- |
| 订单 1001 的物流到哪了 | 气泡上出现「🔧 查物流」徽章,收尾成 `✓ … ms`,答复基于工具返回的物流节点 |
| 退货政策是什么 | 「🔧 查 FAQ」徽章,答复里是 `faq` 表里的 7 天无理由 |
| 邮费是多少 | **徽章出现**(工具确实被调了),但答复是「没查到」 |
| 降噪耳机多少钱 | 「🔧 查商品」徽章 |

第 3 条是本章有意展示的**检索语义鸿沟**:`faq` 表里存的是「运费怎么计算」,
用 `LIKE '%邮费%'` 查不到 —— 关键词检索对同义词无能为力,这正是第 3 章向量检索要解决的东西。

> **实测提醒(2026-09-28):** 真机上第 3 条**未必**照上面这样演。三次实测里,模型都自己把
> 「邮费」改写成了 `keyword="运费"` 再查,于是**命中了**。表侧的事实没变
> (`find_faq(keyword="邮费")` 仍是 0 行,由 `tests/test_db_seed.py` 离线守着),
> 变的是这条鸿沟**被模型在"选关键词"这一步自己填上了一半**。
> 看到命中不要以为坏了 —— 看一下徽章里的参数,那才是这一轮真正发生的事。

### 3. 程序化看工具轨迹(不解析 SSE)

```bash
curl -X POST http://127.0.0.1:8000/ch02/agent \
  -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"session_id":"demo","message":"订单 1001 的物流到哪了"}
JSON
```

返回 JSON,`tool_calls` 是模型选了哪些工具,`tool_results` 是每个工具的结果与耗时:

```json
{
  "session_id": "demo",
  "conversation_id": 1,
  "resumed": false,
  "answer": "订单 1001 已由顺丰发出,目前在中转场……",
  "tool_calls": [{"name": "query_logistics", "args": {"order_id": "1001"}, "id": "call_..."}],
  "tool_results": [{"name": "query_logistics", "ok": true, "content": "...", "elapsed_ms": 3, "attempts": 1}]
}
```

上游出错或模型没产出内容一律 **502**(`detail` 里的文字区分是哪一种)，重发即可重试。

> 中文 body 与 Ch01 一样传不进本机 Git Bash,这里已经用 `--data-binary @-` + here-doc 的写法。
> PowerShell 版见下。

### PowerShell 版

PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名,单引号 JSON 会失败。用 `curl.exe` +
here-string(`@'…'@` 的收尾 `'@` 必须顶格):

```powershell
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
.venv\Scripts\python.exe -m uvicorn mewhelp.main:app --reload
```

```powershell
curl.exe -X POST http://127.0.0.1:8000/ch02/agent `
  -H "Content-Type: application/json" `
  --data-binary "@body.json"
```

here-string 直接管道给 `curl.exe` 在 PowerShell 5.1 下会带上 BOM 和 CRLF,服务端照样 400 ——
**先写成 UTF-8 文件再 `--data-binary "@body.json"`**,这是本机唯一稳的写法。

```powershell
# 写文件时显式指定 utf8(Set-Content 默认走系统 ANSI 代码页,中文会坏)
'{"session_id":"demo","message":"订单 1001 的物流到哪了"}' | Out-File -Encoding utf8 body.json
```

### 4. 跑测试

```bash
env PYTHONIOENCODING=utf-8 PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest
```

工具选择的**质量数字**在标注集里(24 条),真调上游,默认不跑:

```bash
env PYTHONIOENCODING=utf-8 PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -m eval -s
```

它只打印命中率、**不设门槛**。2026-09-28 三次实测:19/24、20/24、19/24。

## 目录结构

```
src/mewhelp/      业务代码
tests/            测试
docs/             设计文档与决策记录
NOTES.md          开发过程中踩的坑、做的取舍、量到的数据
```

## 分支与 worktree

主仓库停在 `main`,每章新功能从 `main` 开出独立 worktree,详见 [docs/worktree.md](docs/worktree.md)。
