"""三个 mock 工具 —— 不接真实接口、不建表,数据由入参定种子。

为什么由入参定种子:同一个 order_id 永远给出同一份物流。验收①因此可复现、
可截图,评估集也能断言内容。真实随机留给"换个订单号"这个维度。
"""

import datetime as dt
import re

import pytest
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from mewhelp.tools.business import build_business_tools


@pytest.fixture
def tools() -> dict:
    # 保留业务事实回归；物流处理器已移到独立 MCP，非内置回退路径。
    from mewhelp.ch08.mcp_servers.mock_data import logistics_data
    @tool
    def query_logistics(order_id: str) -> str:
        """物流 MCP mock 的事实投影，测试中离线核对时间线。"""
        return logistics_data(order_id)['data']['description']
    return {t.name: t for t in [*build_business_tools(), query_logistics]}


def test_builds_only_the_two_builtin_business_tools(tools):
    assert {t.name for t in build_business_tools()} == {"query_order", "query_product"}


@pytest.mark.parametrize(
    ("name", "args"),
    [
        pytest.param("query_order", {"order_id": "1001"}, id="query_order"),
        pytest.param("query_product", {"product_name": "跑鞋"}, id="query_product"),
        pytest.param("query_logistics", {"order_id": "1001"}, id="query_logistics"),
    ],
)
def test_only_business_arguments_are_exposed_to_the_model(tools, name, args):
    """闭包注入的核心断言:模型只看见业务参数。

    这条是 `InjectedToolArg` 的替代品。实测 InjectedToolArg 在 langchain-core
    1.6.5 上不生效 —— 被标注的参数照样进 properties 与 required,模型会看见
    并要求自己编一个值。闭包方案下这些参数根本不在签名里,所以不可能出现。
    """
    schema = convert_to_openai_tool(tools[name])
    assert set(schema["function"]["parameters"]["properties"]) == set(args)
    assert set(schema["function"]["parameters"]["required"]) == set(args)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        pytest.param("query_order", {"order_id": "1001"}, id="query_order"),
        pytest.param("query_product", {"product_name": "跑鞋"}, id="query_product"),
        pytest.param("query_logistics", {"order_id": "1001"}, id="query_logistics"),
    ],
)
async def test_same_input_gives_the_same_output(tools, name, args):
    """同入参 → 同输出。这是验收①可复现的全部依据。"""
    first = await tools[name].ainvoke(args)
    second = await tools[name].ainvoke(args)
    assert first == second


async def test_different_input_gives_different_output(tools):
    a = await tools["query_logistics"].ainvoke({"order_id": "1001"})
    b = await tools["query_logistics"].ainvoke({"order_id": "2002"})
    assert a != b


@pytest.mark.parametrize("name", ["query_order", "query_product", "query_logistics"])
async def test_output_is_non_empty_readable_text(tools, name):
    arg = {"order_id": "1001"} if "order_id" in tools[name].args_schema.model_fields else {
        "product_name": "跑鞋"
    }
    out = await tools[name].ainvoke(arg)
    assert isinstance(out, str)
    assert out.strip()


async def test_order_and_logistics_mention_the_order_id(tools):
    """回灌给模型的内容里要有它问的那个订单号,否则模型答不上"哪个订单"。"""
    out = await tools["query_logistics"].ainvoke({"order_id": "1001"})
    assert "1001" in out


@pytest.mark.parametrize("order_id", ["1001", "2002"])
async def test_order_and_logistics_agree_on_the_order_date(tools, order_id):
    """两条工具对同一个订单号必须给出**自洽**的时间线(计划里没有这条,我补的)。

    计划让两条工具各起各的种子,于是可能推出"09-03 已到达"配"09-11 才下单" ——
    单看一条都正常,同屏出现就是"包裹比订单先到"。演示里用户问完物流接着问订单,
    这一幕正好会被看到。断的是**用户看到的那段文字**,不是内部函数:两条工具各推
    一遍日期、只是恰好一致,也应当过;只是不能出现推导路径分叉。
    """
    order = await tools["query_order"].ainvoke({"order_id": order_id})
    logistics = await tools["query_logistics"].ainvoke({"order_id": order_id})

    ordered = dt.date.fromisoformat(
        re.search(r"下单时间 (\d{4}-\d{2}-\d{2})", order).group(1)
    )
    latest = dt.date.fromisoformat(
        re.search(r"\((\d{4}-\d{2}-\d{2})\)", logistics).group(1)
    )
    assert latest > ordered


@pytest.mark.parametrize("name", ["query_order", "query_product", "query_logistics"])
async def test_mock_tools_never_touch_the_database(tools, name):
    """三个 mock 工具不碰库 —— 按需求写死。它们的 build 函数不收 session 工厂。

    用签名断言:收了 session_factory 的话,这条就会红。
    """
    import inspect

    assert "session_factory" not in inspect.signature(build_business_tools).parameters
