"""Real HTTP acceptance and isolated example server. No synthetic facts enter online SQL."""

import argparse
import datetime as dt
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation
from mewhelp.knowledge.refusals import REASON_CODES, LowConfidenceQuestion
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk, source_id

UNKNOWN_QUESTION = "今天店里新增的外星球旅行险承保条款是什么？"
MODEL_QUESTION = "HX-210S 的蓝牙版本是什么？"


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _verify_answer(payload, client):
    sources = payload.get("sources")
    _require(
        payload.get("refused") is False and isinstance(sources, list) and sources,
        "known answer needs verified sources",
    )
    numbers = set()
    for source in sources:
        chunk_id = source.get("chunk_id")
        _require(
            isinstance(chunk_id, str) and re.fullmatch(r"[1-9][0-9]*", chunk_id),
            "citation ID must remain a string",
        )
        _require(
            type(source.get("number")) is int
            and source["number"] > 0
            and source["number"] not in numbers,
            "invalid citation number",
        )
        numbers.add(source["number"])
        response = client.get("/api/kb/chunks/" + chunk_id)
        response.raise_for_status()
        current = response.json()
        _require(current.get("chunk_id") == chunk_id, "source ID mismatch")
        for key in (
            "answer",
            "questions",
            "section_path",
            "content_hash",
            "product_category",
            "category",
        ):
            _require(
                current.get(key) == source.get(key),
                "citation snapshot differs from original: " + key,
            )
        _require(
            source.get("source_url") == "/kb/source/" + chunk_id, "invalid original source URL"
        )
    citations = {
        int(number) for number in re.findall(r"\[([1-9][0-9]*)\]", payload.get("answer", ""))
    }
    _require(citations and citations <= numbers, "answer needs mapped citation numbers")


def _verify_refusal(payload, factory, question, entry, started):
    _require(
        payload.get("refused") is True and payload.get("sources") == [],
        "unknown question must explicitly refuse without sources",
    )
    _require(
        re.search(r"无法|不足|不能确认", payload.get("answer", "")), "refusal text is not explicit"
    )
    pool_id = payload.get("low_confidence_question_id")
    _require(isinstance(pool_id, str) and pool_id.isdecimal(), "missing durable refusal ID")
    with factory() as session:
        row = session.get(LowConfidenceQuestion, int(pool_id))
        _require(row is not None, "refusal not committed in the corresponding ledger")
        _require(row.original_question == question, "pool must retain original question")
        _require(
            row.source_conversation_id == int(payload["conversation_id"]),
            "pool conversation mismatch",
        )
        _require(
            row.entry_point == entry and row.trigger_stage in ("retrieval", "generation"),
            "pool trigger mismatch",
        )
        _require(
            row.reason_code in REASON_CODES and row.reason.strip(),
            "pool needs a valid specific reason",
        )
        _require(
            row.created_at is not None
            and started - dt.timedelta(seconds=2)
            <= row.created_at
            # MySQL DATETIME(0) may round fractional seconds into the next second.
            <= dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(seconds=1),
            "pool timestamp missing/stale",
        )
        return {
            "id": pool_id,
            "original_question": row.original_question,
            "conversation_id": str(row.source_conversation_id),
            "entry_point": row.entry_point,
            "stage": row.trigger_stage,
            "reason_code": row.reason_code,
            "reason": row.reason,
            "created_at_utc": row.created_at.isoformat() + "Z",
        }


def _parse_sse(text):
    frames = []
    for block in re.split(r"\r?\n\r?\n", text):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            frames.append((name, json.loads("\n".join(data))))
    return frames


