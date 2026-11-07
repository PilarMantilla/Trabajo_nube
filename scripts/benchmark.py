#!/usr/bin/env python3
"""Compare upload/download latency at one and multiple concurrent clients."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.dfs_client import DFSClient


def transfer(base_url: str, username: str, password: str, local_file: str,
             remote_path: str, verify: bool | str) -> tuple[float, float, int]:
    client = DFSClient(verify=verify)
    client.base_url = base_url
    client.login(username, password)
    started = time.perf_counter()
    client.upload(local_file, remote_path)
    upload_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory() as directory:
        output = str(Path(directory) / "download.bin")
        started = time.perf_counter()
        client.download(remote_path, output)
        download_seconds = time.perf_counter() - started
        if hashlib.sha256(Path(local_file).read_bytes()).digest() != hashlib.sha256(Path(output).read_bytes()).digest():
            raise RuntimeError("downloaded hash differs from uploaded hash")
    return upload_seconds, download_seconds, os.path.getsize(local_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mib", type=int, default=32)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--user", default=os.getenv("DFS_TEST_USER", "juan"))
    parser.add_argument("--password", default=os.getenv("DFS_TEST_PASSWORD", "contrasena123"))
    parser.add_argument("--url", default=os.getenv("DFS_NAMENODE_URL", "http://localhost:8100"))
    parser.add_argument("--ca", default=os.getenv("DFS_CA_BUNDLE"))
    args = parser.parse_args()
    if args.size_mib <= 0 or args.clients < 2:
        parser.error("size-mib must be positive and clients must be at least 2")
    verify: bool | str = args.ca or True
    benchmark_dir = f"benchmark-{int(time.time())}"
    bootstrap = DFSClient(verify=verify)
    bootstrap.base_url = args.url
    bootstrap.login(args.user, args.password)
    bootstrap.mkdir(benchmark_dir)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "benchmark.bin"
        source.write_bytes(os.urandom(args.size_mib * 1024 * 1024))
        for concurrency in (1, args.clients):
            jobs = []
            remote_paths = [f"{benchmark_dir}/run-{concurrency}-{i}.bin" for i in range(concurrency)]
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for i, remote_path in enumerate(remote_paths):
                    jobs.append(pool.submit(transfer, args.url, args.user, args.password,
                        str(source), remote_path, verify))
                results = [job.result() for job in jobs]
            wall = time.perf_counter() - started
            total_bytes = sum(result[2] for result in results)
            total_upload = sum(result[0] for result in results)
            total_download = sum(result[1] for result in results)
            print(f"clients={concurrency} total_wall_s={wall:.3f} aggregate_mib_s={total_bytes / 1048576 / wall:.2f} "
                  f"mean_upload_s={total_upload / concurrency:.3f} mean_download_s={total_download / concurrency:.3f} "
                  f"verified_files={len(results)}")
            for remote_path in remote_paths:
                bootstrap.rm(remote_path)
    bootstrap.rmdir(benchmark_dir)


if __name__ == "__main__":
    main()
