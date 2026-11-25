"""Подготовка F1 и F2 в закрытом временном каталоге."""

import json
from pathlib import Path
from shutil import copyfile
import sys

import pyarrow.parquet as pq

from .datasets import split_rows
from .input_schema import InputError
from .input_table import read_table


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare_files(inputs, output, memory_budget):
    passport = json.loads((inputs / "passport.json").read_text(encoding="utf-8-sig"))
    policy = json.loads((inputs / "policy.json").read_text(encoding="utf-8-sig"))
    table = read_table(inputs, passport, max_bytes=memory_budget)
    search, confirmation = split_rows(table, passport, policy["seed"])
    id_name = next(c["name"] for c in passport["columns"] if c["role"] == "id")
    search_dir = output / "search"
    confirmation_dir = output / "confirmation"
    search_dir.mkdir()
    confirmation_dir.mkdir()
    for directory, name, rows in ((search_dir, "f1", search), (confirmation_dir, "f2", confirmation)):
        pq.write_table(table.take(rows), directory / f"{name}.parquet")
    copyfile(inputs / "model.onnx", search_dir / "model.onnx")
    copyfile(inputs / "methods.json", search_dir / "methods.json")

    allowed = {name: passport[name] for name in (
        "columns", "features", "classes", "inputs", "output", "dependence"
    )}
    allowed["provenance"] = {key: passport["provenance"][key] for key in ("passport", "methods")}
    write_json(search_dir / "passport.json", allowed)
    summary = {"dependence": passport["dependence"], "seed": policy["seed"],
               "f1_rows": len(search), "f2_rows": len(confirmation)}
    write_json(output / "split.json", {
        **summary, "id_column": id_name,
        "f1_ids": table[id_name].take(search).to_pylist(),
        "f2_ids": table[id_name].take(confirmation).to_pylist(),
    })
    return summary


if __name__ == "__main__":
    try:
        result = prepare_files(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
    except InputError as exc:
        result = {"field": exc.field, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
