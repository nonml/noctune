from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from ..core.run_state import mark_failed_if_pid_gone, read_run_state
from .db import connect, default_db_path, enqueue_job, list_jobs
from .worker import start_run, stop_run


def _tail_events_jsonl(events_path: Path, *, max_lines: int = 100) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    lim = max(1, min(int(max_lines), 500))
    raw = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for ln in raw[-lim:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


async def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:
        raise RuntimeError('MCP server requires extra deps. Install: pip install -e ".[studio]"') from e

    mcp = FastMCP("noctune-studio")

    @mcp.tool()
    def start(repo_root: str, stage: str = "run") -> dict[str, Any]:
        h = start_run(repo_root=Path(repo_root).resolve(), stage=stage)
        return {"run_id": h.run_id, "pid": h.pid}

    @mcp.tool()
    def stop(repo_root: str, run_id: str, pid: Optional[int] = None) -> dict[str, Any]:
        stop_run(repo_root=Path(repo_root).resolve(), run_id=run_id, pid=pid)
        return {"ok": True}

    @mcp.tool()
    def enqueue(repo_root: str, stage: str = "run") -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        jid = enqueue_job(con, repo_root=str(root), stage=stage)
        return {"job_id": jid, "status": "queued"}

    @mcp.tool()
    def jobs(repo_root: str, limit: int = 20) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        return {"jobs": list_jobs(con, repo_root=str(root), limit=int(limit))}

    @mcp.tool()
    def events(repo_root: str, run_id: str, max_lines: int = 100) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        ep = root / ".noctune_cache" / "runs" / run_id / "events" / "events.jsonl"
        if not ep.exists():
            ep = root / ".noctune_cache" / "runs" / run_id / "logs" / "events.jsonl"
        return {"events": _tail_events_jsonl(ep, max_lines=int(max_lines))}

    @mcp.tool()
    def status(repo_root: str, run_id: str) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        state_dir = root / ".noctune_cache" / "runs" / run_id / "state"
        if not state_dir.exists():
            return {"ok": False, "run": None}
        # Best-effort: if pid is gone, mark failed.
        mark_failed_if_pid_gone(str(state_dir))
        return {"ok": True, "run": read_run_state(str(state_dir))}

    @mcp.tool()
    def approvals(repo_root: str, run_id: str) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        ad = root / ".noctune_cache" / "runs" / run_id / "state" / "approvals"
        if not ad.exists():
            return {"approvals": []}
        out = []
        for p in sorted(ad.glob("*.json")):
            if p.with_suffix(".decision").exists():
                continue
            try:
                out.append(__import__("json").loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"approvals": out}

    @mcp.tool()
    def approve(repo_root: str, run_id: str, approval_id: str, approved: bool, reason: str = "") -> dict[str, Any]:
        root = Path(repo_root).resolve()
        ad = root / ".noctune_cache" / "runs" / run_id / "state" / "approvals"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / f"{approval_id}.decision").write_text(
            __import__("json").dumps({"approved": bool(approved), "reason": reason}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"ok": True}

    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
