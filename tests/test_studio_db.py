from __future__ import annotations

import json
from pathlib import Path


def _write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def test_studio_db_ingest_and_tail(tmp_path: Path) -> None:
    from noctune.studio import db as db_mod

    repo_root = tmp_path / "repo"
    run_id = "20260204_000000_deadbeef"

    state_dir = repo_root / ".noctune_cache" / "runs" / run_id / "state"
    _write(
        state_dir / "run.json",
        json.dumps(
            {
                "run_id": run_id,
                "repo_root": str(repo_root),
                "stage": "run",
                "status": "done",
                "pid": 123,
                "pack": "lint_fix",
                "profile": "local",
                "branch": "main",
                "head_sha": "abc123",
                "started_at": "2026-02-04T00:00:00Z",
                "updated_at": "2026-02-04T00:00:02Z",
                "ended_at": "2026-02-04T00:00:03Z",
                "exit_code": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
    )

    events_path = repo_root / ".noctune_cache" / "runs" / run_id / "events" / "events.jsonl"
    _write(
        events_path,
        "\n".join(
            [
                json.dumps({"type": "run_started", "ts": "2026-02-04T00:00:00Z"}),
                json.dumps({"type": "log", "ts": "2026-02-04T00:00:01Z", "msg": "hello"}),
                json.dumps({"type": "run_done", "ts": "2026-02-04T00:00:03Z"}),
            ]
        )
        + "\n",
    )

    approvals_dir = state_dir / "approvals"
    _write(
        approvals_dir / "a1.json",
        json.dumps(
            {
                "approval_id": "a1",
                "created_at": "2026-02-04T00:00:01Z",
                "file_path": "src/noctune/core/runner.py",
                "symbol": "Runner.run",
                "risk_score": 0.7,
                "reason": "touches core",
                "diff": "--- a\n+++ b\n",
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    _write(
        approvals_dir / "a1.decision",
        json.dumps(
            {
                "approved": True,
                "reason": "ok",
                "decided_at": "2026-02-04T00:00:02Z",
                "decided_by": "user",
            },
            ensure_ascii=False,
        )
        + "\n",
    )

    con = db_mod.connect(db_mod.default_db_path(repo_root))
    db_mod.upsert_run_from_run_json(con, repo_root=repo_root, run_id=run_id)
    db_mod.ingest_run_history(con, repo_root=repo_root, run_id=run_id)

    run = db_mod.get_run(con, run_id=run_id)
    assert run is not None
    assert run["run_id"] == run_id
    assert run["status"] == "done"
    assert run["pack"] == "lint_fix"

    events, cur, next_cur = db_mod.tail_events(con, run_id=run_id, cursor=None, limit=2)
    assert cur == 1
    assert next_cur == 3
    assert [e.get("type") for e in events] == ["log", "run_done"]

    approvals = db_mod.list_approvals_with_decisions(con, run_id=run_id)
    assert len(approvals) == 1
    assert approvals[0]["approval_id"] == "a1"
    assert approvals[0]["decision"] == "approved"

