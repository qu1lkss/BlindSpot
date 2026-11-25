import csv
import io
import json
from pathlib import Path
import sys
from uuid import uuid4

from study_client import Client


def check(client, csv_demo, parquet_demo):
    for demo in (csv_demo, parquet_demo):
        key = str(uuid4())
        status, study = client.upload(demo, key=key)
        assert status == 201, study
        assert client.upload(demo, key=key) == (201, study)
        assert client.command(study, "start")[0] == 409
        token = str(uuid4())
        status, result = client.command(study, "validate", key=token)
        assert status == 200 and result["valid"], result
        assert client.command(study, "validate", key=token) == (200, result)
        assert client.command(study, "validate")[0] == 409
        status, saved = client.request("/studies/" + study["id"])
        assert status == 200 and saved["validation"]["valid"]
        print(demo.name, result["dimensions"])

    passport = json.loads((csv_demo / "passport.json").read_bytes())
    names = {column["role"]: column["name"] for column in passport["columns"]}
    rows = list(csv.DictReader((csv_demo / "data.csv").read_text().splitlines()))

    def change_column(name, value):
        altered = rows.copy()
        altered[1] = dict(altered[1], **{name: value})
        data = io.StringIO()
        writer = csv.DictWriter(data, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(altered)
        return {"data": data.getvalue().encode()}

    policy = json.loads((csv_demo / "policy.json").read_bytes())
    cases = [
        ("повтор ID", change_column(names["id"], rows[0][names["id"]])),
        ("неизвестный класс", change_column(names["target"], "99")),
        ("повреждённая модель", {"model": b"invalid"}),
        ("неверная схема", {"data": b"id,wrong\n001,x\n"}),
        ("неверное значение", change_column(passport["features"][0], "9999999999999999999999999")),
        ("квота диска", {"policy": json.dumps(dict(policy, disk_mb=10**12)).encode()}),
        ("квота памяти", {"policy": json.dumps(dict(policy, memory_mb=10**12)).encode()}),
    ]
    for label, changes in cases:
        status, study = client.upload(csv_demo, changes)
        assert status == 201, study
        status, report = client.command(study, "validate")
        assert status == 200 and not report["valid"] and report["errors"], (label, report)
        assert report["errors"][0]["field"] and report["stage"] == "черновик", report
        assert client.command(report, "start")[0] == 409
        print(label, report["errors"][0])


if __name__ == "__main__":
    check(Client(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
