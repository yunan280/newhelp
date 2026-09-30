"""Published original text and documents resolved strictly inside the corpus root."""

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from sqlalchemy.orm import Session

from .store import ChunkSnapshot, KnowledgeChunk, snapshot_chunk


@dataclass(frozen=True)
class DocumentSource:
    filename: str
    markdown: str
    section_path: str


def read_published_chunk(
    session_factory: Callable[[], Session], chunk_id: int
) -> ChunkSnapshot | None:
    if not 0 < chunk_id < 2**63:
        return None
    with session_factory() as session:
        row = session.get(KnowledgeChunk, chunk_id)
        if (
            row is None
            or row.vectorize_status != "done"
            or row.vector_id != str(row.id)
            or (row.section_path or "").startswith("__deleting__::")
        ):
            return None
        return snapshot_chunk(row)


def read_document_source(chunk: ChunkSnapshot, root: Path) -> DocumentSource | None:
    match = re.fullmatch(r"corpus:([0-9a-f]{16})/(.+?)::(.*)", chunk.section_path or "")
    if match is None or chunk.content_type not in ("policy", "manual"):
        return None
    try:
        resolved_root = root.resolve()
        if not resolved_root.is_dir():
            return None
        canonical_root = resolved_root.as_posix()
        if os.name == "nt":
            canonical_root = canonical_root.casefold()
        corpus = hashlib.sha256(canonical_root.encode("utf-8")).hexdigest()[:16]
        if match[1] != corpus:
            return None
        relative = match[2].replace("\\", "/")
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or PureWindowsPath(relative).drive
            or any(part in ("..", ".") for part in relative.split("/"))
            or path.suffix.casefold() != ".md"
        ):
            return None
        source = (resolved_root / relative).resolve()
        if not source.is_relative_to(resolved_root) or not source.is_file():
            return None
        return DocumentSource(relative, source.read_text(encoding="utf-8-sig"), chunk.section_path)
    except (OSError, ValueError):
        return None
