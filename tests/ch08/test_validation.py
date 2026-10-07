import pytest
from langchain_core.tools import tool


def test_integer_is_not_coerced_and_bounds_are_checked():
    from mewhelp.tools.validation import validate_arguments
    schema = {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 3}}, 'required': ['limit'], 'additionalProperties': False}
    for args in ({'limit': '2'}, {}, {'limit': 0}, {'limit': 4}, {'limit': True}, {'limit': 2, 'extra': 1}):
        assert validate_arguments(schema, args)
    assert not validate_arguments(schema, {'limit': 2})


def test_nested_required_enum_and_local_reference():
    from mewhelp.tools.validation import validate_arguments
    schema = {'type': 'object', '$defs': {'kind': {'enum': ['售后', '投诉']}}, 'properties': {'draft': {'type': 'object', 'properties': {'kind': {'$ref': '#/$defs/kind'}}, 'required': ['kind']}}, 'required': ['draft']}
    assert validate_arguments(schema, {'draft': {}})
    assert validate_arguments(schema, {'draft': {'kind': '未知'}})
    assert not validate_arguments(schema, {'draft': {'kind': '售后'}})


def test_pydantic_and_dict_schemas_and_remote_refs():
    from mewhelp.tools.validation import normalize_schema
    @tool
    def integer(limit: int) -> int:
        """返回一个整数。"""
        return limit
    assert normalize_schema(integer)['properties']['limit']['type'] == 'integer'
    assert normalize_schema(integer, {'type': 'object', 'properties': {}})['type'] == 'object'
    with pytest.raises(ValueError, match='本地'):
        normalize_schema(integer, {'type': 'object', 'properties': {'x': {'$ref': 'https://example.com/schema'}}})
    with pytest.raises(ValueError):
        normalize_schema(integer, {'type': 'array'})
