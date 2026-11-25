from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from blindspot.input_schema import InputError, Passport, check_passport
from blindspot.input_table import read_table


def passport():
    columns = []
    numeric = [f"x{i}" for i in range(9)]
    categories = ["01", "1", " A "]
    for name, role, kind in [("row", "id", "string"), ("truth", "target", "string"),
                             ("instant", "time", "datetime"), ("kind", "feature", "string")]:
        columns.append(dict(name=name, role=role, type=kind, description=name, nullable=False,
                            unit=None, precision=None))
    columns[-1]["categories"] = categories
    columns.extend(dict(name=n, role="feature", type="number", description=n, nullable=True,
                        unit="кг", precision=2, min=0, max=1) for n in numeric)
    source = dict(author="Тест", source="Синтетический пример", received_at="2025-01-01T00:00:00Z", version="1")
    return dict(columns=columns, features=["kind", *numeric], classes=["0", "1"],
                inputs=[dict(name="categories", columns=["kind"], type="string"),
                        dict(name="numbers", columns=numeric, type="float32")],
                output="label", dependence="temporal",
                provenance={k: dict(source) for k in ("model", "data", "passport", "policy", "methods")})


class InputTableTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.passport = passport()
        self.names = [c["name"] for c in self.passport["columns"]]

    def csv(self, change=None):
        rows = [",".join(self.names)]
        for i in range(1000):
            row = [f"{i:05}", str(i % 2), "2026-01-01T00:00:01+03:00", ["01", "1", " A "][i % 3], "", *(["0.55"] * 8)]
            if change and i == 999:
                index, value = change
                row[index] = value
            rows.append(",".join(row))
        (self.path / "data.csv").write_text("\n".join(rows), encoding="utf-8")

    def test_lexical_values_null_and_utc_in_both_formats(self):
        self.csv()
        table = read_table(self.path, self.passport)
        self.assertEqual(table["row"][0].as_py(), "00000")
        self.assertEqual(table["kind"].slice(0, 3).to_pylist(), ["01", "1", " A "])
        self.assertEqual(table["x0"].null_count, 1000)
        self.assertEqual(table["instant"][0].as_py().isoformat(), "2025-12-31T21:00:01+00:00")
        pq.write_table(table, self.path / "data.parquet")
        (self.path / "data.csv").unlink()
        self.assertTrue(table.equals(read_table(self.path, self.passport)))

    def test_rejects_error_in_last_row_with_field(self):
        for change, field in [((0, "00000"), "data.row"), ((1, "02"), "data.truth"),
                              ((2, "2026-01-01T00:00:00"), "data.instant"), ((3, "A"), "data.kind"),
                              ((5, "1.1"), "data.x1"), ((5, "nan"), "data.x1"), ((5, "abc"), "data.x1")]:
            with self.subTest(change=change):
                self.csv(change)
                with self.assertRaises(InputError) as error:
                    read_table(self.path, self.passport)
                self.assertEqual(error.exception.field, field)

    def test_parquet_lexical_source_type_is_checked(self):
        self.csv()
        table = read_table(self.path, self.passport)
        table = table.set_column(0, "row", pa.array(range(1000)))
        (self.path / "data.csv").unlink()
        pq.write_table(table, self.path / "data.parquet")
        with self.assertRaises(InputError) as error:
            read_table(self.path, self.passport)
        self.assertEqual(error.exception.field, "data.row")

    def test_invalid_utf8_anywhere_is_reported_in_russian(self):
        self.csv()
        file = self.path / "data.csv"
        good = file.read_bytes()
        file.write_bytes(good.replace(b"00999", b"0099\xff"))
        with self.assertRaisesRegex(InputError, "кодировку UTF-8"):
            read_table(self.path, self.passport)
        file.write_bytes(good)
        table = read_table(self.path, self.passport)
        raw = [f"{i:05}".encode() for i in range(999)] + [b"0099\xff"]
        column = pa.array(raw).cast(pa.string(), safe=False)
        pq.write_table(table.set_column(0, "row", column), self.path / "data.parquet")
        file.unlink()
        with self.assertRaisesRegex(InputError, "недопустимую последовательность UTF-8") as error:
            read_table(self.path, self.passport)
        self.assertEqual(error.exception.field, "data.row")

    def test_string_classes_preserve_distinct_spellings(self):
        self.csv()
        self.passport["classes"] = ["01", "1"]
        file = self.path / "data.csv"
        file.write_text(file.read_text().replace(",0,2026", ",01,2026"))
        self.assertEqual(read_table(self.path, self.passport)["truth"][:2].to_pylist(), ["01", "1"])
        file.write_text(file.read_text().replace("00999,1,2026", "00999,001,2026"))
        with self.assertRaisesRegex(InputError, "классах"):
            read_table(self.path, self.passport)

    def test_rejects_aliases_in_integer_identifiers(self):
        self.passport["columns"][0]["type"] = "integer"
        self.csv()
        with self.assertRaises(InputError) as error:
            read_table(self.path, self.passport)
        self.assertEqual(error.exception.field, "data.row")

    def test_mutability_requires_label_condition_and_provenance_requires_offset(self):
        check_passport(Passport.model_validate(self.passport))
        self.passport["columns"][5]["mutable"] = True
        with self.assertRaises(InputError) as error:
            check_passport(Passport.model_validate(self.passport))
        self.assertTrue(error.exception.field.endswith(".label_condition"))
        self.passport["columns"][5]["label_condition"] = "В пределах погрешности измерения."
        self.passport["provenance"]["data"]["received_at"] = "2026-01-01"
        with self.assertRaises(InputError) as error:
            check_passport(Passport.model_validate(self.passport))
        self.assertEqual(error.exception.field, "passport.provenance.data.received_at")
