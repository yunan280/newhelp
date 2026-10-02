import asyncio
import json
import sys
import time
from pathlib import Path

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.state import WorkflowContext
from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch06.understanding import understand_query


async def main():
    evidence = json.loads(Path('artifacts/ch06/ch06_20261002_04/http-mysql-01/reference-diagnosis.json').read_text(encoding='utf-8'))
    context = WorkflowContext(lambda: None, lambda: None, lambda: None, Ch05Settings().limits(), router_settings=Ch06Settings())
    rows = []
    for question in ['这个能退吗', '我问另一单，这个能退吗']:
        result = await understand_query(question, evidence['messages'][:2], evidence['trusted_entities'], context=context,
                                        state={'user_id': 'ch06-mysql-http-01-user', 'started_at': time.time()})
        rows.append({'question': question, **result.evaluation_result()})
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as stream:
        json.dump(rows, stream, ensure_ascii=False, indent=2)
    print(json.dumps([{'question': row['question'], 'actual': row['actual']} for row in rows], ensure_ascii=False))


asyncio.run(main())
