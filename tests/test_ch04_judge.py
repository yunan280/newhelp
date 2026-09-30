import pytest

from mewhelp.knowledge.answering import SourceDTO


def source():
    return SourceDTO(
        number=1,
        chunk_id="9007199254740995",
        questions="HX-210蓝牙版本？",
        answer="HX-210支持蓝牙5.2。",
        section_path="示例 / 参数",
        category="参数",
        product_category="耳机",
        content_hash="a" * 64,
        source_url="/kb/source/9007199254740995",
    )


class Model:
    def __init__(self, parsed, error=None):
        self.parsed, self.error, self.messages = parsed, error, []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return {"parsed": self.parsed, "parsing_error": self.error}


@pytest.mark.asyncio
async def test_judge_uses_actual_sources_and_statement_denominator():
    from mewhelp.knowledge.evaluation.judge import judge_answer

    model = Model(
        {
            "claims": [
                {
                    "text": "支持蓝牙5.2",
                    "supported": True,
                    "evidence_numbers": [1],
                    "rationale": "原文明确",
                },
                {
                    "text": "电池400mAh",
                    "supported": False,
                    "evidence_numbers": [],
                    "rationale": "原文未提供",
                },
            ]
        }
    )
    result = await judge_answer("规格？", "支持蓝牙5.2，电池400mAh[1]。", [source()], model=model)
    assert result.score == 0.5 and result.error is None
    assert "400mAh" in model.messages[0][-1].content
    assert "reference_answer" not in model.messages[0][-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parsed,error",
    [
        (None, "parse failure"),
        ({"claims": "bad"}, None),
        (
            {
                "claims": [
                    {"text": "声称", "supported": True, "evidence_numbers": [9], "rationale": "错"}
                ]
            },
            None,
        ),
        (
            {
                "claims": [
                    {"text": "声称", "supported": True, "evidence_numbers": [], "rationale": "无据"}
                ]
            },
            None,
        ),
    ],
)
async def test_judge_parse_failure_remains_an_error(parsed, error):
    from mewhelp.knowledge.evaluation.judge import judge_answer

    result = await judge_answer("参数？", "事实[1]。", [source()], model=Model(parsed, error))
    assert result.score is None and result.error


@pytest.mark.asyncio
async def test_empty_answer_has_no_claims_and_does_not_call_model():
    from mewhelp.knowledge.evaluation.judge import judge_answer

    model = Model(None)
    result = await judge_answer("问题", "", [], model=model)
    assert result.score is None and result.error is None and result.claims == []
    assert not model.messages
