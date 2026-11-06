"""HTTPS transports for W2B. No cloud/storage credentials embedded in Worker.

Backend must implement scoped grants and authoritative integrity verification.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .bootstrap import _validate_url
from .recovery import BootstrapError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError("redirect refused; configure a pinned final origin")


class HTTPTransport:
    def __init__(self, *, backend_url: str, auth_token: str,
                 rustfs_hosts: frozenset[str], worker_id: str, engine: str,
                 max_response_bytes: int = 2_000_000, timeout: int = 30):
        self.backend = backend_url.rstrip("/")
        base = urllib.parse.urlsplit(self.backend)
        if base.scheme != "https" or not base.hostname or base.username or base.password:
            raise ValueError("Backend must be HTTPS with no URL credentials")
        if not auth_token:
            raise ValueError("worker auth token required")
        self.token = auth_token
        self.rustfs_hosts = rustfs_hosts
        self.worker_id = worker_id
        self.engine = engine
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, method, path, data=None, *, headers=None):
        payload = None if data is None else json.dumps(data, separators=(",", ":")).encode()
        req = urllib.request.Request(
            self.backend + path, data=payload, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise BootstrapError("Backend response too large")
                return response.status, json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 409):
                data = exc.read(self.max_response_bytes + 1)
                if len(data) > self.max_response_bytes:
                    raise BootstrapError("Backend error response too large")
                return exc.code, json.loads(data or b"{}")
            raise BootstrapError("Backend denied or unavailable") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise BootstrapError("Backend request failed") from exc

    def init(self):
        return self._request("POST", "/internal/v1/workers/init", {
            "workerId": self.worker_id, "engineCode": self.engine,
            "supportedManifestSchemaVersions": [1]
        })

    def download(self, url, target, expected_size, allowed_hosts):
        _validate_url(url, allowed_hosts)
        req = urllib.request.Request(url, method="GET")
        try:
            with self.opener.open(req, timeout=self.timeout) as source:
                if source.status != 200:
                    raise BootstrapError("upstream download did not return 200")
                count = 0
                while True:
                    chunk = source.read(min(1024 * 1024, expected_size + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > expected_size:
                        raise BootstrapError("upstream download exceeds declared size")
                    target.write(chunk)
        except urllib.error.URLError as exc:
            raise BootstrapError("upstream download unavailable") from exc

    def upload(self, asset, path):
        status, grant = self._request("POST", "/internal/v1/worker-model-proposals/upload-grants", {
            "workerId": self.worker_id, "sha256": asset["sha256"],
            "sizeBytes": asset["sizeBytes"], "assetId": asset["assetId"]
        }, headers={"Idempotency-Key": asset["sha256"]})
        if status != 200 or not isinstance(grant, dict):
            raise BootstrapError("upload grant unavailable")
        url = _validate_url(grant.get("putUrl"), self.rustfs_hosts)
        key = grant.get("objectKey")
        if not isinstance(key, str) or key != "models/sha256/" + asset["sha256"]:
            raise BootstrapError("invalid grant object key")
        # urllib accepts a file object as request body, streaming from disk.
        with path.open("rb") as f:
            req = urllib.request.Request(url, data=f, method="PUT", headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(asset["sizeBytes"])
            })
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    if response.status not in (200, 201, 204):
                        raise BootstrapError("upload was not accepted")
            except urllib.error.URLError as exc:
                raise BootstrapError("RustFS upload unavailable") from exc
        return key

    def object_exists(self, key, asset):
        # Backend must validate object metadata AND digest (metadata alone is not integrity proof).
        status, response = self._request("POST", "/internal/v1/worker-model-proposals/verify-asset", {
            "objectKey": key, "sha256": asset["sha256"], "sizeBytes": asset["sizeBytes"]
        })
        if status != 200:
            raise BootstrapError("storage integrity check unavailable")
        return response.get("verified") is True

    def propose(self, candidate, key):
        status, response = self._request("POST", "/internal/v1/worker-model-proposals",
                                         candidate, headers={"Idempotency-Key": key})
        if status not in (200, 201, 202):
            raise BootstrapError("proposal rejected")
        return response

    def manifest_covers(self, manifest, candidate):
        """Fail closed unless Backend published exactly the candidate capabilities."""
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
            return False
        approved = {}
        for model in manifest.get("models", []):
            identity = (model.get("engineCode"), model.get("modelId"),
                        model.get("modelRevision"))
            approved[identity] = model
        for model in candidate["models"]:
            identity = (model["engineCode"], model["modelId"], model["modelRevision"])
            actual = approved.get(identity)
            if not actual or actual.get("licenseSpdx") != model["licenseSpdx"]:
                return False
            expected_artifacts = {(a["role"], a["assetId"], a["sha256"],
                                   a["sizeBytes"], a["rustfsKey"])
                                  for a in model["artifacts"]}
            actual_artifacts = {(a.get("role"), a.get("assetId"), a.get("sha256"),
                                 a.get("sizeBytes"), a.get("rustfsKey"))
                                for a in actual.get("artifacts", [])}
            if not expected_artifacts.issubset(actual_artifacts):
                return False
            expected_voices = {(v.get("voiceCode"), v.get("locale"))
                               for v in model["voices"]}
            actual_voices = {(v.get("voiceCode"), v.get("locale"))
                             for v in actual.get("voices", [])}
            if not expected_voices.issubset(actual_voices):
                return False
        return True
