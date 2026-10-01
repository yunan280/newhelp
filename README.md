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

# 2. 当前统一应用需要 rag extra；测试依赖在 dev extra
pip install -e ".[rag,dev]"

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
# 项目 MySQL 监听 .env 的 MYSQL_PORT（默认 3307），可与本机 3306 的 MySQL 并存
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
| 邮费是多少 | **徽章出现**(工具确实被调了);答复是「没查到」**或**「单笔满 99 元包邮」——见下 |
| 降噪耳机多少钱 | 「🔧 查商品」徽章 |

第 3 条是本章有意展示的**检索语义鸿沟**:`faq` 表里存的是「运费怎么计算」,
用 `LIKE '%邮费%'` 查不到 —— 关键词检索对同义词无能为力,这正是第 3 章向量检索要解决的东西。

> **实测提醒(2026-09-28,四次真机跑):这一条是波动的,两种结果都正常。**
>
> 模型有时把「邮费」**改写成** `keyword="运费"` 再查(评估集三次跑都是这样 —— 命中了
> 「运费怎么计算」),有时**原样**抽出「邮费」去查(真机验收那次 —— 漏召回,答复如实说没查到)。
> 四次里三次命中、一次漏召回。
>
> **表侧的事实始终没变**:`find_faq(keyword="邮费")` 就是 0 行,由 `tests/test_db_seed.py`
> 离线守着(ch03 那条基线还在,而且它本来就该由离线用例守 —— 判一个真调上游的结果,
> 等于让基线随模型波动)。
>
> 所以看到哪种都不要以为坏了。**看徽章里的参数**(`keyword` 是「邮费」还是「运费」),
> 那才是这一轮真正发生的事。

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

聊天页有个**不用浏览器**的冒烟脚本(要 Node,不在 pytest 套件里),改完 `index.html` 手动跑一次:

```bash
node tests/page-smoke.js src/mewhelp/static/index.html
```

它用最小 DOM 替身把 SSE 帧喂进页面的 `send()`,断言徽章、正文与等待动画的取舍。
第 2 章收尾时抓出的"同名工具调两次 → 徽章配错结果"就是它拦下的 —— 那是个后端测试
看不见的缺陷。

## Ch03 · 语义知识库演示

打开 <http://localhost:8000/kb> 可手工录入知识。填写分类、问法或章节标题、答案，以及可选元数据；提交后先保存 MySQL 原文，再尝试向量化。页面显示最近 50 条知识的 `pending` / `done` 状态；待向量化条目可点“重试向量化”。同一次提交使用固定 UUID，网络失败重试不会重复建行。

`query_faq(keyword: str) -> str` 契约不变。内部使用 BGE-M3 dense 向量在 Milvus `knowledge` 集合查 Top-K，再按主键从 MySQL `knowledge_chunks` 读原文。MySQL 表结构见 [sql/ch03-ddl.sql](sql/ch03-ddl.sql)，设计与计划见 `docs/superpowers/`，开发留痕见 [dev-notes/ch03.md](dev-notes/ch03.md)。

本机使用 Python 3.12、PyMilvus 2.6.x 与 Milvus 2.6.24。首次 BGE-M3 下载约 2.3 GB 权重，需要联网及充足内存。项目根目录运行：

```powershell
uv venv .venv-ch03 --python 3.12
uv pip install --python .venv-ch03\Scripts\python.exe -e '.[rag,dev]'
docker compose up -d mysql
docker compose -f milvus-compose.yml up -d
.venv-ch03\Scripts\python.exe -m mewhelp.knowledge.cli init-db
.venv-ch03\Scripts\python.exe -m mewhelp.db.seed
.venv-ch03\Scripts\python.exe -m mewhelp.knowledge.cli ingest
.venv-ch03\Scripts\python.exe -m mewhelp.knowledge.cli sync
$env:PYTHONIOENCODING = 'utf-8'
.venv-ch03\Scripts\python.exe -m mewhelp.knowledge.cli search --question '邮费是多少'
.venv-ch03\Scripts\python.exe scripts\smoke_ch03_recovery.py
```

