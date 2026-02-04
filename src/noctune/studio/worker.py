from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.state import ensure_run_paths
from ..core.run_state import init_run_state, update_run_state


@dataclass
class RunHandle:
    run_id: str
    pid: int


def start_run(*, repo_root: Path, stage: str, rel_paths: Optional[list[str]] = None, extra_args: Optional[list[str]] = None) -> RunHandle:
    # Create run_id + cache dirs first so stop.flag works immediately.
    rp = ensure_run_paths(str(repo_root), None)
    run_id = rp.run_id
    try:
        init_run_state(
            state_dir=rp.state_dir,
            run_id=run_id,
            repo_root=str(repo_root),
            stage=stage,
            status="starting",
            pid=None,
        )
        update_run_state(rp.state_dir, status="starting", stage=stage, repo_root=str(repo_root))
    except Exception:
        pass

    cmd = [sys.executable, "-m", "noctune", stage, "--root", str(repo_root), "--run-id", run_id]
    if rel_paths:
        # write file list into run dir for reproducibility
        fl = Path(rp.run_dir) / "file_list.txt"
        fl.write_text("\n".join(rel_paths) + "\n", encoding="utf-8")
        cmd += ["--file-list", str(fl)]
    if extra_args:
        cmd += extra_args

    p = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        update_run_state(rp.state_dir, pid=int(p.pid), status="running")
    except Exception:
        pass
    return RunHandle(run_id=run_id, pid=int(p.pid))


def stop_run(*, repo_root: Path, run_id: str, pid: Optional[int] = None) -> None:
    rp = ensure_run_paths(str(repo_root), run_id)
    Path(rp.state_dir).mkdir(parents=True, exist_ok=True)
    (Path(rp.state_dir) / "stop.flag").write_text("stop\n", encoding="utf-8")
    try:
        update_run_state(rp.state_dir, status="stopping", msg="stop requested")
    except Exception:
        pass
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # POSIX: signal 0 checks existence/permission.
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
