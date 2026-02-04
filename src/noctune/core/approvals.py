from __future__ import annotations

import difflib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .state import now_iso, save_json, sha256_text


@dataclass
class ApprovalRequest:
    """Stable, deterministic approval record (v1 schema)."""

    approval_id: str
    run_id: str
    file_path: str
    symbol: str
    diff: str
    risk_score: float
    reason: str
    created_at: str


def _approvals_dir(state_dir: str) -> str:
    d = os.path.join(state_dir, "approvals")
    os.makedirs(d, exist_ok=True)
    return d


def _deterministic_approval_id(
    *, run_id: str, file_path: str, symbol: str, before: str, after: str
) -> str:
    key = f"{run_id}:{file_path}:{symbol}:{sha256_text(before)}:{sha256_text(after)}"
    return sha256_text(key)[:24]


def make_request(
    *,
    state_dir: str,
    run_id: str,
    file_path: str,
    symbol: str,
    before: str,
    after: str,
    risk_score: float,
    reason: str,
) -> ApprovalRequest:
    approval_id = _deterministic_approval_id(
        run_id=run_id, file_path=file_path, symbol=symbol, before=before, after=after
    )

    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{file_path}::{symbol}",
            tofile=f"b/{file_path}::{symbol}",
        )
    )

    req = ApprovalRequest(
        approval_id=approval_id,
        run_id=run_id,
        file_path=file_path,
        symbol=symbol,
        diff=diff,
        risk_score=float(risk_score),
        reason=str(reason or "").strip(),
        created_at=now_iso(),
    )

    d = _approvals_dir(state_dir)
    req_path = os.path.join(d, f"{approval_id}.json")
    if not os.path.exists(req_path):
        # Resume-safety: never overwrite an existing request.
        save_json(req_path, req.__dict__)
    return req


def decision_path(state_dir: str, approval_id: str) -> str:
    d = _approvals_dir(state_dir)
    return os.path.join(d, f"{approval_id}.decision")


def request_path(state_dir: str, approval_id: str) -> str:
    d = _approvals_dir(state_dir)
    return os.path.join(d, f"{approval_id}.json")


def read_decision(state_dir: str, approval_id: str) -> Optional[dict[str, Any]]:
    p = decision_path(state_dir, approval_id)
    if not os.path.exists(p):
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        raw = Path(p).read_text(encoding="utf-8", errors="replace").strip().lower()
        return {
            "approved": raw.startswith("a") or raw == "true",
            "reason": raw,
            "decided_at": now_iso(),
        }


def write_decision(
    state_dir: str, approval_id: str, *, approved: bool, reason: str = ""
) -> None:
    p = decision_path(state_dir, approval_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Path(p).write_text(
        json.dumps(
            {
                "approved": bool(approved),
                "reason": str(reason or ""),
                "decided_at": now_iso(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def wait_for_decision(
    *,
    state_dir: str,
    approval_id: str,
    stop_flag_path: str,
    poll_s: float = 1.0,
) -> Optional[dict[str, Any]]:
    # Returns decision dict or None if stopped.
    while True:
        if os.path.exists(stop_flag_path):
            return None
        d = read_decision(state_dir, approval_id)
        if d is not None:
            return d
        time.sleep(poll_s)


def prompt_user(req: ApprovalRequest) -> bool:
    print("")
    print("=== Noctune Studio: Human approval required ===")
    print(f"Run: {req.run_id}")
    print(f"File: {req.file_path}")
    print(f"Symbol: {req.symbol}")
    print(f"Risk: {req.risk_score}")
    if req.reason:
        print(f"Reason: {req.reason}")
    print("")
    diff_lines = req.diff.splitlines()
    for ln in diff_lines[:200]:
        print(ln)
    if len(diff_lines) > 200:
        print(
            f"... ({len(diff_lines)} diff lines total; see approval request JSON for full diff)"
        )
    print("")
    resp = input("Approve this change? [y/N]: ").strip().lower()
    return resp in ("y", "yes")
