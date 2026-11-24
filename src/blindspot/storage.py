from contextlib import closing
from pathlib import Path
import sqlite3


class StudyStore:
    def __init__(self, directory: Path):
        self.database = directory / "blindspot.sqlite3"

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS research (
                    id TEXT PRIMARY KEY NOT NULL,
                    created_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
                )
            """)

    def list_studies(self) -> list[dict]:
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT id, created_at, stage, revision FROM research
                ORDER BY created_at DESC, id ASC
            """)
            return [dict(row) for row in rows]