`ingest` 从现有 FAQ 及 `knowledge-docs/*.md` 建库。Markdown 章节、完整句重叠和大表表头保留在纯 Python 切分器中；在章节正文加入 `<!-- key-clause -->` 可标记关键条款，此标记不进向量文本。`sync` 扫描 `pending` 并按相同主键 upsert，可在中断后重跑。

历史对话任务由 Windows 计划任务调用 [scripts/run_knowledge_mining.ps1](scripts/run_knowledge_mining.ps1)，该脚本先抽取到 `qa_extraction_staging`，整批去重后发布，再执行向量补偿。手动演示：

```powershell
powershell.exe -NoProfile -File scripts\run_knowledge_mining.ps1
.venv-ch03\Scripts\python.exe -m mewhelp.knowledge.cli status
.venv-ch03\Scripts\python.exe -m pytest -q
.venv-ch03\Scripts\python.exe -m pytest -m eval tests\eval\test_knowledge_mining_eval.py -q
```

注册每日 02:00 的 Windows 计划任务（可用 `-At '03:30'` 改时间）：

```powershell
powershell.exe -NoProfile -File scripts\register_knowledge_mining.ps1
Get-ScheduledTaskInfo -TaskName MewHelp-Ch03-KnowledgeMining
```

任务使用当前登录用户的模型缓存和 `.env`；请保持该用户登录，且让 Docker Desktop 在任务运行时可用。本机已注册，下一次计划时间为 2026-09-29 02:00。手动脚本已执行成功；当前非交互式开发会话中，任务计划程序的手动触发处于 `Queued`，尚未验证调度器实际启动脚本。

本机实测：「邮费是多少」召回“运费说明”和“运费怎么计算”，均给出满 99 元包邮；模拟 MySQL 提交后中断，补偿 1 块并恢复 `done`。网页端同一问法的 FAQ 工具调用成功，答出满 99 元包邮、不满 99 元运费 8 元起。BGE-M3 首次加载视机器状态可能需要几十秒，本机曾测得 58.6 秒；FAQ 工具单次时限为 120 秒，模型加载后查询较快。此前全量离线测试 339 passed、5 deselected；本次连接修复的相关用例 9 passed，真实抽取标注评估 1 passed。实际结果以复跑输出为准。

## Ch04 · 混合检索、证据门控与评估

正式报告为 [ch04_20260930_03/report.md](artifacts/ch04/ch04_20260930_03/report.md)：40条正式测试×4策略共160次，服务/judge错误0；包含类型、难度、交叉桶和有效分母。原生 BM25、重排与生成均为真实模型调用。run01保留余额不足及初始Prompt结果，run02保留评审前结果；最新run03使用评审修复后的归一与完整新缓存，冻结语料/GT未改。

实际总表（检索GT分母每策略32，未知问题每策略8；Faithfulness仅计算完成回答）：

| 策略 | Recall@50 / @10 | MRR@10 | Faithfulness | 回答/40 | 未知正确拒答/8 |
| --- | --- | --- | --- | --- | --- |
| dense | 1.0000 / 1.0000 | 1.0000 | 0.9892 | 31 | 8 |
| BM25 | 1.0000 / 1.0000 | 0.9635 | 0.9896 | 32 | 7 |
| hybrid | 1.0000 / 1.0000 | 0.9844 | 0.9896 | 32 | 7 |
| hybrid_rerank | 1.0000 / 1.0000 | 1.0000 | 1.0000 | 31 | 8 |

BM25和混合各有1次误放；四策略各有1次误拒。这里 dense 已达到检索上限，结果不能证明混合重排在真实商品库上整体更好。生产阈值单独用20条校准，消融报告不使用该门控。

