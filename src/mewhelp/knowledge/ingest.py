"""将 FAQ 与 Markdown 转换为权威原文行，提交后由补偿流程向量化。"""

import hashlib
import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mewhelp.db.models import Faq

from .splitter import split_markdown
from .store import KnowledgeChunk, KnowledgeDraft, put_chunk, source_id


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ingest_faq(session: Session) -> int:
    rows = session.scalars(select(Faq).order_by(Faq.id)).all()
    for row in rows:
        put_chunk(
            session,
            KnowledgeDraft(f"faq:{row.id}", row.category, row.question, row.answer, "", "faq"),
        )
    return len(rows)


def ingest_documents(session: Session, root: Path, *, deleted_ids: list[int] | None = None,
                     include_paths: set[str] | None = None) -> int:
    if not root.is_dir():
        raise FileNotFoundError(f"知识文档目录不存在：{root}")
    # DDL 没有独立来源列；把规范化根目录的短哈希放在主键来源和溯源路径中。
    # 每次只清理当前目录的旧块，其他知识文档目录互不影响。
    canonical_root = root.resolve().as_posix()
    if os.name == "nt":
        canonical_root = canonical_root.casefold()
    corpus = _key(canonical_root)[:16]
    scope = f"corpus:{corpus}/"
    count = 0
    all_seen: set[int] = set()
    links: list[tuple[int, int | None, int | None]] = []
    for path in sorted(root.rglob("*.md")):
        source = path.relative_to(root).as_posix()
        if include_paths is not None and source not in include_paths:
            continue
        prefix = "doc:" + corpus + ":" + _key(source) + ":"
        markdown = path.read_text(encoding="utf-8-sig")
        header = re.match(r"\A\s*<!--\s*product-category:\s*([^\r\n]*?)\s*-->\s*", markdown)
        product_category = None
        if header:
            product_category = header.group(1).strip()
            if not product_category or len(product_category) > 128:
                raise ValueError(f"Invalid product-category header in {source}")
            markdown = markdown[header.end():]
        for chunk in split_markdown(markdown):
            source_key = prefix + _key(chunk.key)
            all_seen.add(source_id(source_key))
            put_chunk(
                session,
                KnowledgeDraft(
                    source_key,
                    chunk.category,
                    chunk.questions,
                    chunk.answer,
                    (scope + source + "::" + chunk.section_path)[:512],
                    "policy" if "policy" in path.name else "manual" if "manual" in path.name else chunk.content_type,
                    chunk.is_key_clause,
                    product_category=product_category,
                ),
            )
            links.append((
                source_id(source_key),
                source_id(prefix + _key(chunk.prev_key)) if chunk.prev_key else None,
                source_id(prefix + _key(chunk.next_key)) if chunk.next_key else None,
            ))
            count += 1
    for row_id, prev_id, next_id in links:
        row = session.get(KnowledgeChunk, row_id)
        row.prev_chunk_id = prev_id
        row.next_chunk_id = next_id
    stale = session.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.section_path.startswith(scope, autoescape=True)
        | KnowledgeChunk.section_path.startswith("__deleting__::" + scope, autoescape=True)
    )).all()
    for row in stale:
        source = row.section_path.removeprefix("__deleting__::").removeprefix(scope).split("::", 1)[0]
        if include_paths is not None and source not in include_paths:
            continue
        if row.id not in all_seen:
            # 先提交不可检索状态，再删 Milvus 向量；若中断，下一次仍能发现此行。
            row.vectorize_status = "pending"
            row.vector_id = None
            if not row.section_path.startswith("__deleting__::"):
                row.section_path = "__deleting__::" + row.section_path[:498]
            if deleted_ids is not None:
                deleted_ids.append(row.id)
    return count
