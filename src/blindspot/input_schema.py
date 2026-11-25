"""Контракты I3, C1 и C2. Имена полей также используются в протоколе A11."""

from collections import Counter
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Label = str | int
Positive = Annotated[int, Field(gt=0)]


class InputError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def require(test, field, message):
    if not test:
        raise InputError(field, message)


class Schema(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="forbid")


class Source(Schema):
    author: Text
    source: Text
    received_at: Text
    version: Text


class Column(Schema):
    name: Text
    description: Text
    role: Literal["id", "target", "feature", "group", "time"]
    type: Literal["string", "integer", "number", "boolean", "datetime"]
    unit: str | None
    precision: Annotated[int, Field(ge=0)] | None
    nullable: bool
    min: int | float | None = None
    max: int | float | None = None
    categories: list[str | int | bool] | None = None
    mutable: bool = False
    label_condition: Text | None = None


class Tensor(Schema):
    name: Text
    columns: Annotated[list[Text], Field(min_length=1)]
    type: Literal["float32", "float64", "int32", "int64", "bool", "string"]


class Passport(Schema):
    columns: list[Column]
    features: Annotated[list[Text], Field(min_length=10, max_length=100)]
    classes: Annotated[list[Label], Field(min_length=2)]
    inputs: Annotated[list[Tensor], Field(min_length=1)]
    output: Text
    dependence: Literal["independent", "grouped", "temporal", "grouped_temporal"]
    provenance: dict[str, Source]


class Seconds(Schema):
    validation: Annotated[Positive, Field(le=300)]
    preparation: Annotated[Positive, Field(le=120)]
    baseline: Annotated[Positive, Field(le=300)]
    search: Annotated[Positive, Field(le=2580)]
    confirmation: Annotated[Positive, Field(le=600)]
    summary: Annotated[Positive, Field(le=120)]
    total: Annotated[Positive, Field(le=3600)]
    report: Annotated[Positive, Field(le=300)]
    export: Annotated[Positive, Field(le=600)]


class Policy(Schema):
    metrics: list[str]
    threshold: float
    alpha: float
    min_group: int
    search_fraction: float
    seed: Annotated[int, Field(ge=0, le=2**63-1)]
    max_experiments: Annotated[Positive, Field(le=100)]
    search_experiments: Annotated[Positive, Field(le=80)]
    confirmation_experiments: Annotated[int, Field(ge=0, le=20)]
    max_candidates: Annotated[int, Field(ge=0, le=20)]
    seconds: Seconds
    memory_mb: Positive
    disk_mb: Positive
    paid: bool = False
    money_limit: Annotated[float, Field(ge=0)] | None = None


class Method(Schema):
    id: Text
    version: Text
    kind: Literal["segment", "noise", "missing"]
    parameters: dict[str, str | bool | float | int]
    ranges: dict[str, Annotated[list[int | float], Field(min_length=2, max_length=2)]]
    prerequisites: Text
    impact_limits: Text
    evaluation: Text


class Catalog(Schema):
    methods: Annotated[list[Method], Field(min_length=1)]


def aware_datetime(value, field):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        raise InputError(field, "Нужна дата и время ISO 8601 с часовым поясом.") from None


