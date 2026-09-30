# Ch04 冻结评估集 v1

这里的商品型号与规则全部为**评估示例业务**，不得导入在线知识库或对外作为真实商品事实。80 条完整原文包含 16 个型号的 64 个参数块及 16 个业务条款块；相近型号、否定、数值条件、空品类、跨品类干扰均在原文中保留。

60 条问题分为 model_exact / colloquial / synonym / multi_constraint / unanswerable，每类 12 条，每难度 4 条。每类第 01、05、09、10 条固定为 calibration（易/中/难 1/1/2），其余为 test（3/3/2）。20 条校准集只用于混合加重排的在线阈值选择；40 条 test 用于四策略报告。冻结摘要见 freeze.json，不按检索结果补写相关 ID。

corpus.jsonl 的稳定主键由 `source_id("ch04-eval:<原文键>")` 得到；所有 BIGINT 在文件中使用十进制字符串。queries.jsonl 包含原话、分类、难度、划分、可信过滤、原文 GT、参考答案、逐字关键事实、完整问题是否应拒答及标注依据。annotation-audit.md 是作者在检索前逐例对照完整原文的核查清单，没有假称第二标注员审核。无答案问题可有相关但不充分的资料；这种资料不是可答 GT。已知的“不支持”仍是可答事实。

验证（PowerShell，在 ch02-tools 目录）：

```powershell
& ./.venv-ch03/Scripts/python.exe -X utf8 -m mewhelp.knowledge.evaluation.dataset --validate eval/ch04
& ./.venv-ch03/Scripts/python.exe -X utf8 -m pytest tests/test_ch04_metrics.py tests/test_ch04_dataset.py -q
```

Recall@K 为实际命中 GT 数 / GT 数；MRR@K 为第一个 GT 的倒数名次，重复结果仅计一次。无 GT 的检索指标为 NA，有 GT 未召回为 0。Faithfulness 为支持的事实声明数 / 实际事实声明数；拒答、无事实声明为 NA，评估故障单独计数，绝不填 1。真实检索、模型生成及事实判定由后续 runner 在隔离数据库/集合中执行；本阶段的结构检查与单测不代表检索效果。

真实依赖就绪后，三条命令使用完全相同的参数（不同数据或 Prompt 使用新的 run-id）：

```powershell
$env:RAG_CONTEXT_BUDGET = '32000' # 保守 UTF-8 上界预算；上线前核实当前供应商模型容量
& ./.venv-ch03/Scripts/python.exe -X utf8 -m mewhelp.knowledge.evaluation prepare --dataset eval/ch04 --workdir artifacts/ch04/ch04_20260930_01 --run-id ch04_20260930_01
& ./.venv-ch03/Scripts/python.exe -X utf8 -m mewhelp.knowledge.evaluation calibrate --dataset eval/ch04 --workdir artifacts/ch04/ch04_20260930_01 --run-id ch04_20260930_01
& ./.venv-ch03/Scripts/python.exe -X utf8 -m mewhelp.knowledge.evaluation compare --dataset eval/ch04 --workdir artifacts/ch04/ch04_20260930_01 --run-id ch04_20260930_01
```

prepare 在 workdir 创建 source.sqlite3，只写 `ch04_eval_<run-id>` 集合，拒绝当前生产集合名；用真实 BGE-M3 编码、native BM25 后审计并一次缓存全部60题的归一结果。calibrate 仅用20条 calibration 的真实 sigmoid 分数：先最少无答案误放、再最多可答放行、并列更高阈值，包含 nextafter(max,+inf) 全拒候选；不掩盖全拒。compare 只跑40条 test ×4策略，禁用在线相关性阈值与在线问题池。前3策略没有隐藏 reranker；同一问题向量只缓存重复计算，实际各路检索仍独立执行。

输出 cases.jsonl / summary.json / report.md、校准文件和冻结运行清单。整体、类型、难度、交叉桶均显示 N、检索有效分母、回答/评分覆盖率、误拒/误放/错误；逐题含实际完整 sources、声明、支持理由、耗时与错误。评分异常不会算拒答或满分，compare 有执行/评分错误时 CLI 非零退出。模型 revision、依赖版本、TopK/RRF、Prompt/dataset/query-cache hash 均留存；准备后原文/索引/Prompt/模型漂移拒绝复用成功标记。独立 judge Prompt 使用项目同一供应商 temperature=0；LLM 判定可能误判，不能替代人工审核。真实标注 Prompt 验证输入/结果在 judge-samples.jsonl / judge-results.jsonl。
