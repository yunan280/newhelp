"""Real four-strategy comparison, physically isolated from online SQL and Milvus."""

import asyncio
import hashlib
import importlib.metadata
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mewhelp.config import get_settings
from mewhelp.db.base import Base
from mewhelp.knowledge.answering import (
    QuestionContext,
    RagRuntime,
    answer_question,
    generate_assessment,
)
from mewhelp.knowledge.embedding import embed_texts
from mewhelp.knowledge.prompts import ANSWER_SYSTEM, QUERY_SYSTEM
from mewhelp.knowledge.query import QueryUnderstanding, understand_query
from mewhelp.knowledge.reranking import rerank_chunks, reranker_metadata
from mewhelp.knowledge.retrieval import RetrievalRuntime, retrieve_evidence
from mewhelp.knowledge.store import (
    KnowledgeChunk,
    KnowledgeDraft,
    put_chunk,
    snapshot_chunk,
    source_id,
)
from mewhelp.knowledge.sync import reindex_all
from mewhelp.knowledge.vectors import MilvusSettings

from .calibration import calibrate_threshold
from .dataset import EvalCase
from .judge import JUDGE_SYSTEM, judge_answer
from .metrics import recall_at_k, reciprocal_rank_at_k

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
METRICS = (
    "candidate_recall50",
    "candidate_mrr50",
    "final_recall5",
    "final_recall10",
    "final_mrr10",
    "faithfulness",
)


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _identity(corpus, cases):
    encoded_cases = [
        {
            **asdict(case),
            "filters": case.filters.model_dump(),
            "relevant_chunk_ids": sorted(case.relevant_chunk_ids),
        }
        for case in cases
    ]
    return _hash([asdict(draft) for draft in corpus]), _hash(encoded_cases)


def _prompt_hashes():
    return {
        "query": _hash(QUERY_SYSTEM),
        "answer": _hash(ANSWER_SYSTEM),
        "judge": _hash(JUDGE_SYSTEM),
    }


def _validate_run(collection, run_id):
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", run_id)
        or collection != "ch04_eval_" + run_id
        or collection == MilvusSettings().milvus_collection
    ):
        raise ValueError("evaluation requires its own ch04_eval_<run_id> collection")


def _factory(workdir):
    engine = create_engine("sqlite:///" + (workdir / "source.sqlite3").resolve().as_posix())
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _snapshots(factory):
    with factory() as session:
        return [
            snapshot_chunk(row)
            for row in session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.id))
        ]


def _new_index(collection):
    return MilvusSettings().connect_hybrid(collection=collection)


def _model_metadata():
    from mewhelp.knowledge.embedding import _model

    model = _model()
    config = model[0].auto_model.config
    revision = getattr(config, "_commit_hash", None)
    if not revision:
        raise RuntimeError("embedding model revision is unavailable")
    return {
        "embedding": {
            "model_id": "BAAI/bge-m3",
            "revision": revision,
            "dimension": 1024,
            "normalize_embeddings": True,
        },
        "reranker": reranker_metadata(),
    }


def _runtime(factory, index):
    settings = get_settings()
    if settings.rag_context_budget is None:
        raise RuntimeError("RAG_CONTEXT_BUDGET must be explicitly configured for evaluation")
    cache, lock = {}, Lock()

    def cached_embed(texts):
        key = tuple(texts)
        with lock:
            if key not in cache:
                cache[key] = embed_texts(texts)
            return cache[key]

    retrieval = RetrievalRuntime(factory, cached_embed, index, rerank_chunks)
    return RagRuntime(retrieval, generate_assessment, factory, 0.0, settings.rag_context_budget)


