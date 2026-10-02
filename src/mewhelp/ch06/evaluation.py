"""Immutable labels and independently graded, fail-closed real-model evaluations."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from pydantic import ValidationError

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.limits import TokenUsage
from mewhelp.ch05.schemas import ExpansionOutput, IntentOutput, OrderAssessment, UnderstandingOutput
from mewhelp.config import get_settings

from .config import Ch06Settings, RouterCalibration

PARTS = ("intents", "understanding", "multiturn", "expansion", "assessment")
DATASETS = (*PARTS, "calibration", "policy-calibration")


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def _read_cases(path: Path) -> list[dict]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not rows:
        raise ValueError(f"empty dataset: {path.name}")
    ids = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row["id"] in ids
            or not isinstance(row.get("expected"), dict)
            or not isinstance(row.get("question"), str)
            or not row["question"].strip()
            or row.get("split") not in {"acceptance", "calibration"}
        ):
            raise ValueError(f"invalid/missing/duplicate labeled case: {path.name}")
        expected_split = (
            "calibration" if path.stem in {"calibration", "policy-calibration"} else "acceptance"
        )
        if row["split"] != expected_split:
            raise ValueError("calibration and acceptance splits must remain separate")
        ids.add(row["id"])
    return rows


def _manifest(dataset: Path) -> dict:
    files = {}
    for part in DATASETS:
        path = dataset / (part + ".jsonl")
        if path.exists():
            rows = _read_cases(path)
            files[path.name] = {
                "sha256": hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest(),
                "count": len(rows),
                "split": rows[0]["split"],
            }
    if not files:
        raise ValueError("no labeled dataset")
    return {"version": 1, "files": files, "dataset_hash": _digest(files)}


def freeze_dataset(dataset: Path) -> dict:
    target = dataset / "freeze.json"
    try:
        manifest = _manifest(dataset)
    except (ValueError, OSError) as error:
        if target.exists():
            raise ValueError("frozen dataset is invalid or missing") from error
        raise
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != manifest:
            raise ValueError("frozen labels changed; do not refreeze acceptance to hide failures")
    else:
        _write_json(target, manifest)
    return manifest


def verify_dataset(dataset: Path) -> dict:
    if not (dataset / "freeze.json").exists():
        raise ValueError("frozen manifest is missing")
    return freeze_dataset(dataset)


def model_hash(
    settings: Ch06Settings | None = None, *, classifier_max_tokens: int | None = None
) -> str:
    settings = settings or Ch06Settings()
    return _digest(
        {
            "primary": settings.primary_model,
            "small": settings.small_model,
            "chat": get_settings().llm_model,
            "upstream": get_settings().openai_base_url,
            "protocol": "json_object/thinking_disabled/no_tools",
            "router_output_limits": {
                key: getattr(settings, key)
                for key in (
                    "understanding_max_tokens",
                    "expansion_max_tokens",
                    "assessment_max_tokens",
                )
            },
            "classifier_output_limit": classifier_max_tokens
            if classifier_max_tokens is not None
            else Ch05Settings().classifier_max_tokens,
        }
    )


def runtime_hash(
    part: str,
    *,
    mode="primary",
    calibration_path: Path | None = None,
    settings: Ch06Settings | None = None,
) -> str:
    paths = [
        Path(__file__),
        Path(__file__).with_name("config.py"),
        Path(__file__).with_name("structured.py"),
        Path(__file__).parents[1] / "ch05/schemas.py",
        Path(__file__).with_name("prompts.py"),
    ]
    names = {
        "intents": ["../ch05/intent.py"],
        "understanding": ["understanding.py"],
        "multiturn": ["understanding.py", "../ch05/intent.py", "../ch05/workflow.py"],
        "expansion": ["expansion.py"],
        "assessment": ["assessment.py", "../ch05/agent.py"],
    }
    paths += [Path(__file__).parent / name for name in names.get(part, [])]
    sources = {
        str(path.relative_to(Path(__file__).parents[1])): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        if path.exists()
        else "missing"
        for path in paths
    }
    calibration = (
        hashlib.sha256(calibration_path.read_bytes()).hexdigest() if calibration_path else None
    )
    return _digest(
        {
            "part": part,
            "sources": sources,
            "model_hash": model_hash(settings),
            "mode": mode,
            "calibration": calibration,
        }
    )


def _schema(part):
    return {
        "intents": IntentOutput,
        "understanding": UnderstandingOutput,
        "multiturn": IntentOutput,
        "expansion": ExpansionOutput,
        "assessment": OrderAssessment,
    }[part]


def grade_case(part: str, case: dict, result: dict | None) -> list[str]:
    if not isinstance(result, dict) or not isinstance(result.get("actual"), dict):
        return ["missing actual output"]
    actual, expected = result["actual"], case["expected"]
    failures = []
    if part in {"intents", "multiturn"}:
        try:
            IntentOutput.model_validate(
                actual
                if part == "intents"
                else {key: actual.get(key) for key in ("intent", "confidence")}
            )
        except ValidationError:
            failures.append("invalid intent/confidence contract")
    if part in {"intents", "multiturn"} and actual.get("intent") != expected["intent"]:
        failures.append("incorrect intent")
    if part in {"understanding", "multiturn"}:
        for key in ("scope", "trusted_order_id", "route"):
            if key in expected and actual.get(key) != expected[key]:
                failures.append(f"incorrect {key}")
        question = actual.get("question", "")
        if "exact_question" in expected and question != expected["exact_question"]:
            failures.append("complete question was rewritten")
        for fragment in expected.get("required_fragments", []):
            if fragment not in question:
                failures.append(f"missing question fragment: {fragment}")
        for forbidden in expected.get("forbidden_facts", []):
            if forbidden in question:
                failures.append(f"invented question fact: {forbidden}")
    if part == "expansion":
        if actual.get("expanded") != expected["expanded"]:
            failures.append("incorrect expansion scope")
        if expected["expanded"]:
            try:
                ExpansionOutput.model_validate({"queries": actual.get("queries")})
            except ValidationError:
                failures.append("invalid expansion contract")
        elif actual.get("queries"):
            failures.append("simple FAQ must skip expansion")
        for forbidden in expected.get("forbidden_facts", []):
            if forbidden in " ".join(actual.get("queries", [])):
                failures.append("expansion invented fact")
    if part == "assessment":
        try:
            OrderAssessment.model_validate(actual)
        except ValidationError:
            failures.append("invalid assessment contract")
        if actual.get("verdict") != expected["verdict"]:
            failures.append("incorrect eligibility verdict")
        text = str(actual.get("explanation", "")) + " ".join(actual.get("missing_facts", []))
        if any(question in text for question in expected.get("forbidden_questions", [])):
            failures.append("agent asks for refund form reason")
    # A returned failure is evidence, but a returned passed flag is never authoritative.
    failures += result.get("failures", [])
    return failures


def _json_stats(part: str, result: dict) -> tuple[int, int, int]:
    raw = (
        result.get("classification_raw", [])
        if part == "multiturn"
        else result.get("raw_responses", [])
    )
    if not raw:
        return 0, 0, 0
    schema = _schema(part)

    def parses(item):
        try:
            schema.model_validate_json(item["content"])
            return 1
        except (ValueError, KeyError, TypeError):
            return 0

    return 1, parses(raw[0]), parses(raw[-1])


async def evaluate(
    dataset: Path,
    outdir: Path,
    *,
    parts: tuple[str, ...],
    mode: str,
    calibration_path: Path | None,
    evaluators: dict | None = None,
) -> int:
    outdir.mkdir(parents=True, exist_ok=False)
    summary = {
        "mode": mode,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "service_errors": 0,
        "integrity_errors": [],
        "json_cases": 0,
        "original_json_parsed": 0,
        "final_json_parsed": 0,
        "manual_semantic_review_required": [],
        "usage": TokenUsage().model_dump(),
        "calls": {},
        "classification_cases": 0,
        "escalations": 0,
        "intent_metrics": {},
        "parts": {},
    }
    rows = []
    try:
        manifest = verify_dataset(dataset)
        if (
            mode not in {"primary", "cascade"}
            or not parts
            or len(set(parts)) != len(parts)
            or any(part not in PARTS for part in parts)
        ):
            raise ValueError("invalid requested evaluation parts/mode")
        for part in parts:
            if part + ".jsonl" not in manifest["files"]:
                raise ValueError(f"frozen part missing: {part}")
        summary["dataset_hash"] = manifest["dataset_hash"]
        for part in parts:
            fingerprint = runtime_hash(part, mode=mode, calibration_path=calibration_path)
            cases = _read_cases(dataset / (part + ".jsonl"))
            part_summary = {"hash": fingerprint, "total": len(cases), "passed": 0, "failed": 0}
            summary["parts"][part] = part_summary
            for case in cases:
                row = {"part": part, "case": case, "hash": fingerprint}
                started = time.perf_counter()
                try:
                    function = (evaluators or {}).get(part, default_evaluator(part))
                    result = await function(case, mode=mode, calibration_path=calibration_path)
                    failures = grade_case(part, case, result)
                    if result and result.get("hash", fingerprint) != fingerprint:
                        failures.append("output hash does not match runtime")
                    row.update({"result": result, "failures": failures, "passed": not failures})
                    stats = _json_stats(part, result or {})
                    summary["json_cases"] += stats[0]
                    summary["original_json_parsed"] += stats[1]
                    summary["final_json_parsed"] += stats[2]
                    if isinstance(result, dict):
                        summary["usage"] = (
                            TokenUsage.model_validate(summary["usage"])
                            .plus(TokenUsage.model_validate(result.get("usage", {})))
                            .model_dump()
                        )
                        for key, count in result.get("calls", {}).items():
                            summary["calls"][key] = summary["calls"].get(key, 0) + count
                        if part in {"intents", "multiturn"}:
                            summary["classification_cases"] += 1
                            summary["escalations"] += int(result.get("escalated", False))
                            category = case["expected"]["intent"]
                            metrics = summary["intent_metrics"].setdefault(
                                category, {"total": 0, "correct": 0}
                            )
                            metrics["total"] += 1
                            metrics["correct"] += result.get("actual", {}).get("intent") == category
                except Exception as error:  # noqa: BLE001 - retain every failed case; never skip SDK/evaluator failures
                    row.update(
                        {
                            "passed": False,
                            "failures": ["service/evaluator error"],
                            "error": type(error).__name__ + ": " + str(error)[:500],
                        }
                    )
                    summary["service_errors"] += 1
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                key = "passed" if row["passed"] else "failed"
                summary[key] += 1
                summary["total"] += 1
                part_summary[key] += 1
                rows.append(row)
                with (outdir / "results.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if part in {"understanding", "multiturn", "expansion"}:
                summary["manual_semantic_review_required"].append(part)
            if runtime_hash(part, mode=mode, calibration_path=calibration_path) != fingerprint:
                raise ValueError("runtime changed during evaluation")
        verify_dataset(dataset)
    except (ValueError, OSError, KeyError) as error:
        summary["integrity_errors"].append(str(error))
    denominator = summary["json_cases"]
    summary["original_json_parse_rate"] = (
        summary["original_json_parsed"] / denominator if denominator else None
    )
    summary["final_json_parse_rate"] = (
        summary["final_json_parsed"] / denominator if denominator else None
    )
    summary["complete"] = (
        not summary["integrity_errors"]
        and summary["total"] == sum(p["total"] for p in summary["parts"].values())
        and summary["total"] > 0
    )
    _write_json(outdir / "summary.json", summary)
    return int(not summary["complete"] or bool(summary["failed"]))


def default_evaluator(part):
    async def run(case, *, mode, calibration_path):
        from mewhelp.ch05.intent import classify_intent, route_intent
        from mewhelp.ch05.state import WorkflowContext

        from .understanding import understand_query

        settings = Ch06Settings(
            cascade_enabled=mode == "cascade", calibration_path=calibration_path
        )
        context = WorkflowContext(
            lambda: None,
            lambda: None,
            lambda: None,
            Ch05Settings().limits(),
            router_settings=settings,
        )
        state = {"usage": {}, "calls": {}, "started_at": time.time()}
        if part == "intents":
            result = await classify_intent(case["question"], context=context, state=state)
            return result.evaluation_result()
        if part in {"understanding", "multiturn"}:
            understood = await understand_query(
                case["question"],
                case.get("history", []),
                case.get("trusted_entities", []),
                context=context,
                state=state,
            )
            result = understood.evaluation_result()
            if part == "multiturn":
                state.update(understood.patch())
                classified = await classify_intent(
                    understood.question, context=context, state=state
                )
                result["actual"].update(
                    intent=classified.intent,
                    confidence=classified.confidence,
                    route=route_intent(classified.intent, understood.scope),
                )
                result["classification_raw"] = classified.raw_responses
                result["usage"] = classified.usage.model_dump()
                result["calls"] = classified.calls
                result["origin"] = classified.origin
                result["escalated"] = classified.escalated
                result["failures"] = [classified.control_error] if classified.control_error else []
            return result
        if part == "expansion":
            from .expansion import expand_queries

            state.update(
                question=case["question"],
                resolved_question=case["question"],
                intent=case["intent"],
                scope=case["scope"],
                order=case["order"],
            )
            return (await expand_queries(state, context)).evaluation_result()
        from .assessment import assess_order

        state.update(
            question=case["question"],
            resolved_question=case["question"],
            intent=case["intent"],
            order=case["order"],
            user_facts=case.get("user_facts", {}),
            evidence={
                "sources": [{"number": 1, "questions": "演示政策", "answer": case["policy"]}]
            },
        )
        return (await assess_order(state, context)).evaluation_result()

    return run


async def calibrate_router(dataset: Path, outdir: Path) -> int:
    """Run the separate split; choose conservative thresholds, never tune on acceptance."""
    from mewhelp.ch05.intent import classifier_messages
    from mewhelp.ch05.state import WorkflowContext

    from .structured import invoke_json

    outdir.mkdir(parents=True, exist_ok=False)
    manifest = verify_dataset(dataset)
    cases = _read_cases(dataset / "calibration.jsonl")
    settings = Ch06Settings()
    context = WorkflowContext(
        lambda: None, lambda: None, lambda: None, Ch05Settings().limits(), router_settings=settings
    )
    rows, errors = [], []
    for case in cases:
        row = {"case": case}
        try:
            for key, model in [
                ("primary", settings.primary_model),
                ("small", settings.small_model),
            ]:
                if model is None:
                    continue
                call = await invoke_json(
                    context,
                    {},
                    purpose="classifier",
                    messages=classifier_messages(case["question"]),
                    schema=IntentOutput,
                    model_name=model,
                    output_tokens=context.limits.classifier_max_tokens,
                )
                row[key] = {
                    "actual": call.parsed.model_dump() if call.parsed else None,
                    "raw_responses": call.raw_responses,
                    "usage": call.usage.model_dump(),
                    "calls": call.calls,
                }
                if call.error:
                    errors.append(case["id"] + ": " + key + ": " + call.error)
        except Exception as error:  # noqa: BLE001 - failed calibration cases invalidate the whole run
            errors.append(case["id"] + ": " + type(error).__name__ + ": " + str(error)[:300])
        rows.append(row)
    (outdir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )
    if errors:
        _write_json(
            outdir / "summary.json", {"complete": False, "errors": errors, "total": len(rows)}
        )
        return 1
    selected, candidates = select_router_thresholds(rows, has_small=bool(settings.small_model))
    calibration = RouterCalibration(
        model_hash=model_hash(),
        understanding_hash=runtime_hash("understanding"),
        intent_hash=runtime_hash("intents"),
        dataset_hash=manifest["dataset_hash"],
        intent_min_confidence=selected[3],
        cascade_upgrade_threshold=selected[4],
        sample_count=len(cases),
    )
    _write_json(outdir / "router.json", calibration.model_dump())
    _write_json(
        outdir / "summary.json",
        {
            "complete": True,
            "total": len(rows),
            "selected": selected,
            "grid": candidates,
            "dataset_hash": manifest["dataset_hash"],
        },
    )
    return 0


def select_router_thresholds(rows: list[dict], *, has_small: bool) -> tuple[tuple, list[tuple]]:
    candidates = []
    for minimum in (0.5, 0.6, 0.7, 0.8):
        for upgrade in (0.7, 0.8, 0.9):
            failures = leaks = escalations = 0
            for row in rows:
                actual = row["small" if has_small else "primary"]["actual"]
                if has_small and actual["confidence"] < upgrade:
                    actual = row["primary"]["actual"]
                    escalations += 1
                intent = actual["intent"] if actual["confidence"] >= minimum else "其他"
                expected = row["case"]["expected"]["intent"]
                failures += intent != expected
                leaks += expected in {"退款退货", "售后"} and intent != expected
            candidates.append((leaks, failures, escalations, minimum, upgrade))
    return min(candidates), candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["freeze", "calibrate", "run"])
    parser.add_argument("--dataset", type=Path, default=Path("eval/ch06"))
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--mode", choices=["primary", "cascade"], default="primary")
    parser.add_argument("--parts", default=",".join(PARTS))
    parser.add_argument("--calibration", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        print(json.dumps(freeze_dataset(args.dataset), ensure_ascii=False, indent=2))
        return 0
    if args.outdir is None:
        parser.error("--outdir required")
    if args.command == "calibrate":
        return asyncio.run(calibrate_router(args.dataset, args.outdir))
    return asyncio.run(
        evaluate(
            args.dataset,
            args.outdir,
            parts=tuple(args.parts.split(",")),
            mode=args.mode,
            calibration_path=args.calibration,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
