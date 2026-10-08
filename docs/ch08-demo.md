# Ch08 工具系统演示与验证

截至2026-10-08：后端实现、一次独立后端审查及六项修复已验证，追加历史消息配对问题也已修复；前端已实现并通过 DOM 验收。用户选择手动页面验收，尚未反馈实际结果。今天Docker启动被Inference manager的`dockerInference`路径访问错误阻止，MySQL/客服尚未恢复，Task 9/10 的页面验收与整体 finish 未完成。HTTP/数据库历史结果不能替代当前现场结果。

## 启动

工作目录：`C:\Users\27497\projects\mewhelp-wt\ch02-tools`。使用已有 `.venv-ch03`、本地 `.env` 和已运行的 MySQL/Milvus；不复制密钥进命令。固定 MCP SDK `1.30.0`、`langchain-mcp-adapters 0.3.2`、`jsonschema 4.26.0`，完整锁定文件为 `requirements-ch08.lock.txt`。

演示地址：客服 <http://127.0.0.1:9020>；物流 Streamable HTTP `http://127.0.0.1:9021/mcp`；售后 `http://127.0.0.1:9022/mcp`。10月8日已恢复两个独立MCP进程，客服需先恢复Docker中的MySQL，当前不能声称9020可访问。占用端口时先核实已有进程并复用，不能盲目再起一份。

首次使用现有章节数据库时执行追加迁移；该脚本核对审计表结构，不删除旧表：

```powershell
Set-Location C:\Users\27497\projects\mewhelp-wt\ch02-tools
.venv-ch03\Scripts\python.exe -X utf8 scripts\migrate_ch08_schema.py
```

需要重新启动时在三个 PowerShell 终端分别执行：

```powershell
.\scripts\run_ch08_mcp.ps1 -Server logistics -Port 9021
.\scripts\run_ch08_mcp.ps1 -Server aftersales -Port 9022
.\scripts\run_ch08.ps1 -Port 9020
```

客服脚本使用 `router-calibration-05/router.json`、已实测的 Ch07 context profile 和 Ch06 policy calibration，checkpoint 为 `.cache/ch08/checkpoints.sqlite3`。默认模型窗口 32768；相关产物位于本工作区的 `artifacts`，没有提交原始模型输出。

如果在另一台机器没有校准产物，先准备该机器的 Ch06/Ch07 配置，再用冻结的 Ch08 独立校准集生成本章路由产物，启动时显式指定路径：

```powershell
.venv-ch03\Scripts\python.exe -X utf8 -m mewhelp.ch08.evaluation calibrate-router --dataset eval/ch08 --outdir artifacts/ch08/my-router
.\scripts\run_ch08.ps1 -RouterCalibration artifacts/ch08/my-router/router.json -ContextCalibration <context-profile.json的仓库相对路径> -PolicyCalibration <policy.json的仓库相对路径>
```

模型/Prompt 或校准来源改变时按 hash 校验重新校准；新增工具只作为每轮目录输入，不要求改核心代码。

## 对话演示

页面选择“工作流 Agent”，新会话中依次测试：

| 输入或动作 | 预期结果 |
| --- | --- |
| 查询订单1001的物流轨迹 | 通过物流 MCP `query_logistics`，展示 mock 轨迹 |
| 查询订单1001的在保情况 | 通过售后 MCP `query_warranty` |
| 查询退货R1001的进度 | 通过售后 MCP `query_return_progress` |
| 帮我建个工单 | 追问问题描述/类型，无工单写入 |
| 键盘坏了，请帮我建售后工单 | 显示类型“售后”、原话问题描述和两个按钮 |
| 点击“确认提交” | tickets 增1，回执和回复带同一工单号 |
| 另一个预览点击“取消” | tickets 增0，审计状态“权限拒绝” |
| 完成或取消后仅说“鼠标也坏了” | 旧建单意愿不继续授权，新工单需新的明确请求 |
| 未提交的预览刷新页面/切换会话再回来 | 恢复对应会话的预览，不自动提交 |
| 确认成功但响应丢失后刷新 | 只读查询回执，不自动重发写操作 |

原投诉建议的“建工单”按钮继续走原确认界面；点击确认本身作为真实授权，后端统一执行与审计。各 MCP 返回的是演示数据，不连接真实业务系统。

## 只注册一个新内置工具

在 `tool_plugins/store_hours.py` 新建下面的可信本地模块，无需修改主力 Agent 或重启客服。下一轮对话刷新插件后可问“查询演示门店的营业时间”。这些 Python 插件只能由本地维护者安装，不能来自模型或远程 Server。

```python
from langchain_core.tools import tool
from mewhelp.tools.contracts import ToolSpec

@tool
def query_store_hours(store_name: str) -> dict:
    """查询指定演示门店的营业时间，仅返回 mock。"""
    return {"store_name": store_name, "hours": "09:00至18:00"}

def register(registry):
    registry.register(ToolSpec(query_store_hours, permission="readonly"))
```

