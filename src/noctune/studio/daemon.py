from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..core.run_state import mark_failed_if_pid_gone, read_run_state, update_run_state
from ..core.state import ensure_run_paths
from .db import (
    claim_next_job,
    connect,
    default_db_path,
    enqueue_job,
    finish_job,
    get_run,
    ingest_run_history,
    list_jobs,
    list_approvals_with_decisions,
    list_runs,
    tail_events,
    upsert_run_from_run_json,
    update_job_running,
)
from .worker import pid_exists, start_run, stop_run


class _RepoJobRunner:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.db_path = default_db_path(repo_root)
        self._stop = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._stop:
                    return

            try:
                con = connect(self.db_path)

                # If any job is running and pid is still alive, do nothing.
                row = con.execute(
                    "SELECT job_id, run_id, pid FROM jobs WHERE repo_root=? AND status='running' ORDER BY job_id DESC LIMIT 1",
                    (str(self.repo_root),),
                ).fetchone()
                if row and row[2] and pid_exists(int(row[2])):
                    time.sleep(2.0)
                    continue

                # If a running job pid is gone, sync run artifacts into sqlite and mark it terminal.
                if row and row[2] and not pid_exists(int(row[2])):
                    job_id, run_id, _pid = int(row[0]), row[1], int(row[2])
                    terminal_status = "done"
                    try:
                        if run_id:
                            state_dir = self.repo_root / ".noctune_cache" / "runs" / str(run_id) / "state"
                            if state_dir.exists():
                                st = mark_failed_if_pid_gone(str(state_dir))
                                terminal_status = (
                                    "failed" if str(st.get("status") or "").lower() == "failed" else "done"
                                )
                            upsert_run_from_run_json(con, repo_root=self.repo_root, run_id=str(run_id))
                            ingest_run_history(con, repo_root=self.repo_root, run_id=str(run_id))
                    except Exception:
                        pass
                    finish_job(con, job_id=job_id, status=terminal_status)

                job = claim_next_job(con, repo_root=str(self.repo_root))
                if not job:
                    time.sleep(2.0)
                    continue

                h = start_run(
                    repo_root=self.repo_root,
                    stage=job["stage"],
                    rel_paths=job.get("rel_paths"),
                    extra_args=job.get("extra_args"),
                )

                try:
                    upsert_run_from_run_json(con, repo_root=self.repo_root, run_id=h.run_id)
                except Exception:
                    con.execute(
                        "INSERT OR REPLACE INTO runs(run_id, repo_root, stage, rel_paths_json, created_at, status, pid) "
                        "VALUES(?,?,?,?,datetime('now'),?,?)",
                        (h.run_id, str(self.repo_root), job["stage"], None, "running", h.pid),
                    )
                    con.commit()
                update_job_running(con, job_id=int(job["job_id"]), run_id=h.run_id, pid=int(h.pid))
            except Exception as e:
                try:
                    con = connect(self.db_path)
                    row = con.execute(
                        "SELECT job_id FROM jobs WHERE repo_root=? AND status='starting' ORDER BY job_id DESC LIMIT 1",
                        (str(self.repo_root),),
                    ).fetchone()
                    if row:
                        finish_job(con, job_id=int(row[0]), status="failed", error=str(e))
                except Exception:
                    pass
                time.sleep(2.0)


_RUNNERS: dict[str, _RepoJobRunner] = {}


def _get_runner(root: Path) -> _RepoJobRunner:
    key = str(root)
    r = _RUNNERS.get(key)
    if r is None:
        r = _RepoJobRunner(root)
        _RUNNERS[key] = r
    return r


def _tail_events_cursor(
    events_path: Path, *, cursor: Optional[int] = None, limit: int = 200
) -> tuple[list[dict[str, Any]], int, int]:
    if not events_path.exists():
        return [], 0, 0

    # Simple + reliable: read all lines (runs are typically small). If this becomes
    # heavy later, replace with chunked seek.
    raw_lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    n = len(raw_lines)
    lim = max(1, int(limit))

    if cursor is None:
        start = max(0, n - lim)
    else:
        start = max(0, min(int(cursor), n))

    end = min(n, start + lim)
    out: list[dict[str, Any]] = []
    for ln in raw_lines[start:end]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue

    return out, start, end


def _pending_approvals(repo_root: Path, run_id: str) -> list[dict[str, Any]]:
    ad = repo_root / ".noctune_cache" / "runs" / run_id / "state" / "approvals"
    if not ad.exists():
        return []
    approvals: list[dict[str, Any]] = []
    for p in sorted(ad.glob("*.json")):
        if p.with_suffix(".decision").exists():
            continue
        try:
            approvals.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return approvals


