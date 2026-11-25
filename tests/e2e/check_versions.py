"""Сквозная проверка подготовки версий через работающий локальный HTTP API."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from study_client import Client


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def verified_upload(client, demo, overrides=None):
    status, draft = client.upload(demo, overrides)
    assert status == 201 and draft["stage"] == "черновик", (status, draft)
    status, checked = client.command(draft, "validate")
    assert status == 200 and checked["valid"] and checked["stage"] == "проверена", (status, checked)
    return checked


def prepare(client, checked, mode, n, seed):
    key = str(uuid4())
    status, ready = client.command(checked, "prepare", key=key)
    assert status == 200 and ready["stage"] == "подготовлена", (status, ready)
    assert ready["revision"] == checked["revision"] + 1 and ready["id"] == checked["id"], ready
    assert client.command(checked, "prepare", key=key) == (200, ready)
    assert client.command(checked, "prepare")[0] == 409
    summary = ready["preparation"]
    assert summary["dependence"] == mode and summary["seed"] == seed, summary
    assert summary["f1_rows"] + summary["f2_rows"] == n and min(summary["f1_rows"], summary["f2_rows"]) > 0, summary
    print(mode, ready["id"], summary)
    return ready


def get_study(client, study):
    status, saved = client.request("/studies/" + study["id"])
    assert status == 200, saved
    return saved


def dependent_files(demo, mode, conflict=False):
    passport = json.loads((demo / "passport.json").read_bytes())
    assert passport["dependence"] == "independent", "Для испытания нужен CSV-пример с независимыми строками."
    with (demo / "data.csv").open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = list(reader.fieldnames)
    passport["dependence"] = mode

    def add(role, kind):
        name = "e2e_" + role
        while name in fields:
            name += "_"
        passport["columns"].append(dict(name=name, role=role, type=kind, description="Служебное поле испытания",
                                        unit=None, precision=None, nullable=False))
        fields.append(name)
        return name

    count = len(rows)
    boundary = (3 * count + 2) // 5
    if mode in ("grouped", "grouped_temporal"):
        group = add("group", "string")
        for i, row in enumerate(rows):
            row[group] = "поиск" if i < boundary else "подтверждение"
        if conflict:
            rows[-1][group] = rows[0][group]
    marker_hash = None
    if mode in ("temporal", "grouped_temporal"):
        date = add("time", "datetime")
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i, row in enumerate(rows):
            row[date] = (start + timedelta(seconds=i)).isoformat()
        if mode == "temporal":
            identity = next(c for c in passport["columns"] if c["role"] == "id")
            identity["type"] = "string"
            for key in ("categories", "min", "max"):
                identity.pop(key, None)
            marker = "закрытая-строка-" + str(uuid4())
            rows[-1][identity["name"]] = marker
            marker_hash = hashlib.sha256(marker.encode("utf-8")).hexdigest()
    passport["provenance"]["data"]["source"] += " / синтетический сценарий зависимостей tests/e2e/check_versions.py"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return {"data": buffer.getvalue().encode("utf-8"), "passport": json_bytes(passport)}, marker_hash


def check(client, demo, control_output):
    policy = json.loads((demo / "policy.json").read_bytes())
    original_seed = policy["seed"]
    checked = verified_upload(client, demo)
    n = checked["dimensions"]["rows"]
    first = prepare(client, checked, "independent", n, original_seed)
    expected = (3 * n + 2) // 5
    assert first["preparation"]["f1_rows"] == expected, first
    saved_first = get_study(client, first)
    policy["seed"] = (original_seed + 1) % (2**63)
    second = prepare(client, verified_upload(client, demo, {"policy": json_bytes(policy)}),
                     "independent", n, policy["seed"])
    assert second["id"] != first["id"] and second["preparation"]["f1_rows"] == expected, second
    assert saved_first == get_study(client, first)

    for mode in ("grouped", "temporal", "grouped_temporal"):
        files, fingerprint = dependent_files(demo, mode)
        study = prepare(client, verified_upload(client, demo, files), mode, n, original_seed)
        assert study["preparation"]["f1_rows"] == expected, study
        if fingerprint is not None and control_output:
            control = {"study_id": study["id"], "control_id_sha256": fingerprint}
            control_output.write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")

    files, _ = dependent_files(demo, "grouped_temporal", conflict=True)
    checked = verified_upload(client, demo, files)
    status, error = client.command(checked, "prepare")
    assert status == 422 and error["code"] == "invalid_split" and error["field"], (status, error)
    saved = get_study(client, checked)
    assert saved["stage"] == "проверена" and saved["revision"] == checked["revision"], saved
    assert saved.get("preparation") is None, saved
    print("Пересечение всех хронологических границ отклонено.", error["field"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("demo", type=Path, help="Папка с CSV-примером")
    parser.add_argument("--control-output", type=Path, help="JSON с UUID версии и хешем контрольного ID из F2")
    args = parser.parse_args()
    check(Client(args.url), args.demo, args.control_output)
