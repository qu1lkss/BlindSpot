import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from blindspot.isolation import search_spec
from blindspot.storage import StudyError, StudyStore, digest
from blindspot.versions import prepare_version


class PublicationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = StudyStore(self.directory)
        self.store.initialize()
        source = next(Path("examples").glob("*/data.csv")).parent
        self.inputs = self.directory / "incoming"
        self.inputs.mkdir()
        for name in ("model.onnx", "passport.json", "policy.json", "methods.json", "data.csv"):
            shutil.copyfile(source / name, self.inputs / name)
        self.hashes = {p.name: digest(p) for p in self.inputs.iterdir()}
        self.study = self.store.create(self.inputs, self.hashes, "create")["id"]
        self.store.validate(self.study, 0, "checked", lambda _: {"valid": True, "errors": []})
        self.root = self.store.versions / self.study

    def test_crash_between_rename_and_commit(self):
        first = prepare_version(self.store, self.study, 1, "prepare")
        original = digest(self.root / "prepared/manifest.json")
        other = self.directory / "incoming"
        shutil.copytree(self.root / "inputs", other)
        second = self.store.create(other, self.hashes, "create-2")["id"]
        self.store.validate(second, 0, "checked", lambda _: {"valid": True, "errors": []})
        # В дочернем процессе прерываем сохранение сразу после реального rename.
        code = """
from pathlib import Path
import os, sys
from blindspot.storage import StudyStore
from blindspot.versions import prepare_version
rename = Path.rename
def interrupt(path, target):
    result = rename(path, target)
    if target.name == 'prepared':
        os._exit(71)
    return result
Path.rename = interrupt
prepare_version(StudyStore(Path(sys.argv[1])), sys.argv[2], 1, 'prepare')
"""
        failed = subprocess.run([sys.executable, "-c", code, str(self.directory), second])
        self.assertEqual(failed.returncode, 71)
        reopened = StudyStore(self.directory)
        reopened.initialize()
        self.assertEqual(reopened.detail(second)["stage"], "проверена")
        with reopened.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifact WHERE research_id=? AND kind='prepared'", (second,)).fetchone()[0], 0)
        self.assertEqual(reopened.detail(self.study)["preparation"], first["preparation"])
        self.assertEqual(digest(self.root / "prepared/manifest.json"), original)
        self.assertEqual(prepare_version(reopened, second, 1, "prepare")["stage"], "подготовлена")
        self.assertEqual(prepare_version(reopened, second, 1, "prepare")["revision"], 2)

    def test_changed_input_blocks_use(self):
        (self.root / "inputs/model.onnx").write_bytes(b"changed")
        with self.assertRaises(StudyError) as raised:
            prepare_version(self.store, self.study, 1, "prepare")
        self.assertEqual(raised.exception.body["code"], "integrity_error")
        self.assertEqual(self.store.detail(self.study)["stage"], "проверена")

    def test_search_spec_checks_files(self):
        prepare_version(self.store, self.study, 1, "prepare")
        self.assertEqual(search_spec(self.store, self.study)["id"], self.study)
        (self.root / "prepared/search/f1.parquet").write_bytes(b"changed")
        with self.assertRaises(StudyError) as raised:
            search_spec(self.store, self.study)
        self.assertEqual(raised.exception.body["code"], "integrity_error")


if __name__ == "__main__":
    unittest.main()
