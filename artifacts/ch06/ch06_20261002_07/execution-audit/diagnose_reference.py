import asyncio,json
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from mewhelp.ch05.workflow import build_workflow
async def main():
    async with AsyncSqliteSaver.from_conn_string('.cache/ch06/acceptance-01/workflow.sqlite3') as saver:
        graph=build_workflow(saver)
        async for snapshot in graph.aget_state_history({'configurable':{'thread_id':'ch06-http-20261002-01-switch'}}):
            state=snapshot.values
            if state.get('question')=='这个能退吗' and state.get('understanding'):
                record={'understanding':state['understanding'],'trusted_entities':state.get('trusted_entities'), 'messages':[{'id':m.id,'type':m.type,'content':m.content} for m in state.get('messages',[])]}
                Path('artifacts/ch06/ch06_20261002_03/http-01/reference-diagnosis.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(record,ensure_ascii=False)[:4000]); break
raise SystemExit(asyncio.run(main()))