本章以 [设计](docs/superpowers/specs/2026-09-30-ch04-retrieval-quality-design.md)、[计划](docs/superpowers/plans/2026-09-30-ch04-retrieval-quality.md) 和 [阶段记录](dev-notes/ch04.md) 为准，替代上述 Ch03 的 dense-only 查询步骤。Milvus 原生 BM25 的中文 analyzer 与 BGE-M3 dense 各召回 50，`hybrid_search` 用 RRF(k=60) 融合；本地 `BAAI/bge-reranker-v2-m3` 精排 10。证据编号沿相关性排名固定，放入 Prompt 的顺序为 1,3,5,7,9,10,8,6,4,2。`category` 是知识主题，独立的可选 `product_category` 是商品品类；过滤在两路召回前生效。

所有命令从项目根目录运行。下例使用当前 `.venv-ch03`（Python 3.11+）；新环境执行 `uv venv .venv-ch03 --python 3.11` 和 `uv pip install --python .venv-ch03/Scripts/python.exe -e '.[rag,dev]'`。复制 `.env.example` 填入自己的凭据，不覆盖现有 `.env`。Docker Desktop 必须运行，两个固定模型首次下载需要网络、磁盘与内存。本次 CPU 运行限定计算线程，16GB 机器建议顺序启动评估、线上及隔离验收进程。

```powershell
$pyCh04 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
$env:PYTHONUTF8 = '1'
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
$env:RAG_CONTEXT_BUDGET = '32000' # UTF-8字节总预算，不是供应商token上限
docker compose up -d mysql
docker compose -f milvus-compose.yml up -d
# 已有 Ch03 数据库先做一致性备份，再执行可重入的增量迁移
& $pyCh04 -X utf8 scripts/migrate_ch04_schema.py
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli reindex --collection knowledge_ch04
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli sync --collection knowledge_ch04
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli audit-index --collection knowledge_ch04
```

新数据库先执行 Ch02 的 `sql/ch02-ddl.sql`（或使用 ORM 创建 Ch02 表），再 seed 示例 FAQ 和创建 Ch03 表，然后迁移、`ingest --docs knowledge-docs`、回填。不在有业务数据的库里初始化/seed。成功审计输出 `[]`；漂移或版本不匹配报错，不能切回 Python BM25 或 Milvus Lite。

```powershell
# 仅用于新的空数据库；导入 models 注册 Ch02 表，seed 本身不建表
& $pyCh04 -X utf8 -c 'from mewhelp.db import models; from mewhelp.db.base import Base; from mewhelp.db.engine import engine; Base.metadata.create_all(engine)'
& $pyCh04 -X utf8 -m mewhelp.db.seed
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli init-db
& $pyCh04 -X utf8 scripts/migrate_ch04_schema.py
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli ingest --docs knowledge-docs
```

实际标注是明确编写的**示例商品/条款**，不是线上商品事实；[标注审计](eval/ch04/annotation-audit.md) 为作者逐例核查。冻结 80 块、60 问：20 条只用于校准，40 条只用于报告，五类问法各 8 条正式测试。生成和 judge 真实调用配置的供应商，可能产生费用。用新的 run ID 运行；同一 run 禁止修改语料、Prompt、归一缓存或模型。

```powershell
$runCh04 = 'ch04_20260930_03' # 已完成的冻结运行；复跑改为新 ID，例如 ch04_20261001_01
$workCh04 = "artifacts/ch04/$runCh04"
& $pyCh04 -X utf8 -m mewhelp.knowledge.evaluation prepare --dataset eval/ch04 --workdir $workCh04 --run-id $runCh04
& $pyCh04 -X utf8 -m mewhelp.knowledge.evaluation calibrate --dataset eval/ch04 --workdir $workCh04 --run-id $runCh04
& $pyCh04 -X utf8 -m mewhelp.knowledge.evaluation compare --dataset eval/ch04 --workdir $workCh04 --run-id $runCh04
```

