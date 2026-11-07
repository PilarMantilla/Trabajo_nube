"""DFS control plane: authenticated REST API and durable JSON metadata store.

This is an educational single-controller prototype. The atomic metadata file is
not a substitute for replicated consensus and therefore the controller remains
a documented single point of failure.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("dfsha.control")

METADATA_PATH = Path(os.getenv("METADATA_PATH", "/data/metadata.json"))
BLOCK_SIZE = int(os.getenv("BLOCK_SIZE", 8 * 1024 * 1024))
REPLICATION_FACTOR = int(os.getenv("REPLICATION_FACTOR", "3"))
WRITE_QUORUM = int(os.getenv("WRITE_QUORUM", "2"))
HEARTBEAT_TIMEOUT_SEC = int(os.getenv("HEARTBEAT_TIMEOUT_SEC", "30"))
TOKEN_SECRET = os.getenv("DFS_TOKEN_SECRET", "local-development-secret-change-me").encode()
INTERNAL_SECRET = os.getenv("DFS_INTERNAL_SECRET", "local-internal-secret-change-me")
TOKEN_TTL_SEC = int(os.getenv("TOKEN_TTL_SEC", "3600"))
CAPABILITY_TTL_SEC = int(os.getenv("CAPABILITY_TTL_SEC", "3600"))
HTTP_TIMEOUT_SEC = float(os.getenv("HTTP_TIMEOUT_SEC", "5"))
TLS_CA_BUNDLE: bool | str = os.getenv("TLS_CA_BUNDLE") or True

_lock = threading.RLock()
_meta: dict[str, Any] = {}
_stop = threading.Event()


def _default_meta() -> dict[str, Any]:
    return {"users": {}, "files": {}, "uploads": {}, "datanodes": {}, "dirs": [], "locks": {}}


def _atomic_save() -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = METADATA_PATH.with_suffix(METADATA_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(_meta, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, METADATA_PATH)
    directory_fd = os.open(METADATA_PATH.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load() -> None:
    global _meta
    if METADATA_PATH.exists():
        _meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    elif Path(os.getenv("SEED_METADATA_PATH", "/seed.json")).exists():
        _meta = json.loads(Path(os.getenv("SEED_METADATA_PATH", "/seed.json")).read_text(encoding="utf-8"))
    else:
        _meta = _default_meta()
    for key, value in _default_meta().items():
        _meta.setdefault(key, value)
    # Discard incomplete sessions after a controller restart. Data blocks are
    # unreferenced and can be removed by a later garbage-collection pass.
    _meta["uploads"] = {}
    _atomic_save()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(claims: dict[str, Any]) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(TOKEN_SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _verify(token: str) -> dict[str, Any]:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(TOKEN_SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(claims.get("exp", 0)) < int(time.time()):
            raise ValueError("expired token")
        return claims
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def _auth(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    claims = _verify(authorization[7:])
    if claims.get("kind") != "access":
        raise HTTPException(status_code=401, detail="Access token required")
    return claims


def _password_matches(stored: str, supplied: str) -> bool:
    # PBKDF2 format: pbkdf2_sha256$iterations$salt_hex$digest_hex.
    # Plaintext comparison remains only for migration of older local demo files.
    if not stored.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(stored, supplied)
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$", 3)
        derived = hashlib.pbkdf2_hmac("sha256", supplied.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(derived.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _internal(x_dfsha_internal: str | None) -> None:
    if not x_dfsha_internal or not hmac.compare_digest(x_dfsha_internal, INTERNAL_SECRET):
        raise HTTPException(status_code=403, detail="Internal service authentication required")


def _normalize_path(raw: str, username: str, *, directory: bool = False) -> str:
    value = "/" + raw.strip().lstrip("/")
    parts = [p for p in value.split("/") if p]
    if parts and parts[0] == "users":
        if len(parts) < 2 or parts[1] != username:
            raise HTTPException(status_code=403, detail="Path is outside the user's namespace")
        parts = parts[2:]
    elif parts and parts[0] == username:
        parts = parts[1:]
    if any(p in {".", ".."} for p in parts):
        raise HTTPException(status_code=400, detail="Relative path components are not allowed")
    path = "/users/" + username + ("/" + "/".join(parts) if parts else "")
    if directory:
        return path.rstrip("/") or f"/users/{username}"
    if not parts:
        raise HTTPException(status_code=400, detail="A file name is required")
    return path


def _user(claims: dict[str, Any]) -> str:
    username = str(claims.get("sub", ""))
    if username not in _meta.get("users", {}):
        raise HTTPException(status_code=401, detail="Unknown user")
    return username


def _alive_nodes() -> list[tuple[str, dict[str, Any]]]:
    now = int(time.time())
    return sorted(
        [(nid, node) for nid, node in _meta["datanodes"].items()
         if node.get("status") == "ALIVE" and now - int(node.get("last_seen", 0)) <= HEARTBEAT_TIMEOUT_SEC],
        key=lambda item: item[0],
    )


def _capability(kind: str, **extra: Any) -> str:
    return _sign({"kind": kind, "exp": int(time.time()) + CAPABILITY_TTL_SEC, **extra})


def _verify_receipt(receipt: str, upload_id: str, block: dict[str, Any]) -> dict[str, Any] | None:
    claims = _verify(receipt)
    if (claims.get("kind") != "stored" or claims.get("upload_id") != upload_id
            or claims.get("block_id") != block["id"] or claims.get("sha256") != block["sha256"]
            or claims.get("node_id") not in block["targets"]):
        return None
    return claims


class LoginBody(BaseModel):
    username: str
    password: str


class NodeRegistration(BaseModel):
    node_id: str
    internal_url: str
    external_url: str


class HeartbeatBody(BaseModel):
    free_bytes: int = 0
    block_ids: list[str] = Field(default_factory=list)


class BeginUpload(BaseModel):
    path: str
    size_bytes: int = Field(ge=0)
    block_hashes: list[str]
    base_version: int | None = None
    lock_id: str | None = None


class CommitUpload(BaseModel):
    receipts: list[str] = Field(default_factory=list)
    file_sha256: str


class MkdirBody(BaseModel):
    path: str


class LockBody(BaseModel):
    path: str
    ttl_seconds: int = Field(default=30, ge=5, le=300)


def _heartbeat_loop() -> None:
    while not _stop.wait(max(1, HEARTBEAT_TIMEOUT_SEC // 3)):
        with _lock:
            changed = False
            now = int(time.time())
            for node in _meta["datanodes"].values():
                if node.get("status") == "ALIVE" and now - int(node.get("last_seen", 0)) > HEARTBEAT_TIMEOUT_SEC:
                    node["status"] = "DEAD"
                    changed = True
            if changed:
                _atomic_save()


def _repair_loop() -> None:
    while not _stop.wait(15):
        with _lock:
            alive = dict(_alive_nodes())
            jobs: list[tuple[str, str, str, str, str]] = []
            for path, record in _meta["files"].items():
                for version_record in [record, *record.get("history", [])]:
                    for block in version_record.get("blocks", []):
                        replicas = list(block.get("replicas", []))
                        block_id = block["id"]
                        good = [node_id for node_id in replicas
                                if node_id in alive and block_id in alive[node_id].get("block_ids", [])]
                        targets = [node_id for node_id in alive
                                   if block_id not in alive[node_id].get("block_ids", [])]
                        if good and targets and len(good) < min(REPLICATION_FACTOR, len(alive)):
                            jobs.append((path, block_id, block["sha256"], good[0], targets[0]))
        for path, block_id, digest, donor, target in jobs:
            try:
                with _lock:
                    donor_node = _meta["datanodes"].get(donor, {})
                    target_node = _meta["datanodes"].get(target, {})
                expiry = int(time.time()) + 300
                read_token = _sign({"kind": "repair_read", "block_id": block_id,
                                    "node_id": donor, "exp": expiry})
                response = requests.get(f"{donor_node['internal_url']}/v1/blocks/{block_id}",
                    headers={"Authorization": f"Bearer {read_token}"}, timeout=HTTP_TIMEOUT_SEC,
                    verify=TLS_CA_BUNDLE)
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != digest:
                    raise ValueError("donor block checksum mismatch")
                write_token = _sign({"kind": "repair_write", "block_id": block_id,
                    "node_id": target, "sha256": digest, "exp": expiry})
                response = requests.put(f"{target_node['internal_url']}/internal/blocks/{block_id}",
                    data=response.content, headers={"Authorization": f"Bearer {write_token}",
                    "X-Block-SHA256": digest}, timeout=HTTP_TIMEOUT_SEC, verify=TLS_CA_BUNDLE)
                response.raise_for_status()
                with _lock:
                    current = _meta["files"].get(path)
                    versions = [current, *current.get("history", [])] if current else []
                    item = next((b for version_record in versions for b in version_record.get("blocks", [])
                                 if b["id"] == block_id), None)
                    if item and target not in item["replicas"]:
                        item["replicas"].append(target)
                        _atomic_save()
                log.info("repaired block=%s target=%s", block_id, target)
            except Exception as exc:
                log.warning("repair failed block=%s donor=%s target=%s: %s", block_id, donor, target, exc)


def _delete_blocks_best_effort(blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        block_id = block.get("id")
        if not block_id:
            continue
        with _lock:
            targets = set(block.get("targets", [])) | set(block.get("replicas", []))
            nodes = {nid: _meta["datanodes"].get(nid, {}) for nid in targets}
        for node in nodes.values():
            base = node.get("internal_url")
            if not base:
                continue
            try:
                requests.delete(f"{base}/v1/blocks/{block_id}",
                    headers={"X-DFSha-Internal": INTERNAL_SECRET}, timeout=HTTP_TIMEOUT_SEC,
                    verify=TLS_CA_BUNDLE)
            except requests.RequestException as exc:
                log.warning("could not remove orphan block %s: %s", block_id, exc)


def _orphan_gc_loop() -> None:
    while not _stop.wait(60):
        with _lock:
            referenced: set[str] = set()
            for record in _meta["files"].values():
                versions = [record, *record.get("history", [])]
                referenced.update(block["id"] for version in versions for block in version.get("blocks", []))
            referenced.update(block["id"] for upload in _meta["uploads"].values()
                              for block in upload.get("blocks", []))
            candidates = [(nid, node.get("internal_url"), set(node.get("block_ids", [])) - referenced)
                          for nid, node in _meta["datanodes"].items()]
        for _node_id, base, block_ids in candidates:
            if not base:
                continue
            for block_id in block_ids:
                try:
                    requests.delete(f"{base}/v1/blocks/{block_id}",
                        headers={"X-DFSha-Internal": INTERNAL_SECRET}, timeout=HTTP_TIMEOUT_SEC,
                        verify=TLS_CA_BUNDLE)
                except requests.RequestException as exc:
                    log.warning("orphan GC failed block=%s: %s", block_id, exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with _lock:
        _load()
    _stop.clear()
    worker = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat-checker")
    repairer = threading.Thread(target=_repair_loop, daemon=True, name="replication-repairer")
    collector = threading.Thread(target=_orphan_gc_loop, daemon=True, name="orphan-gc")
    worker.start()
    repairer.start()
    collector.start()
    yield
    _stop.set()
    worker.join(timeout=2)
    repairer.join(timeout=2)
    collector.join(timeout=2)


app = FastAPI(title="DFSha Control Plane", version="2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    with _lock:
        return {"status": "ok", "alive_datanodes": len(_alive_nodes()), "block_size": BLOCK_SIZE,
                "replication_factor": REPLICATION_FACTOR, "write_quorum": WRITE_QUORUM}


@app.post("/v1/auth/login")
def login(body: LoginBody) -> dict[str, Any]:
    with _lock:
        password = _meta.get("users", {}).get(body.username)
    if password is None or not _password_matches(str(password), body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _sign({"kind": "access", "sub": body.username, "exp": int(time.time()) + TOKEN_TTL_SEC})
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SEC}


@app.post("/internal/nodes/register")
def register_node(body: NodeRegistration, x_dfsha_internal: str | None = Header(default=None)) -> dict[str, bool]:
    _internal(x_dfsha_internal)
    with _lock:
        prior = _meta["datanodes"].get(body.node_id, {})
        _meta["datanodes"][body.node_id] = {**prior, "internal_url": body.internal_url.rstrip("/"),
            "external_url": body.external_url.rstrip("/"), "last_seen": int(time.time()), "status": "ALIVE",
            "free_bytes": prior.get("free_bytes", 0), "block_ids": prior.get("block_ids", [])}
        _atomic_save()
    return {"registered": True}


@app.post("/internal/nodes/{node_id}/heartbeat")
def heartbeat(node_id: str, body: HeartbeatBody, x_dfsha_internal: str | None = Header(default=None)) -> dict[str, bool]:
    _internal(x_dfsha_internal)
    with _lock:
        node = _meta["datanodes"].get(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="DataNode is not registered")
        node.update(last_seen=int(time.time()), status="ALIVE", free_bytes=body.free_bytes, block_ids=body.block_ids)
        # Heartbeats need not each force a disk sync; registration and state-changing
        # operations do. Persist liveness for useful post-restart diagnostics.
        _atomic_save()
    return {"ok": True}


@app.get("/v1/cluster")
def cluster(_claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    with _lock:
        return {"block_size_bytes": BLOCK_SIZE, "replication_factor": REPLICATION_FACTOR,
                "write_quorum": WRITE_QUORUM, "nodes": [{"node_id": nid, "url": n["external_url"]}
                for nid, n in _alive_nodes()]}


@app.put("/v1/dirs")
def mkdir(body: MkdirBody, claims: dict[str, Any] = Depends(_auth)) -> dict[str, str]:
    username = _user(claims)
    path = _normalize_path(body.path, username, directory=True)
    with _lock:
        if path in _meta["dirs"]:
            raise HTTPException(status_code=409, detail="Directory already exists")
        parent = path.rpartition("/")[0]
        if parent and parent not in _meta["dirs"] and parent != f"/users/{username}":
            raise HTTPException(status_code=404, detail="Parent directory does not exist")
        if path in _meta["files"]:
            raise HTTPException(status_code=409, detail="A file already uses this path")
        _meta["dirs"].append(path)
        _atomic_save()
    return {"path": path}


@app.get("/v1/dirs")
def list_dir(path: str = Query(default="/"), claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    username = _user(claims)
    directory = _normalize_path(path, username, directory=True)
    prefix = directory.rstrip("/") + "/"
    with _lock:
        if directory != f"/users/{username}" and directory not in _meta["dirs"]:
            raise HTTPException(status_code=404, detail="Directory not found")
        entries: dict[str, str] = {}
        all_dirs = list(_meta["dirs"])
        for d in all_dirs:
            if d.startswith(prefix):
                name = d[len(prefix):].split("/", 1)[0]
                if name:
                    entries[name] = "directory"
        for file_path, record in _meta["files"].items():
            if record.get("owner") != username or file_path in _meta.get("deleted", []):
                continue
            if file_path.startswith(prefix):
                name = file_path[len(prefix):].split("/", 1)[0]
                if name:
                    entries[name] = "file" if "/" not in file_path[len(prefix):] else "directory"
        return {"path": directory, "entries": [{"name": n, "type": entries[n]} for n in sorted(entries)]}


@app.post("/v1/uploads")
def begin_upload(body: BeginUpload, claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    username = _user(claims)
    path = _normalize_path(body.path, username)
    expected_blocks = math.ceil(body.size_bytes / BLOCK_SIZE) if body.size_bytes else 0
    if len(body.block_hashes) != expected_blocks or any(len(x) != 64 for x in body.block_hashes):
        raise HTTPException(status_code=400, detail="Block hash list does not match file size")
    with _lock:
        parent = path.rpartition("/")[0]
        if parent != f"/users/{username}" and parent not in _meta["dirs"]:
            raise HTTPException(status_code=404, detail="Parent directory does not exist")
        if path in _meta["dirs"]:
            raise HTTPException(status_code=409, detail="A directory already uses this path")
        existing = _meta["files"].get(path)
        if existing and body.base_version is None:
            raise HTTPException(status_code=409, detail="File exists; send base_version to replace it")
        if existing and int(existing.get("version", 0)) != body.base_version:
            raise HTTPException(status_code=409, detail="BASE_VERSION_CONFLICT")
        active_lock = _meta["locks"].get(path)
        if active_lock and int(active_lock["expires_at"]) > int(time.time()) and active_lock.get("lock_id") != body.lock_id:
            raise HTTPException(status_code=423, detail="LOCKED")
        if any(u.get("path") == path for u in _meta["uploads"].values()):
            raise HTTPException(status_code=423, detail="Another write is in progress")
        alive = _alive_nodes()
        if expected_blocks and len(alive) < WRITE_QUORUM:
            raise HTTPException(status_code=503, detail=f"Need at least {WRITE_QUORUM} live DataNodes")
        if body.size_bytes == 0:
            alive = alive or []
        upload_id = uuid.uuid4().hex
        node_order = [nid for nid, _ in alive]
        blocks: list[dict[str, Any]] = []
        for index, digest in enumerate(body.block_hashes):
            targets = [node_order[(index + i) % len(node_order)] for i in range(min(REPLICATION_FACTOR, len(node_order)))]
            block_id = uuid.uuid4().hex
            size = min(BLOCK_SIZE, body.size_bytes - index * BLOCK_SIZE)
            blocks.append({"id": block_id, "index": index, "size_bytes": size, "sha256": digest,
                           "targets": targets})
        node_lookup = dict(alive)
        _meta["uploads"][upload_id] = {"path": path, "owner": username, "size_bytes": body.size_bytes,
            "created_at": int(time.time()), "base_version": body.base_version,
            "lock_id": body.lock_id, "blocks": blocks, "file_sha256": None}
        _atomic_save()
        for block in blocks:
            block["targets_info"] = [{"node_id": nid, "url": node_lookup[nid]["external_url"],
                "token": _capability("write", node_id=nid, upload_id=upload_id,
                    block_id=block["id"], size_bytes=block["size_bytes"])}
                for nid in block["targets"]]
    return {"upload_id": upload_id, "path": path, "block_size_bytes": BLOCK_SIZE,
            "write_quorum": WRITE_QUORUM, "blocks": blocks}


@app.post("/v1/uploads/{upload_id}/commit")
def commit_upload(upload_id: str, body: CommitUpload, claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    username = _user(claims)
    with _lock:
        upload = _meta["uploads"].get(upload_id)
        if not upload:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if upload["owner"] != username:
            raise HTTPException(status_code=403, detail="Upload belongs to another user")
        receipts: dict[str, set[str]] = {}
        blocks = upload["blocks"]
        for receipt in body.receipts:
            try:
                for block in blocks:
                    signed = _verify_receipt(receipt, upload_id, block)
                    if signed:
                        receipts.setdefault(block["id"], set()).add(signed["node_id"])
                        break
            except HTTPException:
                continue
        for block in blocks:
            if len(receipts.get(block["id"], set())) < WRITE_QUORUM:
                raise HTTPException(status_code=503, detail=f"INSUFFICIENT_REPLICAS for block {block['index']}")
        current = _meta["files"].get(upload["path"])
        current_version = int(current.get("version", 0)) if current else 0
        if current_version != int(upload.get("base_version") or 0):
            raise HTTPException(status_code=409, detail="BASE_VERSION_CONFLICT")
        history = list(current.get("history", [])) if current else []
        if current:
            history.append({key: value for key, value in current.items() if key != "history"})
        file_record = {"owner": username, "size_bytes": upload["size_bytes"], "sha256": body.file_sha256,
            "version": current_version + 1, "status": "READY", "committed_at": int(time.time()),
            "blocks": [{"id": b["id"], "index": b["index"], "size_bytes": b["size_bytes"],
                "sha256": b["sha256"], "replicas": sorted(receipts.get(b["id"], set()))} for b in blocks],
            "history": history}
        _meta["files"][upload["path"]] = file_record
        _meta["uploads"].pop(upload_id, None)
        if upload.get("lock_id") and _meta["locks"].get(upload["path"], {}).get("lock_id") == upload["lock_id"]:
            _meta["locks"].pop(upload["path"], None)
        _atomic_save()
    return {"path": upload["path"], "version": file_record["version"], "status": "COMMITTED"}


@app.delete("/v1/uploads/{upload_id}")
def abort_upload(upload_id: str, claims: dict[str, Any] = Depends(_auth)) -> dict[str, bool]:
    username = _user(claims)
    with _lock:
        upload = _meta["uploads"].get(upload_id)
        if upload and upload["owner"] != username:
            raise HTTPException(status_code=403, detail="Upload belongs to another user")
        removed = _meta["uploads"].pop(upload_id, None)
        _atomic_save()
    if removed:
        _delete_blocks_best_effort(removed.get("blocks", []))
    return {"aborted": True}


@app.get("/v1/files/manifest")
def manifest(path: str, version: int | None = None,
             claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    username = _user(claims)
    file_path = _normalize_path(path, username)
    with _lock:
        current = _meta["files"].get(file_path)
        if not current:
            raise HTTPException(status_code=404, detail="File not found")
        if current["owner"] != username:
            raise HTTPException(status_code=403, detail="Forbidden")
        record = current
        if version is not None and version != int(current["version"]):
            record = next((item for item in current.get("history", []) if int(item["version"]) == version), None)
            if record is None:
                raise HTTPException(status_code=404, detail="Version not found")
        nodes = _meta["datanodes"]
        out = []
        for block in sorted(record["blocks"], key=lambda item: item["index"]):
            replicas = []
            for node_id in block["replicas"]:
                node = nodes.get(node_id)
                if node and node.get("status") == "ALIVE":
                    replicas.append({"node_id": node_id, "url": node["external_url"],
                        "token": _capability("read", node_id=node_id, block_id=block["id"], user=username)})
            out.append({**block, "replicas": replicas})
    return {"path": file_path, "size_bytes": record["size_bytes"], "sha256": record["sha256"],
            "version": record["version"], "blocks": out}


@app.delete("/v1/files")
def delete_file(path: str, claims: dict[str, Any] = Depends(_auth)) -> dict[str, bool]:
    username = _user(claims)
    file_path = _normalize_path(path, username)
    with _lock:
        record = _meta["files"].get(file_path)
        if not record:
            raise HTTPException(status_code=404, detail="File not found")
        if record["owner"] != username:
            raise HTTPException(status_code=403, detail="Forbidden")
        del _meta["files"][file_path]
        _atomic_save()
    versions = [record, *record.get("history", [])]
    _delete_blocks_best_effort([block for version in versions for block in version.get("blocks", [])])
    return {"deleted": True}


@app.delete("/v1/dirs")
def delete_dir(path: str, claims: dict[str, Any] = Depends(_auth)) -> dict[str, bool]:
    username = _user(claims)
    directory = _normalize_path(path, username, directory=True)
    prefix = directory.rstrip("/") + "/"
    with _lock:
        if directory not in _meta["dirs"]:
            raise HTTPException(status_code=404, detail="Directory not found")
        if any(p.startswith(prefix) for p in _meta["dirs"]) or any(p.startswith(prefix) for p in _meta["files"]):
            raise HTTPException(status_code=409, detail="Directory is not empty")
        _meta["dirs"].remove(directory)
        _atomic_save()
    return {"deleted": True}


@app.post("/v1/locks")
def acquire_lock(body: LockBody, claims: dict[str, Any] = Depends(_auth)) -> dict[str, Any]:
    username = _user(claims)
    path = _normalize_path(body.path, username)
    now = int(time.time())
    with _lock:
        current = _meta["locks"].get(path)
        if current and int(current.get("expires_at", 0)) > now:
            raise HTTPException(status_code=423, detail="LOCKED")
        lock_id = uuid.uuid4().hex
        expires_at = now + body.ttl_seconds
        _meta["locks"][path] = {"lock_id": lock_id, "owner": username, "expires_at": expires_at}
        _atomic_save()
    return {"lock_id": lock_id, "path": path, "expires_at": expires_at}


@app.delete("/v1/locks/{lock_id}")
def release_lock(lock_id: str, claims: dict[str, Any] = Depends(_auth)) -> dict[str, bool]:
    username = _user(claims)
    with _lock:
        path = next((p for p, item in _meta["locks"].items() if item.get("lock_id") == lock_id), None)
        if path is None:
            return {"released": True}
        if _meta["locks"][path].get("owner") != username:
            raise HTTPException(status_code=403, detail="Lock belongs to another user")
        del _meta["locks"][path]
        _atomic_save()
    return {"released": True}

