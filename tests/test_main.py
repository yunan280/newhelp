"""应用入口的冒烟测试。"""

from fastapi.testclient import TestClient

from mewhelp.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_index_serves_the_chat_page():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_openapi_lists_the_ch01_routes():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/ch01/chat/stream" in paths
    assert "/ch01/extract" in paths
