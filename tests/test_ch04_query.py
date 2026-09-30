import pytest


def normalization(**overrides):
    return {"canonical": "退换货规则是什么？", "synonyms": ["换新"], "route": "knowledge", **overrides}


@pytest.mark.parametrize(("original", "canonical"), [
    ("HX-210 不支持 65W 吗？", "HX-210S 支持 65W 吗？"),
    ("HX-210 不支持 65W 吗？", "HX-210 支持 65W 吗？"),
    ("HX-210 支持 65W 吗？", "HX-210 支持 60W 吗？"),
    ("支持65W吗？", "支持165W吗？"),
    ("满99元且未拆封才能退吗？", "满99元可以退吗？"),
    ("7天以内退货吗？", "7天退货吗？"),
])
def test_damage_to_models_numbers_negation_or_conditions_falls_back(original, canonical):
    from mewhelp.knowledge.query import validate_normalization

    result = validate_normalization(original, normalization(canonical=canonical))
    assert result.canonical == original
    assert result.route == "knowledge"
    assert result.diagnostics


def test_synonyms_only_extend_retrieval_query():
    from mewhelp.knowledge.query import validate_normalization

    result = validate_normalization("咋换货", normalization())
    assert result.original == "咋换货"
    assert result.canonical == "退换货规则是什么？"
    assert result.bm25_query == "退换货规则是什么？ 换新"


def test_synonyms_cannot_add_another_model_or_numeric_constraint():
    from mewhelp.knowledge.query import validate_normalization

    result = validate_normalization("HX-210 蓝牙？", normalization(canonical="HX-210 的蓝牙版本？", synonyms=["HX-210S", "蓝牙5.3", "无线协议"]))
    assert "HX-210S" not in result.bm25_query
    assert "5.3" not in result.bm25_query
    assert "无线协议" in result.bm25_query


@pytest.mark.parametrize("raw", [None, {}, normalization(canonical=""),
    normalization(canonical=123), normalization(original="篡改"),
    normalization(synonyms=["a"] * 6), normalization(synonyms=["a" * 33]),
    normalization(synonyms="退货"), normalization(route="uncertain"),
])
def test_malformed_model_output_falls_back(raw):
    from mewhelp.knowledge.query import validate_normalization

    result = validate_normalization("邮费多少", raw)
    assert result.canonical == "邮费多少"
    assert result.bm25_query == "邮费多少"
    assert result.diagnostics


def test_fact_question_cannot_be_routed_to_greeting_or_business():
    from mewhelp.knowledge.query import validate_normalization

    assert validate_normalization("怎么个退法？", normalization(route="business")).route == "knowledge"
    assert validate_normalization("你好，HX-210的蓝牙版本？", normalization(route="greeting")).route == "knowledge"


@pytest.mark.asyncio
async def test_understanding_sends_only_current_question_and_propagates_service_failure():
    from mewhelp.knowledge.query import understand_query

    class Model:
        def __init__(self):
            self.inputs = []

        async def ainvoke(self, messages):
            self.inputs.append(messages)
            return {"parsed": normalization(), "raw": None, "parsing_error": None}

    model = Model()
    assert (await understand_query("咋换货", model=model)).original == "咋换货"
    assert model.inputs[0][-1] == ("human", "咋换货")
    assert len(model.inputs[0]) == 2

    class Failed:
        async def ainvoke(self, messages):
            raise ConnectionError("upstream unavailable")

    with pytest.raises(ConnectionError):
        await understand_query("邮费多少", model=Failed())
