"""query_faq —— 真实查 faq 表。验收②与③都压在它身上。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.db.seed import seed
from mewhelp.tools.knowledge import build_knowledge_tools


@pytest.fixture
def session_factory():
    """内存库 + StaticPool:多个 Session(工具跑在别的线程里)共用同一个库。

    默认的 SQLite 内存库是 per-connection 的 —— 不用 StaticPool 的话,
    工具在新线程里开的新连接看到的是一个**空库**,种子数据一条都不在。
    这个坑在"每个工具自己开 Session"之后才会显形。
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.commit()
    return lambda: Session(engine)


@pytest.fixture
def query_faq(session_factory):
    return {t.name: t for t in build_knowledge_tools(session_factory)}["query_faq"]


async def test_acceptance_two_the_return_policy_is_found(query_faq):
    """验收②:「退货政策是什么」→ 关键词「退货」→ 命中并答得出 7 天无理由。"""
    out = await query_faq.ainvoke({"keyword": "退货"})
    assert "7 天" in out
    assert "无理由" in out


async def test_acceptance_three_the_shipping_fee_query_misses(query_faq):
    """验收③:「邮费是多少」→ 关键词「邮费」→ **0 行命中**。

    这是**预期结果**,不是 bug —— 答案其实在「运费怎么计算」条目里,
    但字面 LIKE 对不上。漏召回发生在同义词这一层,正是 ch03 向量检索要解决的。
    """
    out = await query_faq.ainvoke({"keyword": "邮费"})
    assert "没有" in out or "未找到" in out
    assert "99" not in out  # 没有把运费那条的内容漏出来


async def test_both_acceptances_go_through_the_same_code_path(query_faq):
    """同一个工具、一问就中一问就漏 —— 这才说明漏的是检索能力,不是工具坏了。"""
    hit = await query_faq.ainvoke({"keyword": "退货"})
    miss = await query_faq.ainvoke({"keyword": "邮费"})
    assert hit != miss
    assert "退货" in hit


async def test_zero_hits_returns_an_explicit_sentence_not_an_empty_string(query_faq):
    """命中 0 行时必须给一句明确的话,不能返回空串。

    空串回灌给模型,模型分不清"查了没有"和"工具坏了",容易自己编一个答案 ——
    而这正是本章唯一那个刻意漏召回的出口,它的措辞决定了模型会不会老实说没查到。
    """
    out = await query_faq.ainvoke({"keyword": "完全不存在的东西xyz"})
    assert out.strip()
    assert len(out) > 5
    assert "没有" in out or "未找到" in out


async def test_declares_it_has_no_information_rather_than_guessing(query_faq):
    """漏召回时的措辞要明确禁止模型自己发挥 —— 否则验收③观察到的会变成
    "模型没调工具",而不是"查表查不出来",那是另一件事。"""
    out = await query_faq.ainvoke({"keyword": "邮费"})
    assert "不要" in out or "请如实" in out or "不要凭" in out


async def test_multiple_hits_are_all_returned(query_faq):
    out = await query_faq.ainvoke({"keyword": "发票"})
    assert "怎么开" in out
    assert "重开" in out


async def test_result_includes_the_category(query_faq):
    out = await query_faq.ainvoke({"keyword": "退货"})
    assert "退换货" in out


async def test_wildcard_keyword_does_not_dump_the_whole_table(query_faq):
    """Review Focus #2 的工具层验证:一个 `%` 不该把 12 条全倒出来。"""
    out = await query_faq.ainvoke({"keyword": "%"})
    assert "没有" in out or "未找到" in out


async def test_only_the_keyword_is_exposed_to_the_model(session_factory):
    """闭包注入:session_factory 不在签名里,模型看不见。"""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    query_faq = {t.name: t for t in build_knowledge_tools(session_factory)}["query_faq"]
    schema = convert_to_openai_tool(query_faq)
    assert set(schema["function"]["parameters"]["properties"]) == {"keyword"}
