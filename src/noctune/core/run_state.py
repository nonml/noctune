from __future__ import annotations

import os
import signal
from typing import Any, Optional

from .state import load_json, now_iso, save_json


TERMINAL_STATUSES = {"done", "failed", "stopped"}
ACTIVE_STATUSES = {"starting", "running", "stopping"}


def run_state_path(state_dir: str) -> str:
    return os.path.join(state_dir, "run.json")


def read_run_state(state_dir: str) -> dict[str, Any]:
    return dict(load_json(run_state_path(state_dir), default={}) or {})


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def init_run_state(
    *,
    state_dir: str,
    run_id: str,
    repo_root: str,
    stage: str,
    status: str = "starting",
    pid: Optional[int] = None,
    pack: Optional[str] = None,
    profile: Optional[str] = None,
) -> None:
    p = run_state_path(state_dir)
    if os.path.exists(p):
        return
    save_json(
        p,
        {
            "run_id": run_id,
            "repo_root": repo_root,
            "stage": stage,
            "status": status,
            "pid": int(pid) if pid else None,
            "pack": pack,
            "profile": profile,
            "started_at": now_iso(),
            "updated_at": now_iso(),
        },
    )


def update_run_state(state_dir: str, **fields: Any) -> dict[str, Any]:
    """
    Merge updates into state/run.json and return the new state.
    Also updates `updated_at`. If `status` becomes terminal, ensures `ended_at` exists.
    """
    p = run_state_path(state_dir)
    cur = dict(load_json(p, default={}) or {})

    nxt = dict(cur)
    for k, v in fields.items():
        nxt[k] = v

    # Normalize pid to int if present.
    if "pid" in nxt and nxt["pid"] is not None:
        try:
            nxt["pid"] = int(nxt["pid"])
        except Exception:
            pass

    # Stable time fields.
    if not nxt.get("started_at"):
        nxt["started_at"] = now_iso()
    nxt["updated_at"] = now_iso()

    status = str(nxt.get("status") or "").strip().lower()
    if status in TERMINAL_STATUSES and not nxt.get("ended_at"):
        nxt["ended_at"] = now_iso()

    save_json(p, nxt)
    return nxt


def mark_failed_if_pid_gone(
    state_dir: str, *, error: str = "worker process exited"
) -> dict[str, Any]:
    cur = read_run_state(state_dir)
    status = str(cur.get("status") or "").strip().lower()
    pid = cur.get("pid")
    if status not in ACTIVE_STATUSES:
        return cur
    if not isinstance(pid, int):
        try:
            pid = int(pid)
        except Exception:
            pid = None
    if not pid:
        return cur
    if pid_exists(pid):
        return cur
    return update_run_state(state_dir, status="failed", error=error)

