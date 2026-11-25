"""Две точки входа: доверенный контроллер и изолированный процесс поиска."""

import hashlib
import json
import os
from pathlib import Path
import socket
import sys

from .storage import StudyError, StudyStore, digest


SEARCH_FILES = {"model.onnx", "f1.parquet", "passport.json", "methods.json"}


def search_spec(store, study_id):
    from .versions import input_records, verified
    root = store.versions / study_id
    with store.connection() as db:
        study = store.row(db, study_id)
        store.check_state(study, study["revision"], {"подготовлена"})
        verified(root, input_records(db, study_id))
        rows = db.execute("SELECT path,sha256 FROM artifact WHERE research_id=? AND access='search' AND ready=1",
                          (study_id,)).fetchall()
        verified(root, rows)
    folder = root / "prepared/search"
    expected = {Path(r["path"]).name: r["sha256"] for r in rows}
    actual = {p.name for p in folder.iterdir()}
    if expected.keys() != SEARCH_FILES or actual != SEARCH_FILES or any(p.is_symlink() for p in folder.iterdir()):
        raise StudyError(409, "search_files", "Состав поискового каталога изменился.")
    policy = json.loads((root / "inputs/policy.json").read_text(encoding="utf-8-sig"))
    return {"id": study_id, "hashes": expected, "memory_mb": policy["memory_mb"],
            "disk_mb": policy["disk_mb"], "seconds": policy["seconds"]["search"]}


def check_search_input(job):
    import pyarrow.parquet as pq

    folder = Path("/input")
    checksums = job["hashes"]
    if set(checksums) != SEARCH_FILES or {p.name for p in folder.iterdir()} != SEARCH_FILES:
        raise ValueError("Неверный состав входов поиска.")
    for name in SEARCH_FILES:
        if digest(folder / name) != checksums[name]:
            raise ValueError("Изменился поисковый материал: " + name)
    passport = json.loads((folder / "passport.json").read_text())
    id_column = next(c["name"] for c in passport["columns"] if c["role"] == "id")
    ids = pq.read_table(folder / "f1.parquet", columns=[id_column])[id_column]
    hidden_hash = job.get("control_id_sha256")
    found = None
    if hidden_hash:
        found = any(hashlib.sha256(str(v.as_py()).encode()).hexdigest() == hidden_hash for v in ids)

    work = Path("/work")
    probe = work / job["task_id"]
    probe.write_text("ok")
    probe.unlink()
    return {"research_id": job["id"], "rows": len(ids), "inputs": sorted(SEARCH_FILES),
            "network_interfaces": sorted(name for _, name in socket.if_nameindex()
                                         if int((Path("/sys/class/net") / name / "flags").read_text(), 16) & 1),
            "socket_visible": Path("/var/run/docker.sock").exists(),
            "database_visible": Path("/var/lib/blindspot/blindspot.sqlite3").exists(),
            "versions_visible": Path("/var/lib/blindspot/versions").exists(),
            "f2_visible": (folder / "f2.parquet").exists(),
            "shared_home_visible": bool(list(Path("/home").iterdir())),
            "input_readonly": bool(os.statvfs(folder).f_flag & os.ST_RDONLY),
            "work_writable": True, "control_id_found": found}


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        output = check_search_input(json.load(sys.stdin))
    elif len(sys.argv) == 3 and sys.argv[1] == "spec":
        store = StudyStore(Path(os.environ["BLINDSPOT_DATA"]))
        try:
            output = search_spec(store, sys.argv[2])
        except StudyError as exc:
            sys.exit(str(exc))
    else:
        sys.exit("Ожидается spec UUID или check.")
    print(json.dumps(output, ensure_ascii=False))