async def prepare_run(
    corpus: list[KnowledgeDraft],
    cases: list[EvalCase],
    *,
    workdir: Path,
    collection: str,
    run_id: str,
    dataset_hashes: dict | None = None,
) -> None:
    _validate_run(collection, run_id)
    workdir.mkdir(parents=True, exist_ok=True)
    cp, qp = _identity(corpus, cases)
    manifest_path = workdir / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            old.get("run_id"),
            old.get("collection"),
            old.get("input_corpus_hash"),
            old.get("input_query_hash"),
        ) != (run_id, collection, cp, qp):
            raise ValueError("run directory belongs to a different frozen dataset/run")
        if old.get("prompt_hashes") != _prompt_hashes():
            raise ValueError(
                "prompt changed; create a new frozen run instead of reusing query cache"
            )
    manifest = {
        "run_id": run_id,
        "collection": collection,
        "frozen": True,
        "ready": False,
        "input_corpus_hash": cp,
        "input_query_hash": qp,
        "corpus_hash": (dataset_hashes or {}).get("corpus_hash", cp),
        "query_hash": (dataset_hashes or {}).get("query_hash", qp),
        "query_count": len(cases),
        "corpus_count": len(corpus),
        "prompt_hashes": _prompt_hashes(),
    }
    _write_json(manifest_path, manifest)
    factory, index = _factory(workdir), _new_index(collection)
    index.ensure_collection()
    with factory() as session:
        for draft in corpus:
            put_chunk(session, draft)
        session.commit()
    await asyncio.to_thread(reindex_all, factory, embed_texts, index)
    issues = await asyncio.to_thread(index.audit, _snapshots(factory))
    if issues:
        _write_json(workdir / "audit-errors.json", issues)
        raise RuntimeError("isolated index audit failed")
    cache_path = workdir / "query-cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    semaphore = asyncio.Semaphore(4)

    async def normalize(case):
        if case.id in cache:
            if cache[case.id].get("original") != case.question:
                raise ValueError("query cache differs from frozen original")
            return
        async with semaphore:
            cache[case.id] = asdict(await understand_query(case.question))
            _write_json(cache_path, cache)

    await asyncio.gather(*(normalize(case) for case in cases))
    if set(cache) != {case.id for case in cases}:
        raise ValueError("query cache does not match frozen cases")
    manifest.update(
        ready=True,
        index_audit_errors=0,
        query_cache_hash=_hash(cache),
        model_metadata=await asyncio.to_thread(_model_metadata),
    )
    _write_json(manifest_path, manifest)
    print(f"prepared: {len(corpus)} chunks, {len(cases)} cached queries, {collection}", flush=True)


