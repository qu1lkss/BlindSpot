from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blindspot.knowledge import retrieve


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.folder, self.work = root / "F1", root / "work"
        self.folder.mkdir()
        self.marker = "закрытый-маркер-936"
        hidden = root / "confirmation"
        hidden.mkdir()
        (hidden / "secret.json").write_text(self.marker)
        self.study = "test-version-1"
        self.sources = {n: {"author": "Лаборатория", "source": n + ".json", "version": "v2",
                            "received_at": "2026-01-02T14:00:00+03:00"} for n in ("passport", "methods")}
        passport = {
            "provenance": self.sources,
            "columns": [{"role": "id", "name": "row_id"},
                        {"role": "feature", "name": "height", "description": "Высота изделия в мм", "min": 1, "max": 2},
                        {"role": "feature", "name": "mass", "description": "Масса детали", "mutable": True,
                         "label_condition": "Сохранить класс при компенсации погрешности"}]}
        catalog = {"methods": [{"id": "slice", "version": "2.1", "kind": "segment"},
                               {"id": "perturb", "version": "3", "kind": "noise", "evaluation": "Сравнить качество"}]}
        (self.folder / "passport.json").write_text(json.dumps(passport, ensure_ascii=False))
        (self.folder / "methods.json").write_text(json.dumps(catalog, ensure_ascii=False))

    def retrieve(self, query, history=(), method="slice", feature="height"):
        return retrieve(self.folder, self.work, self.study, method, feature, query, list(history))

    def record(self, id="A", **updates):
        return {"id": id, "version": "artifact-v1", "partition": "F1", "status": "completed",
                "method": "slice", "feature": "height", "origin": "experiments/" + id,
                "summary": "точность меньше у коротких изделий", "study_id": self.study, **updates}

    def test_empty_history_required_key_fallback_and_provenance(self):
        result = self.retrieve("неизвестно / *")
        self.assertEqual([f["id"] for f in result["fragments"]], ["passport:height", "method:slice"])
        for f in result["fragments"]:
            self.assertEqual(f["selection"], "required_key")
            self.assertIsNone(f["score"])
            self.assertIn("source", f["provenance"])
            self.assertIn("version", f)
        self.assertIs(json.loads(result["fragments"][0]["text"])["mutable"], False)
        self.assertEqual(result["fragments"][1]["version"], "2.1")
        self.assertEqual(result["fragments"][1]["provenance"]["version"], "v2")
        matched = self.retrieve("высота")
        self.assertEqual(matched["fragments"][0]["selection"], "required_match")
        self.assertLess(matched["fragments"][0]["score"], 0)

    def test_version_method_feature_filters_rank_ties_and_history(self):
        history = [self.record("B"), self.record("A"), self.record("wrong-method", method="perturb"),
                   self.record("wrong-feature", feature="mass"),
                   self.record("global", method="baseline", feature=None, summary="Общая accuracy 0,7500.")]
        result = self.retrieve("коротких", history)
        self.assertEqual([f["id"] for f in result["fragments"] if f["kind"] == "experiment"],
                         ["experiment:A", "experiment:B"])
        global_context = self.retrieve("другие формулировки", feature="mass", method="perturb")
        self.assertEqual(global_context["fragments"][-1]["id"], "experiment:global")
        self.assertEqual(global_context["fragments"][-1]["selection"], "history_scope")

    def test_idempotent_sync_persisted_audit_and_transaction_rollback(self):
        first = self.retrieve("точность", [self.record()])
        self.assertEqual(first, self.retrieve("точность", [self.record(), self.record()]))
        with self.assertRaisesRegex(ValueError, "содержимое"):
            self.retrieve("изменение", [self.record("new"), self.record(summary="Другая запись")])
        with closing(sqlite3.connect(self.work / "knowledge.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fragment WHERE kind='experiment'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM retrieval_log").fetchone()[0], 1)
            self.assertEqual(first, json.loads(db.execute("SELECT record FROM retrieval_log WHERE query_id=?",
                                                         (first["query_id"],)).fetchone()[0]))
        self.study = "test-version-2"
        with self.assertRaisesRegex(ValueError, "другой версии"):
            self.retrieve("другой запрос")

    def test_f2_marker_in_sibling_or_rejected_record_never_reaches_index_context_or_log(self):
        self.retrieve("базовый поиск")
        for invalid in ({"partition": "F2"}, {"study_id": "other"}, {"status": "running"}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "только завершённые опыты F1"):
                self.retrieve("поиск", [self.record(summary=self.marker, **invalid)])
        response = self.retrieve("повторный поиск")
        self.assertNotIn(self.marker, json.dumps(response, ensure_ascii=False))
        with closing(sqlite3.connect(self.work / "knowledge.sqlite3")) as db:
            self.assertNotIn(self.marker, "\n".join(db.iterdump()))
            self.assertEqual(db.execute("SELECT count(*) FROM fragment_fts WHERE fragment_fts MATCH ?",
                                        ('"закрытый"',)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
