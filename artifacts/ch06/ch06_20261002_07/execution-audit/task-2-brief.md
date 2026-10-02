### Task 2：冻结标注与可审计评估器

**Files:** Create `eval/ch06/{README.md,intents.jsonl,understanding.jsonl,multiturn.jsonl,expansion.jsonl,assessment.jsonl,calibration.jsonl,policy-calibration.jsonl,freeze.json}`；Create `ch06/evaluation.py`；Modify `ch06/config.py` 添加 RouterCalibration/PolicyCalibration DTO；Test `tests/ch06/test_evaluation.py`。

**Interfaces:** `freeze_dataset(dataset: Path) -> dict`；`evaluate(dataset: Path, outdir: Path, *, parts: tuple[str, ...], mode: Literal['primary','cascade'], calibration_path: Path | None, evaluators: dict | None = None) -> int`，最后一个参数仅作测试依赖注入。CLI 为 `freeze`、`calibrate`、`run`，全新输出目录；hash 覆盖标签、该部分实际 Prompt/输入构造/schema/请求模型/阈值配置。`RouterCalibration` 保存理解/分类 Prompt 与模型 hash、`intent_min_confidence/cascade_upgrade_threshold`；另设政策校准输出，含 policy_rerank_threshold、reranker metadata、政策语料 hash 和检索输入 hash，普通 Ch04 校准不变。

- [ ] **替代纯数据 TDD：** 在任何业务 Prompt 修改前冻结正式标签：八类各 8 条共 64 条分类、32 条理解、8 个 session 各 4 轮、16 条扩写/跳过、12 条资格判断；另 32 条意图校准及独立 `policy-calibration.jsonl` 的 16 条相关/无关政策问题，仅用于阈值和开发，不进入正式结果分母。Prompt few-shot 不复制正式验收样例。
- [ ] 标注完整问法的 `exact_question`，需消解的 `required_fragments/forbidden_facts`，当前 scope、可信 referent、intent、预期固定路径。多候选必须无自动订单；全部标注说明为作者核对的演示事实，不冒充真实客服数据。
- [ ] 对评估器控制代码写 RED：服务错误、缺例、标签/hash 不一致、假 passed flag 均非零退出；原始与纠正后的 JSON 解析率分开。运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_evaluation.py -q`。

```python
assert await evaluate(dataset, outdir, parts=("intents",), mode="primary", calibration_path=None,
                      evaluators={"intents": raise_provider_error}) == 1
assert json.loads((outdir / "summary.json").read_text())["service_errors"] == 1
```
- [ ] 实现评估器，逐例保存 raw、解析/修复、请求与响应模型、用量、耗时、失败和 scope；对理解/扩写的语义保真另要求逐例人工阅读。GREEN 后运行 `& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation freeze --dataset eval/ch06`。
- [ ] 追记冻结 hash/分母/限制并提交 `test(ch06): freeze routing evaluation labels and runner`。此任务不声称新 Prompt 已通过。

