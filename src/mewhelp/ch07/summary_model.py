import json
import re
import time
from collections.abc import Sequence
from typing import Protocol

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

from mewhelp.ch05.config import get_ch05_model

from .budget import check_window
from .config import BudgetProfile, ContextSettings
from .prompts import EMPTY_SUMMARY, SUMMARY_SYSTEM
from .tokens import wire_messages
from .types import SummaryResult


class SummaryModel(Protocol):
    async def summarize(self, *, batch: Sequence[AnyMessage], background: str) -> SummaryResult: ...


def summary_messages(batch: Sequence[AnyMessage], *, background: str) -> list[AnyMessage]:
    return [SystemMessage(SUMMARY_SYSTEM), HumanMessage(json.dumps({
        'old_background_only': background, 'new_batch': wire_messages(batch)}, ensure_ascii=False))]


def numeric_facts(text: str) -> set[str]:
    return set(re.findall(r'\d+(?:[.-]\d+)*', text))


def business_identifiers(batch) -> set[str]:
    text = json.dumps(wire_messages(batch), ensure_ascii=False)
    identifiers = set(re.findall(r'(?<!\d)1\d{10}(?!\d)', text))
    for message in batch:
        if message.type == 'human':
            identifiers.update(re.findall(r'订单(?:号)?[：:为是\s]*([A-Za-z0-9-]+)', str(message.content)))
        for call in getattr(message, 'tool_calls', []):
            for key in ('order_id', 'phone', 'mobile'):
                if call.get('args', {}).get(key):
                    identifiers.add(str(call['args'][key]))
        if message.type == 'tool':
            try:
                payload = json.loads(str(message.content))
            except ValueError:
                continue
            if isinstance(payload, dict):
                for key in ('order_id', 'phone', 'mobile'):
                    if payload.get(key):
                        identifiers.add(str(payload[key]))
    return identifiers


def validate_summary(result: SummaryResult, batch) -> None:
    content = result.content.strip()
    source = json.dumps(wire_messages(batch), ensure_ascii=False)
    if numeric_facts(content) - numeric_facts(source):
        raise ValueError('summary invented numeric facts')
    if any(identifier not in content for identifier in business_identifiers(batch)):
        raise ValueError('summary dropped business identifier')
    if content != EMPTY_SUMMARY and not 30 <= len(content) <= 200:
        raise ValueError(f'summary length {len(content)} outside 30..200')


class ChatSummaryModel:
    def __init__(self, settings: ContextSettings, profile: BudgetProfile, model_factory=get_ch05_model):
        self.settings = settings
        self.profile = profile
        self.model_factory = model_factory

    async def summarize(self, *, batch, background) -> SummaryResult:
        started = time.perf_counter()
        correction = None
        usage = {'input_tokens': 0, 'output_tokens': 0}
        for attempt in range(2):
            messages = summary_messages(batch, background=background)
            if correction:
                messages.append(HumanMessage(correction))
            check_window(messages, [], settings=self.settings, profile=self.profile,
                         output_tokens=256, remaining_tool_calls=0)
            response = await self.model_factory(256, temperature=0.0).ainvoke(messages)
            if response.tool_calls:
                raise ValueError('summary model unexpectedly called tools')
            content = str(response.content).strip()
            for key in usage:
                usage[key] += (response.usage_metadata or {}).get(key, 0)
            result = SummaryResult(content, dict(usage), round((time.perf_counter() - started) * 1000))
            try:
                validate_summary(result, batch)
                return result
            except ValueError as error:
                if attempt or not str(error).startswith('summary length'):
                    raise
                correction = (f'上一候选长度为{len(content)}个字符，长度不合格。重新从原始new_batch提炼，'
                    '输出30到200个字符。按本批事实交代用户明确诉求、客服实际表达及'
                    '本批问题是否已解决。只展开已出现的事实，不加未提及字段，不编处理结果。')
