"""Запускается в отдельном контейнере с --network none."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen
import os


with TemporaryDirectory() as temp:
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "blindspot.api:app", "--port", "8000"],
        env={**os.environ, "BLINDSPOT_DATA": temp},
    )
    try:
        deadline = monotonic() + 10
        while True:
            try:
                with urlopen("http://127.0.0.1:8000/studies", timeout=1) as response:
                    assert json.load(response) == {"items": []}
                break
            except URLError:
                if server.poll() is not None or monotonic() >= deadline:
                    raise
                sleep(0.1)
        assert (Path(temp) / "blindspot.sqlite3").is_file()
        print("OK — приложение запускается без внешней сети")
    finally:
        server.terminate()
        server.wait(timeout=5)
