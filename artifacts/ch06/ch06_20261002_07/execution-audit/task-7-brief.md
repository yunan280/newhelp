### Task 7：持久化暂停、恢复、取消与新动作 API

**Files:** Create `ch06/selection.py`、`ch06/api.py`；Modify `ch05/{state,workflow,service,schemas}.py`、`main.py`；Test `tests/ch06/test_selection.py`、`test_resume_api.py`、`test_resume_persistence.py`。

**Interfaces:** `prepare_selection(state: dict) -> dict` 把 selection_id/候选写入 checkpoint；`offer_order_selection(state, context, emit) -> dict` 仅 interrupt 和读取恢复值，不在 interrupt 前写库/调用模型/发卡片。`stream_order_resume(runtime, request: OrderResumeRequest)`、`resume_order(runtime, request) -> TurnResult`；私有 `cancel_pending_locked(runtime, config) -> None` 只在调用者已持 session 锁时使用，不二次锁死。

- [ ] RED 测试 `test_resume_restarts_only_wait_node`、`test_stale_card_and_wrong_owner_rejected`、`test_new_message_cancels_pending_selection`、`test_resume_after_181_seconds_keeps_usage`、`test_file_reopen_resumes_same_thread`。运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_selection.py tests/ch06/test_resume_api.py tests/ch06/test_resume_persistence.py -q`。

```python
assert waiting.status == "waiting_for_order"
assert resumed.calls["classifier"] == waiting.calls["classifier"]
assert resumed.usage.total >= waiting.usage.total
assert resumed.order.order_id == selected_order_id
```
- [ ] 分离 ensure/prepare 与 wait 节点；传输层在 astream 返回后依据 snapshot.tasks 的 interrupts 和匹配的活跃 selection_id 发 `order_selection`、`waiting_for_order`。不能把有 pending 数据但执行出错误判为正常等待。
- [ ] 新增 `POST /ch06/orders/selection`（JSON）、`POST /ch06/orders/selection/stream`（SSE）、`GET /ch06/sessions/{session_id}/pending?user_id=...`（只读，不创建未知会话）；归属与订单候选校验后用相同 thread_id 的 Command(resume=...)。
- [ ] 原 chat 新消息与点选串行；新消息先通过明确 cancel 恢复结清旧等待，再开始新轮。重复同选择返回已有状态/结果，不重复恢复；不同参数/旧选择 409、归属错误 403。失败恢复仍可同参数重试，等待连接不持锁。
- [ ] GREEN 后记录关开 saver 的实证和 JSON/SSE 语义，提交 `feat(ch06): resume order selection from persistent workflow state`。

