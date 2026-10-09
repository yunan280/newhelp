# Ch09 演示命令

在本章worktree运行，使用独立`.venv-ch09`、`.cache/ch09/checkpoints.sqlite3`、正式`config/ch09-confidence.json`。私有Langfuse凭据保存在忽略的`.env.ch09.langfuse`，不要提交或打印。数据库/向量服务需先启动。

```powershell
powershell -NoProfile -File scripts/start_ch09_langfuse.ps1
./.venv-ch09/Scripts/python.exe -X utf8 scripts/migrate_ch09_schema.py --report artifacts/ch09/schema.json
# 两个独立终端，继续使用官方SDK Streamable HTTP业务Server
./.venv-ch09/Scripts/python.exe -X utf8 -m mewhelp.ch08.mcp_servers.logistics --host 127.0.0.1 --port 9021
./.venv-ch09/Scripts/python.exe -X utf8 -m mewhelp.ch08.mcp_servers.aftersales --host 127.0.0.1 --port 9022
# 第三个终端；先检查真实DB、schema、Milvus、Langfuse和校准hash
powershell -NoProfile -File scripts/run_ch09.ps1 -Port 9030
powershell -NoProfile -File scripts/register_ch09_evaluation.ps1 -At 04:00
```

聊天`http://127.0.0.1:9030/`，审核页`http://127.0.0.1:9030/review`，统计页`http://127.0.0.1:9030/ch09/stats`，本地Langfuse`http://127.0.0.1:3039/`。真实功能验收结果将在后续阶段追加，本段只给命令，不声称页面已验收。

```powershell
powershell -NoProfile -File scripts/run_ch09_evaluation.ps1 -RunId ch09_20261008_r01
powershell -NoProfile -File scripts/run_ch09_evaluation.ps1 -RunId ch09_20261008_r02
# 仅故障/中止的同轮按原配置补齐未完成题
# powershell -NoProfile -File scripts/run_ch09_evaluation.ps1 -RunId <原run_id> -Resume
Invoke-RestMethod 'http://127.0.0.1:9030/api/ch09/eval-trends'
Invoke-RestMethod 'http://127.0.0.1:9030/api/ch09/token-costs?from=2026-10-08T00:00:00Z&to=2026-10-09T00:00:00Z'
```

统计窗口UTC `[from,to)`，只读取chat根的最终intent及实际provider generation计数；后台评估/飞轮不混入。缺input/output任一项记unknown，覆盖率按generation计算，未知时每请求均值为null；没有模型调用的已结束请求为0。只统计token，无真实价格不估金额。历史观测未保存provider原始用量凭证时标未知，不用Langfuse推算补齐。

趋势保留`candidate_recall50/candidate_mrr50/final_recall5/final_recall10/final_mrr10/faithfulness`和各`*_N`；相邻轮配置/数据hash及分母相同才给delta，NA保持null。Scheduler每周日04:00 Asia/Shanghai，仅当前用户已登录时运行，需要Docker和9030服务可用；失败非零并保留日志/任务状态，不声称已日历触发。Ch03原任务保持不变。
