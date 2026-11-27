import numpy as np
import onnx
import onnxruntime as ort


def check_message(message):
    if isinstance(message, onnx.TensorProto) and (message.external_data or message.data_location == 1):
        raise ValueError("Внешние файлы весов запрещены.")
    if isinstance(message, (onnx.NodeProto, onnx.OperatorSetIdProto)) and message.domain not in ("", "ai.onnx.ml"):
        raise ValueError("Нужны стандартные операторы ONNX.")
    for field, value in message.ListFields():
        if field.message_type:
            for child in value if field.is_repeated else (value,):
                check_message(child)


def open_model(path, passport):
    try:
        model = onnx.load_model_from_string(path.read_bytes())
        check_message(model)
        if model.functions or model.training_info:
            raise ValueError("Нужен граф вывода со стандартными операторами.")
        onnx.checker.check_model(model)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(model.SerializeToString(), options, providers=["CPUExecutionProvider"])
        inputs = passport["inputs"]
        if [v.name for v in session.get_inputs()] != [v["name"] for v in inputs]:
            raise ValueError("Имена или порядок входов расходятся с паспортом.")
        types = {"float32":"float", "float64":"double", "string":"string", "int32":"int32", "int64":"int64", "bool":"bool"}
        for tensor, spec in zip(session.get_inputs(), inputs):
            if tensor.type != f"tensor({types[spec['type']]})":
                raise ValueError("Тип тензора расходится с паспортом.")
        return session
    except Exception as exc:
        raise ValueError("Не удалось открыть ONNX: " + str(exc)) from exc


def predict(session, passport, rows):
    try:
        feeds = {}
        for tensor, spec in zip(session.get_inputs(), passport["inputs"]):
            data = [[r[c] for c in spec["columns"]] for r in rows]
            array = np.asarray(data, dtype=object if spec["type"] == "string" else spec["type"])
            if len(tensor.shape) == 1 and array.shape[1] == 1:
                array = array[:, 0]
            feeds[tensor.name] = array
        output = session.run([passport["output"]], feeds)[0]
        if output.shape not in ((len(rows),), (len(rows), 1)):
            raise ValueError("Ожидается одна метка на строку.")
        labels = output.flatten().tolist()
        if any(type(label) is not type(passport["classes"][0]) or label not in passport["classes"] for label in labels):
            raise ValueError("Модель вернула неизвестный класс.")
        return labels
    except Exception as exc:
        raise ValueError("Не удалось проверить сигнатуру или прогноз ONNX. " +
                         (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)) from exc


def probe_model(path, passport, rows):
    predict(open_model(path, passport), passport, rows)
