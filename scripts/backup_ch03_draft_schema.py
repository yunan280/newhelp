"""仅用于把本次开发中先建错的草案表保留为备份，再运行权威 DDL。"""

from sqlalchemy import inspect

from mewhelp.db.engine import engine


def main() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "knowledge_chunks" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
    if "vectorize_status" in columns:
        print("权威结构已在使用，无需备份")
        return
    if "source_key" not in columns:
        raise RuntimeError("现有 knowledge_chunks 不是本次草案结构，拒绝自动迁移")
    names = [name for name in ("knowledge_chunks", "knowledge_qa_staging", "knowledge_jobs") if name in tables]
    if any(name + "_draft_backup" in tables for name in names):
        raise RuntimeError("备份表已存在，拒绝覆盖")
    renames = ", ".join(f"{name} TO {name}_draft_backup" for name in names)
    with engine.begin() as connection:
        connection.exec_driver_sql("RENAME TABLE " + renames)
    print("草案表已保留为 *_draft_backup；现在运行 init-db")


if __name__ == "__main__":
    main()
