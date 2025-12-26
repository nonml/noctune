from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Any


def check_parse(file_abs: str) -> tuple[bool, str | None]:
    try:
        source = Path(file_abs).read_text(encoding="utf-8", errors="replace")
        ast.parse(source)
        return True, None
    except SyntaxError as e:
        return (
            False,
            f"{e.__class__.__name__}: {e.msg} at line {e.lineno}, col {e.offset}",
        )


def check_ruff(file_abs: str) -> tuple[bool, Any | None, str | None]:
    try:
        cp = subprocess.run(
            ["ruff", "check", file_abs, "--output-format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return True, None, "ruff not found on PATH; skipping ruff gate"
    if cp.returncode == 0:
        return True, [], cp.stderr.strip() or None
    try:
        return False, json.loads(cp.stdout or "[]"), cp.stderr.strip() or None
    except Exception:
        return False, cp.stdout[:2000], cp.stderr.strip() or None


def ruff_fix_safe(file_abs: str) -> tuple[bool, str | None]:
    try:
        cp = subprocess.run(
            ["ruff", "check", file_abs, "--fix"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return True, "ruff not found on PATH; skipping ruff --fix"
    return cp.returncode == 0, (cp.stderr.strip() or None)
