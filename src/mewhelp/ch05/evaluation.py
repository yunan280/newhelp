"""Frozen labels, real model outputs and nonzero failure status."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from mewhelp.config import get_settings

from .config import get_ch05_model
from .intent import classify_intent


def evaluation_hash(dataset: Path) -> str:
    digest = hashlib.sha256(dataset.read_bytes())
    for name in ["prompts.py", "intent.py", "config.py"]:
        digest.update(Path(__file__).with_name(name).read_bytes())
    digest.update(get_settings().llm_model.encode())
    return digest.hexdigest()


async def evaluate_prompts(
    dataset_dir: Path, outdir: Path, *, part: str, classifier=classify_intent
) -> int:
    if part != "intents":
        raise NotImplementedError("decisions evaluation arrives with the ReAct nodes")
    outdir.mkdir(parents=True, exist_ok=False)
    dataset = dataset_dir / "intents.jsonl"
    model = get_ch05_model(128)
    results = []
    for line in dataset.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        row = {**case, "request_model": get_settings().llm_model}
        try:
            actual = await classifier(case["question"], model=model)
            row.update(
                actual=actual.intent,
                raw=actual.raw,
                origin=actual.origin,
                response_model=actual.response_model,
                usage=actual.usage.model_dump(),
                calls=0 if actual.origin == "local" else 1,
                passed=actual.intent == case["expected"],
            )
        except Exception as exc:  # noqa: BLE001 -- every provider failure is a failed eval case
            row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        results.append(row)
        (outdir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    summary = {
        "part": part,
        "hash": evaluation_hash(dataset),
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "service_errors": sum("error" in r for r in results),
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return int(summary["passed"] != summary["total"] or not results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--part", choices=["intents", "decisions", "all"], required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(evaluate_prompts(args.dataset, args.outdir, part=args.part)))
