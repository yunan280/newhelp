"""FastAPI 应用入口。"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mewhelp.ch01.api import router as ch01_router
from mewhelp.ch02.api import router as ch02_router
from mewhelp.ch05.api import router as ch05_router
from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.runtime import open_runtime
from mewhelp.ch06.api import router as ch06_router
from mewhelp.ch07.api import router as ch07_router
from mewhelp.ch08.api import router as ch08_router
from mewhelp.knowledge.api import RuntimeDep
from mewhelp.knowledge.api import router as kb_router
from mewhelp.knowledge.sources import read_published_chunk

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from mewhelp.ch07.observability import configure_context_logging
    from mewhelp.db.engine import SessionLocal
    configure_context_logging(Path('log/app.log'))
    from mewhelp.ch08.config import ToolSystemSettings
    from mewhelp.ch08.runtime import create_tool_runtime
    tools = create_tool_runtime(SessionLocal, settings=ToolSystemSettings(
        Path(os.environ.get('CH08_TOOL_CONFIG', 'config/ch08-tools.json')),
        Path(os.environ.get('CH08_PLUGIN_DIR', 'tool_plugins'))))
    from mewhelp.ch09.config import Ch09Settings
    from mewhelp.ch09.runtime import open_ch09_runtime
    async with open_ch09_runtime(SessionLocal, settings=Ch09Settings()) as ch09:
        async with open_runtime(SessionLocal, settings=Ch05Settings(), tool_runtime=tools,
                                observation_runtime=ch09.observation_runtime) as runtime:
            app.state.ch05_runtime = runtime
            app.state.tool_runtime = tools
            app.state.ch09_runtime = ch09
            await ch09.attach_workflow(runtime)
            try:
                yield
            finally:
                del app.state.ch05_runtime
                del app.state.tool_runtime
                del app.state.ch09_runtime
                await tools.aclose()


app = FastAPI(title="MewHelp", version="0.1.0", lifespan=lifespan)
app.include_router(ch01_router)
app.include_router(ch02_router)
app.include_router(ch05_router)
app.include_router(ch06_router)
app.include_router(ch07_router)
app.include_router(ch08_router)
app.include_router(kb_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/kb")
async def knowledge_entry_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "kb.html")


@app.get("/kb/source/{chunk_id}")
def knowledge_source_page(chunk_id: int, runtime: RuntimeDep) -> FileResponse:
    if read_published_chunk(runtime.session_factory, chunk_id) is None:
        raise HTTPException(status_code=404, detail="来源不存在或尚未发布")
    source_page = STATIC_DIR / "source.html"
    if not source_page.is_file():
        raise HTTPException(status_code=404, detail="来源页面暂不可用")
    return FileResponse(source_page)
