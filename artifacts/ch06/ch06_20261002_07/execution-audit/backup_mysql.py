import json,hashlib
from pathlib import Path
from sqlalchemy import inspect,text
from mewhelp.db.engine import engine
out=Path('.cache/ch06/mysql-backup-20261002'); out.mkdir(parents=True,exist_ok=True)
report=Path('artifacts/ch06/ch06_20261002_03/mysql'); report.mkdir(parents=True,exist_ok=True)
def digest(rows): return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
backup={}; stats={}
with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
    with conn.begin():
        names=inspect(conn).get_table_names()
        for name in names:
            quoted=conn.dialect.identifier_preparer.quote(name)
            ddl=conn.exec_driver_sql('SHOW CREATE TABLE '+quoted).one()[1]
            rows=[dict(row) for row in conn.execute(text('SELECT * FROM '+quoted)).mappings()]
            backup[name]={'ddl':ddl,'rows':rows,'columns':[c['name'] for c in inspect(conn).get_columns(name)]}
            stats[name]={'count':len(rows),'sha256':digest(rows)}
path=out/'backup.json'; path.write_text(json.dumps(backup,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
record={'scope':'actual_mysql_3307','backup_path':str(path),'backup_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'tables':stats}
(report/'before-migration.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print({'backup_tables':len(stats),'backup_bytes':path.stat().st_size,'tables':{name:data['count'] for name,data in stats.items()}})
