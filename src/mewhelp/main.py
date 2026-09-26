"""FastAPI 应用入口。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mewhelp.ch01.api import router as ch01_router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MewHelp", version="0.1.0")
app.include_router(ch01_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
