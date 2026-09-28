"""上游 tool-calling 冒烟 —— 全章的地基,先跑这个再写别的。

为什么不写成 pytest:它真调上游、要花钱、结果不确定,而且这一章的
所有测试都必须离线可跑。它是一次性的地基验证,不是回归测试。

用法:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 .venv/Scripts/python.exe scripts/smoke_tool_calling.py
"""

import asyncio
import json

from langchain_core.tools import tool

from mewhelp.llm import get_chat_model


@tool
def query_logistics(order_id: str) -> str:
    """按订单号查物流轨迹。order_id 是订单号,例如 1001。"""
    return f"订单 {order_id}:承运商顺丰,已到达杭州转运中心"


async def main() -> None:
    model = get_chat_model(temperature=0).bind_tools([query_logistics])
    ai = await model.ainvoke("订单 1001 的物流到哪了")

    print("原始返回 content:", repr(ai.content))
    print("原始返回 tool_calls:", json.dumps(ai.tool_calls, ensure_ascii=False))

    if not ai.tool_calls:
        print("\n❌ 上游没有返回 tool_calls —— 停下来问用户,不要自行换方案")
        raise SystemExit(1)

    call = ai.tool_calls[0]
    print(f"\n✅ 选中工具:{call['name']}")
    print(f"✅ 参数:{json.dumps(call['args'], ensure_ascii=False)}")
    print(f"✅ 调用 id:{call['id']}")


if __name__ == "__main__":
    asyncio.run(main())
