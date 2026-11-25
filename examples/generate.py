"""Синтетическая классификация измерений. Запускать в образе BlindSpot."""
import csv
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import pyarrow as pa
import pyarrow.parquet as pq


def generate(classes, folder):
    folder.mkdir(exist_ok=True)
    rng = np.random.default_rng(420 + classes)
    train = rng.normal(size=(500, 10))
    x = rng.normal(size=(1000, 10)).round(4)
    features = [f"x{i}" for i in range(10)]
    def labels(values):
        return np.digitize(values[:, 0] + values[:, 1], [0] if classes == 2 else [-0.6, 0.6])
    mean = train.mean(axis=0).astype("float32")
    z = train - mean
    y = labels(train)
    if classes == 2:
        weights = np.linalg.lstsq(z, np.eye(classes)[y], rcond=None)[0]
        bias = np.eye(classes)[y].mean(axis=0)
    else:
        centers = np.stack([z[y == k].mean(axis=0) for k in range(classes)])
        weights, bias = 2 * centers.T, -(centers ** 2).sum(axis=1)
    nodes = [helper.make_node("Sub", ["features", "mean"], ["z"]),
             helper.make_node("Gemm", ["z", "weights", "bias"], ["scores"]),
             helper.make_node("ArgMax", ["scores"], ["label"], axis=1, keepdims=0)]
    graph = helper.make_graph(nodes, "synthetic", [helper.make_tensor_value_info("features", TensorProto.FLOAT, [None,10])],
        [helper.make_tensor_value_info("label", TensorProto.INT64, [None])],
        [numpy_helper.from_array(a.astype("float32"), n) for n,a in [("mean",mean),("weights",weights),("bias",bias)]])
    onnx.save(helper.make_model(graph, ir_version=9, opset_imports=[helper.make_opsetid("",17)]),folder/"model.onnx")
    rows = [dict(id=f"{i:04}", target=int(k), **dict(zip(features,row.tolist()))) for i,(row,k) in enumerate(zip(x,labels(x)))]
    if classes == 2:
        with (folder/"data.csv").open("w") as stream:
            writer=csv.DictWriter(stream,fieldnames=rows[0],lineterminator="\n");writer.writeheader();writer.writerows(rows)
    else:
        pq.write_table(pa.Table.from_pylist(rows),folder/"data.parquet")
    def write(name, obj):
        (folder/(name+".json")).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n")
    columns = [dict(name=n,role=r,type=t,description=n,unit=None,precision=None,nullable=False)
               for n,r,t in [("id","id","string"),("target","target","integer")]]
    for name in features:
        columns.append(dict(name=name, role="feature",type="number",description="Синтетическое измерение "+name,
                            unit="усл. ед.", precision=4,nullable=False,min=-10,max=10))
    source=dict(author="Генератор BlindSpot",source="examples/generate.py",received_at="2026-11-25T00:00:00Z",version="1")
    write("passport",dict(columns=columns,features=features,classes=list(range(classes)),inputs=[dict(name="features",type="float32",columns=features)],
        output="label",dependence="independent",provenance={k:source for k in ("model","data","passport","policy","methods")}))
    write("policy",dict(metrics=["accuracy","macro_f1","recall","confusion_matrix"],threshold=.05,alpha=.05,min_group=50,search_fraction=.6,
        seed=42,max_experiments=100,search_experiments=80,confirmation_experiments=20,max_candidates=20,memory_mb=512,disk_mb=128,
        seconds=dict(validation=300,preparation=120,baseline=300,search=2580,confirmation=600,summary=120,total=3600,report=300,export=600)))
    write("methods",dict(methods=[dict(id="segment",version="1",kind="segment",parameters={},ranges={},prerequisites="Числовой признак",
         impact_limits="Только отбор строк",evaluation="Сравнение с дополнением")]))


if __name__ == "__main__":
    for count,name in [(2,"binary"),(3,"multiclass")]:
        generate(count,Path(__file__).parent/name)
