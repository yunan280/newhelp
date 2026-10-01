"""真机验收：模拟 MySQL 提交后中断，再运行补偿并检查向量状态。"""

from sqlalchemy import select

from mewhelp.db.engine import SessionFactory
from mewhelp.knowledge.embedding import embed_texts
from mewhelp.knowledge.store import KnowledgeChunk
from mewhelp.knowledge.sync import sync_pending
from mewhelp.knowledge.vectors import MilvusSettings


def main() -> None:
    with SessionFactory() as session:
        row = session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.questions == "运费说明"))
        if row is None:
            raise RuntimeError("先运行 init-db、ingest 和 sync")
        row_id = row.id
        row.vectorize_status = "pending"
        row.vector_id = None
        session.commit()  # 故意停在此处：MySQL 已提交，Milvus 尚未补写。
    vectors = MilvusSettings().connect()
    count = sync_pending(SessionFactory, embed_texts, vectors)
    with SessionFactory() as session:
        row = session.get(KnowledgeChunk, row_id)
        assert row.vectorize_status == "done"
        assert row.vector_id == str(row_id)
    print(f"补偿成功：{count} 块，id={row_id}，状态=done")


if __name__ == "__main__":
    main()
