from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from blindspot.storage import StudyError, StudyStore, digest


class DraftTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = StudyStore(self.root)
        self.store.initialize()
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "policy.json").write_text("{}")
        for name in ("data.csv", "model.onnx", "passport.json", "methods.json"):
            (incoming / name).touch()
        self.hashes = {f.name: digest(f) for f in incoming.iterdir()}
        self.study = self.store.create(incoming, self.hashes, "upload")

    def test_same_key_keeps_one_version(self):
        self.assertEqual(self.study, self.store.create(self.root / "absent", self.hashes, "upload"))
        with self.assertRaises(StudyError) as raised:
            self.store.create(self.root / "absent", {"policy.json": "other"}, "upload")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(len(self.store.list_studies()), 1)

    def test_concurrent_checks_only_publish_one_revision(self):
        study_id = self.study["id"]
        def check(_):
            return self.store.validate(study_id, 0, "same-key", lambda _: {"valid": True, "errors": []})
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(check, range(2))
        self.assertEqual(first, second)
        self.assertEqual(self.store.detail(study_id)["revision"], 1)
        with self.assertRaises(StudyError) as raised:
            self.store.validate(study_id, 0, str(uuid4()), lambda _: self.fail())
        self.assertEqual(raised.exception.status, 409)

    def test_missing_version(self):
        with self.assertRaises(StudyError) as raised:
            self.store.detail(str(uuid4()))
        self.assertEqual(raised.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
