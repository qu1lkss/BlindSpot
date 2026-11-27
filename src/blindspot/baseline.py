"""Учёт базового опыта и регистрация ответа из поискового контейнера."""
import json
from time import time
from uuid import uuid4

from .storage import StudyError, digest
from .versions import input_records, verified


def install(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS experiment (
            research_id TEXT REFERENCES research(id), id TEXT, kind TEXT NOT NULL,
            status TEXT NOT NULL, started REAL NOT NULL, deadline REAL NOT NULL,
            finished REAL, reason TEXT, rows_count INTEGER, seed INTEGER NOT NULL,
            PRIMARY KEY(research_id,id)
        );
        CREATE TABLE IF NOT EXISTS measurement (
            research_id TEXT, experiment_id TEXT, metric TEXT, scope TEXT, actual TEXT, predicted TEXT,
            value REAL, reason TEXT, CHECK(value IS NOT NULL OR reason IS NOT NULL),
            PRIMARY KEY(research_id,experiment_id,metric,scope,actual,predicted),
            FOREIGN KEY(research_id,experiment_id) REFERENCES experiment(research_id,id)
        );
    """)


def snapshot(db, study):
    job = db.execute("SELECT * FROM experiment WHERE research_id=? AND kind='baseline' ORDER BY started DESC LIMIT 1",
                     (study,)).fetchone()
    if not job:
        return None
    rows = db.execute("SELECT metric,actual,predicted,value,reason FROM measurement WHERE research_id=? AND experiment_id=? ORDER BY metric,actual,predicted",
                      (study,job["id"]))
    used = db.execute("SELECT COUNT(*) FROM experiment WHERE research_id=?", (study,)).fetchone()[0]
    return {"id":job["id"], "status":job["status"], "rows":job["rows_count"], "reason":job["reason"],
            "search_used":used, "total_used":used, "measurements":[dict(row) for row in rows]}


def start(store, study, revision, key):
    scope = f"{study}/start"
    with store.connection(write=True) as db:
        previous = store.previous(db,scope,key,str(revision))
        if previous:
            return previous
        store.check_state(store.row(db,study), revision, {"подготовлена"})
        root = store.versions / study
        verified(root, input_records(db,study))
        policy = json.loads((root/"inputs/policy.json").read_text(encoding="utf-8-sig"))
        used = db.execute("SELECT COUNT(*) FROM experiment WHERE research_id=?", (study,)).fetchone()[0]
        if used >= min(policy["search_experiments"], policy["max_experiments"]):
            raise StudyError(409,"budget","Бюджет опытов исчерпан.")
        task = str(uuid4())
        now = time()
        deadline = now + min(policy["seconds"][k] for k in ("baseline","total"))
        db.execute("INSERT INTO experiment(research_id,id,kind,status,started,deadline,seed) VALUES (?,?,'baseline','очередь',?,?,?)",
                   (study,task,now,deadline,policy["seed"]))
        db.execute("UPDATE research SET stage='базовый расчёт', revision=revision+1 WHERE id=?", (study,))
        result = {**store.row(db,study), "operation_id":task}
        store.remember(db,scope,key,str(revision),result)
        return result


def fail(db, study, task, reason):
    changed = db.execute("UPDATE experiment SET status='ошибка',reason=?,finished=? WHERE research_id=? AND id=? AND status IN ('очередь','выполняется')",
                         (reason,time(),study,task)).rowcount
    if changed:
        db.execute("UPDATE research SET stage='отказ',revision=revision+1 WHERE id=?", (study,))


def expire(db):
    for row in db.execute("SELECT research_id,id FROM experiment WHERE status IN ('очередь','выполняется') AND deadline<=?", (time(),)).fetchall():
        fail(db, *row, "Истёк срок базового расчёта.")


def claim(store):
    with store.connection(write=True) as db:
        expire(db)
        job = db.execute("SELECT research_id,id,deadline FROM experiment WHERE status='очередь' ORDER BY started LIMIT 1").fetchone()
        if job:
            db.execute("UPDATE experiment SET status='выполняется' WHERE research_id=? AND id=?", (job[0],job[1]))
            return dict(job)


def publish(store, study, task, report):
    import pyarrow.parquet as pq
    from .statistics import metric_rows
    from .isolation import search_spec

    search_spec(store, study)
    root = store.versions / study
    file = root / "search-work" / (task + ".parquet")
    if file.is_symlink() or digest(file) != report["sha256"]:
        raise ValueError("Контрольная сумма прогнозов расходится.")
    folder = root / "prepared/search"
    passport = json.loads((folder/"passport.json").read_text())
    roles = {c["role"]:c["name"] for c in passport["columns"]}
    data = pq.read_table(folder/"f1.parquet", columns=[roles["id"],roles["target"]])
    output = pq.read_table(file)
    if (output.column_names != ["row_id","truth","prediction"] or len(output) != report["rows"]
            or not output["row_id"].equals(data[roles["id"]]) or not output["truth"].equals(data[roles["target"]])):
        raise ValueError("Строки ответа расходятся с F1.")
    metrics = metric_rows(output["truth"].to_pylist(),output["prediction"].to_pylist(),passport["classes"])
    with store.connection(write=True) as db:
        job = db.execute("SELECT status,deadline FROM experiment WHERE research_id=? AND id=?", (study,task)).fetchone()
        if not job or job["status"] != "выполняется" or time() >= job["deadline"]:
            raise ValueError("Опыт прерван или вышел за срок.")
        target = root / "baseline" / file.name
        target.parent.mkdir(exist_ok=True)
        file.replace(target)
        db.execute("INSERT INTO artifact(research_id,id,kind,path,sha256,access,ready) VALUES (?,?,'predictions',?,?,'controller',1)",
                   (study,task,str(target.relative_to(root)),report["sha256"]))
        db.executemany("INSERT INTO measurement VALUES (?,?,?,'F1',?,?,?,?)", [
            (study,task,r["metric"],r["actual"],r["predicted"],r["value"],r["reason"]) for r in metrics])
        db.execute("UPDATE experiment SET status='сохранён',finished=?,rows_count=? WHERE research_id=? AND id=?",
                   (time(),len(output),study,task))
        db.execute("UPDATE research SET stage='поиск',revision=revision+1 WHERE id=?", (study,))


def predictions(store, study):
    with store.connection() as db:
        store.row(db,study)
        row = db.execute("SELECT path,sha256 FROM artifact WHERE research_id=? AND kind='predictions' AND ready=1",
                         (study,)).fetchone()
        if not row:
            raise StudyError(409,"not_ready","Прогнозы ещё не сохранены.")
        verified(store.versions / study,[row])
        return store.versions / study / row["path"]
