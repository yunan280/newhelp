"""Read-only real-model retrieval verification while provider billing is unavailable."""

import datetime as dt
import json
from pathlib import Path

from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.evaluation.metrics import recall_at_k, reciprocal_rank_at_k
from mewhelp.knowledge.evaluation.runner import _buckets, _open_run, _runtime, summarize
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import retrieve_evidence
from mewhelp.knowledge.store import source_id

workdir = Path("artifacts/ch04/ch04_20260930_01")
corpus, cases = load_dataset(Path("eval/ch04/corpus.jsonl"), Path("eval/ch04/queries.jsonl"))
manifest, factory, index, queries = _open_run(
    corpus, cases, workdir, "ch04_eval_ch04_20260930_01", "ch04_20260930_01"
)
runtime = _runtime(factory, index)
strategies = ("dense", "bm25", "hybrid", "hybrid_rerank")
rows = []
for case in cases:
    if case.split != "test":
        continue
    for strategy in strategies:
        result = retrieve_evidence(runtime.retrieval, queries[case.id], case.filters, strategy=strategy)
        candidates = [item.id for item in result.candidates]
        final = [item.chunk.id for item in result.final]
        rows.append({
            "id": case.id, "strategy": strategy, "query_type": case.query_type,
            "difficulty": case.difficulty, "should_refuse": case.should_refuse,
            "refused": None, "error": None, "judge_error": None, "faithfulness": None,
            "candidate_ids": [str(item) for item in candidates],
            "final": [{"id": str(item.chunk.id), "score": item.score} for item in result.final],
            "candidate_recall50": recall_at_k(candidates, case.relevant_chunk_ids, 50),
            "candidate_mrr50": reciprocal_rank_at_k(candidates, case.relevant_chunk_ids, 50),
            "final_recall5": recall_at_k(final, case.relevant_chunk_ids, 5),
            "final_recall10": recall_at_k(final, case.relevant_chunk_ids, 10),
            "final_mrr10": reciprocal_rank_at_k(final, case.relevant_chunk_ids, 10),
        })
    print("retrieved", case.id, flush=True)

hits = index.search("bm25", vector=None, bm25_query="HX-210S 的蓝牙版本是什么？",
                    filters=SearchFilters(product_category="耳机"))
exact_id = source_id("ch04-eval:HX-210S-1")
assert exact_id in {hit.id for hit in hits}, "native BM25 exact-model miss"
report = {
    "scope": "retrieval_only", "run_id": manifest["run_id"],
    "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    "provider_calls": 0, "generation_and_judge": "not run in this verification",
    "row_count": len(rows), "ground_truth_cases_per_strategy": 32,
    "query_cache_hash": manifest["query_cache_hash"], "model_metadata": manifest["model_metadata"],
    "exact_model_bm25": {"question": "HX-210S 的蓝牙版本是什么？", "product_category": "耳机",
                         "expected_id": str(exact_id), "hit_ids": [str(hit.id) for hit in hits]},
    "strategies": {}, "rows": rows,
}
for strategy in strategies:
    selected = [row for row in rows if row["strategy"] == strategy]
    report["strategies"][strategy] = {
        "overall": summarize(selected), "query_type": _buckets(selected, ["query_type"]),
        "difficulty": _buckets(selected, ["difficulty"]),
        "cross": _buckets(selected, ["query_type", "difficulty"]),
    }
(workdir / "retrieval-only.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines = ["# Ch04 独立检索验收", "", "真实 Milvus/BGE；复用正式冻结语料及归一缓存。160 次检索，32 条有 GT 问题/策略；未知问题 GT 为空，Recall/MRR 为 NA。此次不调用生成或 judge，不表示整体生成验收完成。", "",
         "|策略|Recall@50|MRR@50|Recall@5|Recall@10|MRR@10|有效 GT N|", "|---|---:|---:|---:|---:|---:|---:|"]
for strategy in strategies:
    m = report["strategies"][strategy]["overall"]
    values = [m[k] for k in ("candidate_recall50","candidate_mrr50","final_recall5","final_recall10","final_mrr10")]
    lines.append(f"|{strategy}|"+"|".join(f"{v:.4f}" for v in values)+f"|{m['candidate_recall50_N']}|")
lines += ["", f"原生 BM25 + 耳机过滤命中 HX-210S 蓝牙 GT：`{exact_id}`。", "", "类型/难度/交叉桶与逐题候选、最终 ID/重排分数见 retrieval-only.json。正式生成/judge 的余额不足错误保留在 report.md 和 cases.jsonl。"]
(workdir / "retrieval-only.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print("retrieval-only: 160 real searches, native BM25 model assertion passed",flush=True)
