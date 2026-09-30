"""离线建库、补偿与定时对话挖掘入口：python -m mewhelp.knowledge.cli。"""

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="知识库建库、回填与检索任务")
    parser.add_argument("command", choices=["init-db", "ingest", "sync", "mine", "search", "status", "reindex", "audit-index", "check-query"])
    parser.add_argument("--docs", type=Path, default=Path("knowledge-docs"))
    parser.add_argument("--question", default="邮费是多少")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--samples", type=Path, default=Path("eval/ch04/query-understanding-samples.jsonl"))
    args = parser.parse_args(argv)

    if args.command == "check-query":
        import asyncio

        from .query import check_query_samples

        raise SystemExit(bool(asyncio.run(check_query_samples(args.samples))))

    from mewhelp.db.engine import SessionFactory

    if args.command == "init-db":
        from sqlalchemy import inspect

        from mewhelp.db.engine import engine

        ddl = (Path(__file__).resolve().parents[3] / "sql" / "ch03-ddl.sql").read_text(encoding="utf-8")
        tables = set(inspect(engine).get_table_names())
        if "knowledge_chunks" in tables:
            columns = {col["name"] for col in inspect(engine).get_columns("knowledge_chunks")}
            if "vectorize_status" not in columns:
                raise RuntimeError("已有 knowledge_chunks 不是 ch03 权威 DDL；请先备份迁移")
        if {"knowledge_chunks", "qa_extraction_staging"} <= tables:
            print("Ch03 MySQL 表已就绪")
            return
        with engine.begin() as connection:
            for statement in ddl.split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
        print("Ch03 MySQL 表已就绪")
    elif args.command == "ingest":
        from .ingest import ingest_documents, ingest_faq

        deleted_ids: list[int] = []
        with SessionFactory() as session:
            faq_count = ingest_faq(session)
            document_count = ingest_documents(session, args.docs, deleted_ids=deleted_ids)
            session.commit()
        if deleted_ids:
            from .sync import cleanup_deleting
            from .vectors import MilvusSettings

            vectors = MilvusSettings().connect_hybrid(collection=args.collection)
            vectors.ensure_collection()
            cleanup_deleting(SessionFactory, vectors)
        print(f"MySQL 待向量化：FAQ {faq_count}，文档块 {document_count}")
    elif args.command in ("sync", "reindex", "audit-index"):
        from .embedding import embed_texts
        from .sync import cleanup_deleting, reindex_all, sync_pending
        from .vectors import MilvusSettings

        vectors = MilvusSettings().connect_hybrid(collection=args.collection)
        vectors.ensure_collection()
        if args.command == "audit-index":
            from sqlalchemy import select

            from .store import KnowledgeChunk, snapshot_chunk

            with SessionFactory() as session:
                snapshots = [snapshot_chunk(row) for row in session.scalars(select(KnowledgeChunk).where(
                    KnowledgeChunk.section_path.is_(None)
                    | ~KnowledgeChunk.section_path.startswith("__deleting__::", autoescape=True),
                ))]
            differences = vectors.audit(snapshots)
            print(json.dumps(differences, ensure_ascii=False))
            if differences:
                raise SystemExit(1)
            return
        cleanup_deleting(SessionFactory, vectors)
        if args.command == "reindex":
            print(f"已重建 {reindex_all(SessionFactory, embed_texts, vectors)} 块")
            return
        total = 0
        while count := sync_pending(SessionFactory, embed_texts, vectors):
            total += count
        print(f"已向量化 {total} 块")
    elif args.command == "mine":
        from .mining import mine_conversations

        count = mine_conversations(SessionFactory)
        print(f"对话挖掘后入库 {count} 条；请运行 sync 补齐向量")
    elif args.command == "status":
        from sqlalchemy import func, select

        from mewhelp.db.models import Message

        from .store import KnowledgeChunk, QaStaging

        with SessionFactory() as session:
            print("messages:", session.scalar(select(func.count()).select_from(Message)))
            print("knowledge_chunks:", session.scalar(select(func.count()).select_from(KnowledgeChunk)))
            print("pending:", session.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.vectorize_status == "pending")))
            print("staging:", session.scalar(select(func.count()).select_from(QaStaging)))
    else:
        from mewhelp.tools.knowledge import build_knowledge_tools

        tool = build_knowledge_tools(SessionFactory)[0]
        print(tool.invoke({"keyword": args.question}))


if __name__ == "__main__":
    main()
