"""抽取准确率评估 —— 需要真实调用 DeepSeek。

Prompt 与抽取质量不可单测,这份标注集就是 TDD 的替代品。跑法:

    pytest -m eval -v -s
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from mewhelp.ch01.service import extract_ticket

# loop_scope="session" 不是装饰:上游 client 不随调用关闭,上个事件循环死掉之后,
# 它的析构会落在**下一个**循环里,碰上一个已关闭的 loop 就抛 "Event loop is closed"
# (实测:同一进程里第二次 asyncio.run 必炸,第三次起又正常)。
# 把整场评估钉在同一个循环上,就没有"上一个循环"可供踩。
pytestmark = [pytest.mark.eval, pytest.mark.asyncio(loop_scope="session")]

CASES_PATH = Path(__file__).parent / "aftersales_cases.jsonl"

# intent 是主指标。低于这条线说明 Prompt 或枚举边界有问题,要查混淆矩阵而不是改阈值。
INTENT_ACCURACY_BAR = 0.85
ORDER_ID_ACCURACY_BAR = 0.90

# expected_solution **故意不设门槛** —— 这是诊断量,不是闸门。
# 原因:当前实测值(70%)本身就不是一个"可接受"的水平,把它钉成门槛等于把一个
# 已知不好的数字固化成"合格线",还会因为模型的正常波动让套件时红时绿。
# 它照常打印、照常进混淆矩阵,给人看趋势;要不要设门槛、设多少,等这个字段的
# 边界定清楚之后再说。**别把"这里没有 assert"读成漏了。**

# 模型不调工具时把原始输出截到这么长。整段 raw 可能有几百字,会淹没报告。
RAW_PREVIEW_LIMIT = 200


def load_cases() -> list[dict]:
    lines = CASES_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _preview(raw: str) -> str:
    """把 raw 压成单行短文本。

    换行必须压掉:raw 往往是多行散文,原样打进报告会把一行结果拆成好几行,
    其余用例的排版全乱。截断只是排版约束,不是信息取舍 —— 200 字足够看出
    "模型到底说了什么"。
    """
    flat = " ".join(raw.split())
    if len(flat) <= RAW_PREVIEW_LIMIT:
        return flat
    return flat[:RAW_PREVIEW_LIMIT] + "…"


async def test_extraction_accuracy():
    cases = load_cases()
    # 标注集在修复轮 1 从 20 条长到 27 条,是**有意的**,不是漂移:
    #   +5 条「仅需解释」正样本 —— 原 20 条里该值出现 0 次,而模型生产得最多,
    #      没有正样本就只剩"从错误方向看见它";
    #   +1 条「维修」、+1 条「其他」 —— 两处标注缺陷改判之后,这两个 intent
    #      各只剩 1 条,破了本集「9 个 intent 各至少 2 条」的约定。
    # 分母因此从 20 变成 27;下面的准确率是**对着 27 条**算的,不要与 20 条时的
    # 数字直接比。数量对不上要红,静悄悄地变才最坏。
    assert len(cases) == 27, f"标注样例应为 27 条,实际 {len(cases)} 条"

    intent_hits = 0
    order_id_hits = 0
    solution_hits = 0
    confusion: Counter = Counter()
    rows: list[str] = []

    for case in cases:
        got = await extract_ticket(case["description"])
        ticket = got.ticket
        if ticket is None:
            # raw 是唯一能说明"模型为什么没给结构化结果"的东西 —— 它可能压根没调工具,
            # 也可能调了但参数过不了 schema。吞掉它,这一行就只剩"不知道"。
            rows.append(
                f"#{case['id']:>2}  None  ← 模型没返回结构化结果;raw={_preview(got.raw)}"
            )
            confusion[(case["intent"], "<None>")] += 1
            continue

        intent_ok = ticket.intent.value == case["intent"]
        order_ok = ticket.order_id == case["order_id"]
        solution_ok = ticket.expected_solution.value == case["expected_solution"]

        intent_hits += intent_ok
        order_id_hits += order_ok
        solution_hits += solution_ok
        if not intent_ok:
            confusion[(case["intent"], ticket.intent.value)] += 1

        mark = "OK " if (intent_ok and order_ok and solution_ok) else "NG "
        rows.append(
            f"#{case['id']:>2}  {mark} intent={ticket.intent.value}/{case['intent']}"
            f"  order={ticket.order_id}/{case['order_id']}"
            f"  solution={ticket.expected_solution.value}/{case['expected_solution']}"
        )

    n = len(cases)
    report = [
        "",
        "=" * 72,
        "抽取准确率评估",
        "=" * 72,
        *rows,
        "-" * 72,
        f"intent            {intent_hits}/{n} = {intent_hits / n:.0%}",
        f"order_id          {order_id_hits}/{n} = {order_id_hits / n:.0%}",
        f"expected_solution {solution_hits}/{n} = {solution_hits / n:.0%}",
    ]
    if confusion:
        report.append("intent 混淆(标注 → 预测):")
        report += [f"  {a} → {b}  ×{c}" for (a, b), c in confusion.most_common()]
    report.append("=" * 72)
    print("\n".join(report))

    assert intent_hits / n >= INTENT_ACCURACY_BAR, "intent 准确率低于门槛,查上面的混淆矩阵"
    assert order_id_hits / n >= ORDER_ID_ACCURACY_BAR, "order_id 准确率低于门槛"
