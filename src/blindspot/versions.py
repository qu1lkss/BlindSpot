"""Регистрация материалов версии и атомарная публикация разбиения."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from .storage import StudyError, digest


def install_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS artifact (
            research_id TEXT NOT NULL REFERENCES research(id), id TEXT NOT NULL,
            kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
            access TEXT NOT NULL, author TEXT, source TEXT, received_at TEXT, source_version TEXT,
            ready INTEGER NOT NULL CHECK(ready IN (0,1)), PRIMARY KEY(research_id,id),
            UNIQUE(research_id,path)
        );
        CREATE TABLE IF NOT EXISTS preparation (
            research_id TEXT PRIMARY KEY REFERENCES research(id), summary TEXT NOT NULL
        );
    """)
    # У черновиков предыдущей версии хеши сохранились в команде загрузки.
    db.execute("""
        INSERT OR IGNORE INTO artifact (research_id,id,kind,path,sha256,access,ready)
        SELECT r.id, j.key, 'input', 'inputs/' || j.key, j.value, 'controller', 1
        FROM command c JOIN research r ON r.id=json_extract(c.response,'$.id'),
             json_each(c.request) j
        WHERE c.scope='create'
    """)


def register_inputs(db, study_id, hashes):
    db.executemany("""
        INSERT INTO artifact (research_id,id,kind,path,sha256,access,ready)
        VALUES (?,?,'input',?,?,'controller',1)
    """, [(study_id, name, 'inputs/' + name, checksum) for name, checksum in hashes.items()])


def verified(root, records):
    for row in records:
        path = root / row["path"]
        if not path.is_file() or digest(path) != row["sha256"]:
            raise StudyError(409, "integrity_error",
                             "Файл отсутствует или его контрольная сумма изменилась.", row["path"])


def input_records(db, study_id):
    records = db.execute("SELECT path,sha256 FROM artifact WHERE research_id=? AND kind='input' AND ready=1",
                         (study_id,)).fetchall()
    if len(records) != 5:
        raise StudyError(409, "missing_inputs", "Версия не содержит комплект из пяти зарегистрированных входов.")
    return records


def prepare_version(store, study_id, revision, key):
    scope = f"{study_id}/prepare"
    request = str(revision)
    with store.connection() as db:
        cached = store.previous(db, scope, key, request)
        if cached is not None:
            return cached
        store.check_state(store.row(db, study_id), revision, {"проверена"})
        records = input_records(db, study_id)
        report = json.loads(db.execute("SELECT report FROM validation WHERE research_id=?", (study_id,)).fetchone()[0])

    root = store.versions / study_id
    inputs = root / "inputs"
    started = monotonic()
    verified(root, records)
    policy = json.loads((inputs / "policy.json").read_text(encoding="utf-8-sig"))
    deadline = started + policy["seconds"]["preparation"]
    with TemporaryDirectory(dir=root, prefix=".preparing-") as temporary:
        temp = Path(temporary)
        output = temp / "files"
        output.mkdir()
        try:
            job = subprocess.run(
                [sys.executable, "-m", "blindspot.preparation", str(inputs), str(output),
                 str(policy["memory_mb"] * 1024**2)],
                capture_output=True, check=True, timeout=max(0, deadline - monotonic()),
            )
            summary = json.loads(job.stdout)
        except subprocess.TimeoutExpired:
            raise StudyError(409, "prepare_timeout", "Истёк срок подготовки версии.") from None
        except (subprocess.CalledProcessError, ValueError):
            raise StudyError(503, "prepare_failed", "Не удалось подготовить файлы разбиения.") from None
        if "field" in summary:
            raise StudyError(422, "invalid_split", summary["message"], summary["field"])
        (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        entries = []
        for file in sorted(output.rglob("*")):
            if file.is_file():
                relative = file.relative_to(output).as_posix()
                access = relative.partition("/")[0] if "/" in relative else "controller"
                entries.append({"path": f"prepared/{relative}", "sha256": digest(file), "access": access})
        manifest = {
            "research_id": study_id, "revision": revision + 1, "split": summary,
            "artifacts": [dict(row) for row in records] + entries,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries.append({"path": "prepared/manifest.json", "sha256": digest(manifest_path), "access": "controller"})
        total_bytes = sum((root / row["path"]).stat().st_size for row in records)
        total_bytes += sum(file.stat().st_size for file in output.rglob("*") if file.is_file())
        if total_bytes > policy["disk_mb"] * 1024**2:
            raise StudyError(422, "disk_quota", "Размер комплекта с разбиением превышает квоту.", "policy.disk_mb")

        sources = json.loads((inputs / "passport.json").read_text(encoding="utf-8-sig"))["provenance"]
        with store.connection(write=True) as db:
            cached = store.previous(db, scope, key, request)
            if cached is not None:
                return cached
            store.check_state(store.row(db, study_id), revision, {"проверена"})
            verified(root, records)
            if monotonic() >= deadline:
                raise StudyError(409, "prepare_timeout", "Истёк срок подготовки версии.")
            published = root / "prepared"
            if published.exists():
                # Предыдущая попытка могла остановиться между rename и COMMIT.
                published.rename(temp / "unregistered")
            output.rename(published)
            (root / "search-work").mkdir(exist_ok=True)
            for row in records:
                source = sources[Path(row["path"]).stem]
                db.execute("""
                    UPDATE artifact SET author=?,source=?,received_at=?,source_version=?
                    WHERE research_id=? AND path=?
                """, (source["author"], source["source"], source["received_at"], source["version"], study_id, row["path"]))
            for entry in entries:
                db.execute("""
                    INSERT INTO artifact (research_id,id,kind,path,sha256,access,ready)
                    VALUES (?,?,'prepared',?,?,?,1)
                """, (study_id, str(uuid4()), entry["path"], entry["sha256"], entry["access"]))
            db.execute("INSERT INTO preparation VALUES (?,?)", (study_id, json.dumps(summary)))
            db.execute("UPDATE research SET stage='подготовлена', revision=revision+1 WHERE id=?", (study_id,))
            result = {**store.row(db, study_id), "preparation": summary}
            store.remember(db, scope, key, request, result)
            return result
