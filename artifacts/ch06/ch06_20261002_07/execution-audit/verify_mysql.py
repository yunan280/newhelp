import json,hashlib
from pathlib import Path
from sqlalchemy import inspect,text
from mewhelp.db.engine import engine
backup=json.loads(Path('.cache/ch06/mysql-backup-20261002/backup.json').read_text(encoding='utf-8'))
record={'scope':'actual_mysql_3307','old_rows_unchanged':True,'old_tables':{},'schema':{}}
with engine.connect() as conn:
    for name,old in backup.items():
        quote=conn.dialect.identifier_preparer.quote
        cols=', '.join(quote(col) for col in old['columns'])
        rows=[dict(row) for row in conn.execute(text('SELECT '+cols+' FROM '+quote(name))).mappings()]
        normalize=lambda value: json.loads(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str))
        same=normalize(rows)==old['rows']
        record['old_tables'][name]={'before':len(old['rows']),'after':len(rows),'unchanged':same}
        record['old_rows_unchanged'] &= same
    for name in ('messages','refund_applications'):
        inspector=inspect(conn)
        record['schema'][name]={'columns':[dict(col,type=str(col['type'])) for col in inspector.get_columns(name)],'indexes':inspector.get_indexes(name),'foreign_keys':inspector.get_foreign_keys(name)}
Path('artifacts/ch06/ch06_20261002_03/mysql/after-migration.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
assert record['old_rows_unchanged']
print('Actual MySQL: 10 old tables and all old rows unchanged; ch06 column and refund table inspected.')
