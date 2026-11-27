"""Сверка A21 через HTTP, независимо от кода измерений приложения."""

import argparse
from collections import Counter
import csv
import io
import json
from math import isclose
from pathlib import Path
import time
from urllib.request import urlopen
from uuid import uuid4

import onnx
from onnx import TensorProto, helper
import pyarrow.parquet as pq

from study_client import Client


def run_baseline(client, demo, overrides=None):
    status, draft = client.upload(demo, overrides)
    assert status == 201, draft
    status, checked = client.command(draft, "validate")
    assert status == 200 and checked["valid"], checked
    status, prepared = client.command(checked, "prepare")
    assert status == 200, prepared
    key = str(uuid4())
    status, operation = client.command(prepared, "start", key=key)
    assert status == 202 and operation["operation_id"], (status, operation)
    assert client.command(prepared, "start", key=key) == (status, operation)
    assert client.command(prepared, "start")[0] == 409
    path = "/studies/" + prepared["id"]
    deadline = time.monotonic() + 320
    while time.monotonic() < deadline:
        status, study = client.request(path)
        assert status == 200, study
        result = study["baseline"]
        if result and result["status"] not in ("очередь", "выполняется"):
            assert result["search_used"] == result["total_used"] == 1, result
            return study
        time.sleep(0.5)
    raise TimeoutError("Базовый расчёт не завершился за 320 секунд.")


def reference_labels(demo, passport):
    columns = {c["role"]: c for c in passport["columns"]}
    identity, target = columns["id"], columns["target"]
    if (demo / "data.csv").exists():
        with (demo / "data.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    else:
        rows = pq.read_table(demo / "data.parquet", columns=[identity["name"], target["name"]]).to_pylist()
    key_type = int if identity["type"] == "integer" else str
    target_type = int if target["type"] == "integer" else str
    return {key_type(row[identity["name"]]): target_type(row[target["name"]]) for row in rows}


def verify_measurements(client, study, passport, labels):
    result = study["baseline"]
    assert study["stage"] == "поиск" and result["status"] == "сохранён", study
    with urlopen(client.address + "/studies/" + study["id"] + "/predictions", timeout=30) as response:
        rows = pq.read_table(io.BytesIO(response.read())).to_pylist()
    assert len(rows) == result["rows"] == study["preparation"]["f1_rows"]
    assert len({row["row_id"] for row in rows}) == len(rows)
    classes = passport["classes"]
    counts = Counter()
    for row in rows:
        truth = labels[row["row_id"]]
        assert row["truth"] == truth and row["prediction"] in classes, row
        counts[truth, row["prediction"]] += 1
    expected = {("accuracy", None, None): sum(counts[c, c] for c in classes) / len(rows)}
    f1 = []
    for c in classes:
        actual = sum(counts[c, j] for j in classes)
        predicted = sum(counts[j, c] for j in classes)
        expected["recall", c, None] = counts[c, c] / actual if actual else None
        if actual + predicted:
            f1.append(2 * counts[c, c] / (actual + predicted))
        for j in classes:
            expected["confusion_matrix", c, j] = counts[c, j]
    expected["macro_f1", None, None] = sum(f1) / len(classes) if len(f1) == len(classes) else None
    for item in result["measurements"]:
        key = (item["metric"], json.loads(item["actual"]) if item["actual"] else None,
               json.loads(item["predicted"]) if item["predicted"] else None)
        number = expected.pop(key)
        if number is None:
            assert item["value"] is None and item["reason"], item
        else:
            assert isclose(item["value"], number, rel_tol=0, abs_tol=1e-12), (item, number)
    assert not expected, expected
    print(f'{study["id"]}: {len(rows)} уникальных ID, accuracy={sum(counts[c,c] for c in classes)/len(rows):.6f}')


def partial_model(demo, output):
    # Первые восемь ответов проходят проверочную пробу, полный пакет теряет строки.
    model = onnx.load(demo / "model.onnx")
    next(node for node in model.graph.node if output in node.output).output[0] = "all_e2e_labels"
    args = []
    for name, value in (("start", 0), ("end", 8), ("axis", 0)):
        name = "e2e_" + name
        model.graph.initializer.append(helper.make_tensor(name, TensorProto.INT64, [1], [value]))
        args.append(name)
    model.graph.node.append(helper.make_node("Slice", ["all_e2e_labels", *args], [output]))
    onnx.checker.check_model(model)
    return model.SerializeToString()


def check(client, demo):
    passport = json.loads((demo / "passport.json").read_bytes())
    labels = reference_labels(demo, passport)
    verify_measurements(client, run_baseline(client, demo), passport, labels)
    classes = passport["classes"]
    absent = max(classes) + 1 if isinstance(classes[0], int) else max(classes) + "_нет"
    classes.append(absent)
    for col in passport["columns"]:
        if col["role"] == "target" and col.get("categories"):
            col["categories"].append(absent)
    missing = run_baseline(client, demo, {"passport": json.dumps(passport, ensure_ascii=False).encode()})
    verify_measurements(client, missing, passport, labels)
    failed = run_baseline(client, demo, {"model": partial_model(demo, passport["output"])})
    result = failed["baseline"]
    assert failed["stage"] == "отказ" and result["status"] == "ошибка" and result["reason"], failed
    assert not result["measurements"]
    assert client.request("/studies/" + failed["id"] + "/predictions")[0] in (404, 409)
    print("Неполные ответы остановили расчёт.", result["reason"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("demo", type=Path, help="Папка с примером examples/binary или examples/multiclass")
    args = parser.parse_args()
    check(Client(args.url), args.demo)
