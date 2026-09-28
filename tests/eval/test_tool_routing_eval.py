"""工具选择评估集 —— 真调上游的用例标 `eval`,手动触发。

**只报数字、不设门槛**(spec §15.2)。本章的质量目标是"能看见工具被选中",
不是"命中率 ≥ X"。定一个门槛会诱导为门槛调参,那是 ch01 已经栽过的形状。

走 `run_agent_turn` 而不是解析 SSE:eval 要的是工具轨迹,不是帧序列。

用例文件本身的三条**结构**用例是离线守门的 —— 它们不调上游,所以在默认套件里
照常跑。把整个模块都标上 eval 的话,这 24 条标注的**形状**就只剩手动跑才守得住,
而形状最容易在"随手补一条样例"时被悄悄改坏。
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import service
from mewhelp.db.base import Base
from mewhelp.db.seed import seed

# 交付物是**标注集 + 运行器**,真调上游的两条是 opt-in,各自标在函数上。
#
# `loop_scope="session"` 不是装饰,照抄 ch01 实测的结论:上游 client 不随调用关闭,
# 上个事件循环死掉之后它的析构会落在**下一个**循环里,碰上一个已关闭的 loop
# 就抛 "Event loop is closed"(同一进程里第二次 asyncio.run 必炸)。本模块有两条
# 真调上游的用例,默认函数级循环正好踩中那个形状 —— 钉在同一个循环上就没得踩。
#
# 为什么不用 ch01 那样的模块级 `pytestmark`:`pytestmark` 会把**整个模块**标成 eval,
# 于是上面三条离线结构用例在默认套件里也被 deselect —— 24 条标注的**形状**就只剩
# 手动跑才守得住。而那正是最容易在"随手补一条样例"时被悄悄改坏的东西。

CASES_PATH = Path(__file__).parent / "tool_routing_cases.jsonl"


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.commit()
    return lambda: Session(engine)


def test_the_case_file_has_24_cases():
    assert len(load_cases()) == 24


def test_the_category_distribution_matches_the_spec():
    """类别分布是 spec §15.2 钉死的表,合计 24。"""
    counts = Counter(case["category"] for case in load_cases())
    assert counts == {
        "订单物流商品": 6,
        "政策问答": 5,
        "同义词漏召回": 2,
        "转人工": 4,
        "不该调工具": 4,
        "不该凭知识直答": 3,
    }


def test_every_case_declares_the_required_keys():
    for case in load_cases():
        assert "question" in case
        assert "expected_tool" in case          # 可以是 null
        assert "category" in case


@pytest.mark.eval
@pytest.mark.asyncio(loop_scope="session")
async def test_tool_routing_against_the_real_upstream(session_factory, capsys):
    """对真实上游跑 24 条,打印命中表 + 数字。

    **这个数字只记录,不判定通过与否** —— 所以本用例本身总是绿(除非上游全挂)。
    数进 dev-notes,作为 ch02 的质量证据。
    """
    cases = load_cases()
    hits = 0
    missed: list[tuple[str, str | None, list[str]]] = []

    for index, case in enumerate(cases, start=1):
        # 每条用例一个独立会话,避免跨条污染
        session_id = f"eval-{index}"
        result = await service.run_agent_turn(
            session_factory,
            session_id=session_id,
            user_id="eval",
            message=case["question"],
        )
        selected = [call["name"] for call in result.tool_calls]
        expected = case["expected_tool"]

        matched = (expected in selected) if expected else (not selected)
        if matched:
            hits += 1
        else:
            missed.append((case["question"], expected, selected))

        print(f"  [{'✓' if matched else '✗'}] {case['question']}  "
              f"期望={expected} 实选={selected or '无'}")

    total = len(cases)
    print(f"\n工具选择命中:{hits}/{total} = {hits / total:.0%}")
    if missed:
        print("\n未命中明细:")
        for question, expected, selected in missed:
            print(f"  - {question!r}:期望 {expected},实选 {selected or '无'}")

    with capsys.disabled():
        print(f"\n>>> ch02 工具选择命中率:{hits}/{total} = {hits / total:.0%}")


@pytest.mark.eval
@pytest.mark.asyncio(loop_scope="session")
async def test_the_synonym_gap_is_recorded_not_assumed(session_factory, capsys):
    """验收③在真机上的实测结果 —— 以及它**为什么是波动的**。

    spec 的设计:用户问「邮费是多少」→ 模型抽关键词「邮费」→ LIKE 0 行命中 →
    漏召回。这个漏召回就是 ch03 向量检索要解决的那条基线。

    真机实测(2026-09-28,**四次**)两种结果都出现过:

    - 评估集三次跑 —— 模型**自己**把「邮费」改写成「运费」再查,于是命中
      「[物流] 运费怎么计算:单笔满 99 元包邮…」;
    - 真机验收那次 —— `keyword="邮费"` 原样抽出来,LIKE 0 行,工具返回
      「知识库中没有找到与「邮费」相关的条目」,答复如实说没查到。

    所以**别把任何一次的结果当规律**:既不能说 spec 的设计不成立,也不能说必然漏召回。

    **表侧的事实始终没变**:`find_faq(keyword="邮费")` 仍然是 0 行,由
    `tests/test_db_seed.py` 离线守着(ch03 的那条基线还在,而且它本来就该由
    离线用例守 —— 判一个真调上游的结果等于让基线随模型波动)。

    所以这条用例只判定它**判得动**的那一半:工具确实被调用了(query_faq)——
    也就是 prompt 的「政策类问题一律先查表,不许凭自己的知识回答」确实生效。
    关键词与命中与否**如实打印**、进 dev-notes,不写成断言。第一次改它是为了避免
    钉一个错的基线;第四次数据之后,它是**唯一**不会因模型波动而红的写法。
    """
    result = await service.run_agent_turn(
        session_factory, session_id="synonym-gap", user_id="eval", message="邮费是多少"
    )

    assert [call["name"] for call in result.tool_calls] == ["query_faq"]

    keyword = result.tool_results[0].args.get("keyword")
    missed = "没有找到" in result.tool_results[0].content
    with capsys.disabled():
        print(
            f"\n>>> 验收③实测:问「邮费是多少」→ query_faq(keyword={keyword!r}) → "
            f"{'漏召回(与 spec 一致)' if missed else '命中(模型自己改写了同义词)'}"
        )
