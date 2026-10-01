"""一次性清理早期未带 corpus 命名空间的文档向量；先运行新版 ingest。"""

from sqlalchemy import select

from mewhelp.db.engine import SessionFactory
from mewhelp.knowledge.store import KnowledgeChunk
from mewhelp.knowledge.sync import cleanup_deleting
from mewhelp.knowledge.vectors import MilvusSettings


def main() -> None:
    with SessionFactory() as session:
        legacy = session.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.section_path.like("%::%"),
            ~KnowledgeChunk.section_path.like("corpus:%"),
            ~KnowledgeChunk.section_path.like("__deleting__::corpus:%"),
        )).all()
        for row in legacy:
            row.vectorize_status = "pending"
            row.vector_id = None
            if not row.section_path.startswith("__deleting__::"):
                row.section_path = "__deleting__::" + row.section_path[:498]
        session.commit()
    vectors = MilvusSettings().connect()
    vectors.ensure_collection()
    count = cleanup_deleting(SessionFactory, vectors)
    print(f"已清理旧版文档向量 {count} 条")


if __name__ == "__main__":
    main()