def check_passport(passport):
    columns = {c.name: c for c in passport.columns}
    require(len(columns) == len(passport.columns), "passport.columns", "Имена столбцов должны быть уникальными.")
    roles = Counter(c.role for c in passport.columns)
    for role in ("id", "target", "group", "time"):
        expected = role in ("id", "target") or (
            role == "group" and passport.dependence in ("grouped", "grouped_temporal")
        ) or (role == "time" and passport.dependence in ("temporal", "grouped_temporal"))
        require(roles[role] == int(expected), "passport.columns",
                f"Для зависимости «{passport.dependence}» ожидается столбцов роли {role}: {int(expected)}.")
    expected_features = {c.name for c in passport.columns if c.role == "feature"}
    require(set(passport.features) == expected_features and len(passport.features) == len(expected_features),
            "passport.features", "Перечислите все признаки ровно один раз в порядке модели.")
    require(len(set(passport.classes)) == len(passport.classes), "passport.classes", "Классы повторяются.")
    for index, col in enumerate(passport.columns):
        key = f"passport.columns.{index}"
        if col.role == "time":
            require(col.type == "datetime", f"{key}.type", "Для времени нужен тип datetime.")
        if col.role in ("id", "group", "target"):
            require(col.type in ("string", "integer"), f"{key}.type", "Нужен строковый или целочисленный тип.")
        if col.role != "feature":
            require(not col.mutable and not col.nullable, key, "Служебное поле должно быть неизменяемым и обязательным.")
        if col.role == "feature":
            require(col.type != "datetime", f"{key}.type", "Поддерживаются числовые и категориальные признаки.")
            if col.type == "string":
                require(col.categories, f"{key}.categories", "Перечислите допустимые категории.")
            if col.type in ("integer", "number") and col.categories is None:
                require(col.min is not None and col.max is not None, key, "Задайте обе границы допустимого диапазона.")
        if col.mutable:
            require(col.label_condition, f"{key}.label_condition", "Опишите условия сохранения правильного класса.")
        if col.min is not None or col.max is not None:
            require(col.type in ("integer", "number") and col.min is not None and col.max is not None and col.min <= col.max,
                    key, "Нужны упорядоченные числовые границы min и max.")
        if col.categories is not None:
            scalar_type = {"integer": int, "string": str, "boolean": bool}.get(col.type)
            require(col.categories and all(type(v) is scalar_type for v in col.categories)
                    and len(set(col.categories)) == len(col.categories), f"{key}.categories",
                    "Перечислите уникальные категории объявленного типа.")
        if col.role == "target":
            require(all(type(v) is (int if col.type == "integer" else str) for v in passport.classes),
                    "passport.classes", "Тип меток должен совпадать с типом правильного класса.")
            if col.categories is not None:
                require(set(col.categories) == set(passport.classes), f"{key}.categories", "Категории цели должны совпадать с классами.")
        for value in (col.categories or []) + (passport.classes if col.role == "target" else []):
            if type(value) is int:
                require(-2**63 <= value < 2**63, key, "Целое значение должно помещаться в int64.")
        if col.min is not None and col.type == "integer":
            require(type(col.min) is int and type(col.max) is int and -2**63 <= col.min <= col.max < 2**63,
                    key, "Границы целочисленного поля должны помещаться в int64.")
    names = [t.name for t in passport.inputs]
    require(len(names) == len(set(names)), "passport.inputs", "Имена входных тензоров повторяются.")
    require([c for t in passport.inputs for c in t.columns] == passport.features, "passport.inputs",
            "Колонки тензоров должны воспроизвести полный порядок признаков.")
    for i, tensor in enumerate(passport.inputs):
        types = {"string": {"string"}, "bool": {"boolean"}, "int32": {"integer"}, "int64": {"integer"},
                 "float32": {"number", "integer"}, "float64": {"number", "integer"}}
        for name in tensor.columns:
            column = columns[name]
            require(column.type in types[tensor.type], f"passport.inputs.{i}.type", f"Тип тензора несовместим с «{name}».")
            require(not column.nullable or tensor.type in ("float32", "float64"), f"passport.inputs.{i}.type",
                    f"Для пропусков в «{name}» нужен вещественный тензор с представлением NaN.")
    for item in ("model", "data", "passport", "policy", "methods"):
        key = f"passport.provenance.{item}"
        require(item in passport.provenance, key, "Укажите происхождение материала.")
        aware_datetime(passport.provenance[item].received_at, key + ".received_at")


def check_policy(policy):
    metrics = {"accuracy", "macro_f1", "recall", "confusion_matrix"}
    require(set(policy.metrics) == metrics and len(policy.metrics) == len(metrics), "policy.metrics",
            "Укажите accuracy, macro_f1, recall и confusion_matrix по одному разу.")
    require(policy.search_fraction == 0.6, "policy.search_fraction", "Доля поиска должна равняться 0,6.")
    require(0.05 <= policy.threshold < 1, "policy.threshold", "Порог ухудшения должен быть от 0,05 до 1 исключительно справа.")
    require(0 < policy.alpha <= 0.05, "policy.alpha", "Уровень значимости должен быть больше 0 и не выше 0,05.")
    require(policy.min_group >= 50, "policy.min_group", "Минимальный размер группы — 50 объектов.")
    require(not policy.paid or policy.money_limit is not None, "policy.money_limit", "Для платных средств нужен денежный предел.")


def check_catalog(catalog):
    ids = set()
    for i, method in enumerate(catalog.methods):
        key = f"methods.methods.{i}"
        require(method.id not in ids, f"{key}.id", "ID методики повторяется.")
        ids.add(method.id)
        for name, bounds in method.ranges.items():
            require(bounds[0] <= bounds[1], f"{key}.ranges.{name}", "Нижняя граница превышает верхнюю.")
            require(name in method.parameters, f"{key}.ranges.{name}", "Укажите значение параметра.")
        for name, value in method.parameters.items():
            if type(value) in (int, float):
                require(name in method.ranges, f"{key}.ranges.{name}", "Для числового параметра нужен диапазон.")
            if name in method.ranges:
                low, high = method.ranges[name]
                require(type(value) in (float, int) and low <= value <= high, f"{key}.parameters.{name}",
                        "Параметр находится вне допустимого диапазона.")
