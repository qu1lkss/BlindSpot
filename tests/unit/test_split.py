from collections import Counter
import unittest

import pyarrow as pa

from blindspot.datasets import split_rows
from blindspot.input_schema import InputError


def fixture(labels, groups=None, times=None):
    data = {"row": [f"{i:05}" for i in range(len(labels))], "truth": labels}
    columns = [{"name": "row", "role": "id"}, {"name": "truth", "role": "target"}]
    if groups is not None:
        data["subject"] = groups
        columns.append({"name": "subject", "role": "group"})
    if times is not None:
        data["when"] = pa.array(times, type=pa.timestamp("ns", tz="UTC"))
        columns.append({"name": "when", "role": "time"})
    mode = ("grouped_temporal" if groups is not None else "temporal") if times is not None else (
        "grouped" if groups is not None else "independent")
    return pa.table(data), {"columns": columns, "dependence": mode}


class SplitTest(unittest.TestCase):
    def split(self, case, seed=42):
        table, passport = case
        f1, f2 = split_rows(table, passport, seed)
        self.assertTrue(f1)
        self.assertTrue(f2)
        self.assertEqual(sorted(f1 + f2), list(range(len(table))))
        return f1, f2

    def test_exact_size_class_proportions_singleton_and_seed(self):
        case = fixture(["A"] * 499 + ["B"] * 500 + ["rare"])
        f1, f2 = self.split(case)
        self.assertEqual(len(f1), 600)
        counts = Counter(case[0]["truth"].take(f1).to_pylist())
        self.assertEqual(counts["A"], 299)
        self.assertEqual(counts["B"], 300)
        self.assertEqual(counts["rare"], 1)
        self.assertEqual((f1, f2), self.split(case))
        self.assertNotEqual(f1, self.split(case, seed=43)[0])

    def test_id_assignment_is_stable_when_input_order_changes(self):
        labels = ["A", "B"] * 20
        for groups, times in [(None, None), ([i // 3 for i in range(40)], None),
                              (None, list(range(40))), ([i // 3 for i in range(40)], list(range(40)))]:
            with self.subTest(grouped=groups is not None, temporal=times is not None):
                table, passport = fixture(labels, groups, times)
                rev = table.take(list(reversed(range(40))))
                a = self.split((table, passport))
                b = self.split((rev, passport))
                for rows, reversed_rows in zip(a, b):
                    self.assertEqual(table["row"].take(rows).to_pylist(), rev["row"].take(reversed_rows).to_pylist())

    def test_whole_groups_find_attainable_ratio(self):
        groups = ["red"] * 48 + ["green"] * 35 + ["blue"] * 12 + ["white"] * 5
        table, passport = fixture(["A"] * 100, groups)
        f1, f2 = self.split((table, passport))
        self.assertEqual(len(f1), 60)
        self.assertFalse(set(table["subject"].take(f1).to_pylist()) & set(table["subject"].take(f2).to_pylist()))
        with self.assertRaisesRegex(InputError, "две группы"):
            self.split(fixture(["A"] * 10, ["same"] * 10))

    def test_equal_instants_remain_together_and_nearest_feasible_cut_wins(self):
        f1, f2 = self.split(fixture(["A"] * 10, times=[1]*5 + [2]*3 + [3]*2))
        self.assertEqual((len(f1), len(f2)), (5, 5))
        with self.assertRaisesRegex(InputError, "границу"):
            self.split(fixture(["A"] * 10, times=[1]*10))

    def test_combined_dependency_including_conflicting_groups(self):
        table, passport = fixture(["A"] * 10, ["A","B","A","C","C","C","C","D","D","D"], list(range(10)))
        first, last = self.split((table, passport))
        self.assertEqual((len(first), len(last)), (7, 3))
        self.assertLess(max(first), min(last))
        self.assertFalse(set(table["subject"].take(first).to_pylist()) & set(table["subject"].take(last).to_pylist()))
        with self.assertRaisesRegex(InputError, "без пересечения групп"):
            self.split(fixture(["A"] * 4, ["one", "two", "two", "one"], [10,20,30,40]))


if __name__ == "__main__":
    unittest.main()
