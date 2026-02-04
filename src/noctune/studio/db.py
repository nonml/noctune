from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..core.state import now_iso


def _try_json_loads(raw: str) -> Optional[dict[str, Any]]:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  repo_root TEXT NOT NULL,
  stage TEXT NOT NULL,
  rel_paths_json TEXT,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  pid INTEGER,
  last_heartbeat TEXT,
  pack TEXT,
  profile TEXT,
  branch TEXT,
  head_sha TEXT,
  error TEXT,
  started_at TEXT,
  updated_at TEXT,
  ended_at TEXT,
  exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_root TEXT NOT NULL,
  stage TEXT NOT NULL,
  rel_paths_json TEXT,
  extra_args_json TEXT,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  run_id TEXT,
  pid INTEGER,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_repo_status_created
  ON jobs(repo_root, status, created_at);

-- Audit trail (v1): store run events + approvals/decisions in sqlite for durable history.
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  ts TEXT,
  type TEXT,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, idx)
);

CREATE TABLE IF NOT EXISTS approvals (
  run_id TEXT NOT NULL,
  approval_id TEXT NOT NULL,
  created_at TEXT,
  file_path TEXT,
  symbol TEXT,
  risk_score REAL,
  reason TEXT,
  diff TEXT,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, approval_id)
);

