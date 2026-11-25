"""Чтение типизированной таблицы с одинаковой проверкой для A11 и подготовки."""

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as csv
import pyarrow.parquet as pq

from .input_schema import InputError, require


MIN_ROWS = 1_000
MAX_ROWS = 100_000
TYPES = {"integer": pa.int64(), "number": pa.float64(), "string": pa.string(), "boolean": pa.bool_()}


def data_file(directory):
    paths = [p for p in (directory / "data.csv", directory / "data.parquet") if p.is_file()]
    require(len(paths) == 1, "data", "Нужен ровно один файл данных CSV или Parquet.")
    return paths[0]


def _column(array, column, from_csv):
    name = column["name"]
    field = "data." + name
    declared = column["type"]
    if pa.types.is_dictionary(array.type):
        array = array.dictionary_decode()
    source = array.type
    if pa.types.is_null(source):
        return pa.nulls(len(array), type=TYPES.get(declared, pa.timestamp("us", tz="UTC")))
    is_text = pa.types.is_string(source) or pa.types.is_large_string(source)
    if is_text and not from_csv:
        # В Parquet логическая метка UTF8 сама по себе не проверяет содержимое.
        try:
            array.validate(full=True)
        except pa.ArrowInvalid as exc:
            raise InputError(field, "Строковое значение содержит недопустимую последовательность UTF-8.") from exc
    if declared == "datetime":
        if is_text:
            # Arrow требует смещение при разборе временной метки с зоной.
            return pc.cast(array, pa.timestamp("ns", tz="UTC"))
        require(pa.types.is_timestamp(source) and source.tz is not None,
                field, "Временная метка должна содержать часовой пояс.")
        return pc.cast(array, pa.timestamp(source.unit, tz="UTC"))
    if not from_csv:
        compatible = {"string": is_text, "boolean": pa.types.is_boolean(source),
                      "integer": pa.types.is_integer(source),
                      "number": pa.types.is_floating(source) or pa.types.is_integer(source) or pa.types.is_decimal(source)}
        require(compatible[declared], field, "Физический тип Parquet расходится с паспортом.")
    if from_csv and declared == "integer" and (column["role"] in ("id", "group", "target") or column.get("categories")):
        # С альтернативной записью (например, 001) int64 потерял бы исходный идентификатор.
        bad = pc.invert(pc.match_substring_regex(array, r"^(0|-?[1-9][0-9]*)$"))
        require(not pc.any(bad).as_py(), field, "Для сохранения точной записи идентификатора или категории укажите строковый тип.")
    return pc.cast(array, TYPES[declared])


def _check_values(array, column, passport, tensor_type):
    key = "data." + column["name"]
    require(not array.null_count or column["nullable"], key, "Паспорт запрещает пропуски в столбце.")
    if column["type"] == "number":
        require(not pc.any(pc.invert(pc.is_finite(array))).as_py(), key, "Число должно быть конечным.")
    if column["role"] in ("id", "group") and column["type"] == "string":
        require(not pc.any(pc.equal(array, "")).as_py(), key, "Идентификатор не может быть пустым.")
    categories = passport["classes"] if column["role"] == "target" else column.get("categories")
    if categories is not None:
        allowed = pa.array(categories, type=array.type)
        unknown = pc.and_(pc.is_valid(array), pc.invert(pc.is_in(array, value_set=allowed)))
        require(not pc.any(unknown).as_py(), key, "Значение отсутствует в объявленных классах или категориях.")
    if column.get("min") is not None or tensor_type in ("float32", "int32"):
        bounds = pc.min_max(array).as_py()
        low, high = bounds["min"], bounds["max"]
        if low is not None:
            if column.get("min") is not None:
                require(column["min"] <= low and high <= column["max"], key, "Значение вне диапазона паспорта.")
            if tensor_type == "float32":
                limit = float.fromhex("0x1.fffffep+127")
                require(low >= -limit and high <= limit, key, "Значение не помещается в тензор float32.")
            elif tensor_type == "int32":
                require(low >= -(2**31) and high < 2**31, key, "Значение не помещается в тензор int32.")


def read_table(directory: Path, passport: dict, *, max_bytes: int | None = None) -> pa.Table:
    """Прочитать таблицу по проверенному паспорту, сохраняя строковые категории и ID."""
    path = data_file(directory)
    from_csv = path.suffix == ".csv"
    columns = passport["columns"]
    names = [col["name"] for col in columns]
    tensors = {c: t["type"] for t in passport["inputs"] for c in t["columns"]}
    id_name = next(c["name"] for c in columns if c["role"] == "id")
    batches = []
    rows = 0
    size = 0
    try:
        if from_csv:
            source = csv.open_csv(
                path, read_options=csv.ReadOptions(use_threads=False),
                convert_options=csv.ConvertOptions(column_types=dict.fromkeys(names, pa.string()),
                    null_values=[""], strings_can_be_null=True, quoted_strings_can_be_null=False),
            )
            schema_names = source.schema.names
            iterator = source
        else:
            source = pq.ParquetFile(path)
            schema_names = source.schema_arrow.names
            require(MIN_ROWS <= source.metadata.num_rows <= MAX_ROWS, "data.rows", "Ожидается от 1 000 до 100 000 строк.")
            iterator = source.iter_batches(batch_size=2048, use_threads=False)
        require(len(schema_names) == len(set(schema_names)), "data.columns", "Имена столбцов таблицы повторяются.")
        missing = sorted(set(names) - set(schema_names))
        extra = sorted(set(schema_names) - set(names))
        require(not missing and not extra, "data.columns",
                f"Расхождение с паспортом. Отсутствуют: {', '.join(missing) or '—'}. Лишние: {', '.join(extra) or '—'}.")
        for batch in iterator:
            rows += len(batch)
            require(rows <= MAX_ROWS, "data.rows", "Допускается не более 100 000 строк.")
            arrays = []
            for column in columns:
                name = column["name"]
                try:
                    array = _column(batch.column(name), column, from_csv)
                    _check_values(array, column, passport, tensors.get(name))
                except (pa.ArrowException, OverflowError) as exc:
                    message = "Нужна дата ISO 8601 с часовым поясом." if column["type"] == "datetime" else "Значение не соответствует объявленному типу."
                    raise InputError("data." + name, message) from exc
                arrays.append(array)
            normalized = pa.RecordBatch.from_arrays(arrays, names=names)
            size += normalized.nbytes
            require(max_bytes is None or size <= max_bytes, "policy.memory_mb", "Типизированная таблица превышает доступную квоту памяти.")
            batches.append(normalized)
    except (pa.ArrowException, UnicodeError, OSError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("data", "Не удалось прочитать таблицу. Проверьте формат, кодировку UTF-8 и структуру строк.") from exc
    require(rows >= MIN_ROWS, "data.rows", "Нужно не менее 1 000 строк.")
    table = pa.Table.from_batches(batches)
    require(pc.count_distinct(table[id_name]).as_py() == rows, "data." + id_name, "Идентификаторы строк повторяются.")
    return table
