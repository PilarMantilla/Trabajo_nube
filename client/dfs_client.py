"""DFS CLI client. Node locations are discovered from the control-plane manifest."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


class DFSClient:
    def __init__(self, namenode_host: str = "localhost", namenode_port: int = 8100,
                 *, scheme: str | None = None, verify: bool | str | None = None):
        scheme = scheme or os.getenv("DFS_SCHEME", "http")
        self.base_url = os.getenv("DFS_NAMENODE_URL", f"{scheme}://{namenode_host}:{namenode_port}").rstrip("/")
        self.verify = verify if verify is not None else os.getenv("DFS_CA_BUNDLE") or True
        if isinstance(self.verify, str) and self.verify.lower() in {"false", "0", "no"}:
            self.verify = False
        self.session = requests.Session()
        self.token: str | None = None
        self.username: str | None = None
        self.cwd = "/"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.session.request(method, self.base_url + path, headers=headers,
                                        verify=self.verify, timeout=kwargs.pop("timeout", 30), **kwargs)
        return response

    def login(self, username: str, password: str) -> None:
        response = self.session.post(self.base_url + "/v1/auth/login", json={"username": username,
            "password": password}, verify=self.verify, timeout=10)
        response.raise_for_status()
        self.token = response.json()["access_token"]
        self.username = username

    def _remote(self, path: str) -> str:
        if not self.username:
            raise RuntimeError("Login is required")
        raw = path.strip()
        if raw.startswith("/users/") or raw.startswith("users/"):
            parts = raw.lstrip("/").split("/")
            if len(parts) < 2 or parts[1] != self.username:
                raise PermissionError("Path is outside the current user's namespace")
            raw = "/".join(parts[2:])
            base_parts: list[str] = []
        elif raw == f"/{self.username}" or raw == self.username:
            raw = ""
            base_parts = []
        elif raw.startswith(f"/{self.username}/"):
            raw = raw[len(self.username) + 2:]
            base_parts = []
        else:
            base_parts = [] if raw.startswith("/") else [part for part in self.cwd.split("/") if part]
            raw = raw.lstrip("/")
        for part in raw.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                if base_parts:
                    base_parts.pop()
                continue
            base_parts.append(part)
        return f"/users/{self.username}" + (f"/{'/'.join(base_parts)}" if base_parts else "")

    def cluster(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/cluster")
        response.raise_for_status()
        return response.json()

    def mkdir(self, path: str) -> None:
        response = self._request("PUT", "/v1/dirs", json={"path": self._remote(path)})
        response.raise_for_status()

    def ls(self, path: str = "/") -> list[str]:
        response = self._request("GET", "/v1/dirs", params={"path": self._remote(path)})
        response.raise_for_status()
        return [f"{item['name']}{'/' if item['type'] == 'directory' else ''}" for item in response.json()["entries"]]

    def change_directory(self, path: str) -> str:
        remote = self._remote(path)
        response = self._request("GET", "/v1/dirs", params={"path": remote})
        response.raise_for_status()
        prefix = f"/users/{self.username}"
        self.cwd = remote[len(prefix):] or "/"
        return self.cwd

    def pwd(self) -> str:
        return self.cwd

    def rmdir(self, path: str) -> None:
        response = self._request("DELETE", "/v1/dirs", params={"path": self._remote(path)})
        response.raise_for_status()

    def rm(self, path: str) -> None:
        response = self._request("DELETE", "/v1/files", params={"path": self._remote(path)})
        response.raise_for_status()

    def _current_version(self, remote_path: str) -> int | None:
        response = self._request("GET", "/v1/files/manifest", params={"path": remote_path})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return int(response.json()["version"])

    def acquire_lock(self, path: str, ttl_seconds: int = 30) -> str:
        response = self._request("POST", "/v1/locks", json={"path": self._remote(path), "ttl_seconds": ttl_seconds})
        response.raise_for_status()
        return response.json()["lock_id"]

    def release_lock(self, lock_id: str) -> None:
        response = self._request("DELETE", f"/v1/locks/{quote(lock_id)}")
        response.raise_for_status()

    def _upload(self, local_path: str, remote_path: str, lock_id: str | None = None) -> dict[str, Any]:
        local = Path(local_path)
        if not local.is_file():
            raise FileNotFoundError(local_path)
        cluster = self.cluster()
        block_size = int(cluster["block_size_bytes"])
        size = local.stat().st_size
        block_hashes: list[str] = []
        file_hash = hashlib.sha256()
        with local.open("rb") as stream:
            while True:
                data = stream.read(block_size)
                if not data:
                    break
                block_hashes.append(hashlib.sha256(data).hexdigest())
                file_hash.update(data)
        remote = self._remote(remote_path)
        base_version = self._current_version(remote)
        body: dict[str, Any] = {"path": remote, "size_bytes": size, "block_hashes": block_hashes,
                                "lock_id": lock_id}
        if base_version is not None:
            body["base_version"] = base_version
        begin = self._request("POST", "/v1/uploads", json=body)
        begin.raise_for_status()
        plan = begin.json()
        receipts: list[str] = []
        try:
            with local.open("rb") as stream:
                for block in plan["blocks"]:
                    stream.seek(block["index"] * block_size)
                    data = stream.read(block["size_bytes"])
                    successes = 0
                    errors: list[str] = []
                    for target in block["targets_info"]:
                        try:
                            response = self.session.put(f"{target['url']}/v1/blocks/{block['id']}", data=data,
                                headers={"Authorization": f"Bearer {target['token']}",
                                         "X-Block-SHA256": block["sha256"]},
                                verify=self.verify, timeout=120)
                            response.raise_for_status()
                            receipts.append(response.json()["receipt"])
                            successes += 1
                        except requests.RequestException as exc:
                            errors.append(f"{target['node_id']}: {exc}")
                    if successes < int(plan["write_quorum"]):
                        raise RuntimeError(f"Block {block['index']} stored on {successes} nodes; quorum is "
                                           f"{plan['write_quorum']}. " + "; ".join(errors))
            commit = self._request("POST", f"/v1/uploads/{plan['upload_id']}/commit",
                json={"receipts": receipts, "file_sha256": file_hash.hexdigest()})
            commit.raise_for_status()
            return commit.json()
        except Exception:
            self._request("DELETE", f"/v1/uploads/{plan['upload_id']}")
            raise

    def upload(self, local_path: str, remote_path: str) -> dict[str, Any]:
        lock_id = self.acquire_lock(remote_path)
        try:
            return self._upload(local_path, remote_path, lock_id)
        finally:
            self.release_lock(lock_id)

    def manifest(self, remote_path: str, version: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"path": self._remote(remote_path)}
        if version is not None:
            params["version"] = version
        response = self._request("GET", "/v1/files/manifest", params=params)
        response.raise_for_status()
        return response.json()

    def open(self, remote_path: str, version: int | None = None) -> dict[str, Any]:
        """Open a stable manifest version for a read operation."""
        return self.manifest(remote_path, version)

    def download(self, remote_path: str, local_path: str, version: int | None = None) -> dict[str, Any]:
        manifest = self.manifest(remote_path, version)
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
        file_hash = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as output:
                for block in manifest["blocks"]:
                    last_error: Exception | None = None
                    data: bytes | None = None
                    for replica in block["replicas"]:
                        try:
                            response = self.session.get(f"{replica['url']}/v1/blocks/{block['id']}",
                                headers={"Authorization": f"Bearer {replica['token']}"},
                                verify=self.verify, timeout=30)
                            response.raise_for_status()
                            candidate = response.content
                            if hashlib.sha256(candidate).hexdigest() != block["sha256"]:
                                raise ValueError("block checksum mismatch")
                            data = candidate
                            break
                        except Exception as exc:
                            last_error = exc
                    if data is None:
                        raise RuntimeError(f"No valid replica for block {block['index']}: {last_error}")
                    output.write(data)
                    file_hash.update(data)
                output.flush()
                os.fsync(output.fileno())
            if os.path.getsize(tmp_name) != manifest["size_bytes"]:
                raise ValueError("Reconstructed file size mismatch")
            if file_hash.hexdigest() != manifest["sha256"]:
                raise ValueError("Reconstructed file SHA-256 mismatch")
            os.replace(tmp_name, target)
            return {"size_bytes": manifest["size_bytes"], "sha256": manifest["sha256"],
                    "version": manifest["version"]}
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def read(self, remote_path: str, offset: int = 0, length: int | None = None,
             version: int | None = None) -> bytes:
        if offset < 0 or (length is not None and length < 0):
            raise ValueError("offset and length must be non-negative")
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "read.bin"
            self.download(remote_path, str(local), version)
            with local.open("rb") as stream:
                stream.seek(offset)
                return stream.read(length)

    def write(self, remote_path: str, offset: int, data: bytes) -> dict[str, Any]:
        """Copy-on-write patch: downloads the current version and uploads a new version."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "patched.bin"
            try:
                self.download(remote_path, str(local))
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
                local.write_bytes(b"")
            with local.open("r+b") as stream:
                stream.seek(offset)
                stream.write(data)
            lock_id = self.acquire_lock(remote_path)
            try:
                return self._upload(str(local), remote_path, lock_id)
            finally:
                self.release_lock(lock_id)
