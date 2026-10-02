### Task 1：公共契约、模型请求与累计用量

**Files:** Modify `src/mewhelp/ch05/{schemas,state,runtime,config,limits}.py`、`src/mewhelp/llm.py`；Create `src/mewhelp/ch06/{__init__,config,structured}.py`；Test `tests/ch06/test_contracts.py`、`test_model_requests.py`、`test_model_budget.py`。

**Interfaces:** `Ch06Settings` 采用 `CH06_` 前缀：primary_model 默认 pro、cascade_enabled=False、small_model=None、calibration_path 可选路径；understanding/expansion 输出上限 512、assessment 768。`get_chat_model(*, model_name: str | None = None, temperature=None, **kwargs)` 默认仍用原 LLM_MODEL。`invoke_json(context: WorkflowContext, state: dict, *, purpose: str, messages: list, schema: type[BaseModel], model_name: str, output_tokens: int) -> StructuredCall` 返回 parsed/raw/error、累计 usage 与 calls patch；最多一次纠正，服务故障向上抛。

DTO 的决策字段钉为：OrderDTO 的 order_id/user_id/product_name/product_category/status/paid_amount（Decimal）/ordered_at/received_at/condition（unopened/opened/unknown）/as_of；OrderSelection 的 selection_id/turn_id/orders；OrderResumeRequest 的 session_id/user_id/selection_id/order_id。TurnResult 新增 status/resolved_question/intent_confidence/order_selection/order/assessment/refund_offer，旧 ActionOffer 不混入退款字段。StructuredCall 的 parsed/error/raw_responses/usage/calls 是前置模块共同消费的字段；WorkflowContext 新 router_settings/router_model_factory 均 keyword 注入并有兼容默认值。

- [ ] 写契约 RED：`IntentOutput` 拒绝额外字段、布尔/字符串/NaN confidence；ExpansionOutput 拒绝 0/1/5 条、空白元素和额外字段；TurnResult 区分 `completed/waiting_for_order`。保留旧字段默认值和四位置参数构造。

```python
assert IntentOutput(intent="退款退货", confidence=0.9).model_dump() == {"intent": "退款退货", "confidence": 0.9}
with pytest.raises(ValidationError):
    IntentOutput(intent="退款退货", confidence=True)
```
- [ ] 运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_contracts.py tests/ch06/test_model_requests.py tests/ch06/test_model_budget.py -q`，保存预期缺失接口/行为的 RED。
- [ ] 实现上述 DTO/工厂；runtime 增加 keyword-only `router_settings`、`router_model_factory` 注入，测试工厂用 purpose 区分请求，不用 token 上限猜调用用途。StructuredCall 一并计入初始/纠正请求，并预留最终回答预算。
- [ ] 使用真实 ChatOpenAI + 现有 httpx MockTransport 核验实际 payload 的 model、JSON mode、max_tokens、thinking disabled；普通 Agent 工具请求保持原协议。相同定向命令 GREEN，补测试最后剩余预算不足不发第二请求。
- [ ] 追记任务结果并提交 `feat(ch06): add router contracts and bounded model requests`；不写字符串镜像 Prompt 测试。