新增 MCP 工具时，在对应 Server 的 `build_server()` 内通过官方 `server.tool()` 注册，只重启该 Server。随后在 `config/ch08-tools.json` 的对应 Server 下添加本地授权，例如 `permissions.logistics.query_delivery_window = {"mode":"readonly"}`。客服下一轮重新发现工具并热加载授权。未知工具默认拒绝；Server 的描述和 annotations 不授予权限。本章所有外部写工具均拒绝。

## 验证命令与实际证据

```powershell
.venv-ch03\Scripts\python.exe -X utf8 -m pytest -q
.venv-ch03\Scripts\python.exe -X utf8 -m ruff check src tests scripts smoke_ch08_acceptance.py
node tests/ch08/page-ticket-preview.js src/mewhelp/static/index.html
node tests/page-smoke.js src/mewhelp/static/index.html
node tests/ch07/page-conversations.js src/mewhelp/static/index.html
```

2026-10-08 最终后端回归：**966 passed, 5 deselected**（5个历史真实模型评估按默认配置排除），日志`process-evidence/full-suite-06.log`；Ruff通过。预览 DOM 脚本8类场景通过，另外两份脚本通过。这些结果不声称真实浏览器点击已通过。

真实模型评估 **26/26**，结果 `artifacts/ch08/20261007-01/eval-04/{summary.json,results.jsonl}`；最新16条独立路由校准为`router-calibration-05`，投影修复导致来源hash改变，已实际重做该校准。根 freeze 覆盖54条标签/引用，不等于已执行54条模型验收。已通过样例沿用真实记录，只重跑受修复影响或未完成样例。

需要新执行模型评估时（会产生模型费用），用新输出目录；启动客服时的 `-RouterCalibration` 才决定其路由校准：

```powershell
.venv-ch03\Scripts\python.exe -X utf8 -m mewhelp.ch08.evaluation run --dataset eval/ch08 --base-url http://127.0.0.1:9020 --outdir artifacts/ch08/new-eval
```

六项真实 **HTTP/数据库** 验收：`artifacts/ch08/20261007-01/acceptance-http-02/{summary.json,evidence.json,responses.json}`。工具热注册、新 MCP 工具热发现保持客服 PID/代码不变、确认新增1、取消新增0且“权限拒绝”、读超时 retry2、写超时 retry0均通过。其中原三项 MCP 业务查询复用未受改动影响的 `acceptance-http-01` 真实记录，报告明确标注来源。HTTP确认实际工单号 **T20261007001**。写超时故障探针用可信确认权限执行隔离的异步延迟 handler，未新建真实工单；真实建单写入另由确认场景验证。

完整验收 runner 会安装临时演示插件、暂改权限并恢复、重启明确指定的标准物流进程，不能把未知 PID 传入。需要重跑时先检查9021监听进程确实是 `mewhelp.ch08.mcp_servers.logistics`，再显式给其 PID：

```powershell
.venv-ch03\Scripts\python.exe -X utf8 smoke_ch08_acceptance.py --base-url http://127.0.0.1:9020 --outdir artifacts/ch08/new-http --expected-logistics-pid <已核对的标准物流PID>
```

仅查数据库及 DDL（不重跑模型或重启进程）：

```powershell
.venv-ch03\Scripts\python.exe -X utf8 smoke_ch08_acceptance.py --outdir artifacts/ch08/new-db-probe --probe-only
```

原样DDL为 `sql/ch08-ddl.sql`，真实 `SELECT 1`/`SHOW CREATE` 证据在 `environment/database.json` 及 HTTP evidence。核对某次页面操作可在 utf8mb4 客户端执行：

```sql
SET NAMES utf8mb4;
SELECT ticket_no, conversation_id, ticket_type, description, request_id
FROM tickets ORDER BY created_at DESC LIMIT 10;
SELECT conversation_id, tool_call_id, tool_name, tool_source, mcp_server,
       status, error_message, retry_count, duration_ms, created_at
FROM tool_audit_logs ORDER BY id DESC LIMIT 20;
```

一次独立后端审查发现6项Important，全部通过真实失败复现和一次修复验证，详见 `docs/superpowers/reviews/2026-10-07-ch08-backend.md`。随后现场补查发现下一轮的工具申请/结果被预览提示隔开，新增3项RED、14项GREEN并修复模型投影；完整checkpoint和页面流水保留。受影响HTTP补查尚未完成：昨天首次补查遇到上游400，今天Docker故障阻止继续；不能称为修复后的真实HTTP通过。

最终报告为`docs/ch08-final-report.md`，过程四项记录在 `dev-notes/ch08.md`。报告交付与整章finish分开记录。真实浏览器确认/取消、刷新/重启恢复、旧投诉按钮页面回归仍待用户手动结果；原始日志与checkpoint保留，没有恢复出厂设置或删除Docker数据。
