from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from blindspot import baseline
from blindspot.storage import StudyError, StudyStore, digest


class BaselineBudgetTest(unittest.TestCase):
    def test_failed_attempt_is_charged_once(self):
        with TemporaryDirectory() as directory:
            store = StudyStore(Path(directory))
            store.initialize()
            inputs = Path(directory) / "incoming"
            inputs.mkdir()
            for name in ("model.onnx","data.csv","passport.json","methods.json"):
                (inputs/name).touch()
            (inputs/"policy.json").write_text('{"max_experiments":1,"search_experiments":1,"seed":7,"seconds":{"baseline":1,"total":60}}')
            study = store.create(inputs,{f.name:digest(f) for f in inputs.iterdir()},str(uuid4()))["id"]
            with store.connection(write=True) as db:
                db.execute("UPDATE research SET stage='подготовлена' WHERE id=?",(study,))
            result = baseline.start(store,study,0,"run")
            self.assertEqual(result,baseline.start(store,study,0,"run"))
            task = baseline.claim(store)
            self.assertEqual(task["id"],result["operation_id"])
            self.assertIsNone(baseline.claim(store))
            with store.connection(write=True) as db:
                db.execute("UPDATE experiment SET deadline=0 WHERE research_id=?",(study,))
                baseline.expire(db)
            detail = store.detail(study)
            self.assertEqual(detail["stage"],"отказ")
            self.assertEqual(detail["baseline"]["search_used"],1)
            self.assertEqual(detail["baseline"]["total_used"],1)
            self.assertEqual(detail["baseline"]["measurements"],[])
            with self.assertRaises(StudyError):
                baseline.predictions(store,study)
