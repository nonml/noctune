from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunPaths:
    root: str
    run_id: str
    run_dir: str
    state_dir: str
    logs_dir: str
    artifacts_dir: str
    backups_dir: str


def ensure_run_paths(repo_root: str, run_id: str | None) -> RunPaths:
    if not run_id:
        run_id = (
            time.strftime("%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:8]
        )
    # Repo-local cache. Never write outside repo_root.
    # Layout: <repo_root>/.noctune_cache/runs/<run_id>/...
    run_dir = os.path.join(repo_root, ".noctune_cache", "runs", run_id)
    state_dir = os.path.join(run_dir, "state")
    logs_dir = os.path.join(run_dir, "logs")
    artifacts_dir = os.path.join(run_dir, "artifacts")
    backups_dir = os.path.join(run_dir, "backups")
    for d in [state_dir, logs_dir, artifacts_dir, backups_dir]:
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(state_dir, "tasks"), exist_ok=True)
    return RunPaths(
        repo_root, run_id, run_dir, state_dir, logs_dir, artifacts_dir, backups_dir
    )


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def write_text(path: str, text: str, newline: str | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def detect_newline_style(data: bytes) -> str:
    # returns '\r\n' or '\n'
    if b"\r\n" in data:
        return "\r\n"
    return "\n"
