import asyncio,json,sys
from pathlib import Path
from langchain_core.messages import HumanMessage,SystemMessage
from mewhelp.ch06.config import Ch06Settings,get_router_model
from mewhelp.ch06.prompts import INTENT_SYSTEM
async def main():
    model=Ch06Settings().primary_model
    try:
        result=await get_router_model(purpose='classifier',model_name=model,output_tokens=128,request_seconds=30).ainvoke([SystemMessage(content=INTENT_SYSTEM),HumanMessage(content='谢谢你')])
        record={'available':True,'request_model':model,'response_model':result.response_metadata.get('model_name'),'content':result.content,'usage':result.usage_metadata}
        code=0
    except Exception as error:
        record={'available':False,'request_model':model,'error':type(error).__name__+': '+str(error)[:600]}; code=1
    out=Path(sys.argv[1]); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as stream: json.dump(record,stream,ensure_ascii=False,indent=2)
    print(record); return code
raise SystemExit(asyncio.run(main()))