报告为 `$workCh04/report.md`、`summary.json`、`cases.jsonl`，校准为 `calibration.json`。四策略为 dense/bm25/hybrid/hybrid_rerank；消融关闭生产相关性阈值和入池，前三种不调用重排。报告包括 Recall@50/MRR@50、最终 Recall@5/10/MRR@10、声明支持率 Faithfulness、类型/难度/交叉桶 N、有效分母、覆盖、误放/误拒、服务/judge 错误。拒答不计 Faithfulness，有评分错误会非零退出；不保证混合重排必然胜出。供应商请求别名与实际响应模型、本机服务版本及预算依据记录在实际运行的 `runtime-verification.json`。

切换前停止旧应用及知识发布/定时挖掘，补偿 pending/deleting 后审计。备份旧代码、`.env` 与 MySQL，保留旧 `knowledge` 集合。`.env` 设置 `MILVUS_COLLECTION=knowledge_ch04`、`RAG_CALIBRATION_PATH=<实际 calibration.json 路径>`、`RAG_CONTEXT_BUDGET=32000`、`KNOWLEDGE_DOCS_ROOT=<与 ingest 相同的目录>`，重启新版后恢复原来的定时任务状态。没有校准或显式预算时知识问答报配置错误。

```powershell
& $pyCh04 -X utf8 -m uvicorn mewhelp.main:app --host 127.0.0.1 --port 8000
# 另开终端（同样设置线程/UTF-8）；验证实际 MySQL 的原有知识和未知问题
& $pyCh04 -X utf8 scripts/smoke_ch04_acceptance.py --base-url http://127.0.0.1:8000 --report-dir $workCh04
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli search --question '邮费是多少' --collection knowledge_ch04
# 主题过滤只命中该主题；商品品类 NULL 不绕过商品过滤
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli search --question '邮费是多少' --category 物流 --collection knowledge_ch04
```

具体型号和文档跳转在**独立验收应用**演示，使用真实 Milvus/模型，SQLite `acceptance.sqlite` 隔离会话/原文/问题池；80 条示例及另一个跳转文档不写线上库，也不改正式对比集合。设置进程环境后启动；同一端口不要同时启动两个服务；本机16GB内存，先停止本任务8000进程再启动8001，验收后恢复8000。

```powershell
$acceptCh04 = 'artifacts/ch04/acceptance_ch04_20260930_03'
$env:RAG_CALIBRATION_PATH = (Resolve-Path "$workCh04/calibration.json").Path
& $pyCh04 -X utf8 scripts/smoke_ch04_acceptance.py --serve --workdir $acceptCh04 --collection ch04_eval_acceptance_ch04_20260930_03 --calibration "$workCh04/calibration.json" --port 8001
# 另开终端
& $pyCh04 -X utf8 scripts/smoke_ch04_acceptance.py --base-url http://127.0.0.1:8001 --ledger-db "$acceptCh04/acceptance.sqlite" --report-dir $workCh04
```

打开 `http://127.0.0.1:8001/`，问“HX-210S 的蓝牙版本是什么？”，点击答案的 [N] 查看原文和路径；文档来源可跳入原章节。问“今天店里新增的外星球旅行险承保条款是什么？”得到拒答。JSON/SSE 验收报告分别保存来源映射、拒答、已提交的问题池字段与原生 BM25 型号命中。每段完成回答左下角 👍/👎 点一次后点亮、“已反馈”并锁定；信号只写浏览器本地，错误/中断回答不出现反馈。真实8001页面由用户手动确认「效果正常」，自动绑定页面被浏览器URL策略拦截，未绕过；协议样例的完整UI检查截图另见 artifacts/ch04/frontend/，不能冒充真实检索截图。

