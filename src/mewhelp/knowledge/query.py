"""Conservative single-turn normalization; retrieval-side synonyms only."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .prompts import QUERY_SYSTEM

Route = Literal["knowledge", "business", "greeting"]


class Normalization(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    canonical: str = Field(min_length=1, max_length=4096)
    synonyms: list[str] = Field(max_length=5)
    route: Route


@dataclass(frozen=True)
class QueryUnderstanding:
    original: str
    canonical: str
    bm25_query: str
    route: Route
    diagnostics: list[str]


_MODEL = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*\d[A-Za-z0-9_-]*", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:\s*(?:W|V|A|mAh|Ah|Hz|kHz|GHz|GB|TB|mm|cm|kg|g|%|元|天|小时|分钟|年|米))?", re.IGNORECASE)
_CONDITIONS = ("不支持", "不能", "不得", "不要", "不超过", "未拆封", "未开封", "拆封", "开封", "没有", "不", "未", "只有", "仅", "必须", "以内", "以上", "以下", "超过", "至少", "最多", "满", "如果", "同时", "且", "才能")
_GREETING = re.compile(r"(?:你好|您好|嗨|哈喽|hello|hi|谢谢|多谢|感谢|再见|拜拜)[呀啊哦呢！!。,.，\s]*", re.IGNORECASE)
_BUSINESS = re.compile(r"订单|物流轨迹|快递到哪|快递在哪|查物流|库存|多少钱|价格|创建.*工单|转人工|人工工单")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def protected_fragments(question: str) -> list[str]:
    return list(dict.fromkeys([
        *_MODEL.findall(question), *_NUMBER.findall(question),
        *(token for token in _CONDITIONS if token in question),
    ]))


def validate_normalization(original: str, raw: dict | None) -> QueryUnderstanding:
    def fallback(reason: str) -> QueryUnderstanding:
        return QueryUnderstanding(original, original, original, "knowledge", [reason])

    try:
        parsed = Normalization.model_validate(raw)
    except ValidationError:
        return fallback("invalid_normalization")
    canonical = parsed.canonical.strip()
    if not canonical or any(not item.strip() or len(item) > 32 for item in parsed.synonyms):
        return fallback("invalid_normalization")
    missing = [item for item in protected_fragments(original) if _compact(item) not in _compact(canonical)]
    # Prevent a model substitution whose old identifier merely prefixes the new one.
    original_models = {_compact(item) for item in _MODEL.findall(original)}
    canonical_models = {_compact(item) for item in _MODEL.findall(canonical)}
    original_numbers = {_compact(item) for item in _NUMBER.findall(original)}
    canonical_numbers = {_compact(item) for item in _NUMBER.findall(canonical)}
    if missing or original_models != canonical_models or original_numbers != canonical_numbers:
        return fallback("protected_information_changed")
    route = parsed.route
    diagnostics = []
    if route == "greeting" and not _GREETING.fullmatch(original.strip()):
        route, diagnostics = "knowledge", ["uncertain_fact_route"]
    if route == "business" and not _BUSINESS.search(original):
        route, diagnostics = "knowledge", ["uncertain_fact_route"]
    synonyms = []
    for item in dict.fromkeys(item.strip() for item in parsed.synonyms):
        if ({_compact(part) for part in _MODEL.findall(item)} - original_models
                or {_compact(part) for part in _NUMBER.findall(item)} - original_numbers):
            diagnostics.append("unsafe_synonym_removed")
        else:
            synonyms.append(item)
    return QueryUnderstanding(original, canonical, " ".join([canonical, *synonyms]), route, diagnostics)


async def understand_query(question: str, *, model: Any | None = None) -> QueryUnderstanding:
    if model is None:
        from mewhelp.llm import get_structured_model

        model = get_structured_model(Normalization, include_raw=True)
    envelope = await model.ainvoke([("system", QUERY_SYSTEM), ("human", question)])
    parsed = envelope.get("parsed") if isinstance(envelope, dict) else None
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    return validate_normalization(question, parsed)


async def check_query_samples(path: Path) -> int:
    from mewhelp.llm import get_structured_model

    upstream = get_structured_model(Normalization, include_raw=True)
    failed = 0
    output = path.with_name("query-understanding-results.jsonl")
    with output.open("w", encoding="utf-8") as target:
        for line in path.read_text(encoding="utf-8").splitlines():
            sample = json.loads(line)
            envelope = await upstream.ainvoke([("system", QUERY_SYSTEM), ("human", sample["question"])])
            parsed = envelope.get("parsed")
            raw = parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
            result = validate_normalization(sample["question"], raw)
            valid = result.route == sample["route"] and all(
                _compact(item) in _compact(result.canonical) for item in sample.get("preserve", [])
            ) and (not sample.get("needs_rewrite") or result.canonical != result.original)
            failed += not valid
            record = {"id": sample["id"], "question": sample["question"], "raw": raw,
                      "raw_message": envelope["raw"].model_dump(mode="json"),
                      "parsing_error": str(envelope.get("parsing_error") or ""),
                      "result": asdict(result), "passed": valid}
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps({"id": sample["id"], "passed": valid, "result": asdict(result)}, ensure_ascii=False), flush=True)
    print(f"failed={failed}; results={output}")
    return failed
