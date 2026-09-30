"""FastAPI 应用入口。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mewhelp.ch01.api import router as ch01_router
from mewhelp.ch02.api import router as ch02_router
from mewhelp.knowledge.api import RuntimeDep
from mewhelp.knowledge.api import router as kb_router
from mewhelp.knowledge.sources import read_published_chunk

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MewHelp", version="0.1.0")
app.include_router(ch01_router)
app.include_router(ch02_router)
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
