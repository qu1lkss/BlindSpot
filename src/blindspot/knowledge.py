"""Версионированный поиск FTS5 по разрешённым материалам поискового контура."""

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Нужно непустое строковое поле {field}.")
    return value


def _source_fragments(folder):
    passport = json.loads((folder / "passport.json").read_text(encoding="utf-8-sig"))
    catalog = json.loads((folder / "methods.json").read_text(encoding="utf-8-sig"))
    sources = passport["provenance"]
    documents = []
    for column in passport["columns"]:
        if column["role"] != "feature":
            continue
        source = sources["passport"]
        name = column["name"]
        # Отсутствие флага в исходном паспорте означает явный запрет для модели.
        descriptor = {**column, "mutable": column.get("mutable", False)}
        documents.append({"id": "passport:" + name, "kind": "passport", "version": source["version"],
                          "origin": source["source"], "provenance": source, "feature": name, "method": None,
                          "text": _json(descriptor)})
    for method in catalog["methods"]:
        source = sources["methods"]
        name = method["id"]
        documents.append({"id": "method:" + name, "kind": "method", "version": method["version"],
                          "origin": source["source"], "provenance": source, "feature": None, "method": name,
                          "text": _json(method)})
    return documents


def _history_fragments(history, study_id, methods, features):
    records = []
    for item in history:
        if not isinstance(item, dict) or (
            item.get("study_id"), item.get("partition"), item.get("status")
        ) != (study_id, "F1", "completed"):
            raise ValueError("В индекс допускаются только завершённые опыты F1 текущей версии.")
        method = item.get("method")
        feature = item.get("feature")
        baseline = method == "baseline" and feature is None
        if not baseline and (method not in methods or feature not in features):
            raise ValueError("Методика или признак опыта отсутствуют в текущих поисковых материалах.")
        source = _text(item.get("origin"), "origin")
        exp_id = _text(item.get("id"), "id")
        version = _text(item.get("version"), "version")
        records.append({"id": "experiment:" + exp_id, "kind": "baseline" if baseline else "experiment",
                        "version": version, "origin": source, "feature": feature, "method": method,
                        "provenance": {"study_id": study_id, "partition": "F1", "experiment_id": exp_id,
                                       "status": "completed", "version": version, "source": source},
                        "text": _text(item.get("summary"), "summary")})
    return records


def _open_index(db, study_id, materials):
    db.execute("CREATE TABLE IF NOT EXISTS scope (singleton INTEGER PRIMARY KEY CHECK(singleton=1), study TEXT, materials TEXT)")
    db.execute("INSERT OR IGNORE INTO scope VALUES (1,?,?)", (study_id, materials))
    scope = db.execute("SELECT study,materials FROM scope WHERE singleton=1").fetchone()
    if tuple(scope) != (study_id, materials):
        raise ValueError("Индекс принадлежит другой версии или исходные поисковые материалы изменились.")
    db.execute("""CREATE TABLE IF NOT EXISTS fragment (
        id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, method TEXT, feature TEXT, document TEXT NOT NULL)""")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fragment_fts USING fts5(body, tokenize='unicode61')")
    db.execute("""CREATE TABLE IF NOT EXISTS retrieval_log (
        query_id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, record TEXT NOT NULL)""")


def _synchronize(db, documents):
    for item in documents:
        encoded = _json(item)
        prev = db.execute("SELECT document FROM fragment WHERE id=?", (item["id"],)).fetchone()
        if prev is not None:
            if prev[0] != encoded:
                raise ValueError("Изменилось содержимое уже зарегистрированного поискового источника.")
            continue
        row = db.execute("INSERT INTO fragment VALUES (?,?,?,?,?)",
                         (item["id"], item["kind"], item["method"], item["feature"], encoded))
        db.execute("INSERT INTO fragment_fts(rowid,body) VALUES (?,?)", (row.lastrowid, item["text"]))


