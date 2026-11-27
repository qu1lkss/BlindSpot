import json

import pyarrow as pa
import pyarrow.parquet as pq

from .adapters.base import open_model, predict
from .storage import digest


def compute_baseline(folder, output):
    passport = json.loads((folder / "passport.json").read_text())
    roles = {column["role"]: column["name"] for column in passport["columns"]}
    session = open_model(folder / "model.onnx", passport)
    source = pq.ParquetFile(folder / "f1.parquet")
    schema = pa.schema([(name, source.schema_arrow.field(roles[role]).type)
                        for name, role in (("row_id","id"), ("truth","target"), ("prediction","target"))])
    count = 0
    try:
        with pq.ParquetWriter(output, schema) as writer:
            for batch in source.iter_batches(batch_size=1024):
                labels = predict(session, passport, batch.to_pylist())
                writer.write_table(pa.Table.from_arrays(
                    [batch.column(roles["id"]), batch.column(roles["target"]), pa.array(labels,type=schema.field("prediction").type)],
                    schema=schema))
                count += len(labels)
        return {"rows": count, "sha256": digest(output)}
    except Exception:
        output.unlink(missing_ok=True)
        raise
