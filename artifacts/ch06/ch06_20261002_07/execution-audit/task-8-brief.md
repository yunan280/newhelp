### Task 8：固定 Workflow、主力判断与账本幂等

**Files:** Create `ch06/assessment.py`；Modify `ch05/{workflow,service,agent,evidence,schemas,state}.py`、`db/{models,repository}.py`；Create `scripts/migrate_ch06_schema.py`、`sql/ch06-ddl.sql`；Test `tests/ch06/test_workflow.py`、`test_assessment.py`、`test_ledger.py`、`test_migration.py`；Update 被取代的 `tests/ch05/{conftest,test_workflow,test_persistence,test_api}.py` 与 `tests/test_db_ddl_drift.py`。

**Interfaces:** `assess_order(state: dict, context: WorkflowContext) -> dict` 使用已加载 OrderDTO 与过闸证据；OrderAssessment 为 verdict (`eligible/ineligible/needs_clarification`)、说明和缺事实列表，不能包含退款原因追问。`append_messages_once(session, *, conversation_id: int, rows: list[TurnMessage]) -> None` 使用 nullable `messages.ch06_event_key VARCHAR(64)` 及唯一索引，旧消息 NULL 不改；key 是 turn_id+阶段+位置的稳定 SHA256。

TurnMessage 增加可选 `ch06_event_key: str | None = None`；新 wait/complete/cancel 阶段写账本均由这一函数去重，原消息 user key 跨阶段保持一致。等待提示作为 assistant 消息持久化，选择动作不伪造 user 原话；候选和活跃状态仍以 checkpoint 为准。

- [ ] RED：本单顺序严格为理解/分类/拿订单/扩写/政策/gate/assessment；缺订单、弱证据、未知拆封等不得自动资格通过。一般 FAQ 不出现 ensure/expand。物流→退款→物流的新轮不能继承订单政策；旧普通 Agent 的依赖工具调用仍正常。
- [ ] 写账本/迁移 RED：初始 waiting 与恢复最终各只写所需消息一次、提交后 checkpoint 故障可重发、旧表记录不改、迁移两次结果相同、同名不兼容列/索引报错。运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_workflow.py tests/ch06/test_assessment.py tests/ch06/test_ledger.py tests/ch06/test_migration.py tests/ch05 -q`。

```python
assert [trace.index(node) for node in ("load_order", "expand_queries", "retrieve_policy", "assess_order")] == sorted(
    trace.index(node) for node in ("load_order", "expand_queries", "retrieve_policy", "assess_order"))
assert len([row for row in messages if row.role.value == "user" and row.content == original]) == 1
```
- [ ] 实现固定边和本轮状态重置；可信实体只来自实际订单/工具观察与点选，保留在本 session 历史映射。核心判断不绑定业务工具；普通路径沿用既有只读 Agent。最终正文仍独立流式，来源先于答案首字。
- [ ] 编写增量迁移，只添加账本幂等键，本任务先在隔离 SQLite/DDL 编译测试；MySQL 迁移留 Task 11 备份后执行。更新 DDL 漂移测试组合 Ch02/05/06 增量定义，不重写历史 DDL 来掩盖变化。
- [ ] **Prompt 验证替代 TDD：** 用 12 条冻结资格数据验证符合/不符合/缺事实和引用，不把相关性当资格；按模型事实澄清、不追问退款原因、不保证到账。修改 Prompt 后重跑受影响的正式部分。
- [ ] GREEN 后追记旧契约被新需求替代的具体项、图轨迹和账本行数，提交 `feat(ch06): enforce order policy workflow and idempotent turn ledger`。

