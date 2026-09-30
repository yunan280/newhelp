import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.knowledge.store import ChunkSnapshot, KnowledgeChunk
from mewhelp.main import app


def document_chunk(root, filename="manual.md"):
    value = root.resolve().as_posix()
    if os.name == "nt":
        value = value.casefold()
    corpus = hashlib.sha256(value.encode()).hexdigest()[:16]
    return ChunkSnapshot(
        9007199254740993,
        "正文",
        "问题",
        "蓝牙5.3",
        f"corpus:{corpus}/{filename}::耳机 / 蓝牙",
        "参数",
        "耳机",
        "manual",
        False,
        "a" * 64,
    )


def test_valid_markdown_document_returns_original_and_section(tmp_path):
    from mewhelp.knowledge.sources import read_document_source

    text = "# 耳机\n## 蓝牙\n蓝牙5.3。\n"
    (tmp_path / "manual.md").write_text(text, encoding="utf-8-sig")
    result = read_document_source(document_chunk(tmp_path), tmp_path)
    assert result.filename == "manual.md" and result.markdown == text
    assert result.section_path.endswith("耳机 / 蓝牙")


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.md",
        "..\\secret.md",
        "/secret.md",
        "C:/secret.md",
        "C:secret.md",
        "\\\\server\\share\\secret.md",
        "manual.txt",
    ],
)
def test_invalid_file_paths_are_rejected(tmp_path, filename):
    from mewhelp.knowledge.sources import read_document_source

    (tmp_path / "manual.txt").write_text("不能返回")
    assert read_document_source(document_chunk(tmp_path, filename), tmp_path) is None


def test_wrong_corpus_and_faq_do_not_have_document_source(tmp_path):
    from mewhelp.knowledge.sources import read_document_source

    (tmp_path / "manual.md").write_text("正文")
    chunk = document_chunk(tmp_path)
    assert (
        read_document_source(
            replace(chunk, section_path="corpus:" + "0" * 16 + "/manual.md::蓝牙"), tmp_path
        )
        is None
    )
    assert (
        read_document_source(
            replace(chunk, section_path="FAQ / 蓝牙", content_type="faq"), tmp_path
        )
        is None
    )


def test_symlink_outside_root_is_rejected(tmp_path):
    from mewhelp.knowledge.sources import read_document_source

    root = tmp_path / "docs"
    root.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("不能泄漏")
    link = root / "manual.md"
    try:
        link.symlink_to(outside)
        filename = "manual.md"
    except OSError:
        if os.name != "nt":
            raise
        # NTFS directory junctions exercise the same resolve containment without admin rights.
        external = tmp_path / "external"
        external.mkdir()
        (external / "manual.md").write_text("不能泄漏")
        junction = root / "escape"
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0
        filename = "escape/manual.md"
    assert read_document_source(document_chunk(root, filename), root) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows path case behavior")
def test_windows_root_and_relative_path_case_are_supported(tmp_path):
    from mewhelp.knowledge.sources import read_document_source

    (tmp_path / "Manual.md").write_text("原文", encoding="utf-8")
    assert (
        read_document_source(
            document_chunk(tmp_path, "manual.MD"), Path(str(tmp_path).swapcase())
        ).markdown
        == "原文"
    )


@pytest.fixture
def sources_api(tmp_path):
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    chunk = document_chunk(tmp_path)
    (tmp_path / "manual.md").write_text("# 耳机\n蓝牙5.3", encoding="utf-8")
    with factory() as session:
        session.add(
            KnowledgeChunk(
                id=chunk.id,
                category=chunk.category,
                questions=chunk.questions,
                answer=chunk.answer,
                section_path=chunk.section_path,
                product_category=chunk.product_category,
                content_type="manual",
                vectorize_status="done",
                vector_id=str(chunk.id),
                is_key_clause=False,
            )
        )
        session.commit()
    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(
        factory, lambda _: None, docs_root=tmp_path
    )
    try:
        yield TestClient(app), factory, chunk
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_current_source_id_is_string_and_document_is_selected_only_by_id(sources_api):
    client, _, chunk = sources_api
    response = client.get(f"/api/kb/chunks/{chunk.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == "9007199254740993" and "number" not in body
    assert body["answer"] == "蓝牙5.3" and body["product_category"] == "耳机"
    raw = client.get(f"/api/kb/chunks/{chunk.id}/document?file=C:/secret.md")
    assert raw.status_code == 200 and raw.json()["filename"] == "manual.md"
    assert "C:/" not in raw.text


@pytest.mark.parametrize("state", ["missing", "pending", "deleting", "invalid_vector_id"])
def test_unpublished_or_missing_sources_are_not_exposed(sources_api, state):
    client, factory, chunk = sources_api
    assert client.get(f"/api/kb/chunks/{chunk.id}").status_code == 200
    with factory() as session:
        row = session.get(KnowledgeChunk, chunk.id)
        if state == "missing":
            session.delete(row)
        elif state == "pending":
            row.vectorize_status = "pending"
        elif state == "deleting":
            row.section_path = "__deleting__::" + row.section_path
        else:
            row.vector_id = "different"
        session.commit()
    for suffix in ("", "/document"):
        response = client.get(f"/api/kb/chunks/{chunk.id}{suffix}")
        assert response.status_code == 404 and str(factory) not in response.text


def test_manual_entry_retains_optional_product_category(sources_api):
    client, _, _ = sources_api
    response = client.post(
        "/api/kb/entries",
        json={
            "entry_id": "7aa3c113-d42f-40a6-bd50-e0a6e0c3ea0d",
            "category": "参数",
            "product_category": "耳机",
            "questions": "型号",
            "answer": "规格",
        },
    )
    assert response.status_code == 200 and response.json()["product_category"] == "耳机"
    bad = client.post(
        "/api/kb/entries",
        json={
            "entry_id": "7aa3c113-d42f-40a6-bd50-e0a6e0c3ea0d",
            "category": "参数",
            "product_category": " ",
            "questions": "型号",
            "answer": "规格",
        },
    )
    assert bad.status_code == 422
