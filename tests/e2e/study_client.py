"""Небольшой клиент для испытаний настоящего HTTP API."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


class Client:
    def __init__(self, address):
        self.address = address.rstrip("/")

    def request(self, path, method="GET", data=None, headers=None):
        req = Request(self.address + path, data=data, method=method, headers=headers or {})
        try:
            response = urlopen(req, timeout=320)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def command(self, study, action, revision=None, key=None):
        return self.request(f"/studies/{study['id']}/{action}", "POST", b"", {
            "Idempotency-Key": key or str(uuid4()),
            "If-Match": str(study["revision"] if revision is None else revision),
        })

    def upload(self, directory, overrides=None, key=None):
        overrides = overrides or {}
        boundary = uuid4().hex
        parts = []
        inputs = [directory / name for name in ("model.onnx", "passport.json", "policy.json", "methods.json")]
        inputs.append(next(path for path in directory.glob("data.*") if path.suffix in (".csv", ".parquet")))
        for path in inputs:
            parts.extend([
                f'--{boundary}\r\nContent-Disposition: form-data; name="{path.stem}"; filename="{path.name}"\r\n\r\n'.encode(),
                overrides.get(path.stem, path.read_bytes()), b"\r\n",
            ])
        parts.append(f"--{boundary}--\r\n".encode())
        return self.request("/studies", "POST", b"".join(parts), {
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "If-Match": "0", "Idempotency-Key": key or str(uuid4()),
        })