def main(
    base_url: str,
    *,
    report_dir: Path,
    session_factory: Callable[[], Session],
    demo_docs: Path | None = None,
) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    records = []
    scope = "isolated_example" if demo_docs is not None else "online_existing_knowledge"
    report = {
        "scope": scope,
        "base_url": base_url,
        "started_at_utc": started.isoformat() + "Z",
        "records": records,
        "passed": False,
    }
    try:
        with session_factory() as session:
            published = session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.vectorize_status == "done")
                .order_by(KnowledgeChunk.id)
            ).all()
            published = [
                row
                for row in published
                if row.vector_id == str(row.id)
                and not (row.section_path or "").startswith("__deleting__::")
            ]
            _require(published, "no existing published knowledge for online acceptance")
            known_question = MODEL_QUESTION if demo_docs is not None else published[0].questions
        filters = {"product_category": "耳机"} if demo_docs is not None else {}
        with httpx.Client(base_url=base_url, timeout=300.0) as client:
            session_id = None
            for question, should_refuse in ((known_question, False), (UNKNOWN_QUESTION, True)):
                response = client.post(
                    "/ch02/agent",
                    json={
                        "message": question,
                        "user_id": "ch04-smoke",
                        "session_id": session_id,
                        "filters": filters if not should_refuse else {},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                session_id = payload["session_id"]
                if should_refuse:
                    pool = _verify_refusal(payload, session_factory, question, "agent", started)
                else:
                    _verify_answer(payload, client)
                    pool = None
                    if demo_docs is not None:
                        _require(
                            "5.3" in payload["answer"], "nearby model parameters were confused"
                        )
                        _require(
                            all(
                                source.get("product_category") == "耳机"
                                for source in payload["sources"]
                            ),
                            "trusted category filter was bypassed",
                        )
                        _require(
                            str(source_id("ch04-eval:HX-210S-1"))
                            in {source["chunk_id"] for source in payload["sources"]},
                            "exact model source missing",
                        )
                records.append(
                    {"entry_point": "agent", "question": question, "payload": payload, "pool": pool}
                )
            for question, should_refuse in ((known_question, False), (UNKNOWN_QUESTION, True)):
                response = client.post(
                    "/ch02/chat/stream",
                    json={
                        "message": question,
                        "user_id": "ch04-smoke",
                        "session_id": session_id,
                        "filters": filters if not should_refuse else {},
                    },
                )
                response.raise_for_status()
                frames = _parse_sse(response.text)
                names = [name for name, _ in frames]
                _require(
                    "error" not in names and names[0] == "session" and names[-1] == "done",
                    "SSE did not complete successfully",
                )
                _require(
                    names.count("sources") == 1
                    and "token" in names
                    and names.index("sources") < names.index("token"),
                    "verified sources must precede knowledge text",
                )
                source_payload = next(data for name, data in frames if name == "sources")
                _require(
                    set(source_payload) == {"sources", "refused", "low_confidence_question_id"},
                    "SSE sources schema changed",
                )
                sid = frames[0][1]["session_id"]
                with session_factory() as session:
                    conversation_id = session.scalar(
                        select(Conversation.id).where(Conversation.session_id == sid)
                    )
                _require(conversation_id is not None, "SSE source conversation not persisted")
                payload = {
                    **source_payload,
                    "session_id": sid,
                    "conversation_id": conversation_id,
                    "answer": "".join(data["text"] for name, data in frames if name == "token"),
                }
                if should_refuse:
                    pool = _verify_refusal(
                        payload, session_factory, question, "chat_stream", started
                    )
                else:
                    _verify_answer(payload, client)
                    pool = None
                records.append(
                    {
                        "entry_point": "chat_stream",
                        "question": question,
                        "frames": frames,
                        "payload": payload,
                        "pool": pool,
                    }
                )
            if demo_docs is not None:
                from mewhelp.knowledge.filters import SearchFilters
                from mewhelp.knowledge.vectors import MilvusSettings

                workdir = demo_docs.parent
                manifest = json.loads(
                    (workdir / "acceptance-manifest.json").read_text(encoding="utf-8")
                )
                index = MilvusSettings().connect_hybrid(collection=manifest["collection"])
                hits = index.search(
                    "bm25",
                    vector=None,
                    bm25_query=MODEL_QUESTION,
                    filters=SearchFilters(product_category="耳机"),
                )
                _require(
                    source_id("ch04-eval:HX-210S-1") in {hit.id for hit in hits},
                    "native BM25 did not hit the exact model",
                )
                report["bm25_hit_ids"] = [str(hit.id) for hit in hits]
                with session_factory() as session:
                    document = session.scalar(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.content_type == "policy",
                            KnowledgeChunk.vectorize_status == "done",
                        )
                    )
                    _require(document is not None, "isolated document source missing")
                    document_id = str(document.id)
                doc_response = client.get(f"/api/kb/chunks/{document_id}/document")
                doc_response.raise_for_status()
                _require("退款边界" in doc_response.json()["markdown"], "original document changed")
                page = client.get("/kb/source/" + document_id)
                page.raise_for_status()
                _require("loadSource" in page.text, "source viewer missing")
                report["document"] = {"chunk_id": document_id, **doc_response.json()}
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 — smoke 必须保存实际失败并非零退出
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["completed_at_utc"] = dt.datetime.now(dt.UTC).isoformat()
    filename = "acceptance-isolated.json" if demo_docs is not None else "acceptance-online.json"
    (report_dir / filename).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "scope": scope,
                "report": str(report_dir / filename),
                "error": report.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


def sqlite_factory(path: Path):
    engine = create_engine("sqlite:///" + path.resolve().as_posix())

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        previous = connection.isolation_level
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        connection.isolation_level = previous

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def serve_acceptance(*, workdir: Path, collection: str, port: int = 8001) -> None:
    import uvicorn

    from mewhelp.ch02 import api, service
    from mewhelp.knowledge.answering import get_rag_runtime
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime
    from mewhelp.knowledge.embedding import embed_texts
    from mewhelp.knowledge.evaluation.dataset import load_dataset
    from mewhelp.knowledge.ingest import ingest_documents
    from mewhelp.knowledge.store import put_chunk
    from mewhelp.knowledge.sync import reindex_all, sync_pending
    from mewhelp.knowledge.vectors import MilvusSettings
    from mewhelp.main import app

    if (
        not re.fullmatch(r"ch04_eval_acceptance_[a-z][a-z0-9_]{0,63}", collection)
        or collection == MilvusSettings().milvus_collection
    ):
        raise ValueError("acceptance must use a separate real example collection")
    workdir.mkdir(parents=True, exist_ok=True)
    docs = workdir / "demo-docs"
    docs.mkdir(exist_ok=True)
    (docs / "policy-acceptance.md").write_text(
        "# 验收示例文档\n\n这份文档只用于隔离验收。\n\n## 退款边界\n\n退款到账取决于支付渠道；不承诺具体到账时间，不保证审批通过。\n",
        encoding="utf-8",
    )
    factory = sqlite_factory(workdir / "acceptance.sqlite")
    dataset_root = Path(__file__).resolve().parents[1] / "eval/ch04"
    corpus, _cases = load_dataset(dataset_root / "corpus.jsonl", dataset_root / "queries.jsonl")
    index = MilvusSettings().connect_hybrid(collection=collection)
    index.ensure_collection()
    with factory() as session:
        for draft in corpus:
            put_chunk(session, draft)
        ingest_documents(session, docs)
        session.commit()
    reindex_all(factory, embed_texts, index)
    with factory() as session:
        issues = index.audit(
            [snapshot_chunk(row) for row in session.scalars(select(KnowledgeChunk))]
        )
    if issues:
        raise RuntimeError("isolated acceptance index audit failed")
    runtime = get_rag_runtime(
        factory, calibration_path=workdir / "calibration.json", collection=collection
    )
    original_factory = service.get_rag_runtime

    def bound_runtime(request_factory, **kwargs):
        if request_factory is not factory:
            raise RuntimeError("acceptance cannot access online session factory")
        return runtime

    def publish(row_id):
        sync_pending(factory, embed_texts, index, row_ids=[row_id])

    app.dependency_overrides[api.get_session_factory] = lambda: factory
    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(factory, publish, docs)
    service.get_rag_runtime = bound_runtime
    (workdir / "acceptance-manifest.json").write_text(
        json.dumps(
            {
                "collection": collection,
                "database": "acceptance.sqlite",
                "corpus_count": len(corpus),
                "demo_docs": "demo-docs",
                "index_audit_errors": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"isolated acceptance ready: {collection}, audit=0, port={port}", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port)
    finally:
        service.get_rag_runtime = original_factory
        app.dependency_overrides.clear()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report-dir", type=Path, default=Path("artifacts/ch04"))
    parser.add_argument("--ledger-db", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--collection")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.serve:
        if args.workdir is None or args.collection is None or args.calibration is None:
            parser.error("--serve requires --workdir --collection --calibration")
        args.workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.calibration, args.workdir / "calibration.json")
        serve_acceptance(workdir=args.workdir, collection=args.collection, port=args.port)
        return 0
    if args.ledger_db is None:
        from mewhelp.db.engine import SessionFactory

        factory, docs = SessionFactory, None
    else:
        _require(args.ledger_db.is_file(), "ledger DB must already exist")
        factory, docs = sqlite_factory(args.ledger_db), args.ledger_db.parent / "demo-docs"
    return main(args.base_url, report_dir=args.report_dir, session_factory=factory, demo_docs=docs)


if __name__ == "__main__":
    raise SystemExit(cli())
