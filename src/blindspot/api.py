from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from fastapi import Depends, FastAPI, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .inspection import inspect_inputs
from .storage import StudyError, StudyStore

WEB = Path(__file__).with_name("web")


def command_headers(idempotency_key: str = Header(), if_match: str = Header()):
    if not idempotency_key.strip() or len(idempotency_key) > 256:
        raise StudyError(422, "invalid_key", "Нужен ключ команды длиной до 256 символов.", "Idempotency-Key")
    if not if_match.isascii() or not if_match.isdigit() or len(if_match) > 18:
        raise StudyError(422, "invalid_revision", "Укажите номер ревизии.", "If-Match")
    return idempotency_key, int(if_match)


def create_app(directory: Path) -> FastAPI:
    store = StudyStore(directory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        yield

    api = FastAPI(title="BlindSpot", lifespan=lifespan, docs_url=None, redoc_url=None)
    api.mount("/static", StaticFiles(directory=WEB), name="static")

    @api.exception_handler(StudyError)
    async def study_error(request: Request, error: StudyError):
        return JSONResponse(status_code=error.status, content=error.body)

    @api.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        field = ".".join(str(v) for v in error.errors()[0]["loc"][1:])
        return JSONResponse(status_code=422, content={
            "code": "invalid_input", "message": "Обязательное поле пропущено или имеет неверный тип.",
            "field": field,
        })

    @api.exception_handler(sqlite3.Error)
    @api.exception_handler(OSError)
    async def storage_error(request: Request, error: Exception):
        return JSONResponse(status_code=503, content={
            "code": "storage_unavailable", "message": "Хранилище временно недоступно.",
        })

    @api.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEB / "index.html")

    @api.get("/studies")
    def studies():
        return {"items": store.list_studies()}

    @api.get("/studies/{study_id}")
    def detail(study_id: str):
        return store.detail(study_id)

    @api.post("/studies", status_code=201)
    def create(model: UploadFile, data: UploadFile, passport: UploadFile, policy: UploadFile,
               methods: UploadFile, headers=Depends(command_headers)):
        key, revision = headers
        if revision != 0:
            raise StudyError(409, "stale_revision", "Для нового исследования нужна ревизия 0.")
        suffix = Path(data.filename or "").suffix.lower()
        if suffix not in (".csv", ".parquet"):
            raise StudyError(422, "data_format", "Выберите таблицу CSV или Parquet.", "data")
        files = {"model.onnx": model, f"data{suffix}": data, "passport.json": passport,
                 "policy.json": policy, "methods.json": methods}
        with TemporaryDirectory(dir=store.directory) as temporary:
            inputs = Path(temporary) / "inputs"
            inputs.mkdir()
            hashes = {}
            for name, upload in files.items():
                checksum = hashlib.sha256()
                with (inputs / name).open("wb") as target:
                    while chunk := upload.file.read(1024 * 1024):
                        target.write(chunk)
                        checksum.update(chunk)
                hashes[name] = checksum.hexdigest()
            return store.create(inputs, hashes, key)

    @api.post("/studies/{study_id}/validate")
    def validate(study_id: str, headers=Depends(command_headers)):
        key, revision = headers
        return store.validate(study_id, revision, key, inspect_inputs)

    @api.post("/studies/{study_id}/prepare")
    def prepare(study_id: str, headers=Depends(command_headers)):
        from .versions import prepare_version
        key, revision = headers
        return prepare_version(store, study_id, revision, key)

    @api.post("/studies/{study_id}/start")
    def start(study_id: str, headers=Depends(command_headers)):
        _, revision = headers
        study = store.detail(study_id)
        store.check_state(study, revision, {"подготовлена"})
        raise StudyError(409, "baseline_unavailable", "Базовый расчёт пока недоступен.")

    return api


app = create_app(Path(os.environ.get("BLINDSPOT_DATA", "data")))
