from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .storage import StudyStore

WEB = Path(__file__).with_name("web")


def create_app(directory: Path) -> FastAPI:
    store = StudyStore(directory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        yield

    api = FastAPI(title="BlindSpot", lifespan=lifespan, docs_url=None, redoc_url=None)
    api.mount("/static", StaticFiles(directory=WEB), name="static")

    @api.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error: sqlite3.Error):
        return JSONResponse(
            status_code=503,
            content={"code": "storage_unavailable", "message": "Не удалось прочитать список исследований."},
        )

    @api.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEB / "index.html")

    @api.get("/studies")
    def studies():
        return {"items": store.list_studies()}

    return api


app = create_app(Path(os.environ.get("BLINDSPOT_DATA", "data")))
