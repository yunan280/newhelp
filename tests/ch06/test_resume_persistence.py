from tests.ch06.conftest import open_selection_runtime
from tests.ch06.test_selection import request, waiting


async def test_file_reopen_resumes_same_thread(session_factory, tmp_path):
    from mewhelp.ch06.selection import pending_selection, resume_order
    path = tmp_path / "durable.sqlite"
    async with open_selection_runtime(session_factory, path) as runtime:
        before = await waiting(runtime, "reopen")
    async with open_selection_runtime(session_factory, path) as runtime:
        recovered = await pending_selection(runtime, "reopen", "demo-user")
        assert recovered.order_selection == before.order_selection
        after = await resume_order(runtime, request(before))
        assert after.order.order_id == "1001" and after.calls == before.calls
        assert await pending_selection(runtime, "unknown", "demo-user") is None
