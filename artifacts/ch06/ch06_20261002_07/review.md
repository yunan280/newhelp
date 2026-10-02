# Ch06 独立后端审查

审查范围：74e325b..38f04c970afa35c064191d8595bb4d752e18fd89；一名独立 gpt-6-astra high reviewer，只读源码、安装的 LangGraph 实现和保存证据，没有运行测试、模型、HTTP 或业务服务。前端按用户 Vibe 例外排除。

结论：With fixes；Critical 0，Important 2，Minor 1。固定流程边、订单归属、政策域/SQL 权威回查、SQL-first 退款回执及唯一 offer 键合理。父代理按实际用户影响复核后维持上述分级，只修 Important，一次修复、无二次审查。

1. Important / Medium，selection.py:159：A 选择完成后 B 正在等待，重放 A 的已保存回执仍调用 _remember_result/aupdate_state。新 checkpoint 不携带旧 pending writes 中 B 的 INTERRUPT，导致 B 刷新消失、点选 409。已有回执应只读返回；仅首次完成或补记缺失回执写 checkpoint。需要真实 SQLite saver 的 A 完成→B 等待→A 重放→B 仍可恢复回归。
2. Important / Medium，demo_publish.py:35：SQL commit 后索引/embedding/upsert 失败，重跑时 new_ids=[]，sync_pending(row_ids=[]) 跳过已入库的 pending 政策。done-only snapshots/audit 可能错误报告成功。按批准的根目录和 aftersales-policy.md 稳定选择全部政策 ID，补偿同步并确认全部已发布，排除其他 pending 来源。需要提交后故障→重跑的回归。
3. Minor / Low，ch05/intent.py:140：小模型低置信度已成功，升级的首次预算预检失败；workflow 捕获停止时未保留第一次的 calls/usage，少计用量。业务正确停止，但账本审计不完整。按 Native 规则延期，默认主模型模式不经过该分支。

五项重点：双订单/跨意图未发现新确定性缺陷；并发及旧卡重放发现 1；180 秒/重启/恢复异常控制成立但受发现 1 影响，用量有发现 3；非法扩写/政策过滤/弱证据控制成立，发布有发现 2；MySQL 提交后 checkpoint 故障的 SQL-first 回执与唯一键成立，真实故障报告支持。

分类器校准离线复用依据成立：分类 Prompt、请求、校准算法、冻结分类输入未变，阈值未调，新增模型调用 0。理解环节变化后的总体模型准确率不能由兼容 hash 重绑定认证。历史 776 passed/5 deselected；run-05 primary 156/156、cascade 155/156；run-06 primary 93/156、cascade 121/156（含服务错误）。不改称当前全模型通过。

Declined to judge（父代理逐项在 ledger 裁定）：前端；生产登录/真实订单隔离；真实支付/审批；多 worker/多实例；理解修改后的最新全模型准确率及最新真实联调通过率。理由分别是用户 Vibe 例外、批准的 demo identity/固定目录、仅 pending 申请、单 worker 约束、用户禁止重复运行。
