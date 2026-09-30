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
    business_only: bool = Field(
        default=False, description="本轮所有请求是否都是业务操作，不含知识事实"
    )


@dataclass(frozen=True)
class QueryUnderstanding:
    original: str
    canonical: str
    bm25_query: str
    route: Route
    diagnostics: list[str]


_MODEL = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*\d[A-Za-z0-9_-]*", re.IGNORECASE
)
_NUMBER = re.compile(
    r"\d+(?:\.\d+)?(?:\s*(?:[A-Za-zµμ°℃℉%][A-Za-z0-9µμ°℃℉%/·^²³_-]*|小时|分钟|毫秒|千克|厘米|毫米|摄氏度|元|天|秒|年|米|瓦|伏|安))?",
    re.IGNORECASE,
)
_CONDITIONS = (
    "不支持",
    "不能",
    "不得",
    "不要",
    "不超过",
    "未拆封",
    "未开封",
    "拆封",
    "开封",
    "没有",
    "不",
    "未",
    "只有",
    "仅",
    "必须",
    "以内",
    "以上",
    "以下",
    "超过",
    "至少",
    "最多",
    "满",
    "如果",
    "同时",
    "且",
    "才能",
)
_GREETING = re.compile(
    r"(?:你好|您好|嗨|哈喽|hello|hi|谢谢|多谢|感谢|再见|拜拜)[呀啊哦呢！!。,.，\s]*", re.IGNORECASE
)
_BUSINESS = re.compile(
    r"订单|物流轨迹|快递到哪|快递在哪|查物流|库存|多少钱|价格|创建.*工单|转人工|人工工单"
)
_QUALIFIER = re.compile(
    "|".join(_CONDITIONS) + r"|保修期|质保期|期内|期间|除非|否则|之前|之后|前提|情况下|但|并且|或者"
)
_KNOWLEDGE_REQUEST = re.compile(
    r"政策|规则|退换|退货|退款|保修|质保|发票|运费|包邮|到账|承诺|保证|蓝牙版本|技术参数|功率|电压|续航|分辨率|处理器|容量|协议|防水|支持.*[吗么嘛?？]"
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def protected_fragments(question: str) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *_MODEL.findall(question),
                *_NUMBER.findall(question),
                *(token for token in _CONDITIONS if token in question),
            ]
        )
    )


def requires_knowledge(question: str) -> bool:
    return _KNOWLEDGE_REQUEST.search(question) is not None


def _sensitive_clauses(question: str) -> list[str]:
    """Opaque numeric/conditional clauses preserve units and modifier attachment together."""
    clauses = []
    for clause in re.split(r"[。！？!?；;\n]", question):
        models = [match.span() for match in _MODEL.finditer(clause)]
        outside_model = any(
            not any(start <= match.start() < end for start, end in models)
            for match in _NUMBER.finditer(clause)
        )
        if outside_model or _QUALIFIER.search(clause):
            clauses.append(_compact(clause))
    return clauses


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
    missing = [
        item for item in protected_fragments(original) if _compact(item) not in _compact(canonical)
    ]
    # Prevent a model substitution whose old identifier merely prefixes the new one.
    original_models = {_compact(item) for item in _MODEL.findall(original)}
    canonical_models = {_compact(item) for item in _MODEL.findall(canonical)}
    original_numbers = {_compact(item) for item in _NUMBER.findall(original)}
    canonical_numbers = {_compact(item) for item in _NUMBER.findall(canonical)}
    if (
        missing
        or original_models != canonical_models
        or original_numbers != canonical_numbers
        or _sensitive_clauses(original) != _sensitive_clauses(canonical)
    ):
        return fallback("protected_information_changed")
    route = parsed.route
    diagnostics = []
    if route == "greeting" and not _GREETING.fullmatch(original.strip()):
        route, diagnostics = "knowledge", ["uncertain_fact_route"]
    if route == "business" and (
        not parsed.business_only or not _BUSINESS.search(original) or requires_knowledge(original)
    ):
        route, diagnostics = "knowledge", ["uncertain_fact_route"]
    synonyms = []
    for item in dict.fromkeys(item.strip() for item in parsed.synonyms):
        if (
            {_compact(part) for part in _MODEL.findall(item)} - original_models
            or {_compact(part) for part in _NUMBER.findall(item)} - original_numbers
            or any(token in item and token not in original for token in _CONDITIONS)
            or _sensitive_clauses(item)
            and _compact(item) not in _compact(original)
        ):
            diagnostics.append("unsafe_synonym_removed")
        else:
            synonyms.append(item)
    return QueryUnderstanding(
        original, canonical, " ".join([canonical, *synonyms]), route, diagnostics
    )


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
            envelope = await upstream.ainvoke(
                [("system", QUERY_SYSTEM), ("human", sample["question"])]
            )
            parsed = envelope.get("parsed")
            raw = parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
            result = validate_normalization(sample["question"], raw)
            valid = (
                result.route == sample["route"]
                and all(
                    _compact(item) in _compact(result.canonical)
                    for item in sample.get("preserve", [])
                )
                and (not sample.get("needs_rewrite") or result.canonical != result.original)
            )
            failed += not valid
            record = {
                "id": sample["id"],
                "question": sample["question"],
                "raw": raw,
                "raw_message": envelope["raw"].model_dump(mode="json"),
                "parsing_error": str(envelope.get("parsing_error") or ""),
                "result": asdict(result),
                "passed": valid,
            }
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                json.dumps(
                    {"id": sample["id"], "passed": valid, "result": asdict(result)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    print(f"failed={failed}; results={output}")
    return failed
