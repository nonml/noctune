from __future__ import annotations

from typing import Dict, Optional

from .config import PolicyPack


def builtin_policy_packs() -> Dict[str, PolicyPack]:
    # Sensible defaults; users can override in noctune.toml under [tool.noctune.policy_packs.<name>]
    return {
        "lint_fix": PolicyPack(
            allowed_globs=["**/*.py"],
            max_diff_lines=200,
            tools_allowed=["ruff"],
            auto_approve_max_diff_lines=40,
            auto_approve_globs=["**/*.py"],
        ),
        "typing_pass": PolicyPack(
            allowed_globs=["**/*.py"],
            max_diff_lines=150,
            tools_allowed=["ruff", "mypy", "pyright"],
            auto_approve_max_diff_lines=25,
            auto_approve_globs=["**/*.py"],
        ),
        "py_upgrade": PolicyPack(
            allowed_globs=["**/*.py"],
            max_diff_lines=200,
            tools_allowed=["ruff"],
            auto_approve_max_diff_lines=30,
            auto_approve_globs=["**/*.py"],
        ),
        "deps_bump": PolicyPack(
            allowed_globs=[
                "pyproject.toml",
                "requirements*.txt",
                "poetry.lock",
                "pdm.lock",
                "uv.lock",
            ],
            max_diff_lines=120,
            tools_allowed=[],
            auto_approve_max_diff_lines=20,
            auto_approve_globs=[
                "pyproject.toml",
                "requirements*.txt",
                "poetry.lock",
                "pdm.lock",
                "uv.lock",
            ],
        ),
    }


def resolve_policy_pack(
    cfg_packs: Dict[str, PolicyPack], pack_name: str
) -> Optional[PolicyPack]:
    if not pack_name:
        return None
    if pack_name in cfg_packs:
        return cfg_packs[pack_name]
    builtins = builtin_policy_packs()
    return builtins.get(pack_name)
