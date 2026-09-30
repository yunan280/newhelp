"""应用入口的冒烟测试。"""

from fastapi.testclient import TestClient

from mewhelp.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_index_serves_the_chat_page():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_kb_page_opens_with_entry_fields():
    resp = TestClient(app).get("/kb")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "知识库录入" in resp.text
    assert "分类" in resp.text
    assert "问法" in resp.text
    assert "答案" in resp.text


def test_static_mount_serves_the_chat_page_assets():
    """`app.mount("/static", ...)` 光靠另外三条测试钉不住 —— 把它删掉,它们照样全绿。

    只断言 200 与 content-type,**不断言页面内容**:`static/index.html` 是占位页,
    内容归 Task 11,这里断言内容会在 Task 11 干活那天变红,而错不在它。
    """
    resp = TestClient(app).get("/static/index.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_openapi_lists_the_ch01_routes():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/ch01/chat/stream" in paths
    assert "/ch01/extract" in paths


def test_openapi_lists_chunk_sources_and_viewer_route():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/api/kb/chunks/{chunk_id}" in paths
    assert "/api/kb/chunks/{chunk_id}/document" in paths
    assert "/kb/source/{chunk_id}" in paths
