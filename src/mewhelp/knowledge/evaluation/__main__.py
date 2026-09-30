import argparse
import asyncio
import json
from pathlib import Path

from .dataset import file_hash, load_dataset
from .runner import calibrate_run, prepare_run, run_comparison


async def main():
    parser = argparse.ArgumentParser(description="Ch04 isolated real-model evaluation")
    parser.add_argument("command", choices=("prepare", "calibrate", "compare"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    cp, qp = args.dataset / "corpus.jsonl", args.dataset / "queries.jsonl"
    corpus, cases = load_dataset(cp, qp)
    frozen = json.loads((args.dataset / "freeze.json").read_text(encoding="utf-8"))
    hashes = {"corpus_hash": file_hash(cp), "query_hash": file_hash(qp)}
    if any(frozen.get(key) != value for key, value in hashes.items()):
        raise ValueError("dataset changed after annotation freeze")
    kwargs = {
        "workdir": args.workdir,
        "collection": "ch04_eval_" + args.run_id,
        "run_id": args.run_id,
    }
    if args.command == "prepare":
        await prepare_run(corpus, cases, **kwargs, dataset_hashes=hashes)
    elif args.command == "calibrate":
        await calibrate_run(corpus, cases, **kwargs)
    else:
        path = await run_comparison(corpus, cases, **kwargs)
        print(f"report: {path}")
        report = json.loads((args.workdir / "summary.json").read_text(encoding="utf-8"))
        if report["errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
