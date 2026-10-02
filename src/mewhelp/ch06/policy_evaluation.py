"""Real isolated policy calibration, separate from formal acceptance labels."""

import argparse
import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.evidence import policy_corpus_hash, policy_dataset_hash, policy_input_hash
from mewhelp.ch05.state import WorkflowContext
from mewhelp.db.base import Base
from mewhelp.knowledge.embedding import embed_texts
from mewhelp.knowledge.evaluation.calibration import calibrate_threshold
from mewhelp.knowledge.evaluation.dataset import EvalCase
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.ingest import ingest_documents
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.reranking import rerank_chunks, reranker_metadata
from mewhelp.knowledge.retrieval import RetrievalRuntime, retrieve_multi_evidence
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk
from mewhelp.knowledge.sync import sync_pending
from mewhelp.knowledge.vectors import MilvusSettings

from .config import PolicyCalibration
from .evaluation import _read_cases, _write_json, verify_dataset
from .expansion import expand_queries
from .orders import load_demo_order


def isolated_policy_runtime(workdir: Path, collection: str):
    if not re.fullmatch(r"ch06_eval_[a-z][a-z0-9_]{0,63}", collection):
        raise ValueError("use an explicitly separate ch06_eval_ collection")
    workdir.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + (workdir / "business.sqlite").resolve().as_posix())

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    index = MilvusSettings().connect_hybrid(collection=collection)
    if collection == MilvusSettings().milvus_collection:
        raise ValueError("isolated calibration cannot write the online collection")
    index.ensure_collection()
    # Use exactly the approved source; never create split copies of knowledge.
    root = Path(__file__).resolve().parents[3]
    with factory() as session:
        ingest_documents(session, root / "knowledge-docs")
        session.commit()
    sync_pending(factory, embed_texts, index)
    with factory() as session:
        chunks = [snapshot_chunk(row) for row in session.scalars(select(KnowledgeChunk))
                  if row.vectorize_status == "done"]
    issues = index.audit(chunks)
    if issues:
        raise RuntimeError("isolated policy index audit failed")
    _write_json(workdir / "manifest.json", {
        "scope": "isolated_acceptance_sqlite", "collection": collection,
        "database": "business.sqlite", "index_audit_errors": 0,
        "corpus_hash": policy_corpus_hash(factory), "chunks": len(chunks),
    })
    return RetrievalRuntime(factory, embed_texts, index, rerank_chunks)


async def calibrate_policy(dataset, outdir, runtime):
    verify_dataset(dataset)
    outdir.mkdir(parents=True, exist_ok=False)
    records, cases, scores = [], [], {}
    corpus_hash, input_hash = policy_corpus_hash(runtime.session_factory), policy_input_hash()
    filters = SearchFilters(category="退款售后政策", content_type="policy")
    context = WorkflowContext(runtime.session_factory, lambda: None, lambda: None,
                              Ch05Settings().limits())
    order = load_demo_order("policy-calibration", "1001")
    for row in _read_cases(dataset / "policy-calibration.jsonl"):
        record = {"case": row}
        try:
            relevant = row["expected"]["relevant"]
            expanded = await expand_queries(row["question"], order, context=context,
                                            state={"scope": "order_specific" if relevant else "general",
                                                   "intent": "退款退货"})
            record["expansion"] = expanded.evaluation_result()
            texts = list(dict.fromkeys([row["question"], *expanded.queries]))
            queries = [QueryUnderstanding(t, t, t, "knowledge", []) for t in texts]
            question = row["question"] + "\n订单事实：" + json.dumps(
                order.model_dump(mode="json"), ensure_ascii=False,
            )
            result = await asyncio.to_thread(retrieve_multi_evidence, runtime, queries,
                                             filters, rerank_question=question)
            top = max((r.score for r in result.final), default=None)
            scores[row["id"]] = top
            cases.append(EvalCase(row["id"], row["question"], "policy", "boundary",
                                  "calibration", filters, set(), "", [], not relevant,
                                  "frozen ch06 policy-calibration annotation"))
            record.update({"queries": texts,
                      "ranked": [{"id": str(r.chunk.id), "score": r.score,
                                  "section": r.chunk.section_path} for r in result.final],
                      "top_score": top})
        except Exception as exc:  # noqa: BLE001 - retain service failure and invalidate run
            record["error"] = type(exc).__name__ + ": " + str(exc)[:500]
        records.append(record)
        with (outdir / "results.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    if len(scores) != 16:
        _write_json(outdir / "summary.json", {"complete": False, "service_errors": 16 - len(scores)})
        return 1
    if corpus_hash != policy_corpus_hash(runtime.session_factory) or input_hash != policy_input_hash():
        raise ValueError("calibration inputs changed while running")
    result = calibrate_threshold(cases, scores, reranker_metadata(),
                                 corpus_hash=corpus_hash, query_hash=input_hash)
    calibration = PolicyCalibration(
        policy_rerank_threshold=result.threshold, reranker_metadata=result.model_metadata,
        corpus_hash=corpus_hash, retrieval_input_hash=input_hash,
        dataset_hash=policy_dataset_hash(), sample_count=16,
    )
    _write_json(outdir / "policy.json", calibration.model_dump())
    _write_json(outdir / "summary.json", {"complete": True, "service_errors": 0,
                                         "calibration": asdict(result), "sample_count": 16})
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("eval/ch06"))
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    runtime = isolated_policy_runtime(args.workdir, args.collection)
    return asyncio.run(calibrate_policy(args.dataset, args.outdir, runtime))


if __name__ == "__main__":
    raise SystemExit(main())
