"""System Prompt 行为约束评估 —— 需要真实调用 DeepSeek。

Prompt 的六条硬约束没法单测,只能拿对抗性输入去撞。跑法:

    pytest -m eval -v -s
"""

from pathlib import Path

import pytest
import yaml

from mewhelp.ch01.service import EmptyCompletionError, stream_chat

# 与 test_extraction_eval.py 同因:整场评估必须跑在同一个事件循环上,否则
# 第二个循环里第一次调用上游就抛 "Event loop is closed"(上一循环的 client 析构)。
pytestmark = [pytest.mark.eval, pytest.mark.asyncio(loop_scope="session")]

CASES_PATH = Path(__file__).parent / "prompt_behaviors.yaml"


def load_cases() -> list[dict]:
    return yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))


async def test_prompt_behaviours():
    cases = load_cases()
    results: list[str] = []
    failed: list[str] = []

    for case in cases:
        empty_note = ""
        try:
            reply = "".join(
                [chunk async for chunk in stream_chat(f"eval-{case['id']}", case["input"])]
            )
        except EmptyCompletionError:
            # 空回复在服务层就已经被判为失败回合了(service 抛、api 转 SSE error)。
            # 这里不能让它掀翻整个评估:一个空回复会把后面 5 条用例的产出一起带走,
            # 而报告的价值正在于六条并排看。按 FAIL 记账,继续跑下一条。
            reply = ""
            empty_note = "\n  !! 空回复:模型本轮没有任何输出"

        violations = [w for w in case.get("must_not_contain", []) if w in reply]
        signals = [w for w in case.get("should_mention", []) if w in reply]

        if violations or empty_note:
            verdict = "FAIL"
            failed.append(case["id"])
        elif case.get("should_mention") and not signals:
            verdict = "REVIEW"
        else:
            verdict = "PASS"

        results.append(
            f"\n[{verdict}] {case['id']}\n"
            f"  提问: {case['input']}\n"
            f"  回复: {reply}\n"
            f"  人工确认: {case.get('review', '-')}"
            + (f"\n  !! 违规词: {violations}" if violations else "")
            + empty_note
        )

    print("\n" + "=" * 72 + "\nSystem Prompt 行为约束评估\n" + "=" * 72)
    print("\n".join(results))
    print("=" * 72)

    assert not failed, f"出现硬性违规:{failed}"