def _open_run(corpus, cases, workdir, collection, run_id):
    _validate_run(collection, run_id)
    try:
        manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
        cache = json.loads((workdir / "query-cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("prepare a frozen run before calibrate/compare") from exc
    cp, qp = _identity(corpus, cases)
    if manifest.get("prompt_hashes") != _prompt_hashes():
        raise ValueError("prompt changed after prepare")
    if (
        not manifest.get("ready")
        or not manifest.get("frozen")
        or manifest.get("run_id") != run_id
        or manifest.get("collection") != collection
        or (manifest.get("input_corpus_hash"), manifest.get("input_query_hash")) != (cp, qp)
        or manifest.get("query_cache_hash") != _hash(cache)
        or not (workdir / "source.sqlite3").is_file()
        or set(cache) != {case.id for case in cases}
    ):
        raise ValueError("run manifest/cache does not match prepared frozen input")
    queries = {case.id: QueryUnderstanding(**cache[case.id]) for case in cases}
    if any(queries[case.id].original != case.question for case in cases):
        raise ValueError("cached query changed original question")
    factory, index = _factory(workdir), _new_index(collection)
    index.ensure_collection()
    expected = {
        source_id(draft.source_key): snapshot_chunk(
            KnowledgeChunk(
                id=source_id(draft.source_key),
                category=draft.category,
                questions=draft.questions,
                answer=draft.answer,
                section_path=draft.section_path,
                content_type=draft.content_type,
                product_category=draft.product_category,
                is_key_clause=draft.is_key_clause,
            )
        ).content_hash
        for draft in corpus
    }
    actual = _snapshots(factory)
    if {item.id: item.content_hash for item in actual} != expected or index.audit(actual):
        raise ValueError("prepared original/index changed after freeze")
    if manifest.get("model_metadata") != _model_metadata():
        raise ValueError("actual model revision changed after prepare")
    return manifest, factory, index, queries


async def calibrate_run(corpus, cases, *, workdir, collection, run_id):
    manifest, factory, index, queries = await asyncio.to_thread(
        _open_run, corpus, cases, workdir, collection, run_id
    )
    runtime, scores, records = _runtime(factory, index), {}, []
    calibration = [case for case in cases if case.split == "calibration"]
    for case in calibration:
        evidence = await asyncio.to_thread(
            retrieve_evidence, runtime.retrieval, queries[case.id], case.filters
        )
        score = evidence.final[0].score if evidence.final else None
        scores[case.id] = score
        records.append(
            {
                "id": case.id,
                "should_refuse": case.should_refuse,
                "top_score": score,
                "candidate_ids": [str(item.id) for item in evidence.candidates],
                "final": [
                    {"id": str(item.chunk.id), "score": item.score} for item in evidence.final
                ],
            }
        )
        print(f"calibration {case.id}: top_score={score}", flush=True)
    result = calibrate_threshold(
        calibration,
        scores,
        manifest["model_metadata"]["reranker"],
        corpus_hash=manifest["corpus_hash"],
        query_hash=manifest["query_hash"],
    )
    _write_json(workdir / "calibration-scores.json", records)
    _write_json(workdir / "calibration.json", asdict(result))
    print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
    return result


def summarize(rows):
    n = len(rows)
    answer_count = sum(row.get("refused") is False and not row.get("error") for row in rows)
    scored = sum(row.get("faithfulness") is not None and not row.get("judge_error") for row in rows)
    result = {
        "N": n,
        "answers": answer_count,
        "scored": scored,
        "answer_coverage": answer_count / n if n else None,
        "scored_coverage": scored / n if n else None,
        "errors": sum(bool(row.get("error")) for row in rows),
        "judge_errors": sum(bool(row.get("judge_error")) for row in rows),
        "correct_refusals": sum(
            row.get("should_refuse") and row.get("refused") is True for row in rows
        ),
        "false_refusals": sum(
            not row.get("should_refuse") and row.get("refused") is True for row in rows
        ),
        "false_allows": sum(
            row.get("should_refuse") and row.get("refused") is False for row in rows
        ),
    }
    for metric in METRICS:
        values = [row[metric] for row in rows if row.get(metric) is not None]
        result[metric] = sum(values) / len(values) if values else None
        result[metric + "_N"] = len(values)
    return result


def _buckets(rows, names):
    groups = defaultdict(list)
    for row in rows:
        groups["/".join(row[name] for name in names)].append(row)
    return {key: summarize(value) for key, value in sorted(groups.items())}


async def run_comparison(
    corpus: list[KnowledgeDraft],
    cases: list[EvalCase],
    *,
    workdir: Path,
    collection: str,
    run_id: str,
) -> Path:
    started = time.perf_counter()
    manifest, factory, index, queries = await asyncio.to_thread(
        _open_run, corpus, cases, workdir, collection, run_id
    )
    runtime = _runtime(factory, index)
    manifest["comparison_status"] = "running"
    _write_json(workdir / "manifest.json", manifest)

    async def evaluate(case, strategy):
        row = {
            "id": case.id,
            "query_type": case.query_type,
            "difficulty": case.difficulty,
            "split": case.split,
            "strategy": strategy,
            "question": case.question,
            "query": asdict(queries[case.id]),
            "filters": case.filters.model_dump(exclude_none=True),
            "should_refuse": case.should_refuse,
            "reference_answer": case.reference_answer,
            "key_facts": case.key_facts,
            "relevant_chunk_ids": [str(item) for item in sorted(case.relevant_chunk_ids)],
            "error": None,
            "judge_error": None,
            "refused": None,
            "faithfulness": None,
        }
        began = time.perf_counter()
        try:
            result = await answer_question(
                runtime,
                queries[case.id],
                filters=case.filters,
                context=QuestionContext(case.question, None, "cli"),
                strategy=strategy,
                apply_relevance_gate=False,
                record_pool=False,
            )
            candidates = [item.id for item in result.retrieval.candidates]
            final = [item.chunk.id for item in result.retrieval.final]
            row.update(
                answer=result.answer,
                refused=result.refused,
                candidate_ids=[str(item) for item in candidates],
                final=[
                    {"id": str(item.chunk.id), "score": item.score}
                    for item in result.retrieval.final
                ],
                sources=[source.model_dump() for source in result.sources],
                candidate_recall50=recall_at_k(candidates, case.relevant_chunk_ids, 50),
                candidate_mrr50=reciprocal_rank_at_k(candidates, case.relevant_chunk_ids, 50),
                final_recall5=recall_at_k(final, case.relevant_chunk_ids, 5),
                final_recall10=recall_at_k(final, case.relevant_chunk_ids, 10),
                final_mrr10=reciprocal_rank_at_k(final, case.relevant_chunk_ids, 10),
            )
            if not result.refused:
                judgement = await judge_answer(case.question, result.answer, result.sources)
                row.update(
                    claims=[claim.model_dump() for claim in judgement.claims],
                    faithfulness=judgement.score,
                    judge_error=judgement.error,
                )
        except Exception as exc:  # noqa: BLE001 — 逐题保存实际错误而非冒充拒答
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["elapsed_seconds"] = time.perf_counter() - began
        return row

    rows = []
    with (workdir / "cases.jsonl").open("w", encoding="utf-8") as target:
        for case in (case for case in cases if case.split == "test"):
            batch = await asyncio.gather(*(evaluate(case, strategy) for strategy in STRATEGIES))
            for row in batch:
                rows.append(row)
                target.write(json.dumps(row, ensure_ascii=False) + "\n")
            target.flush()
            print(
                f"compared {case.id}: "
                + ", ".join(
                    f"{row['strategy']}={'ERROR' if row['error'] else 'refused' if row['refused'] else 'answer'}"
                    for row in batch
                ),
                flush=True,
            )
    strategy_reports = {}
    for strategy in STRATEGIES:
        selected = [row for row in rows if row["strategy"] == strategy]
        strategy_reports[strategy] = {
            "overall": summarize(selected),
            "query_type": _buckets(selected, ["query_type"]),
            "difficulty": _buckets(selected, ["difficulty"]),
            "cross": _buckets(selected, ["query_type", "difficulty"]),
        }
    calibration_path = workdir / "calibration.json"
    calibration = (
        json.loads(calibration_path.read_text(encoding="utf-8"))
        if calibration_path.exists()
        else None
    )
    settings = get_settings()
    report = {
        "run": manifest,
        "strategies": strategy_reports,
        "elapsed_seconds": time.perf_counter() - started,
        "configuration": {
            "candidate_top_k_each": 50,
            "fused_top_k": 50,
            "final_top_k": 10,
            "rrf_k": 60,
            "analyzer": "chinese",
            "bm25": "Milvus native Function",
            "production_relevance_gate": False,
            "pool_writes": False,
            "context_budget": settings.rag_context_budget,
            "generation_model": settings.llm_model,
            "temperature": 0,
            "method": "function_calling",
            "judge_model": settings.llm_model,
            "prompt_hashes": {
                "query": _hash(QUERY_SYSTEM),
                "answer": _hash(ANSWER_SYSTEM),
                "judge": _hash(JUDGE_SYSTEM),
            },
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in (
                    "pymilvus",
                    "sentence-transformers",
                    "transformers",
                    "torch",
                    "SQLAlchemy",
                    "langchain-core",
                    "langchain-openai",
                    "pydantic",
                )
            },
        },
        "calibration": calibration,
        "errors": [
            {
                "id": row["id"],
                "strategy": row["strategy"],
                "error": row["error"],
                "judge_error": row["judge_error"],
            }
            for row in rows
            if row["error"] or row["judge_error"]
        ],
    }
    manifest["comparison_status"] = "completed_with_errors" if report["errors"] else "complete"
    _write_json(workdir / "manifest.json", manifest)
    report["run"] = manifest
    _write_json(workdir / "summary.json", report)
    lines = [
        "# Ch04 四策略真实对比",
        "",
        f"Run: `{run_id}`；示例业务隔离集合 `{collection}`。",
        "",
        "只比较 test 集；四策略共用冻结原文、查询缓存、可信过滤和生成 Prompt。消融均不启用生产相关性阈值，不向在线问题池写入。拒答的 Faithfulness 为 NA，错误单独统计。",
        "",
        "|策略/桶|N|Recall@50|MRR@50|Recall@5|Recall@10|MRR@10|Faithfulness|回答覆盖|有效评分覆盖|正确拒答/误拒/误放|错误/评分错误|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]

    def display(value):
        return "NA" if value is None else f"{value:.4f}"

    for strategy, groups in strategy_reports.items():
        for group in ("overall", "query_type", "difficulty", "cross"):
            buckets = {"all": groups[group]} if group == "overall" else groups[group]
            for label, metrics in buckets.items():
                values = [display(metrics[key]) for key in METRICS]
                lines.append(
                    f"|{strategy}/{label}|{metrics['N']}|"
                    + "|".join(values)
                    + f"|{display(metrics['answer_coverage'])}|{display(metrics['scored_coverage'])}|{metrics['correct_refusals']}/{metrics['false_refusals']}/{metrics['false_allows']}|{metrics['errors']}/{metrics['judge_errors']}|"
                )
    lines += [
        "",
        f"运行状态：{manifest['comparison_status']}；耗时 {report['elapsed_seconds']:.2f}s。",
        "",
        "每项指标的有效分母见 summary.json 的 *_N；实际问题、原文、声明与理由、耗时和失败见 cases.jsonl。LLM judge 可能误判，不能把此分数当人工审核结论。",
        "",
        "## 校准与实际配置",
        "",
        "```json",
        json.dumps(
            {
                "calibration": calibration,
                "configuration": report["configuration"],
                "frozen_run": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 失败与误拒/误放样例",
        "",
    ]
    for row in rows:
        if (
            row["error"]
            or row["judge_error"]
            or (row["refused"] is not None and row["refused"] != row["should_refuse"])
        ):
            lines.append(
                f"- {row['id']} / {row['strategy']}：{row.get('answer') or row['error']}；评分错误={row['judge_error']}。"
            )
    path = workdir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
