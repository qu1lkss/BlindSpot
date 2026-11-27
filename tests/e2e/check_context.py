"""Сквозная проверка истории поиска и закрытой контрольной строки F2."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID

from study_client import Client


def retrieve(project, study, query):
    answer = subprocess.check_output([
        sys.executable, "scripts/search_context.py", study, "--project", project,
        "--method", "segment", "--feature", "x0", "--query", query,
    ], text=True, timeout=320)
    result = json.loads(answer)
    assert (result["study_id"], result["method"], result["feature"], result["query"]) == (
        study, "segment", "x0", query), result
    by_id = {fragment["id"]: fragment for fragment in result["fragments"]}
    assert "method:segment" in by_id and "passport:x0" in by_id, by_id.keys()
    for fragment in by_id.values():
        assert fragment["origin"] and fragment["version"] and fragment["provenance"], fragment
    return result


def saved_baseline(client, study, project):
    status, operation = client.command(study, "start")
    assert status == 202 and operation["operation_id"], (status, operation)
    deadline = time.monotonic() + 320
    while time.monotonic() < deadline:
        status, snapshot = client.request("/studies/" + study["id"])
        assert status == 200, (status, snapshot)
        baseline = snapshot["baseline"]
        assert baseline["id"] == operation["operation_id"], baseline
        if baseline["status"] == "сохранён":
            assert baseline["rows"] == study["preparation"]["f1_rows"], baseline
            return baseline
        assert baseline["status"] in ("очередь", "выполняется"), baseline
        if baseline["status"] == "очередь":
            # Работает и в чистом проекте, где постоянный диспетчер ещё не запущен.
            subprocess.run([sys.executable, "scripts/dispatch_jobs.py", "--project", project, "--once"],
                           check=True, timeout=max(1, deadline - time.monotonic()))
        else:
            time.sleep(0.3)
    raise TimeoutError("Контроллер не сохранил базовый опыт за отведённое время.")


def audit_in_controller(project, study, control_hash, before, after):
    # Настоящий ID извлекает только контроллер. Поиску и его журналу
    # не передаются ни значение, ни хеш контрольного ID.
    script = """
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import pyarrow.parquet as pq

study, fingerprint = sys.argv[1:3]
root = Path('/var/lib/blindspot/versions') / study
contexts = json.load(sys.stdin)
passport = json.loads((root / 'prepared/search/passport.json').read_text())
id_column = next(c['name'] for c in passport['columns'] if c['role'] == 'id')
def ids(path):
    return pq.read_table(path, columns=[id_column])[id_column].to_pylist()
matches = [value for value in ids(root / 'prepared/confirmation/f2.parquet')
           if hashlib.sha256(value.encode()).hexdigest() == fingerprint]
assert len(matches) == 1, 'В F2 должен присутствовать ровно один контрольный ID'
marker = matches[0]
assert marker not in ids(root / 'prepared/search/f1.parquet'), 'Контрольная строка обнаружена в F1'
index = root / 'search-work/knowledge.sqlite3'
assert index.is_file(), 'Поисковый контейнер не сохранил индекс'
with sqlite3.connect(index.as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('SELECT study FROM scope').fetchall() == [(study,)]
    for table, column in [('fragment','document'), ('fragment_fts','body'), ('retrieval_log','record')]:
        values = [value for value, in db.execute(f'SELECT {column} FROM {table}')]
        assert values, 'Пустой источник доказательств: ' + table
        assert all(marker not in value for value in values), 'Контрольный ID найден в ' + table
    for ctx in contexts:
        assert marker not in json.dumps(ctx, ensure_ascii=False)
        saved = db.execute('SELECT record FROM retrieval_log WHERE query_id=?',(ctx['query_id'],)).fetchone()
        assert saved and ctx == json.loads(saved[0]), 'Поисковый журнал расходится с выдачей'
    rows = db.execute('SELECT count(*) FROM fragment').fetchone()[0]
print(json.dumps({'confirmed_f2_control_rows':len(matches), 'indexed_sources':rows, 'verified_queries':len(contexts)}))
"""
    answer = subprocess.check_output(
        ["docker", "compose", "-p", project, "exec", "-T", "controller",
         "python", "-c", script, study, control_hash],
        input=json.dumps([before, after], ensure_ascii=False), text=True, timeout=45)
    return json.loads(answer)


def check(client, project, control):
    study = str(UUID(control["study_id"]))
    status, prepared = client.request("/studies/" + study)
    assert status == 200 and prepared["stage"] == "подготовлена", (status, prepared)
    assert prepared.get("baseline") is None, "Нужна новая контрольная версия до первого поиска."
    empty = retrieve(project, study, "лексемабезсовпадений")
    fragments = empty["fragments"]
    assert {f["kind"] for f in fragments} == {"method", "passport"}, fragments
    assert all(f["selection"] == "required_key" for f in fragments), fragments

    baseline = saved_baseline(client, prepared, project)
    observed = retrieve(project, study, "базовый опыт F1 accuracy")
    results = [f for f in observed["fragments"] if f["kind"] == "baseline"]
    assert len(results) == 1, results
    fragment = results[0]
    assert fragment["id"] == "experiment:" + baseline["id"], fragment
    assert fragment["provenance"]["partition"] == "F1" and fragment["provenance"]["study_id"] == study, fragment
    summary = json.loads(fragment["text"])
    expected = {m["metric"]: m["value"] for m in baseline["measurements"]
                if m["metric"] in ("accuracy", "macro_f1")}
    assert set(expected) == {"accuracy", "macro_f1"}, baseline
    assert summary["rows"] == baseline["rows"], summary
    for metric, value in expected.items():
        assert summary[metric] == value, (metric, summary[metric], value)
    assert empty["query_id"] != observed["query_id"]
    proof = audit_in_controller(project, study, control["control_id_sha256"], empty, observed)
    print(json.dumps({"study_id": study, "baseline_id": baseline["id"], "f1_rows": baseline["rows"],
                      "before_query": empty["query_id"], "after_query": observed["query_id"], **proof},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("control", type=Path, help="JSON из check_versions.py --control-output")
    parser.add_argument("--project", default="blindspot")
    args = parser.parse_args()
    check(Client(args.url), args.project, json.loads(args.control.read_bytes()))
