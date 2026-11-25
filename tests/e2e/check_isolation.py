"""Проверить обе стороны границы на версии из check_versions.py."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def check(project, control):
    study, marker = control["study_id"], control["control_id_sha256"]
    code = """
import hashlib, sys
import pyarrow.parquet as pq
path = '/var/lib/blindspot/versions/' + sys.argv[1] + '/prepared/confirmation/f2.parquet'
ids = pq.read_table(path, columns=['id'])['id']
assert any(hashlib.sha256(x.as_py().encode()).hexdigest() == sys.argv[2] for x in ids)
print('Контрольный ID присутствует в F2; строк:', len(ids))
"""
    subprocess.run(["docker", "compose", "-p", project, "exec", "-T", "controller", "python",
                    "-c", code, study, marker], check=True)
    output = subprocess.check_output([sys.executable, "scripts/search_worker.py", study,
                                      "--project", project, "--control-id-sha256", marker], text=True)
    report = json.loads(output)
    assert report["network_interfaces"] == ["lo"] and report["rows"] > 0, report
    assert report["input_readonly"] and report["work_writable"], report
    for field in ("control_id_found", "f2_visible", "versions_visible", "database_visible",
                  "shared_home_visible", "socket_visible"):
        assert report[field] is False, report
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("--project", default="blindspot")
    args = parser.parse_args()
    check(args.project, json.loads(args.control.read_bytes()))