def _select(db, method, feature, expression):
    rows = []
    if expression:
        rows = db.execute("""
            SELECT f.id,f.document,bm25(fragment_fts) AS score
            FROM fragment_fts JOIN fragment f ON f.rowid=fragment_fts.rowid
            WHERE fragment_fts MATCH ? AND (
                 (f.kind='passport' AND f.feature=?) OR (f.kind='method' AND f.method=?)
                 OR (f.kind='experiment' AND f.method=? AND f.feature=?) OR f.kind='baseline')
            ORDER BY score ASC, f.id COLLATE BINARY ASC LIMIT 8
        """, (expression, feature, method, method, feature)).fetchall()
    matches = {row["id"]: row for row in rows}
    fragments = []
    for reference in ("passport:" + feature, "method:" + method):
        hit = matches.pop(reference, None)
        if hit is not None:
            document, score = hit["document"], hit["score"]
        else:
            document = db.execute("SELECT document FROM fragment WHERE id=?", (reference,)).fetchone()[0]
            score = None
        fragments.append({**json.loads(document), "score": score,
                          "selection": "required_match" if hit is not None else "required_key"})
    for hit in matches.values():
        fragments.append({**json.loads(hit["document"]), "score": hit["score"], "selection": "full_text"})
    if not any(f["kind"] in ("experiment", "baseline") for f in fragments):
        # Та же пара методика/признак — контекст даже при другой лексике.
        # До первого профильного опыта доступна глобальная база F1.
        scoped = db.execute("""
            SELECT document FROM fragment WHERE
                (kind='experiment' AND method=? AND feature=?) OR kind='baseline'
            ORDER BY CASE kind WHEN 'experiment' THEN 0 ELSE 1 END, id COLLATE BINARY LIMIT 1
        """, (method, feature)).fetchone()
        if scoped is not None:
            fragments.append({**json.loads(scoped[0]), "score": None, "selection": "history_scope"})
    return fragments


def retrieve(folder: Path, work: Path, study_id: str, method: str, feature: str, query: str, history: list[dict]) -> dict:
    """Сохранить новые завершённые опыты, подобрать контекст и записать доказательства одной транзакцией.

    Один рабочий каталог соответствует одной неизменяемой версии. История может
    содержать только новые записи либо повторять ранее сохранённые записи.
    """
    _text(study_id, "study_id")
    if not isinstance(query, str):
        raise ValueError("Поисковый запрос должен быть строкой.")
    materials = _source_fragments(folder)
    methods = {d["method"] for d in materials if d["kind"] == "method"}
    features = {d["feature"] for d in materials if d["kind"] == "passport"}
    if method not in methods or feature not in features:
        raise ValueError("Выбранная методика или признак отсутствуют в текущей версии.")
    completed = _history_fragments(history, study_id, methods, features)
    # Передаём FTS5 литералы. Операторы и пунктуация пользовательской строки
    # не изменяют фильтры источников и не превращаются в выражение MATCH.
    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold())))[:32]
    expression = " OR ".join(f'"{word}"' for word in tokens)
    work.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(work / "knowledge.sqlite3", timeout=10)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        _open_index(db, study_id, _hash(_json(materials)))
        _synchronize(db, materials)
        _synchronize(db, completed)
        selected = _select(db, method, feature, expression)
        result = {"study_id": study_id, "method": method, "feature": feature, "query": query,
                  "expression": expression, "ranking": "fts5_bm25", "fragments": selected}
        query_id = _hash(_json(result))
        result["query_id"] = query_id
        db.execute("INSERT OR IGNORE INTO retrieval_log(query_id,record) VALUES (?,?)", (query_id, _json(result)))
    return result


def synchronize(folder, work, study_id, history):
    """Добавить опубликованный опыт, не создавая фиктивный поисковый запрос."""
    materials = _source_fragments(folder)
    methods = {doc["method"] for doc in materials if doc["kind"] == "method"}
    features = {doc["feature"] for doc in materials if doc["kind"] == "passport"}
    completed = _history_fragments(history, study_id, methods, features)
    with closing(sqlite3.connect(work / "knowledge.sqlite3", timeout=10)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        _open_index(db, study_id, _hash(_json(materials)))
        _synchronize(db, materials)
        _synchronize(db, completed)
    return {"study_id": study_id, "completed": len(completed)}
