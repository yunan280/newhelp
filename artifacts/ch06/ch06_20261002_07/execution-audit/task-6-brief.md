### Task 6：扩写与强制政策多 Query 检索

**Files:** Create `ch06/expansion.py`；Modify `ch06/prompts.py`、`knowledge/retrieval.py`、`ch05/evidence.py`；Test `tests/ch06/test_expansion.py`、`test_policy_retrieval.py`、`tests/test_ch04_retrieval.py`。

**Interfaces:** `expand_queries(question: str, order: OrderDTO, *, context, state) -> ExpansionResult`；`retrieve_multi_evidence(runtime: RetrievalRuntime, queries: list[QueryUnderstanding], filters: SearchFilters, *, rerank_question: str, candidate_limit: int = 50) -> RetrievalResult`；`retrieve_policy(question: str, order: OrderDTO, queries: list[str], *, rag, filters: SearchFilters, calibration: PolicyCalibration) -> EvidenceEnvelope`，envelope.threshold 来自匹配的政策校准，普通 FAQ 仍用原 Ch04 阈值。

- [ ] 控制 RED：FAQ 零扩写、非法输出回退原查询但仍检索政策；所有查询强制 `content_type=policy/category=退款售后政策`；配送条款不进入本单资格。pending/hash 不匹配/删除块剔除，重复 ID 只保留一次，重排调用只有一次。

```python
assert len({item.chunk.id for item in result.final}) == len(result.final)
assert all(item.chunk.content_type == "policy" and item.chunk.category == "退款售后政策"
           for item in result.final)
assert rerank_call_count == 1
```
- [ ] 运行 `& $pyCh06 -X utf8 -m pytest tests/ch06/test_expansion.py tests/ch06/test_policy_retrieval.py tests/test_ch04_retrieval.py -q`。
- [ ] 实现原查询 + 2～4 扩写查询的独立既有 hybrid 检索，按 ID 聚合各列表排名的 RRF(k=60)，回查 MySQL 权威事实后取最多 50 候选统一重排，最终最多 10 条重编号。复用单 Query 回查校验，不复制失效规则；用户过滤与强制域冲突直接报参数错误。
- [ ] **Prompt 验证替代 TDD：** 冻结条件不新增，扩写覆盖资格/期限/例外/流程；实际跑 `--parts expansion` 的 16 条。使用独立 16 条 policy-calibration 和真实检索分数复用 `knowledge/evaluation/calibration.py::calibrate_threshold`，候选来自观察分数并保留拒绝全部候选；按 false_allow、false_refuse、较高阈值排序，并保存 model/corpus/query hash。正式测试不得调阈值，相关性不代替资格/证据完整性。
- [ ] GREEN 后追记合并/去重/原始 JSON 和弱证据路径，提交 `feat(ch06): expand retrieval queries and enforce policy evidence`。

