from dataclasses import replace

import pytest
from pydantic import ValidationError

from mewhelp.knowledge.store import ChunkSnapshot


def snapshot():
    return ChunkSnapshot(1, "原文", "型号参数", "支持蓝牙", "手册", "参数", "耳机", "manual", False, "a" * 64)


def test_filter_values_are_parameters_and_not_expressions():
    from mewhelp.knowledge.filters import SearchFilters, compile_filter

    value = '耳机" or id > 0 or category == "\\'
    expr, params = compile_filter(SearchFilters(product_category=value, is_key_clause=False))
    assert expr == "product_category == {product_category} and is_key_clause == {is_key_clause}"
    assert params == {"product_category": value, "is_key_clause": False}
    assert value not in expr


def test_empty_filter_searches_all():
    from mewhelp.knowledge.filters import SearchFilters, compile_filter

    assert compile_filter(SearchFilters()) == ("", {})


def test_product_filter_excludes_null_and_other_categories():
    from mewhelp.knowledge.filters import SearchFilters

    filters = SearchFilters(product_category="耳机")
    assert filters.matches(snapshot())
    assert not filters.matches(replace(snapshot(), product_category=None))
    assert not filters.matches(replace(snapshot(), product_category="充电器"))


@pytest.mark.parametrize("values", [
    {"expr": "id > 0"}, {"product_category": " "}, {"is_key_clause": "false"},
    {"category": 123}, {"content_type": "x" * 33}, {"product_category": "x" * 129},
])
def test_invalid_filters_are_rejected(values):
    from mewhelp.knowledge.filters import SearchFilters

    with pytest.raises(ValidationError):
        SearchFilters(**values)
