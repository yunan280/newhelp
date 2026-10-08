"""严格 JSON Schema 校验；注册阶段拒绝远程引用，不转换调用参数。"""
from copy import deepcopy

from jsonschema import Draft202012Validator
from langchain_core.tools import BaseTool


def normalize_schema(tool: BaseTool, schema: dict | None = None,
                     *, forbid_extra: bool = False) -> dict:
    if schema is None:
        definition = tool.args_schema
        schema = (definition if isinstance(definition, dict)
                  else tool.get_input_schema().model_json_schema())
    schema = deepcopy(schema)
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError('工具参数必须是 object JSON Schema')
    nodes = []
    def resolve(ref):
        target = schema
        try:
            for part in ref[2:].split('/') if ref != '#' else []:
                key = part.replace('~1', '/').replace('~0', '~')
                target = target[int(key)] if isinstance(target, list) else target[key]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError('Schema 本地引用不存在') from exc
        return target

    def check(value):
        if isinstance(value, dict):
            nodes.append(value)
            if '$id' in value or '$dynamicRef' in value:
                raise ValueError('Schema 只允许本地静态引用')
            if '$ref' in value:
                ref = value['$ref']
                if not isinstance(ref, str) or not (ref == '#' or ref.startswith('#/')):
                    raise ValueError('Schema 只允许本地引用')
                resolve(ref)
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)
    check(schema)
    if forbid_extra:
        schema['additionalProperties'] = False
    Draft202012Validator.check_schema(schema)
    # Only edges validating the same instance can recurse without consuming data.
    # properties/items refs recurse into children and remain valid for finite trees.
    active, complete = set(), set()
    def check_cycle(node):
        if not isinstance(node, dict) or id(node) in complete:
            return
        identity = id(node)
        if identity in active:
            raise ValueError('Schema 存在不消耗参数层级的引用循环')
        active.add(identity)
        if '$ref' in node:
            check_cycle(resolve(node['$ref']))
        for key in ('allOf', 'anyOf', 'oneOf'):
            for child in node.get(key, []):
                check_cycle(child)
        for key in ('not', 'if', 'then', 'else'):
            check_cycle(node.get(key))
        for child in node.get('dependentSchemas', {}).values():
            check_cycle(child)
        active.remove(identity)
        complete.add(identity)
    for node in nodes:
        check_cycle(node)
    return schema


def validate_arguments(schema: dict, args: object) -> list[dict]:
    return [{'path': '.'.join(map(str, e.absolute_path)) or '$',
             'rule': e.validator, 'message': e.message}
            for e in sorted(Draft202012Validator(schema).iter_errors(args),
                            key=lambda e: str(list(e.absolute_path)))]
