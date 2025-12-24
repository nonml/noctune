from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_ruff_check(path: Path) -> tuple[bool, str]:
    """
    Returns (ok, output). Uses ruff if available.
    """
    exe = which("ruff")
    if not exe:
        return False, "ruff not found on PATH"
    p = subprocess.run([exe, "check", str(path)], capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out


def run_ruff_fix_safe(path: Path) -> tuple[bool, str]:
    """
    Apply safe fixes only (default behavior of ruff --fix is safe-only unless unsafe is enabled).
    Returns (ok, output) where ok means 'command succeeded', not 'lint is clean'.
    """
    exe = which("ruff")
    if not exe:
        return False, "ruff not found on PATH"
    p = subprocess.run(
        [exe, "check", "--fix", str(path)], capture_output=True, text=True
    )
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out
