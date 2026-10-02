### Task 4：八类意图 Prompt 与可选升级

**Files:** Modify `ch05/intent.py`、`ch05/prompts.py`、`ch05/evaluation.py`（适配正式分类调用，保留旧标签/报告）；Modify `ch06/prompts.py/config.py/evaluation.py`；Test `tests/ch06/test_intent.py`；Update 被取代契约的 `tests/ch05/test_intent_contract.py`、`conftest.py`。

**Interfaces:** `classify_intent(text: str, *, context: WorkflowContext, state: dict) -> ClassificationResult` 返回 intent/confidence/origin、调用 patch 和 raw；`route_intent(intent: Intent, scope: QueryScope = 'general') -> Route` 的新增出口为 `aftersales/other`。八类 schema 与 Task 1 共用，分类函数不产出 scope/槽位/资格。

- [ ] 控制 RED：默认仅主模型；级联低于阈值才一次升级，主模型仍不确定归其他；失效 JSON 不执行业务，服务失败保留错误；`route_intent('退款退货','general') == 'knowledge'`、`('售后','order_specific') == 'aftersales'`、`('其他',...) == 'other'`。

```python
assert route_intent("退款退货", "general") == "knowledge"
assert route_intent("退款退货", "order_specific") == "aftersales"
assert route_intent("其他", "general") == "other"
```
- [ ] 运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_intent.py tests/ch05/test_intent_contract.py -q`；实现固定路由、LLM 请求和有界纠正，移除正式分类的本地问候捷径。
- [ ] **Prompt 验证替代 TDD：** 写八类选择题、只有两字段的 JSON、边界 few-shot、其他兜底；将本轮当前诉求置于历史意图之上。主模型/成本模式使用同一分类 Prompt，不训练新模型。
- [ ] 用独立 calibration 样例：主模型阈值网格 `(0.5,0.6,0.7,0.8)`、升级阈值网格 `(0.7,0.8,0.9)`。先最小化核心退款/售后漏判，再最小化总错误，仍相同则选更少升级的配置；报告全部候选误判与升级率。未生成校准前仅校准执行器可请求原始 intent/confidence，正式 runtime 缺失/失效 calibration 返回配置错误，不能静默用默认小模型。生成 calibration.json，再运行正式 64 条 `--parts intents` 的 primary/cascade 两套报告。低成本模型仅显式配置才启用；保留升级分母，不因低成本模式错误换模型。
- [ ] 后端定向测试 GREEN，追记原始/最终解析率与分类结果并提交 `feat(ch06): classify eight intents with confidence and bounded escalation`。

