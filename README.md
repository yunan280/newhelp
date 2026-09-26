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

# 2. 依赖
pip install -e .

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
中文变成 GBK 字节,服务端按 UTF-8 解析,直接返回:

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

## 目录结构

```
src/mewhelp/      业务代码
tests/            测试
docs/             设计文档与决策记录
NOTES.md          开发过程中踩的坑、做的取舍、量到的数据
```

## 分支与 worktree

主仓库停在 `main`,每章新功能从 `main` 开出独立 worktree,详见 [docs/worktree.md](docs/worktree.md)。
