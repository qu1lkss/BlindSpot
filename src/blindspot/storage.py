from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class StudyError(Exception):
    def __init__(self, status, code, message, field=None):
        super().__init__(message)
        self.status = status
        self.body = {"code": code, "message": message}
        if field:
            self.body["field"] = field


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class StudyStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.database = directory / "blindspot.sqlite3"
        self.versions = directory / "versions"

    @contextmanager
    def connection(self, *, write=False):
        with closing(sqlite3.connect(self.database, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    def initialize(self):
        self.versions.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research (
                    id TEXT PRIMARY KEY NOT NULL,
                    created_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
                );
                CREATE TABLE IF NOT EXISTS command (
                    scope TEXT NOT NULL, key TEXT NOT NULL,
                    request TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY (scope, key)
                );
                CREATE TABLE IF NOT EXISTS validation (
                    research_id TEXT PRIMARY KEY REFERENCES research(id),
                    report TEXT NOT NULL
                );
            """)

    def list_studies(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, created_at, stage, revision FROM research ORDER BY created_at DESC, id"
            )]

    @staticmethod
    def row(db, study_id):
        row = db.execute("SELECT * FROM research WHERE id=?", (study_id,)).fetchone()
        if row is None:
            raise StudyError(404, "unknown_study", "Исследование не найдено.")
        return dict(row)

    def detail(self, study_id):
        with self.connection() as db:
            study = self.row(db, study_id)
            entry = db.execute("SELECT report FROM validation WHERE research_id=?", (study_id,)).fetchone()
            study["validation"] = json.loads(entry["report"]) if entry else None
            return study

    @staticmethod
    def previous(db, scope, key, request):
        entry = db.execute("SELECT request,response FROM command WHERE scope=? AND key=?",
                           (scope, key)).fetchone()
        if entry is None:
            return None
        if entry["request"] != request:
            raise StudyError(409, "key_conflict", "Этот ключ уже принадлежит другой команде.")
        return json.loads(entry["response"])

    @staticmethod
    def remember(db, scope, key, request, result):
        db.execute("INSERT INTO command VALUES (?,?,?,?)",
                   (scope, key, request, json.dumps(result, ensure_ascii=False)))

    @staticmethod
    def check_state(study, revision, allowed):
        if study["revision"] != revision:
            raise StudyError(409, "stale_revision", "Исследование изменилось. Обновите его состояние.")
        if study["stage"] not in allowed:
            raise StudyError(409, "invalid_stage", "Команда недоступна на текущем этапе.")

    def create(self, inputs, hashes, key):
        request = json.dumps(hashes, sort_keys=True)
        with self.connection(write=True) as db:
            cached = self.previous(db, "create", key, request)
            if cached is not None:
                return cached
            study_id = str(uuid4())
            root = self.versions / study_id
            root.mkdir()
            inputs.rename(root / "inputs")
            db.execute("INSERT INTO research VALUES (?,?,?,0)",
                       (study_id, datetime.now(timezone.utc).isoformat(), "черновик"))
            result = self.row(db, study_id)
            self.remember(db, "create", key, request, result)
            return result

    def validate(self, study_id, revision, key, inspect):
        scope = f"{study_id}/validate"
        request = str(revision)
        with self.connection() as db:
            cached = self.previous(db, scope, key, request)
            if cached is not None:
                return cached
            self.check_state(self.row(db, study_id), revision, {"черновик", "проверена"})

        report = inspect(self.versions / study_id / "inputs")
        with self.connection(write=True) as db:
            cached = self.previous(db, scope, key, request)
            if cached is not None:
                return cached
            self.check_state(self.row(db, study_id), revision, {"черновик", "проверена"})
            stage = "проверена" if report["valid"] else "черновик"
            db.execute("UPDATE research SET stage=?, revision=revision+1 WHERE id=?", (stage, study_id))
            db.execute("INSERT OR REPLACE INTO validation VALUES (?,?)",
                       (study_id, json.dumps(report, ensure_ascii=False)))
            result = {**self.row(db, study_id), **report}
            self.remember(db, scope, key, request, result)
            return result
