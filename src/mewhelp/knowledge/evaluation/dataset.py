"""Validate frozen annotations before any indexing or model calls."""

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.store import KnowledgeDraft, source_id

QUERY_TYPES = ("model_exact", "colloquial", "synonym", "multi_constraint", "unanswerable")
DIFFICULTIES = ("easy", "medium", "hard")


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    query_type: str
    difficulty: str
    split: str
    filters: SearchFilters
    relevant_chunk_ids: set[int]
    reference_answer: str
    key_facts: list[str]
    should_refuse: bool
    label_basis: str


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _records(path: Path) -> list[dict]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid JSONL: {path.name}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("records must be objects")
    return rows


def _nonblank(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonblank string")
    return value


def _id(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError("IDs must be positive decimal strings")
    number = int(value)
    if number >= 2**63:
        raise ValueError("ID outside supported BIGINT range")
    return number


def load_dataset(
    corpus_path: Path, queries_path: Path
) -> tuple[list[KnowledgeDraft], list[EvalCase]]:
    corpus, by_id, source_keys = [], {}, set()
    corpus_fields = {
        "source_key",
        "chunk_id",
        "category",
        "product_category",
        "questions",
        "answer",
        "section_path",
        "content_type",
        "is_key_clause",
    }
    for raw in _records(corpus_path):
        if set(raw) != corpus_fields:
            raise ValueError("unexpected corpus fields")
        chunk_id = _id(raw["chunk_id"])
        fields = {key: value for key, value in raw.items() if key != "chunk_id"}
        for name in (
            "source_key",
            "category",
            "questions",
            "answer",
            "section_path",
            "content_type",
        ):
            _nonblank(fields[name])
        if (
            not fields["source_key"].startswith("ch04-eval:")
            or chunk_id != source_id(fields["source_key"])
            or fields["source_key"] in source_keys
            or chunk_id in by_id
            or type(fields["is_key_clause"]) is not bool
        ):
            raise ValueError("invalid/duplicate source identity or clause flag")
        if fields["product_category"] is not None:
            _nonblank(fields["product_category"])
        draft = KnowledgeDraft(**fields)
        source_keys.add(draft.source_key)
        by_id[chunk_id] = draft
        corpus.append(draft)
    if len(corpus) < 80:
        raise ValueError("corpus requires at least 80 original chunks")
    cases, seen = [], set()
    case_fields = set(EvalCase.__dataclass_fields__)
    for raw in _records(queries_path):
        if set(raw) != case_fields:
            raise ValueError("unexpected case fields")
        for name in ("id", "question", "reference_answer", "label_basis"):
            _nonblank(raw[name])
        if (
            raw["id"] in seen
            or raw["query_type"] not in QUERY_TYPES
            or raw["difficulty"] not in DIFFICULTIES
            or raw["split"] not in ("calibration", "test")
            or type(raw["should_refuse"]) is not bool
        ):
            raise ValueError("invalid/duplicate case or label")
        if not isinstance(raw["relevant_chunk_ids"], list) or not isinstance(
            raw["key_facts"], list
        ):
            raise ValueError("relevant IDs and facts must be lists")  # noqa: TRY004 — JSON 数据统一校验错误
        relevant = {_id(item) for item in raw["relevant_chunk_ids"]}
        if len(relevant) != len(raw["relevant_chunk_ids"]) or not relevant <= by_id.keys():
            raise ValueError("duplicate/unknown ground truth chunk")
        filters = SearchFilters.model_validate(raw["filters"])
        if any(
            any(
                getattr(by_id[item], key) != value
                for key, value in filters.model_dump(exclude_none=True).items()
            )
            for item in relevant
        ):
            raise ValueError("filters exclude ground truth")
        if raw["should_refuse"]:
            if relevant or raw["key_facts"]:
                raise ValueError("unanswerable questions cannot have fact ground truth")
        else:
            if not relevant or not raw["key_facts"]:
                raise ValueError("answerable cases need ground truth and facts")
            evidence = "\n".join(by_id[item].answer for item in relevant)
            if any(_nonblank(fact) not in evidence for fact in raw["key_facts"]):
                raise ValueError("key fact not present in annotated original evidence")
        cases.append(EvalCase(**{**raw, "filters": filters, "relevant_chunk_ids": relevant}))
        seen.add(raw["id"])
    counts = Counter((case.query_type, case.difficulty, case.split) for case in cases)
    for query_type in QUERY_TYPES:
        for difficulty in DIFFICULTIES:
            calibration = 2 if difficulty == "hard" else 1
            if (
                counts[query_type, difficulty, "calibration"] != calibration
                or counts[query_type, difficulty, "test"] != 4 - calibration
            ):
                raise ValueError(
                    "expected 60 cases with frozen 20/40 balanced type/difficulty split"
                )
    if len(cases) != 60:
        raise ValueError("expected exactly 60 cases")
    return corpus, cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", type=Path, required=True)
    args = parser.parse_args()
    cp, qp = args.validate / "corpus.jsonl", args.validate / "queries.jsonl"
    corpus, cases = load_dataset(cp, qp)
    print(
        json.dumps(
            {
                "corpus": len(corpus),
                "cases": len(cases),
                "calibration": sum(case.split == "calibration" for case in cases),
                "test": sum(case.split == "test" for case in cases),
                "corpus_hash": file_hash(cp),
                "query_hash": file_hash(qp),
                "validated": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