CREATE TABLE IF NOT EXISTS decisions (
  run_id TEXT NOT NULL,
  approval_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  reason TEXT,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, approval_id)
);
"""


def default_db_path(repo_root: Path) -> Path:
    return repo_root / ".noctune_cache" / "noctune_studio.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript(SCHEMA)
    _ensure_runs_columns(con)
    con.commit()
    return con


def _ensure_runs_columns(con: sqlite3.Connection) -> None:
    cols = {
        "pack": "TEXT",
        "profile": "TEXT",
        "branch": "TEXT",
        "head_sha": "TEXT",
        "error": "TEXT",
        "started_at": "TEXT",
        "updated_at": "TEXT",
        "ended_at": "TEXT",
        "exit_code": "INTEGER",
    }
    for name, typ in cols.items():
        try:
            con.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
        except Exception:
            pass


def _dumps(obj: Any) -> str | None:
    return None if obj is None else json.dumps(obj, ensure_ascii=False)


def _loads(s: str | None) -> Any:
    return None if not s else json.loads(s)


def enqueue_job(
    con: sqlite3.Connection,
    *,
    repo_root: str,
    stage: str,
    rel_paths: Optional[list[str]] = None,
    extra_args: Optional[list[str]] = None,
) -> int:
    cur = con.execute(
        "INSERT INTO jobs(repo_root, stage, rel_paths_json, extra_args_json, created_at, status) "
        "VALUES(?,?,?,?,datetime('now'),?)",
        (repo_root, stage, _dumps(rel_paths), _dumps(extra_args), "queued"),
    )
    con.commit()
    return int(cur.lastrowid)


def list_jobs(
    con: sqlite3.Connection, *, repo_root: str, limit: int = 50
) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT job_id, stage, status, created_at, run_id, pid, error FROM jobs "
        "WHERE repo_root=? ORDER BY job_id DESC LIMIT ?",
        (repo_root, int(limit)),
    ).fetchall()
    return [
        {
            "job_id": r[0],
            "stage": r[1],
            "status": r[2],
            "created_at": r[3],
            "run_id": r[4],
            "pid": r[5],
            "error": r[6],
        }
        for r in rows
    ]


def list_runs(
    con: sqlite3.Connection, *, repo_root: str, limit: int = 50
) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT run_id, stage, status, created_at, pid, pack, profile, branch, head_sha, error "
        "FROM runs WHERE repo_root=? ORDER BY created_at DESC LIMIT ?",
        (repo_root, int(limit)),
    ).fetchall()
    return [
        {
            "run_id": r[0],
            "stage": r[1],
            "status": r[2],
            "created_at": r[3],
            "pid": r[4],
            "pack": r[5],
            "profile": r[6],
            "branch": r[7],
            "head_sha": r[8],
            "error": r[9],
        }
        for r in rows
    ]


def claim_next_job(con: sqlite3.Connection, *, repo_root: str) -> Optional[dict[str, Any]]:
    # Atomic-ish claim: select then update inside immediate txn.
    con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT job_id, stage, rel_paths_json, extra_args_json FROM jobs "
        "WHERE repo_root=? AND status='queued' ORDER BY job_id ASC LIMIT 1",
        (repo_root,),
    ).fetchone()
    if not row:
        con.execute("COMMIT")
        return None
    job_id, stage, rel_paths_json, extra_args_json = row
    con.execute("UPDATE jobs SET status='starting' WHERE job_id=?", (job_id,))
    con.execute("COMMIT")
    return {
        "job_id": int(job_id),
        "stage": stage,
        "rel_paths": _loads(rel_paths_json),
        "extra_args": _loads(extra_args_json),
    }


def update_job_running(
    con: sqlite3.Connection, *, job_id: int, run_id: str, pid: int
) -> None:
    con.execute(
        "UPDATE jobs SET status='running', run_id=?, pid=? WHERE job_id=?",
        (run_id, int(pid), int(job_id)),
    )
    con.commit()


def finish_job(
    con: sqlite3.Connection, *, job_id: int, status: str, error: str | None = None
) -> None:
    con.execute(
        "UPDATE jobs SET status=?, error=? WHERE job_id=?",
        (status, error, int(job_id)),
    )
    con.commit()


def upsert_run_from_run_json(
    con: sqlite3.Connection, *, repo_root: Path, run_id: str
) -> Optional[dict[str, Any]]:
    """Best-effort: upsert the `runs` row from `.noctune_cache/.../state/run.json`."""
    rp = repo_root / ".noctune_cache" / "runs" / run_id / "state" / "run.json"
    if not rp.exists():
        return None
    obj = _try_json_loads(rp.read_text(encoding="utf-8", errors="replace"))
    if not obj:
        return None

    existing = con.execute(
        "SELECT created_at FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    created_at = (
        (existing[0] if existing else None)
        or str(obj.get("started_at") or "").strip()
        or now_iso()
    )

    con.execute(
        "INSERT OR REPLACE INTO runs(run_id, repo_root, stage, rel_paths_json, created_at, status, pid, pack, profile, branch, head_sha, error, started_at, updated_at, ended_at, exit_code) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(obj.get("run_id") or run_id),
            str(repo_root),
            str(obj.get("stage") or ""),
            None,
            created_at,
            str(obj.get("status") or ""),
            obj.get("pid"),
            obj.get("pack"),
            obj.get("profile"),
            obj.get("branch"),
            obj.get("head_sha"),
            obj.get("error"),
            obj.get("started_at"),
            obj.get("updated_at"),
            obj.get("ended_at"),
            obj.get("exit_code"),
        ),
    )
    con.commit()
    return obj


def ingest_run_history(con: sqlite3.Connection, *, repo_root: Path, run_id: str) -> None:
    """Best-effort ingestion of run artifacts (events + approvals/decisions) into sqlite."""
    try:
        _ingest_events(con, repo_root=repo_root, run_id=run_id)
    except Exception:
        pass
    try:
        _ingest_approvals_and_decisions(con, repo_root=repo_root, run_id=run_id)
    except Exception:
        pass


def _ingest_events(con: sqlite3.Connection, *, repo_root: Path, run_id: str) -> None:
    ep = repo_root / ".noctune_cache" / "runs" / run_id / "events" / "events.jsonl"
    if not ep.exists():
        ep = repo_root / ".noctune_cache" / "runs" / run_id / "logs" / "events.jsonl"
    if not ep.exists():
        return

    idx = 0
    with ep.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _try_json_loads(line)
            if obj:
                typ = str(obj.get("type") or obj.get("event") or "")
                ts = str(obj.get("ts") or obj.get("time") or obj.get("created_at") or "")
                con.execute(
                    "INSERT OR IGNORE INTO events(run_id, idx, ts, type, payload_json) VALUES(?,?,?,?,?)",
                    (run_id, int(idx), ts, typ, json.dumps(obj, ensure_ascii=False)),
                )
            idx += 1
    con.commit()


def _ingest_approvals_and_decisions(
    con: sqlite3.Connection, *, repo_root: Path, run_id: str
) -> None:
    ad = repo_root / ".noctune_cache" / "runs" / run_id / "state" / "approvals"
    if not ad.exists():
        return

    for p in sorted(ad.glob("*.json")):
        obj = _try_json_loads(p.read_text(encoding="utf-8", errors="replace"))
        if not obj:
            continue

        approval_id = str(obj.get("approval_id") or p.stem)
        con.execute(
            "INSERT OR REPLACE INTO approvals(run_id, approval_id, created_at, file_path, symbol, risk_score, reason, diff, payload_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                approval_id,
                obj.get("created_at"),
                obj.get("file_path"),
                obj.get("symbol"),
                obj.get("risk_score"),
                obj.get("reason"),
                obj.get("diff"),
                json.dumps(obj, ensure_ascii=False),
            ),
        )

        dp = p.with_suffix(".decision")
        if not dp.exists():
            continue
        raw = dp.read_text(encoding="utf-8", errors="replace").strip()
        d = _try_json_loads(raw) or {}
        approved = d.get("approved")
        decision = None
        if isinstance(approved, bool):
            decision = "approved" if approved else "rejected"
        elif raw:
            low = raw.lower()
            decision = (
                "approved"
                if low.startswith("a") or low in ("true", "yes", "y")
                else "rejected"
            )

        if decision:
            payload = d if d else {"raw": raw}
            con.execute(
                "INSERT OR REPLACE INTO decisions(run_id, approval_id, decision, decided_at, decided_by, reason, payload_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    approval_id,
                    decision,
                    payload.get("decided_at"),
                    payload.get("decided_by"),
                    payload.get("reason") or "",
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    con.commit()


def get_run(con: sqlite3.Connection, *, run_id: str) -> Optional[dict[str, Any]]:
    row = con.execute(
        "SELECT run_id, repo_root, stage, created_at, status, pid, pack, profile, branch, head_sha, error, started_at, updated_at, ended_at, exit_code "
        "FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0],
        "repo_root": row[1],
        "stage": row[2],
        "created_at": row[3],
        "status": row[4],
        "pid": row[5],
        "pack": row[6],
        "profile": row[7],
        "branch": row[8],
        "head_sha": row[9],
        "error": row[10],
        "started_at": row[11],
        "updated_at": row[12],
        "ended_at": row[13],
        "exit_code": row[14],
    }


def tail_events(
    con: sqlite3.Connection, *, run_id: str, cursor: Optional[int] = None, limit: int = 200
) -> tuple[list[dict[str, Any]], int, int]:
    lim = max(1, int(limit))
    if cursor is None:
        mx = con.execute("SELECT MAX(idx) FROM events WHERE run_id=?", (run_id,)).fetchone()
        max_idx = int(mx[0]) if mx and mx[0] is not None else -1
        start = max(0, max_idx - lim + 1) if max_idx >= 0 else 0
    else:
        start = max(0, int(cursor))

    rows = con.execute(
        "SELECT idx, payload_json FROM events WHERE run_id=? AND idx>=? ORDER BY idx ASC LIMIT ?",
        (run_id, int(start), lim),
    ).fetchall()

    out: list[dict[str, Any]] = []
    last_idx = start
    for idx, payload_json in rows:
        last_idx = int(idx)
        try:
            out.append(json.loads(payload_json))
        except Exception:
            out.append({"idx": last_idx, "raw": payload_json})
    next_cursor = (last_idx + 1) if rows else start
    return out, start, next_cursor


def list_approvals_with_decisions(
    con: sqlite3.Connection, *, run_id: str
) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT "
        "a.approval_id, a.created_at, a.file_path, a.symbol, a.risk_score, a.reason, a.diff, a.payload_json, "
        "d.decision, d.decided_at, d.decided_by, d.reason, d.payload_json "
        "FROM approvals a "
        "LEFT JOIN decisions d ON (d.run_id=a.run_id AND d.approval_id=a.approval_id) "
        "WHERE a.run_id=? "
        "ORDER BY a.created_at ASC, a.approval_id ASC",
        (run_id,),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "approval_id": r[0],
                "created_at": r[1],
                "file_path": r[2],
                "symbol": r[3],
                "risk_score": r[4],
                "reason": r[5],
                "diff": r[6],
                "approval_payload": _try_json_loads(r[7]) or {"raw": r[7]},
                "decision": r[8],
                "decided_at": r[9],
                "decided_by": r[10],
                "decision_reason": r[11],
                "decision_payload": _try_json_loads(r[12]) or ({"raw": r[12]} if r[12] else None),
            }
        )
    return out
