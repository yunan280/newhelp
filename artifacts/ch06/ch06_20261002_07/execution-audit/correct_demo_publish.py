import json
from pathlib import Path
from sqlalchemy import select
from mewhelp.db.engine import SessionLocal
from mewhelp.knowledge.store import KnowledgeChunk
from mewhelp.knowledge.vectors import MilvusSettings
before=json.loads(Path('.cache/ch06/mysql-backup-20261002/backup.json').read_text(encoding='utf-8'))
old_ids={row['id'] for row in before['knowledge_chunks']['rows']}
with SessionLocal() as db:
    extra=[row for row in db.scalars(select(KnowledgeChunk)) if row.id not in old_ids and 'shipping-policy.md::' in (row.section_path or '')]
    assert len(extra)==2
    rows=[{col.name:getattr(row,col.name) for col in row.__table__.columns} for row in extra]
    Path('.cache/ch06/mysql-backup-20261002/extra-shipping-rollback.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    ids=[row.id for row in extra]
    for row in extra: db.delete(row)
    db.commit()
MilvusSettings().connect_hybrid(collection='ch06_eval_mysql_20261002_01').delete(ids)
Path('artifacts/ch06/ch06_20261002_03/mysql/publish-correction.json').write_text(json.dumps({'only_own_new_shipping_ids_removed':[str(i) for i in ids],'original_ids_preserved':True,'recovery_backup':'.cache/ch06/mysql-backup-20261002/extra-shipping-rollback.json'},indent=2),encoding='utf-8')
print('Removed only two newly inserted duplicate shipping chunks; original 20 SQL rows preserved.')
