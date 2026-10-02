### Task 3：同轮指代消解与口语归一

**Files:** Create `ch06/prompts.py`、`ch06/understanding.py`；Modify `ch05/state.py`；Test `tests/ch06/test_understanding.py`。

**Interfaces:** `understand_query(question: str, history: list, trusted_entities: list[dict], *, context: WorkflowContext, state: dict) -> UnderstandingResult`；`UnderstandingResult` 含 original、question、scope (`general/order_specific`)、trusted_order_id 与 model patch。严格输出字段为 question/scope/reference_order_id/reference_message_id；引用 ID 只能来自本轮显式订单或给定可信实体，并可映射到已有历史消息。

- [ ] 后处理 RED 覆盖：`test_rewrite_preserves_numbers_negations_and_conditions`、`test_two_orders_cannot_invent_referent`、`test_new_session_does_not_import_old_entities`；保护项可复用 knowledge/query.py 的纯函数，不重复调用 Ch04 归一模型。

```python
result = await understand_query(original, [], [], context=context, state=state)
assert result.question == original  # 完整问法夹具：含型号、1001、未拆封和7天
ambiguous = await understand_query("它能退吗", [], [], context=context, state=state)
assert ambiguous.trusted_order_id is None
```
- [ ] 运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_understanding.py -q`，确认上述行为失败。
- [ ] 实现严格校验、同 session 历史和可靠实体检查；模型认定已完整时直接返回原字符，不能 trim 或润色。无可靠指代保留歧义并清空 order_id，核心诉求以后进入选择器。历史输出文本不能单独成为可信订单事实。
- [ ] **Prompt 验证替代 TDD：** 编写含完整问题透传、唯一指代、双候选、换意图的理解 Prompt；跑 `evaluation run --dataset eval/ch06 --parts understanding --mode primary --outdir artifacts/ch06/<新ID>/understanding`，人工阅读所有改写，失败保留并在新运行记录修正。
- [ ] 控制定向测试 GREEN，追记评估结果与 Prompt hash，提交 `feat(ch06): resolve references and normalize queries conservatively`。

