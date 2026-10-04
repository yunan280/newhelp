import json
import math
import unicodedata
from collections.abc import Sequence

from langchain_core.messages import AnyMessage, convert_to_openai_messages

from .config import BudgetProfile


def estimate_text(text: str, *, profile: BudgetProfile) -> int:
    # One calibration package owns both this conversion and all budget reserves.
    wide = sum(unicodedata.east_asian_width(char) in ('W', 'F') for char in text)
    return math.ceil(wide * profile.cjk_tokens_per_char
                     + (len(text) - wide) / profile.ascii_chars_per_token)


def wire_messages(messages: Sequence[AnyMessage]) -> list[dict]:
    return convert_to_openai_messages(list(messages), include_id=False)


def estimate_messages(messages: Sequence[AnyMessage], *, profile: BudgetProfile) -> int:
    payload = json.dumps(wire_messages(messages), ensure_ascii=False, separators=(',', ':'))
    return estimate_text(payload, profile=profile) + 6 * len(messages) + 3


def estimate_request(messages: Sequence[AnyMessage], tools: Sequence[dict],
                     *, profile: BudgetProfile) -> int:
    schemas = json.dumps(list(tools), ensure_ascii=False, separators=(',', ':')) if tools else ''
    return estimate_messages(messages, profile=profile) + estimate_text(schemas, profile=profile)
