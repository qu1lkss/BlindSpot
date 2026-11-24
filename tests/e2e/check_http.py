"""Проверка опубликованного порта на хосте."""

import json
import sys
from urllib.request import urlopen


def check(address):
    with urlopen(address + "/studies", timeout=5) as response:
        assert response.status == 200
        assert isinstance(json.load(response)["items"], list)
    for path, expected in [
        ("/", "Исследования"),
        ("/static/app.js", 'fetch("/studies"'),
        ("/static/styles.css", ":root"),
    ]:
        with urlopen(address + path, timeout=5) as response:
            assert response.status == 200
            assert expected in response.read().decode("utf-8"), path
    print("OK — список, страница, JavaScript и CSS доступны через HTTP")


if __name__ == "__main__":
    check(sys.argv[1].rstrip("/"))
