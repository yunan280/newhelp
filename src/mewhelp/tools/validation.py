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
    def check(value):
        if isinstance(value, dict):
            if '$id' in value or '$dynamicRef' in value:
                raise ValueError('Schema 只允许本地静态引用')
            if '$ref' in value:
                ref = value['$ref']
                if not isinstance(ref, str) or not (ref == '#' or ref.startswith('#/')):
                    raise ValueError('Schema 只允许本地引用')
                target = schema
                try:
                    for part in ref[2:].split('/') if ref != '#' else []:
                        key = part.replace('~1', '/').replace('~0', '~')
                        target = target[int(key)] if isinstance(target, list) else target[key]
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise ValueError('Schema 本地引用不存在') from exc
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)
    check(schema)
    if forbid_extra:
        schema['additionalProperties'] = False
    Draft202012Validator.check_schema(schema)
    return schema


def validate_arguments(schema: dict, args: object) -> list[dict]:
    return [{'path': '.'.join(map(str, e.absolute_path)) or '$',
             'rule': e.validator, 'message': e.message}
            for e in sorted(Draft202012Validator(schema).iter_errors(args),
                            key=lambda e: str(list(e.absolute_path)))]
