import json
import sys
from pathlib import Path

from sqlalchemy import inspect, text

from mewhelp.db.engine import engine

backup = json.loads(Path('.cache/ch06/mysql-backup-20261002/backup.json').read_text(encoding='utf-8'))
record = {'database': 'actual MySQL localhost:3307/mewhelp', 'old_rows_unchanged': True, 'tables': {}}
normalize = lambda rows: json.loads(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str))
with engine.connect() as connection:
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for name, old in backup.items():
        columns = ', '.join(quote(column) for column in old['columns'])
        current = normalize([dict(row) for row in connection.execute(text('SELECT ' + columns + ' FROM ' + quote(name))).mappings()])
        keys = inspector.get_pk_constraint(name)['constrained_columns']
        by_key = {tuple(row[key] for key in keys): row for row in current}
        missing_or_changed = sum(by_key.get(tuple(row[key] for key in keys)) != row for row in old['rows'])
        record['tables'][name] = {'before': len(old['rows']), 'current': len(current), 'old_missing_or_changed': missing_or_changed}
        record['old_rows_unchanged'] &= missing_or_changed == 0
    record['added_aftersales_policy_chunks'] = connection.execute(text("SELECT COUNT(*) FROM knowledge_chunks WHERE section_path LIKE :source"), {'source': '%aftersales-policy.md::%'}).scalar_one()
    record['explicit_test_refunds'] = connection.execute(text("SELECT COUNT(*) FROM refund_applications WHERE user_id LIKE :prefix"), {'prefix': 'ch06-mysql-%'}).scalar_one()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('artifacts/ch06/ch06_20261002_05/mysql-old-rows.json')
with out.open('x', encoding='utf-8') as stream:
    json.dump(record, stream, ensure_ascii=False, indent=2)
assert record['old_rows_unchanged'] and record['added_aftersales_policy_chunks'] == 8
print(record)
