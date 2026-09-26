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

## 目录结构

```
src/mewhelp/      业务代码
tests/            测试
docs/             设计文档与决策记录
NOTES.md          开发过程中踩的坑、做的取舍、量到的数据
```

## 分支与 worktree

主仓库停在 `main`,每章新功能从 `main` 开出独立 worktree,详见 [docs/worktree.md](docs/worktree.md)。
