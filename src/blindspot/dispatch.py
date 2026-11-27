"""Команды доверенного диспетчера; рабочий контейнер не видит основную SQLite."""
import json
import os
from pathlib import Path
import sys

from . import baseline
from .storage import StudyStore


def main():
    store = StudyStore(Path(os.environ["BLINDSPOT_DATA"]))
    action = sys.argv[1]
    if action == "claim":
        return baseline.claim(store)
    study, task = sys.argv[2:]
    report = json.load(sys.stdin)
    if action == "publish":
        baseline.publish(store, study, task, report)
    elif action == "fail":
        with store.connection(write=True) as db:
            baseline.fail(db, study, task, report["reason"])
    else:
        raise ValueError(action)


if __name__ == "__main__":
    print(json.dumps(main()))
