### Task 11：真实联调、完整评估与可运行交付命令

**Files:** Create `scripts/smoke_ch06_acceptance.py`；Modify `README.md`、`.env.example`、`eval/ch06/README.md`；Test `tests/ch06/test_acceptance.py`；报告保存全新 `artifacts/ch06/<run-id>/`，不覆盖历史失败。

**Interfaces:** acceptance CLI 接受 `--base-url --report-dir --session-prefix`，另外隔离服务模式 `--serve --workdir --port --collection --calibration`；同一真实图使用 SQLite 隔离业务账本 + 真实 Milvus/模型，仅作验收隔离，线上业务仍 MySQL。HTTP 与浏览器报告标出所用数据库和模型，不能把隔离 SQLite 当真实 MySQL 通过。

- [ ] 验收器代码 TDD：502、缺 waiting 终态、错误节点序列或假申请号导致失败，保存真实错误与有效分母；运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_acceptance.py -q` 并 GREEN。
- [ ] 核对现有 Docker/服务/端口而不停止未知进程；备份真实 MySQL 后执行 `& $pyCh06 -X utf8 scripts/migrate_ch06_schema.py` 两次，保存旧行数与实际 schema。复用现有 ingest/sync/audit 把单份政策发布至明确的验收集合，线上发布只使用经确认的演示政策范围且不清旧库。
- [ ] 运行 `evaluation calibrate --dataset eval/ch06 --outdir artifacts/ch06/<新ID>/calibration`，再 `evaluation run --dataset eval/ch06 --parts all --mode primary --calibration <实际路径> --outdir artifacts/ch06/<新ID>/primary`；显式配置低成本模型后单独跑 cascade。模型可用性或余额失败如实停下询问，不切换选型、删除失败例或伪装完成。
- [ ] 用真实 HTTP 分别跑一般 FAQ、本单有号/无号、点选恢复、多轮切换、双候选、其他兜底、弱政策与退款表单幂等；验证本机 MySQL 真实申请写入/响应失败重试另保存报告。非人工写接口只在脚本显式确认的带测试标识申请中执行，不操作支付或旧业务记录。
- [ ] 运行 `& $pyCh06 -X utf8 -m pytest -q`、`& $pyCh06 -X utf8 -m ruff check src tests scripts/migrate_ch06_schema.py scripts/smoke_ch06_acceptance.py`，只修本任务引入的问题。Ch04 冻结评估集不改；Ch05 旧纯意图标签保留历史报告，新分类使用 Ch06 正式标签，Agent 决策评估仍验证原 Function Calling 能力。
- [ ] 文档给出实际验证过的启动/迁移/评估/HTTP 命令：单 worker，原 `mewhelp.main:app`、空闲端口、实际 router/RAG calibration 路径。追记新鲜结果、人工语义逐例阅读与未验证项，提交 `test(ch06): verify routing resume and refund flows end to end`。

