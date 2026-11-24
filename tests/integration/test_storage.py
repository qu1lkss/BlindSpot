from pathlib import Path
import sqlite3
import tempfile
import unittest

from blindspot.storage import StudyStore


class StudyStoreTest(unittest.TestCase):
    def test_new_store_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudyStore(Path(temp) / "data")
            store.initialize()
            self.assertEqual(store.list_studies(), [])

    def test_restart_keeps_studies_in_display_order(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            store = StudyStore(path)
            store.initialize()
            rows = [
                ("a", "2026-01-01T10:00:00Z", "черновик", 0),
                ("b", "2026-01-02T10:00:00Z", "проверена", 1),
            ]
            with sqlite3.connect(store.database) as db:
                db.executemany("INSERT INTO research VALUES (?, ?, ?, ?)", rows)

            reopened = StudyStore(path)
            reopened.initialize()
            self.assertEqual([tuple(row.values()) for row in reopened.list_studies()], rows[::-1])


if __name__ == "__main__":
    unittest.main()
