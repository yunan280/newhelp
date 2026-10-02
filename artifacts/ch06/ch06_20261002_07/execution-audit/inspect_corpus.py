from sqlalchemy import select
from mewhelp.db.engine import SessionLocal
from mewhelp.knowledge.store import KnowledgeChunk
with SessionLocal() as db:
    rows=db.scalars(select(KnowledgeChunk)).all()
    print([(str(r.id),r.section_path,r.category,r.content_type,r.vectorize_status) for r in rows])
