### Task 5：统一演示订单事实与单份政策原文

**Files:** Create `tools/business_data.py`、`ch06/orders.py`、`knowledge-docs/aftersales-policy.md`；Modify `tools/business.py`；Test `tests/ch06/test_orders.py`，运行 `tests/test_tool_business.py`。

**Interfaces:** 纯函数 `get_demo_order_record(order_id: str) -> DemoOrderRecord` 与原订单/物流工具共用；`list_demo_orders(user_id: str) -> list[OrderDTO]`；`load_demo_order(user_id: str, order_id: str) -> OrderDTO`，目录外 ID 明确不可选，不在退款侧用原任意 ID 生成器冒充所属订单。OrderDTO 包含 owner、商品/品类、金额、状态、下单/签收日期和可未知的拆封状态。

- [ ] RED：`test_card_and_query_order_share_facts`、`test_unknown_order_needs_selection`、`test_demo_owner_is_bound_to_request_identity`，原三读工具不碰数据库/签名不变；运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_orders.py tests/test_tool_business.py -q`。

```python
order = load_demo_order("alice", "1001")
assert order.user_id == "alice"
assert order.product_name == get_demo_order_record("1001").product_name
assert "999999" not in {item.order_id for item in list_demo_orders("alice")}
```
- [ ] 定义固定目录：1001 机械键盘，199.00 元、09-27 下单/09-29 签收、未拆封；1002 降噪耳机，399.00 元、09-10 下单/09-15 签收、拆封状态未知；1003 保温杯，89.00 元、10-01 下单、未发货/未签收、拆封状态未知。年份均 2026，判断传明确 `as_of=2026-10-02`；DemoOrderRecord 字段名与 OrderDTO 相同（不含 user_id），物流状态/最新日期与这些事实共用来源，不用墙钟漂移。非目录旧只读演示仍沿用既有种子规则。
- [ ] 抽出唯一结构化事实来源，再格式化既有工具文本；不建立订单库或外部接口，保持非目录订单的旧只读演示行为。
- [ ] **数据验证替代 TDD：** 写一份演示退款/保修政策，根标题「退款售后政策」、content_type 由既有 policy 文件名规则识别；期限、例外、质量问题、未发货、保修和申请方式分别成章节，关键条款显式标记，原文不因 Query 数量复制。与原 FAQ 共通事实核对一致。
- [ ] GREEN 后追记数据/政策边界与 hash，提交 `feat(ch06): unify demo order facts and aftersales policy source`。此阶段不直接向线上库发布演示语料。