修复后线上与隔离 HTTP 报告为 run03/acceptance-online.json、acceptance-isolated.json，均4/4通过。额外5条预先标注问法见 acceptance-extra-inputs.json / acceptance-extra.json：退款明确不承诺到账，4条缺关键参数问题均在 generation 阶段以 insufficient_evidence 入池。mixed-acceptance.json另确认混合订单/商品知识在JSON/SSE均拒答并以generation/insufficient_evidence入池。真实8001手动UI确认发生于run02；最终修复未改前端，run03复验后端实际数据链路。线上校准已切到run03、knowledge_ch04保留真实20条原知识；原挖掘任务Enabled/Ready。最终评审7项Important均在唯一修复pass完成，证据见 [结论](artifacts/ch04/final-review-resolution.md) 和 [Native台账](artifacts/ch04/native-process.md)。需要复跑时在8001运行期间执行（PYTHONPATH只为根目录的验收脚本导入）：

```powershell
$env:PYTHONPATH = (Resolve-Path '.').Path
& $pyCh04 -X utf8 artifacts/ch04/ch04_20260930_03/verify-acceptance-extra.py
& $pyCh04 -X utf8 artifacts/ch04/ch04_20260930_03/verify-mixed-acceptance.py
```

回退必须成对恢复旧代码和旧集合：停止新版及发布，在原目录使用迁移前保存的可运行代码目录和旧 `.env` 启动旧应用（它指向 `knowledge`）。本次私人基线位于 `C:/Users/27497/projects/ch04-baselines/20260930-native/files`，切换前最新恢复后的配置为同目录 `before-cutover-restored-provider.env`（初始 before-ch04.env 仅留作历史备份），数据库备份为 `mewhelp-before-ch04.sql`，均不进 Git。不要用 `git reset --hard` 覆盖原工作区，也不要删卷；Ch04 增量列可保留。如果切换后有新知识发布，先用旧代码的 `reindex` 同步旧集合，再恢复发布。恢复数据库备份只用于明确需要回退数据的维护窗口，会丢失备份后的写入，不能当默认代码回退步骤。

```powershell
# 只在实际回退时于新终端运行；先停止新版和发布
$oldCh04 = 'C:/Users/27497/projects/ch04-baselines/20260930-native/files'
$pyCh04 = 'C:/Users/27497/projects/mewhelp-wt/ch02-tools/.venv-ch03/Scripts/python.exe'
Copy-Item -LiteralPath 'C:/Users/27497/projects/ch04-baselines/20260930-native/before-cutover-restored-provider.env' -Destination "$oldCh04/.env"
Set-Location -LiteralPath $oldCh04
$env:PYTHONPATH = "$oldCh04/src" # 明确使用旧源码，避开 editable 安装指向新版的问题
$env:MILVUS_COLLECTION = 'knowledge'
# 有切换后新知识时，先同步旧集合；没有新写入可直接启动
& $pyCh04 -X utf8 -m mewhelp.knowledge.cli reindex
& $pyCh04 -X utf8 -m uvicorn mewhelp.main:app --host 127.0.0.1 --port 8000
```

```powershell
& $pyCh04 -X utf8 -m pytest -q
& $pyCh04 -X utf8 -m ruff check src tests scripts/smoke_ch04_acceptance.py scripts/migrate_ch04_schema.py
```

查询是单轮归一，同义词只扩展检索文本；不做指代消解/多轮改写。低置信度、空证据、生成自评不足及引用不合格共用明确拒答并独立提交问题池；基础设施错误返回错误，不伪装成完成的拒答。当前示例评估不能证明真实商品库上的泛化提升，需要以后补充真实匿名问题的人工标注。

## 目录结构

```
src/mewhelp/      业务代码
tests/            测试
docs/             设计文档与决策记录
NOTES.md          开发过程中踩的坑、做的取舍、量到的数据
```

## 分支与 worktree

主仓库停在 `main`,每章新功能从 `main` 开出独立 worktree,详见 [docs/worktree.md](docs/worktree.md)。
