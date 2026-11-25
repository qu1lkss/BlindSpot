"""Проверка комплекта I1–I3, C1/C2 до допуска к подготовке исследования."""

import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys

from pydantic import ValidationError

from .adapters.base import probe_model
from .input_schema import Catalog, InputError, Passport, Policy, check_catalog, check_passport, check_policy, require
from .input_table import data_file, read_table


MIB = 1024**2


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяется ключ JSON.")
        result[key] = value
    return result


def _json_constant(value):
    raise ValueError("Число должно быть конечным.")


def _parse(directory, key, model, check):
    path = directory / f"{key}.json"
    require(path.is_file(), key, "Обязательный файл отсутствует.")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_json_pairs,
                           parse_constant=_json_constant)
    except (ValueError, OSError, RecursionError) as exc:
        raise InputError(key, "Проверьте кодировку UTF-8, синтаксис, конечность чисел и уникальность ключей JSON.") from exc
    result = model.model_validate(value)
    check(result)
    return result


def _schema_errors(source, exc):
    messages = {
        "missing": "Обязательное поле отсутствует.",
        "extra_forbidden": "Неизвестное поле.",
        "literal_error": "Недопустимое значение. Ожидается {expected}.",
        "greater_than": "Значение должно превышать {gt}.",
        "greater_than_equal": "Значение должно быть не меньше {ge}.",
        "less_than_equal": "Значение должно быть не больше {le}.",
        "too_short": "Требуется не менее {min_length} элементов.",
        "too_long": "Допускается не более {max_length} элементов.",
        "string_pattern_mismatch": "Текст не может состоять только из пробелов.",
        "string_too_short": "Укажите непустой текст.",
    }
    for error in exc.errors(include_input=False, include_url=False):
        yield {
            "field": ".".join([source, *map(str, error["loc"])]),
            "message": messages.get(error["type"], "Значение имеет неверный тип или формат.").format(**error.get("ctx", {})),
        }


def _available_memory():
    if sys.platform == "darwin":
        # vm_stat выводит число страниц и размер страницы текущего хоста.
        import re
        output = subprocess.check_output(["/usr/bin/vm_stat"], text=True, timeout=3)
        page = int(re.search(r"page size of (\d+) bytes", output)[1])
        pages = sum(int(re.search(rf"Pages {kind}:\s+(\d+)", output)[1]) for kind in ("free", "inactive", "speculative"))
        return page * pages
    info = Path("/proc/meminfo").read_text()
    available = int(next(line for line in info.splitlines() if line.startswith("MemAvailable:")).split()[1]) * 1024
    # Учитываем ограничения контейнера и его родителей при общем cgroup namespace.
    root = Path("/sys/fs/cgroup")
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    for group in groups:
        _, controllers, relative = group.split(":", 2)
        if controllers == "":
            mount, maximum, usage = root, "memory.max", "memory.current"
        elif "memory" in controllers.split(","):
            mount, maximum, usage = root / "memory", "memory.limit_in_bytes", "memory.usage_in_bytes"
        else:
            continue
        path = mount.joinpath(*[part for part in Path(relative).parts if part not in ("/", "..")])
        while path.is_relative_to(mount):
            if (path / maximum).is_file():
                limit = (path / maximum).read_text().strip()
                if limit != "max":
                    available = min(available, max(0, int(limit) - int((path / usage).read_text())))
            if path == mount:
                break
            path = path.parent
    return available


def _peak_memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _resources(directory, policy, model_path, data_path):
    size = sum(path.stat().st_size for path in (model_path, data_path, *(directory / f"{k}.json" for k in ("passport", "policy", "methods"))))
    try:
        memory = _available_memory()
        disk = shutil.disk_usage(directory).free
    except (OSError, ValueError, StopIteration, AttributeError, TypeError, subprocess.SubprocessError) as exc:
        raise InputError("resources", "Не удалось измерить доступные память и диск.") from exc
    # Рабочая оценка для пробного вывода и копирования материалов с разбиением.
    # Размер раскодированной таблицы дополнительно контролируется по пакетам Arrow.
    return {
        "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "input_bytes": size,
        "memory_available_bytes": memory, "disk_available_bytes": disk,
        "memory_limit_bytes": policy.memory_mb * MIB, "disk_limit_bytes": policy.disk_mb * MIB,
        "memory_base_bytes": _peak_memory() + model_path.stat().st_size * 2,
        "disk_estimate_bytes": 2 * size,
    }


def validate_bundle(directory: Path) -> dict:
    errors = []
    report = {"valid": False, "errors": errors, "dimensions": {}, "resources": {}}
    parsed = {}
    for key, model, check in (("passport", Passport, check_passport), ("policy", Policy, check_policy),
                              ("methods", Catalog, check_catalog)):
        try:
            parsed[key] = _parse(directory, key, model, check)
        except ValidationError as exc:
            errors.extend(_schema_errors(key, exc))
        except InputError as exc:
            errors.append({"field": exc.field, "message": str(exc)})
    model_path = directory / "model.onnx"
    if not model_path.is_file() or not model_path.stat().st_size:
        errors.append({"field": "model", "message": "Нужен непустой файл модели ONNX."})
    try:
        table_path = data_file(directory)
    except InputError as exc:
        errors.append({"field": exc.field, "message": str(exc)})
    if errors:
        return report
    passport = parsed["passport"].model_dump()
    policy = parsed["policy"]
    try:
        limits = report["resources"] = _resources(directory, policy, model_path, table_path)
        require(limits["memory_limit_bytes"] <= limits["memory_available_bytes"], "policy.memory_mb",
                "Запрошенная квота превышает фактически доступную память.")
        require(limits["disk_limit_bytes"] <= limits["disk_available_bytes"], "policy.disk_mb",
                "Запрошенная квота превышает свободное место.")
        require(limits["disk_estimate_bytes"] <= limits["disk_limit_bytes"], "policy.disk_mb",
                "Квоты диска недостаточно для копии входов и подготовленной версии.")
        table_budget = (limits["memory_limit_bytes"] - limits["memory_base_bytes"]) // 2
        require(table_budget > 0, "policy.memory_mb", "Квоты памяти недостаточно для процесса проверки и модели.")
        table = read_table(directory, passport, max_bytes=table_budget)
        report["dimensions"] = {"rows": len(table), "features": len(passport["features"]), "classes": len(passport["classes"])}
        limits["table_bytes"] = table.nbytes
        limits["memory_estimate_bytes"] = limits["memory_base_bytes"] + table.nbytes * 2
        try:
            probe_model(model_path, passport, table.slice(0, min(len(table), 8)).to_pylist())
        except ValueError as exc:
            raise InputError("model", str(exc)) from exc
        limits["peak_memory_bytes"] = _peak_memory()
        require(limits["peak_memory_bytes"] <= limits["memory_limit_bytes"], "policy.memory_mb",
                "Измеренный пик памяти при пробном прогнозе превысил квоту.")
    except InputError as exc:
        errors.append({"field": exc.field, "message": str(exc)})
    report["valid"] = not errors
    return report
