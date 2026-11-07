#!/usr/bin/env python3
"""Demonstrate upload/read availability and re-replication after one DataNode loss."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.dfs_client import DFSClient


def docker(*args: str) -> str:
    result = subprocess.run(["docker", "compose", *args], check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return result.stdout


def main() -> None:
    base = os.getenv("DFS_NAMENODE_URL", "http://localhost:8100").rstrip("/")
    client = DFSClient(verify=os.getenv("DFS_CA_BUNDLE") or True)
    client.base_url = base
    username = os.getenv("DFS_TEST_USER", "juan")
    client.login(username, os.getenv("DFS_TEST_PASSWORD", "contrasena123"))
    remote_dir = "failure-demo"
    remote_file = f"{remote_dir}/node-loss.bin"
    docker("start", "datanode1")
    try:
        try:
            client.rm(remote_file)
        except Exception:
            pass
        try:
            client.rmdir(remote_dir)
        except Exception:
            pass
        client.mkdir(remote_dir)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "node-loss.bin"
            recovered = Path(directory) / "recovered.bin"
            source.write_bytes(os.urandom(10 * 1024 * 1024 + 13))
            docker("stop", "datanode1")
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    if len(client.cluster()["nodes"]) == 2:
                        break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("Control plane did not detect the stopped DataNode")
            client.upload(str(source), remote_file)
            client.download(remote_file, str(recovered))
            if source.read_bytes() != recovered.read_bytes():
                raise RuntimeError("Download fallback after node loss returned different bytes")
            print("PASS: upload and download completed with one DataNode down and two replicas")

        docker("start", "datanode1")
        deadline = time.time() + 90
        metadata = None
        while time.time() < deadline:
            time.sleep(3)
            try:
                raw = docker("exec", "-T", "namenode", "cat", "/data/metadata.json")
                metadata = json.loads(raw)
                blocks = metadata["files"][f"/users/{username}/failure-demo/node-loss.bin"]["blocks"]
                if blocks and all("dn1" in block["replicas"] for block in blocks):
                    break
            except Exception:
                continue
        else:
            raise RuntimeError("Re-replication to the restored DataNode was not observed")
        print(f"PASS: restored DataNode received a replica for all {len(blocks)} blocks")
        client.rm(remote_file)
        client.rmdir(remote_dir)
    finally:
        docker("start", "datanode1")


if __name__ == "__main__":
    main()
