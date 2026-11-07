#!/usr/bin/env python3
"""Reproducible online acceptance smoke checks against a running DFSha stack."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.dfs_client import DFSClient


def main() -> None:
    base = os.getenv("DFS_NAMENODE_URL", "http://localhost:8100").rstrip("/")
    verify = os.getenv("DFS_CA_BUNDLE") or True
    if isinstance(verify, str) and verify.lower() in {"false", "0", "no"}:
        verify = False
    user = DFSClient(verify=verify)
    user.base_url = base
    username = os.getenv("DFS_TEST_USER", "juan")
    user.login(username, os.getenv("DFS_TEST_PASSWORD", "contrasena123"))
    outsider = DFSClient(verify=verify)
    outsider.base_url = base
    outsider_user = os.getenv("DFS_TEST_OUTSIDER", "nico" if username != "nico" else "juan")
    outsider_password = os.getenv("DFS_TEST_OUTSIDER_PASSWORD", "12345678" if outsider_user == "nico" else "contrasena123")
    outsider.login(outsider_user, outsider_password)
    competitor = DFSClient(verify=verify)
    competitor.base_url = base
    competitor.login(username, os.getenv("DFS_TEST_PASSWORD", "contrasena123"))

    cluster = user.cluster()
    assert len(cluster["nodes"]) >= 3, f"Expected 3 DataNodes, found {len(cluster['nodes'])}"
    try:
        user.rm("acceptance/payload.bin")
    except requests.HTTPError:
        pass
    try:
        user.rmdir("acceptance")
    except requests.HTTPError:
        pass
    user.mkdir("acceptance")
    assert user.change_directory("acceptance") == "/acceptance"
    assert user.pwd() == "/acceptance"
    assert user.change_directory("..") == "/"
    payload = os.urandom(20 * 1024 * 1024 + 137)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.bin"
        recovered = Path(directory) / "recovered.bin"
        old_version = Path(directory) / "old-version.bin"
        source.write_bytes(payload)
        result = user.upload(str(source), "acceptance/payload.bin")
        assert result["version"] == 1
        manifest = user.open("acceptance/payload.bin")
        assert len(manifest["blocks"]) >= 3
        assert all(len(block["replicas"]) >= 2 for block in manifest["blocks"])
        downloaded = user.download("acceptance/payload.bin", str(recovered))
        assert hashlib.sha256(recovered.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
        print(f"PASS upload/download: {downloaded['size_bytes']} bytes, SHA-256 identical")

        sample = user.read("acceptance/payload.bin", offset=1024, length=128)
        assert sample == payload[1024:1152]
        patch = b"DFSha-write-test"
        updated = user.write("acceptance/payload.bin", 4096, patch)
        assert updated["version"] == 2
        user.download("acceptance/payload.bin", str(old_version), version=1)
        assert old_version.read_bytes() == payload
        print("PASS RF3: partial read/write and immutable prior version")

        lock_id = user.acquire_lock("acceptance/payload.bin")
        response = outsider.session.get(base + "/v1/files/manifest",
            params={"path": f"/users/{username}/acceptance/payload.bin"},
            headers={"Authorization": f"Bearer {outsider.token}"}, verify=verify, timeout=10)
        assert response.status_code == 403, f"Cross-user access returned {response.status_code}"
        try:
            competitor.acquire_lock("acceptance/payload.bin")
            raise AssertionError("Expected a lock conflict")
        except requests.HTTPError as exc:
            assert exc.response is not None and exc.response.status_code == 423
        finally:
            user.release_lock(lock_id)
        print("PASS authorization and writer lock: foreign path denied; competing lock returns 423")

    user.rm("acceptance/payload.bin")
    user.rmdir("acceptance")
    print("Acceptance smoke checks passed. Run the separate node-failure and performance scenarios from README.md.")


if __name__ == "__main__":
    main()
