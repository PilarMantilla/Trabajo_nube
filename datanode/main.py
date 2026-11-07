"""Data plane node with authenticated block access and atomic durable writes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("dfsha.datanode")

NODE_ID = os.getenv("NODE_ID", "dn-unknown")
NAMENODE_URL = os.getenv("NAMENODE_URL", "http://namenode:8000").rstrip("/")
NAMENODE_INTERNAL_URL = os.getenv("NAMENODE_INTERNAL_URL", NAMENODE_URL).rstrip("/")
EXTERNAL_URL = os.getenv("NODE_EXTERNAL_URL", "http://localhost:8001").rstrip("/")
INTERNAL_SECRET = os.getenv("DFS_INTERNAL_SECRET", "local-internal-secret-change-me")
TOKEN_SECRET = os.getenv("DFS_TOKEN_SECRET", "local-development-secret-change-me").encode()
BLOCKS_DIR = Path(os.getenv("BLOCKS_DIR", "/blocks"))
MAX_BLOCK_BYTES = int(os.getenv("BLOCK_SIZE", str(8 * 1024 * 1024)))
TLS_CA_BUNDLE = os.getenv("TLS_CA_BUNDLE") or True
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "10"))
BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
_stop = threading.Event()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(claims: dict) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64(hmac.new(TOKEN_SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def _claims(token: str) -> dict:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(TOKEN_SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        result = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(result.get("exp", 0)) < int(time.time()):
            raise ValueError("expired")
        return result
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired capability") from exc


def _capability(authorization: str | None, expected_kind: str, block_id: str) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer capability required")
    claims = _claims(authorization[7:])
    if claims.get("kind") != expected_kind or claims.get("block_id") != block_id or claims.get("node_id") != NODE_ID:
        raise HTTPException(status_code=403, detail="Capability does not authorize this operation")
    return claims


def _path(block_id: str) -> Path:
    if not block_id or any(c not in "0123456789abcdef-" for c in block_id.lower()):
        raise HTTPException(status_code=400, detail="Invalid block id")
    return BLOCKS_DIR / f"{block_id}.bin"


def _block_ids() -> list[str]:
    return sorted(p.stem for p in BLOCKS_DIR.glob("*.bin"))


def _register_and_heartbeat() -> None:
    internal = os.getenv("NODE_INTERNAL_URL", f"http://{NODE_ID}:8001").rstrip("/")
    headers = {"X-DFSha-Internal": INTERNAL_SECRET}
    for attempt in range(60):
        try:
            response = requests.post(f"{NAMENODE_INTERNAL_URL}/internal/nodes/register",
                headers=headers, json={"node_id": NODE_ID, "internal_url": internal,
                                       "external_url": EXTERNAL_URL}, timeout=3, verify=TLS_CA_BUNDLE)
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            log.info("waiting for control plane (%d/60): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        raise RuntimeError("Could not register with control plane")
    while not _stop.wait(HEARTBEAT_SEC):
        try:
            free = shutil.disk_usage(BLOCKS_DIR).free
            response = requests.post(f"{NAMENODE_INTERNAL_URL}/internal/nodes/{NODE_ID}/heartbeat",
                headers=headers, json={"free_bytes": free, "block_ids": _block_ids()}, timeout=3,
                verify=TLS_CA_BUNDLE)
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("heartbeat failed: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _stop.clear()
    worker = threading.Thread(target=_register_and_heartbeat, daemon=True, name=f"{NODE_ID}-heartbeat")
    worker.start()
    yield
    _stop.set()
    worker.join(timeout=2)


app = FastAPI(title=f"DFSha DataNode {NODE_ID}", version="2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "node_id": NODE_ID, "blocks": len(_block_ids()),
            "free_bytes": shutil.disk_usage(BLOCKS_DIR).free}


@app.put("/v1/blocks/{block_id}")
async def store_block(block_id: str, request: Request,
                      authorization: str | None = Header(default=None),
                      x_block_sha256: str | None = Header(default=None)) -> dict:
    claims = _capability(authorization, "write", block_id)
    data = await request.body()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_BLOCK_BYTES:
        raise HTTPException(status_code=413, detail="Block exceeds configured block size")
    if len(data) != int(claims.get("size_bytes", -1)):
        raise HTTPException(status_code=422, detail="Block size does not match upload plan")
    if not x_block_sha256 or not hmac.compare_digest(digest, x_block_sha256):
        raise HTTPException(status_code=422, detail="CHECKSUM_MISMATCH")
    path = _path(block_id)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{block_id}.", suffix=".part", dir=BLOCKS_DIR)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(BLOCKS_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    receipt = _sign({"kind": "stored", "node_id": NODE_ID, "upload_id": claims["upload_id"],
                     "block_id": block_id, "sha256": digest, "exp": int(time.time()) + 3600})
    return {"node_id": NODE_ID, "block_id": block_id, "size_bytes": len(data),
            "sha256": digest, "receipt": receipt}


async def _atomic_store(block_id: str, data: bytes, expected_sha256: str) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, expected_sha256):
        raise HTTPException(status_code=422, detail="CHECKSUM_MISMATCH")
    path = _path(block_id)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{block_id}.", suffix=".part", dir=BLOCKS_DIR)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@app.put("/internal/blocks/{block_id}")
async def repair_store(block_id: str, request: Request,
                       authorization: str | None = Header(default=None),
                       x_block_sha256: str | None = Header(default=None)) -> dict:
    claims = _capability(authorization, "repair_write", block_id)
    data = await request.body()
    if len(data) > MAX_BLOCK_BYTES or not x_block_sha256 or x_block_sha256 != claims.get("sha256"):
        raise HTTPException(status_code=422, detail="Invalid repair request")
    await _atomic_store(block_id, data, x_block_sha256)
    return {"stored": True, "node_id": NODE_ID, "sha256": x_block_sha256}


@app.get("/v1/blocks/{block_id}")
def read_block(block_id: str, authorization: str | None = Header(default=None)) -> Response:
    claims = _claims(authorization[7:]) if authorization and authorization.startswith("Bearer ") else {}
    if claims.get("kind") not in {"read", "repair_read"} or claims.get("block_id") != block_id or claims.get("node_id") != NODE_ID:
        raise HTTPException(status_code=403, detail="Capability does not authorize this operation")
    path = _path(block_id)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Block not found") from exc
    return Response(data, media_type="application/octet-stream",
                    headers={"X-Block-SHA256": hashlib.sha256(data).hexdigest()})


@app.head("/v1/blocks/{block_id}")
def head_block(block_id: str, authorization: str | None = Header(default=None)) -> Response:
    _capability(authorization, "read", block_id)
    path = _path(block_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Block not found")
    data = path.read_bytes()
    return Response(status_code=200, headers={"X-Block-SHA256": hashlib.sha256(data).hexdigest(),
                                                "Content-Length": str(len(data))})


@app.delete("/v1/blocks/{block_id}")
def delete_block(block_id: str, x_dfsha_internal: str | None = Header(default=None)) -> dict:
    if not x_dfsha_internal or not hmac.compare_digest(x_dfsha_internal, INTERNAL_SECRET):
        raise HTTPException(status_code=403, detail="Internal service authentication required")
    _path(block_id).unlink(missing_ok=True)
    return {"deleted": True, "block_id": block_id}