def create_app():
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel
    except Exception as e:
        raise RuntimeError('Noctune Studio requires extra deps. Install: pip install -e ".[studio]"') from e

    app = FastAPI(title="Noctune Studio API")

    class RunStart(BaseModel):
        repo_root: str
        stage: str = "run"
        rel_paths: Optional[list[str]] = None
        extra_args: Optional[list[str]] = None

    @app.post("/runs/start")
    def runs_start(body: RunStart) -> dict[str, Any]:
        root = Path(body.repo_root).resolve()
        h = start_run(repo_root=root, stage=body.stage, rel_paths=body.rel_paths, extra_args=body.extra_args)
        con = connect(default_db_path(root))
        try:
            upsert_run_from_run_json(con, repo_root=root, run_id=h.run_id)
        except Exception:
            con.execute(
                "INSERT OR REPLACE INTO runs(run_id, repo_root, stage, rel_paths_json, created_at, status, pid) "
                "VALUES(?,?,?,?,datetime('now'),?,?)",
                (h.run_id, str(root), body.stage, None, "running", h.pid),
            )
            con.commit()
        return {"run_id": h.run_id, "pid": h.pid}

    class RunStop(BaseModel):
        repo_root: str

    @app.post("/runs/{run_id}/stop")
    def runs_stop(run_id: str, body: RunStop) -> dict[str, Any]:
        root = Path(body.repo_root).resolve()
        con = connect(default_db_path(root))
        row = con.execute("SELECT pid FROM runs WHERE run_id=?", (run_id,)).fetchone()
        pid = int(row[0]) if row and row[0] else None
        stop_run(repo_root=root, run_id=run_id, pid=pid)
        try:
            rp = ensure_run_paths(str(root), run_id)
            update_run_state(rp.state_dir, status="stopping", msg="stop requested")
        except Exception:
            pass
        con.execute("UPDATE runs SET status=? WHERE run_id=?", ("stopping", run_id))
        con.commit()
        return {"ok": True}

    @app.get("/runs/{run_id}/status")
    def runs_status(run_id: str, repo_root: str) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        state_dir = root / ".noctune_cache" / "runs" / run_id / "state"
        st = read_run_state(str(state_dir)) if state_dir.exists() else {}
        if st:
            # If the worker died, mark the run failed (best-effort).
            st = mark_failed_if_pid_gone(str(state_dir))
            try:
                con = connect(default_db_path(root))
                upsert_run_from_run_json(con, repo_root=root, run_id=run_id)
                if str(st.get("status") or "").lower() in ("done", "failed", "stopped"):
                    ingest_run_history(con, repo_root=root, run_id=run_id)
            except Exception:
                pass
            return st

        # Fallback: legacy sqlite-only status
        con = connect(default_db_path(root))
        row = con.execute(
            "SELECT run_id, status, pid, created_at FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run_id": row[0], "status": row[1], "pid": row[2], "created_at": row[3]}

    @app.get("/runs/list")
    def runs_list(repo_root: str, limit: int = 50) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        return {"runs": list_runs(con, repo_root=str(root), limit=int(limit))}

    @app.get("/runs/{run_id}/events")
    def runs_events(
        run_id: str,
        repo_root: str,
        cursor: Optional[int] = None,
        limit: int = 200,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        ep = root / ".noctune_cache" / "runs" / run_id / "events" / "events.jsonl"
        if not ep.exists():
            ep = root / ".noctune_cache" / "runs" / run_id / "logs" / "events.jsonl"
        # Backward-compatible: if callers still use max_lines, it behaves like tail.
        lim = int(limit) if limit is not None else int(max_lines)
        events, cur, next_cur = _tail_events_cursor(ep, cursor=cursor, limit=lim)
        return {"events": events, "cursor": cur, "next_cursor": next_cur}

    @app.get("/runs/{run_id}/events_db")
    def runs_events_db(
        run_id: str, repo_root: str, cursor: Optional[int] = None, limit: int = 200
    ) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        try:
            upsert_run_from_run_json(con, repo_root=root, run_id=run_id)
            ingest_run_history(con, repo_root=root, run_id=run_id)
        except Exception:
            pass
        events, cur, next_cur = tail_events(con, run_id=run_id, cursor=cursor, limit=limit)
        return {"events": events, "cursor": cur, "next_cursor": next_cur}

    @app.get("/runs/{run_id}/approvals")
    def runs_approvals(run_id: str, repo_root: str) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        return {"approvals": _pending_approvals(root, run_id)}

    @app.get("/runs/{run_id}/audit")
    def runs_audit(run_id: str, repo_root: str) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        try:
            upsert_run_from_run_json(con, repo_root=root, run_id=run_id)
            ingest_run_history(con, repo_root=root, run_id=run_id)
        except Exception:
            pass
        run = get_run(con, run_id=run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run": run, "approvals": list_approvals_with_decisions(con, run_id=run_id)}

    class ApprovalDecision(BaseModel):
        repo_root: str
        approved: bool
        reason: str = ""

    @app.post("/runs/{run_id}/approvals/{approval_id}")
    def runs_approvals_decide(run_id: str, approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
        root = Path(body.repo_root).resolve()
        ad = root / ".noctune_cache" / "runs" / run_id / "state" / "approvals"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / f"{approval_id}.decision").write_text(
            json.dumps({"approved": bool(body.approved), "reason": body.reason}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            con = connect(default_db_path(root))
            ingest_run_history(con, repo_root=root, run_id=run_id)
        except Exception:
            pass
        return {"ok": True}

    # Queue endpoints (single-repo sequential runner)
    class JobEnqueue(BaseModel):
        repo_root: str
        stage: str = "run"
        rel_paths: Optional[list[str]] = None
        extra_args: Optional[list[str]] = None

    @app.post("/jobs/enqueue")
    def jobs_enqueue(body: JobEnqueue) -> dict[str, Any]:
        root = Path(body.repo_root).resolve()
        con = connect(default_db_path(root))
        jid = enqueue_job(
            con,
            repo_root=str(root),
            stage=body.stage,
            rel_paths=body.rel_paths,
            extra_args=body.extra_args,
        )
        _get_runner(root)
        return {"job_id": jid, "status": "queued"}

    @app.get("/jobs/list")
    def jobs_list(repo_root: str, limit: int = 50) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        con = connect(default_db_path(root))
        return {"jobs": list_jobs(con, repo_root=str(root), limit=int(limit))}

    # Minimal approve UI
    UI_HOME = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\"/>
    <title>Noctune Studio</title>
    <style>
      body { font-family: ui-sans-serif, system-ui; margin: 24px; }
      input, button { padding: 8px; }
      .card { border: 1px solid #ddd; padding: 12px; border-radius: 10px; margin: 12px 0; }
    </style>
  </head>
  <body>
    <h2>Noctune Studio ✅</h2>
    <p>Paste repo_root + run_id to review pending approvals.</p>
    <div class=\"card\">
      <div><label>repo_root</label><br/><input id=\"repo\" style=\"width: 480px\" placeholder=\"/path/to/repo\"/></div>
      <div style=\"margin-top: 8px\"><label>run_id</label><br/><input id=\"run\" style=\"width: 240px\" placeholder=\"run id\"/></div>
      <div style=\"margin-top: 8px\"><button onclick=\"go()\">Open</button></div>
    </div>
    <script>
      function go(){
        const repo = encodeURIComponent(document.getElementById('repo').value.trim());
        const run = encodeURIComponent(document.getElementById('run').value.trim());
        if(!repo || !run) return;
        location.href = '/ui/run/' + run + '?repo_root=' + repo;
      }
    </script>
  </body>
</html>"""

    @app.get("/ui", response_class=HTMLResponse)
    def ui_home() -> str:
        return UI_HOME

    UI_RUN_TMPL = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\"/>
    <title>Noctune Studio - Approvals</title>
    <style>
      body { font-family: ui-sans-serif, system-ui; margin: 24px; }
      button { padding: 8px 10px; margin-right: 8px; }
      .card { border: 1px solid #ddd; padding: 12px; border-radius: 10px; margin: 12px 0; }
      pre { white-space: pre-wrap; background: #f7f7f7; padding: 10px; border-radius: 8px; }
      .meta { color: #555; font-size: 12px; }
    </style>
  </head>
  <body>
    <h2>Run __RUN_ID__</h2>
    <div class=\"meta\">repo_root: __REPO__</div>
    <div id=\"list\"></div>

    <script>
      const repo_root = __REPO_JSON__;
      const run_id = __RUN_ID_JSON__;
      const approvals = __APPROVALS__;

      function esc(s){
        return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }

      async function decide(id, approved){
        const reason = prompt(approved ? 'Approval note (optional)' : 'Rejection reason (optional)') || '';
        const res = await fetch('/runs/' + run_id + '/approvals/' + id, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo_root, approved, reason })
        });
        if(res.ok) location.reload();
        else alert('Failed: ' + (await res.text()));
      }

      function render(){
        const el = document.getElementById('list');
        if(!approvals.length){
          el.innerHTML = '<p>No pending approvals 🎉</p>';
          return;
        }
        let html = '';
        for(const a of approvals){
          html += '<div class="card">';
          html += '<div><b>' + esc(a.rel_path) + '</b> <span class="meta">(' + esc(a.qname) + ')</span></div>';
          html += '<div class="meta">' + esc(a.summary || '') + '</div>';
          html += '<div style="margin-top:8px;">';
          html += '<button onclick="decide(\'' + a.approval_id + '\', true)">Approve</button>';
          html += '<button onclick="decide(\'' + a.approval_id + '\', false)">Reject</button>';
          html += '</div>';
          html += '<details style="margin-top:8px;"><summary>diff</summary><pre>' + esc(a.diff || '') + '</pre></details>';
          html += '</div>';
        }
        el.innerHTML = html;
      }
      render();
    </script>
  </body>
</html>"""

    @app.get("/ui/run/{run_id}", response_class=HTMLResponse)
    def ui_run(run_id: str, repo_root: str) -> str:
        repo = Path(repo_root).resolve()
        approvals = _pending_approvals(repo, run_id)
        payload = json.dumps(approvals, ensure_ascii=False)
        return (
            UI_RUN_TMPL.replace("__RUN_ID__", run_id)
            .replace("__REPO__", str(repo))
            .replace("__REPO_JSON__", json.dumps(str(repo)))
            .replace("__RUN_ID_JSON__", json.dumps(run_id))
            .replace("__APPROVALS__", payload)
        )

    return app
