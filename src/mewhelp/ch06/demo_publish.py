"""Add the approved policy without replacing old SQL knowledge or its Milvus collection."""

import argparse
import json
import re
from pathlib import Path

from sqlalchemy import select

from mewhelp.knowledge.ingest import ingest_documents
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk
from mewhelp.knowledge.sync import sync_pending


def _row_values(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def prepare_demo_corpus(factory, index, docs, embed):
    if not re.fullmatch(r"ch06_eval_mysql_[a-z0-9_]+", index.collection):
        raise ValueError("use an explicitly separate ch06_eval_mysql_ collection")
    if not (docs / "aftersales-policy.md").is_file():
        raise ValueError("approved aftersales-policy.md is required")
    with factory() as db:
        before = {row.id: _row_values(row) for row in db.scalars(select(KnowledgeChunk))}
        ingest_documents(db, docs, include_paths={"aftersales-policy.md"})
        db.flush()
        after = {row.id: _row_values(row) for row in db.scalars(select(KnowledgeChunk))}
        if any(after.get(row_id) != data for row_id, data in before.items()):
            db.rollback()
            raise ValueError("existing knowledge would change; stopped before publishing")
        new_ids = sorted(after.keys() - before.keys())
        db.commit()
    index.ensure_collection()
    sync_pending(factory, embed, index, row_ids=new_ids)
    with factory() as db:
        snapshots = [snapshot_chunk(row) for row in db.scalars(select(KnowledgeChunk))
                     if row.vectorize_status == "done" and row.vector_id == str(row.id)
                     and not (row.section_path or "").startswith("__deleting__::")]
    # Build a separate demo index from the same published SQL originals, never duplicate SQL rows.
    old = [snapshot for snapshot in snapshots if snapshot.id not in new_ids]
    for row, vector in zip(old, embed([row.text for row in old]), strict=True):
        index.upsert(row, vector)
    issues = index.audit(snapshots)
    if issues:
        raise ValueError("demo index audit failed: " + json.dumps(issues))
    return {"scope": "actual_mysql_business_separate_milvus_demo_collection",
            "collection": index.collection, "new_policy_rows": len(new_ids),
            "published_rows": len(snapshots), "index_audit_errors": 0,
            "old_sql_rows_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--docs", type=Path, default=Path("knowledge-docs"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-demo-policy", action="store_true")
    args = parser.parse_args()
    if not args.confirm_demo_policy:
        parser.error("backs up MySQL separately first; add --confirm-demo-policy for the approved demo source")
    from mewhelp.db.engine import SessionLocal
    from mewhelp.knowledge.embedding import embed_texts
    from mewhelp.knowledge.vectors import MilvusSettings
    result = prepare_demo_corpus(SessionLocal, MilvusSettings().connect_hybrid(collection=args.collection),
                                 args.docs, embed_texts)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
