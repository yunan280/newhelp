"""Actual provider counts; Langfuse's inferred counts are never substituted."""
import asyncio
import json
import math
from datetime import UTC, datetime

from langchain_core.messages import AIMessage
from langfuse.langchain import CallbackHandler

METRICS = ("candidate_recall50", "candidate_mrr50", "final_recall5", "final_recall10",
           "final_mrr10", "faithfulness")


class ProviderUsageCallback(CallbackHandler):
    # SDK 4.17.0's conversion hook is inspected and pinned. This only adds provenance
    # to the exported output, never to messages sent to the model.
    def _convert_message_to_dict(self, message):
        output = super()._convert_message_to_dict(message)
        if isinstance(message, AIMessage):
            raw = message.usage_metadata or message.response_metadata.get("token_usage") or {}
            output["_mewhelp_provider_usage"] = {
                target: raw[source]
                for target, sources in (
                    ("input_tokens", ("input_tokens", "prompt_tokens")),
                    ("output_tokens", ("output_tokens", "completion_tokens")),
                )
                for source in sources
                if source in raw and type(raw[source]) is int and raw[source] >= 0
            }
        return output


def object_json(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def utc_window(from_time, to_time):
    if from_time.tzinfo is None or to_time.tzinfo is None:
        raise ValueError("查询时间必须带时区")
    start, end = from_time.astimezone(UTC), to_time.astimezone(UTC)
    if not start < end:
        raise ValueError("查询起始时间必须早于结束时间")
    return start, end


async def observations(client, **filters):
    cursor, cursors, ids = None, set(), set()
    while True:
        response = await asyncio.to_thread(
            client.api.observations.get_many,
            fields="core,basic,time,metadata,io,model,usage", limit=100, cursor=cursor,
            **filters,
        )
        for item in response.data:
            if item.id not in ids:
                ids.add(item.id)
                yield item
        cursor = response.meta.cursor
        if not cursor:
            return
        if cursor in cursors:
            raise RuntimeError("Langfuse游标重复，读取未完成")
        cursors.add(cursor)


async def read_token_costs(*, client, from_time: datetime, to_time: datetime) -> dict:
    start, end = utc_window(from_time, to_time)
    groups, traces = {}, set()
    async for root in observations(client, is_root_observation=True,
                                   from_start_time=start, to_start_time=end):
        metadata = object_json(root.metadata)
        if metadata.get("trace_kind") != "chat" or not root.trace_id:
            continue
        if root.start_time.tzinfo is None:
            raise ValueError("Langfuse观测时间缺少时区")
        if not start <= root.start_time.astimezone(UTC) < end:
            continue
        if root.trace_id in traces:
            raise RuntimeError("同一chat trace存在多个请求根，无法准确归属")
        traces.add(root.trace_id)
        intent = metadata.get("intent") or "未分类"
        item = groups.setdefault(intent, {
            "intent": intent, "request_count": 0, "generation_count": 0, "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "unknown_usage_count": 0,
            "pending_request_count": 0})
        item["request_count"] += 1
        if metadata.get("status") == "running":
            item["pending_request_count"] += 1
        async for generation in observations(client, trace_id=root.trace_id, type="GENERATION"):
            if generation.type != "GENERATION":
                continue
            item["generation_count"] += 1
            usage = object_json(object_json(generation.output).get("_mewhelp_provider_usage"))
            complete = True
            for name in ("input_tokens", "output_tokens"):
                value = usage.get(name)
                if type(value) is int and value >= 0:
                    item[name] += value
                else:
                    complete = False
            item["unknown_usage_count"] += int(not complete)
    for item in groups.values():
        item["total_tokens"] = item["input_tokens"] + item["output_tokens"]
        n = item["generation_count"]
        item["usage_coverage"] = (n - item["unknown_usage_count"]) / n if n else 1.0
        item["per_request_mean"] = (None if item["unknown_usage_count"]
                                    or item["pending_request_count"] else
                                    item["total_tokens"] / item["request_count"])
    return {"from": start.isoformat(), "to": end.isoformat(), "window": "[from,to)",
            "read_at": datetime.now(UTC).isoformat(),
            "usage_source": "generation output: actual inclusive provider counts",
            "items": sorted(groups.values(), key=lambda i: (-i["total_tokens"], i["intent"]))}


def eval_trend(rows) -> list[dict]:
    result, previous = [], None
    for row in sorted(rows, key=lambda r: (r.created_at, r.id)):
        metrics = row.metrics
        config = metrics.get("_meta", {}).get("comparison_hash")
        old = previous.metrics if previous else {}
        same = bool(config and previous and previous.dataset_size == row.dataset_size
                    and config == old.get("_meta", {}).get("comparison_hash"))
        changes = {}
        for name in METRICS:
            before, after = old.get(name), metrics.get(name)
            n = metrics.get(name + "_N")
            comparable = bool(same and type(n) is int and n > 0
                              and n == old.get(name + "_N")
                              and type(before) in (int, float) and math.isfinite(before)
                              and type(after) in (int, float) and math.isfinite(after))
            changes[name] = {"comparable": comparable,
                             "delta": after - before if comparable else None}
        result.append({"id": str(row.id), "triggered_by": row.triggered_by,
                       "dataset_size": row.dataset_size, "metrics": metrics,
                       "created_at": row.created_at.replace(tzinfo=UTC).isoformat(),
                       "previous_id": str(previous.id) if previous else None, "changes": changes})
        previous = row
    return result
