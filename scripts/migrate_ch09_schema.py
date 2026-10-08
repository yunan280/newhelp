"""Save the original structure before applying the approved incremental DDL."""
import argparse
import json
from pathlib import Path

from sqlalchemy import inspect

from mewhelp.ch09.migration import migrate_ch09
from mewhelp.db.engine import engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as connection:
        existing = inspect(connection).get_table_names()
        before = {table: connection.exec_driver_sql(f'SHOW CREATE TABLE `{table}`').one()[1]
                  for table in ('review_queue', 'eval_runs', 'low_confidence_questions', 'messages')
                  if table in existing}
    args.report.with_name(args.report.stem + '-before.json').write_text(
        json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    result = migrate_ch09(engine)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'changed': result['changed'], 'report': str(args.report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
