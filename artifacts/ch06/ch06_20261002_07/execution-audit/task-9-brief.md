### Task 9：退款申请的持久化与幂等回执

**Files:** Create `ch06/refunds.py`；Modify `ch06/api.py`、`ch05/{schemas,workflow,state,service}.py`、`db/models.py`、`sql/ch06-ddl.sql`、`scripts/migrate_ch06_schema.py`；Test `tests/ch06/test_refunds.py`、`test_refund_api.py`、`test_migration.py`。

**Interfaces:** `create_refund_offer(state: dict) -> RefundOffer | None` 仅 stop_reason=completed、未拒答且 eligible 的退款请求；offer_id 由原 turn_id 的固定退款命名空间 SHA256 派生，保存为稳定服务端建议，不能在响应重试时再生成 UUID。`submit_refund(runtime, request: RefundRequest) -> RefundReceipt`；`POST /ch06/refunds` 要求 confirmed 为显式 True、服务端 offer_id、订单号和固定 reason；`GET /ch06/refunds/{offer_id}?session_id=...&user_id=...` 只读取归属匹配回执。

- [ ] RED：未确认/空原因/未知原因/维修请求不写；跨用户或改订单拒绝；同 offer 同参数返回原号，改变原因 409。模拟 commit 成功但返回/存 checkpoint 失败，再调用仍只有一行申请。运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_refunds.py tests/ch06/test_refund_api.py tests/ch06/test_migration.py -q`。

```python
first = await submit_refund(runtime, request)
again = await submit_refund(runtime, request)
assert again.application_no == first.application_no and again.replayed
assert application_count == 1
```
- [ ] 新建 `refund_applications`，字段 id、application_no、offer_id（唯一）、conversation_id/user_id/order_id、reason、order_snapshot、assessment_snapshot、policy_snapshot、status=pending、created_at。先查已提交回执，独立事务提交，唯一冲突 rollback 后核对原参数；应用号不得取自模型。
- [ ] 核验活跃 offer 与本单资格来源；订单/金额/资格由服务端快照决定，模型及客户端不能覆盖。提交动作不绑定退款工具到 Agent。迁移验证精确列/唯一键，旧工单流程继续可用。
- [ ] GREEN 后追记提交前/后计数及故障重试回执，提交 `feat(ch06): persist confirmed refund applications idempotently`。

