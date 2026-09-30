"""按 Markdown 章节和完整语义单元切分，不把半句话送去向量化。"""

import re
from dataclasses import dataclass, replace

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE = re.compile(r".*?(?:[。！？.!?](?:[”’」』])?|$)", re.DOTALL)


@dataclass(frozen=True)
class DocumentChunk:
    key: str
    category: str
    questions: str
    answer: str
    section_path: str
    content_type: str
    is_key_clause: bool = False
    prev_key: str | None = None
    next_key: str | None = None
    oversize: bool = False


def _units(lines: list[str]) -> list[tuple[str, str]]:
    """表格按行分组，普通文字按句分组；每个单元始终完整。"""
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|\-]+\|$", lines[i + 1].strip()):
            header = line + "\n" + lines[i + 1].strip()
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                result.append(("table", header + "\n" + lines[i].strip()))
                i += 1
            continue
        if line.startswith("|"):
            result.append(("text", line))
            i += 1
            continue
        paragraph: list[str] = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("|"):
            paragraph.append(lines[i].strip())
            i += 1
        joined = "\n".join(paragraph)
        for match in _SENTENCE.finditer(joined):
            sentence = match.group().strip()
            if sentence:
                result.append(("text", sentence))
    return result


def _pack(units: list[tuple[str, str]], max_chars: int, overlap_chars: int) -> list[tuple[str, str, bool]]:
    packed: list[tuple[str, str, bool]] = []
    current: list[str] = []
    kind = "text"
    for unit_kind, unit in units:
        header, row = unit.rsplit("\n", 1) if unit_kind == "table" else ("", "")
        addition = row if current and kind == unit_kind == "table" else unit
        projected = (
            len("\n".join([*current, addition]))
            if unit_kind == kind == "table"
            else len("".join(current)) + len(addition)
        )
        if current and (unit_kind != kind or projected > max_chars):
            value = "\n".join(current) if kind == "table" else "".join(current)
            packed.append((kind, value, len(value) > max_chars))
            overlap: list[str] = []
            if kind == unit_kind == "text" and overlap_chars:
                for old in reversed(current):
                    if len("".join(overlap)) + len(old) > min(overlap_chars, max_chars - len(unit)):
                        break
                    overlap.insert(0, old)
            current = overlap
        if unit_kind == "table":
            if current:
                current.append(row)
            else:
                current = [header, row]
        else:
            current.append(unit)
        kind = unit_kind
    if current:
        value = "\n".join(current) if kind == "table" else "".join(current)
        packed.append((kind, value, len(value) > max_chars))
    return packed


def split_markdown(markdown: str, *, max_chars: int = 900, overlap_chars: int = 120) -> list[DocumentChunk]:
    """标题划分章节，长章节按完整句和表格行切；重叠只复制完整句。"""
    if max_chars <= 0 or overlap_chars < 0:
        raise ValueError("切分长度必须为正，重叠不能为负")
    overlap_chars = min(overlap_chars, max_chars - 1)
    headings: list[tuple[int, str]] = []
    sections: list[tuple[list[str], list[str]]] = []
    lines: list[str] = []
    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match:
            if lines:
                sections.append(([title for _, title in headings], lines))
                lines = []
            depth = len(match.group(1))
            while headings and headings[-1][0] >= depth:
                headings.pop()
            headings.append((depth, match.group(2)))
        else:
            lines.append(line)
    if lines:
        sections.append(([title for _, title in headings], lines))

    chunks: list[DocumentChunk] = []
    occurrences: dict[str, int] = {}
    for path, body in sections:
        if not path:
            path = ["文档"]
        section_path = " / ".join(path)
        occurrence = occurrences.get(section_path, 0)
        occurrences[section_path] = occurrence + 1
        is_key_clause = any(line.strip() == "<!-- key-clause -->" for line in body)
        parts = _pack(_units([line for line in body if line.strip() != "<!-- key-clause -->"]), max_chars, overlap_chars)
        for index, (kind, answer, oversize) in enumerate(parts):
            key = f"{section_path}#{occurrence}:{index}"
            chunks.append(
                DocumentChunk(
                    key=key,
                    category=" / ".join(path[:-1]),
                    questions=path[-1],
                    answer=answer,
                    section_path=section_path,
                    content_type="table" if kind == "table" else "document",
                    is_key_clause=is_key_clause,
                    oversize=oversize,
                )
            )
    return [
        replace(
            chunk,
            prev_key=chunks[i - 1].key if i else None,
            next_key=chunks[i + 1].key if i + 1 < len(chunks) else None,
        )
        for i, chunk in enumerate(chunks)
    ]
