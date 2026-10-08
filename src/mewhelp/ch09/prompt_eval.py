"""Frozen labeled prompt checks with actual provider output and usage."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from mewhelp.ch05.config import get_ch05_model
from mewhelp.ch05.evidence import EvidenceEnvelope
from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import source_dtos
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk

from .generation import KNOWLEDGE_ANSWER_SYSTEM, generate_knowledge_answer
from .snapshots import snapshot_result


def load_generation_samples(path):
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError("duplicate prompt sample id")
        seen.add(row["id"])
        if (
            type(row.get("answerable")) is not bool
            or not row.get("question", "").strip()
            or not row.get("label_basis", "").strip()
            or not row.get("evidence")
            or any(not item.get("question") or not item.get("answer") for item in row["evidence"])
        ):
            raise ValueError("invalid labeled generation sample")
    if len(rows) < 12:
        raise ValueError("at least12 labeled generation samples required")
    return rows


async def run_generation_samples(samples, output, *, model_factory=get_ch05_model):
    rows = load_generation_samples(samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("use a new output path; never overwrite actual prompt evidence")
    manifest = {
        "kind": "generation_prompt_validation_only",
        "sample_count": len(rows),
        "dataset_hash": hashlib.sha256(samples.read_bytes()).hexdigest(),
        "prompt_hash": hashlib.sha256(KNOWLEDGE_ANSWER_SYSTEM.encode()).hexdigest(),
        "model": get_settings().llm_model,
        "labels_frozen_before_run": True,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failures = 0
    with output.open("x", encoding="utf-8") as target:
        for sample in rows:
            captured = {}

            def factory(*args, record=captured, **kwargs):
                base = model_factory(*args, **kwargs)

                class Proxy:
                    model_name = getattr(base, "model_name", manifest["model"])

                    def with_structured_output(self, schema, **options):
                        upstream = base.with_structured_output(schema, **options)

                        class Capture:
                            async def ainvoke(self, messages):
                                record["messages"] = [m.model_dump(mode="json") for m in messages]
                                envelope = await upstream.ainvoke(messages)
                                record.update(envelope)
                                return envelope

                        return Capture()

                return Proxy()

            chunks = [
                snapshot_chunk(
                    KnowledgeChunk(
                        id=i,
                        category="标注样例",
                        questions=item["question"],
                        answer=item["answer"],
                        section_path=f"样例/{sample['id']}/{i}",
                        content_type="faq",
                        is_key_clause=False,
                    )
                )
                for i, item in enumerate(sample["evidence"], 1)
            ]
            # Fixed labeled evidence exercises generation only; no artificial score claims retrieval quality.
            evidence = RetrievalResult(chunks, [RankedChunk(c, None) for c in chunks])
            snapshot = snapshot_result(evidence, query=sample["question"], filters=SearchFilters())
            envelope = EvidenceEnvelope(
                sources=source_dtos(evidence),
                scores=[None] * len(chunks),
                threshold=0.0,
                context_budget=32000,
                retrieved_chunks=snapshot.model_dump(mode="json"),
            )
            ctx = WorkflowContext(None, factory, None, AgentLimits())
            state = {
                "question": sample["question"],
                "resolved_question": sample["question"],
                "route": "knowledge",
                "evidence": envelope.model_dump(),
                "gate": {"passed": True},
                "entry_point": "cli",
                "started_at": time.time(),
                "usage": {},
                "calls": {},
            }
            started = time.perf_counter()
            try:
                result = await generate_knowledge_answer(
                    state, ctx, lambda x: None, record_pool=False
                )
                parsed = captured.get("parsed")
                parsed_json = parsed.model_dump(mode="json") if parsed is not None else None
                passed = (
                    bool(parsed_json)
                    and parsed_json.get("answerable") == sample["answerable"]
                    and result.get("refused") == (not sample["answerable"])
                    and all(t in result["answer"] for t in sample.get("must_include", []))
                    and all(t not in result["answer"] for t in sample.get("must_not_include", []))
                )
                error = None
            except Exception as exc:  # noqa: BLE001 — each actual failure is saved, then exits nonzero
                result, parsed_json, passed, error = (
                    None,
                    None,
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            raw = captured.get("raw")
            row = {
                "id": sample["id"],
                "expected": sample,
                "parsed": parsed_json,
                "result": result,
                "raw_message": raw.model_dump(mode="json") if raw is not None else None,
                "parsing_error": str(captured.get("parsing_error") or ""),
                "messages": captured.get("messages"),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "passed": bool(passed),
                "error": error,
            }
            target.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            target.flush()
            failures += not passed
            print(
                json.dumps(
                    {"id": sample["id"], "passed": bool(passed), "error": error}, ensure_ascii=False
                ),
                flush=True,
            )
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["generation", "flywheel"], required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = run_generation_samples if args.suite == "generation" else run_flywheel_samples
    raise SystemExit(1 if asyncio.run(run(args.samples, args.output)) else 0)


def load_flywheel_samples(root):
    result = {}
    for suite, minimum in [("normalization", 12), ("dedup", 16)]:
        rows = [
            json.loads(line)
            for line in (root / f"{suite}-samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(rows) < minimum or len({r.get("id") for r in rows}) != len(rows):
            raise ValueError("标注样例数量不足或ID重复")
        for row in rows:
            if not row.get("id") or not row.get("label_basis"):
                raise ValueError("样例缺少标注依据")
            if suite == "normalization":
                if not row.get("original") or not row.get("must_include") or "snapshot" not in row:
                    raise ValueError("标准化样例不完整")
            elif (
                not row.get("question")
                or not row.get("candidates")
                or len(row["candidates"]) > 8
                or "expected_ids" not in row
                or not set(row["expected_ids"]) <= {i for i, _ in row["candidates"]}
            ):
                raise ValueError("查重标注候选或期望ID不合法")
        result[suite] = rows
    return result


async def run_flywheel_samples(samples, output, *, model_factory=get_ch05_model):
    from .contracts import EvidenceSnapshot
    from .dedup import DEDUP_SYSTEM, find_equivalent
    from .normalization import NORMALIZE_SYSTEM, normalize_gap

    rows = load_flywheel_samples(samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("use new output path; never overwrite real prompt evidence")
    manifest = {
        "kind": "flywheel_prompt_validation_only",
        "counts": {k: len(v) for k, v in rows.items()},
        "dataset_hashes": {
            k: hashlib.sha256((samples / f"{k}-samples.jsonl").read_bytes()).hexdigest()
            for k in rows
        },
        "prompt_hashes": {
            k: hashlib.sha256(v.encode()).hexdigest()
            for k, v in [("normalization", NORMALIZE_SYSTEM), ("dedup", DEDUP_SYSTEM)]
        },
        "model": get_settings().llm_model,
        "labels_frozen_before_run": True,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failures = 0
    with output.open("x", encoding="utf-8") as target:
        for suite, items in rows.items():
            for sample in items:
                captured = {}
                base = model_factory(1024, streaming=False)

                class Model:
                    def with_structured_output(self, schema, bound_base=base, record=captured, **options):
                        upstream = bound_base.with_structured_output(schema, **options)

                        class Capture:
                            async def ainvoke(self, messages):
                                record["messages"] = [m.model_dump(mode="json") for m in messages]
                                result = await upstream.ainvoke(messages)
                                record.update(result)
                                return result

                        return Capture()

                started = time.perf_counter()
                try:
                    if suite == "normalization":
                        snapshot = (
                            EvidenceSnapshot.model_validate(sample["snapshot"])
                            if sample["snapshot"]
                            else None
                        )
                        parsed = await normalize_gap(
                            sample["original"], snapshot=snapshot, model=Model()
                        )
                        passed = all(t in parsed.question for t in sample["must_include"])
                    else:
                        parsed = await find_equivalent(
                            sample["question"], candidates=sample["candidates"], model=Model()
                        )
                        passed = set(parsed.matched_ids) == set(sample["expected_ids"])
                    result = parsed.model_dump(mode="json")
                    error = None
                except Exception as exc:  # noqa: BLE001 — persist every real sample failure
                    result = None
                    passed = False
                    error = f"{type(exc).__name__}: {exc}"
                raw = captured.get("raw")
                row = {
                    "suite": suite,
                    "id": sample["id"],
                    "expected": sample,
                    "parsed": result,
                    "raw_message": raw.model_dump(mode="json") if raw is not None else None,
                    "messages": captured.get("messages"),
                    "error": error,
                    "passed": passed,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
                target.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                target.flush()
                failures += not passed
                print(
                    json.dumps(
                        {"id": sample["id"], "passed": passed, "error": error}, ensure_ascii=False
                    ),
                    flush=True,
                )
    return failures


if __name__ == "__main__":
    main()
