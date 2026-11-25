"""Детерминированное разделение строк F1/F2 по паспорту проверенной таблицы."""

from collections import defaultdict
import random

import pyarrow as pa

from blindspot.input_schema import InputError


def _stratified_search(labels, ids, rng):
    by_class = defaultdict(list)
    for i, label in enumerate(labels):
        by_class[label].append(i)
    # Распределяем ровно round(3N/5) мест по наибольшим остаткам классов.
    # Начальная перестановка разрешает равенства без зависимости от порядка CSV.
    classes = sorted(by_class)
    rng.shuffle(classes)
    counts = {label: 3 * len(by_class[label]) // 5 for label in classes}
    extra = (3 * len(ids) + 2) // 5 - sum(counts.values())
    for label in sorted(classes, key=lambda v: 3 * len(by_class[v]) % 5, reverse=True)[:extra]:
        counts[label] += 1
    chosen = set()
    for label in classes:
        rows = sorted(by_class[label], key=ids.__getitem__)
        rng.shuffle(rows)
        chosen.update(rows[:counts[label]])
    return chosen


def _grouped_search(groups, rng, field):
    sizes = defaultdict(int)
    for group in groups:
        sizes[group] += 1
    if len(sizes) < 2:
        raise InputError(field, "Для двух непустых выборок нужны как минимум две группы.")
    order = sorted(sizes)
    rng.shuffle(order)
    total = len(groups)
    target = (3 * total + 2) // 5
    # Бит k указывает, что сумма k достижима. Для каждой новой суммы
    # достаточно запомнить одну последнюю группу, чтобы восстановить выборку.
    reachable = 1
    parent = [None] * (total + 1)
    for group in order:
        shifted = reachable << sizes[group]
        new = shifted & ~reachable
        reachable |= shifted
        while new:
            bit = new & -new
            parent[bit.bit_length() - 1] = group
            new ^= bit
        if parent[target] is not None:
            break
    if parent[target] is None:
        target = min((i for i in range(1, total) if parent[i] is not None),
                     key=lambda i: (abs(5 * i - 3 * total), i))
    chosen_groups = set()
    while target:
        group = parent[target]
        chosen_groups.add(group)
        target -= sizes[group]
    return {i for i, group in enumerate(groups) if group in chosen_groups}


def _temporal_parts(times, ids, groups, field):
    total = len(ids)
    order = sorted(range(total), key=lambda i: (times[i], ids[i]))
    if groups is not None:
        last = {groups[row]: pos for pos, row in enumerate(order)}
    last_seen = -1
    cut = None
    best_gap = None
    for pos in range(total - 1):
        row = order[pos]
        if groups is not None:
            last_seen = max(last_seen, last[groups[row]])
        if pos < last_seen or times[row] == times[order[pos + 1]]:
            continue
        size = pos + 1
        gap = abs(5 * size - 3 * total)
        if best_gap is None or gap < best_gap:
            cut, best_gap = size, gap
    if cut is None:
        detail = " без пересечения групп" if groups is not None else ""
        raise InputError(field, f"Невозможно провести строгую хронологическую границу{detail}.")
    return order[:cut], order[cut:]


def split_rows(table: pa.Table, passport: dict, seed: int) -> tuple[list[int], list[int]]:
    """Вернуть индексы F1 и F2. Вход прошёл read_table и check_passport."""
    roles = {column["role"]: column["name"] for column in passport["columns"]}
    ids = table[roles["id"]].to_pylist()
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise InputError("data." + roles["id"], "Для разбиения нужны минимум две строки с уникальными ID.")
    mode = passport["dependence"]
    groups = table[roles["group"]].to_pylist() if mode in ("grouped", "grouped_temporal") else None
    if mode in ("temporal", "grouped_temporal"):
        # Целые значения сохраняют исходную точность вплоть до наносекунд.
        times = table[roles["time"]].cast(pa.int64()).to_pylist()
        return _temporal_parts(times, ids, groups, "data." + roles["time"])
    rng = random.Random(seed)
    if groups is None:
        chosen = _stratified_search(table[roles["target"]].to_pylist(), ids, rng)
    else:
        chosen = _grouped_search(groups, rng, "data." + roles["group"])
    order = sorted(range(len(ids)), key=ids.__getitem__)
    return [i for i in order if i in chosen], [i for i in order if i not in chosen]
