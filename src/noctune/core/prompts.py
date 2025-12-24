# src/noctune/core/prompts.py
from __future__ import annotations

from importlib import resources
from pathlib import Path

_PROMPT_FILES = ("plan.md", "review.md", "edit.md", "repair.md")


def _packaged_text(name: str) -> str:
    return resources.files("noctune.prompts").joinpath(name).read_text(encoding="utf-8")


def _overrides_dir(root: Path) -> Path:
    return root.resolve() / ".noctune_cache" / "overrides"


def ensure_prompt_overrides(root: Path, overwrite: bool = False) -> Path:
    """
    Ensure --root/.noctune_cache/overrides/{plan,review,edit,repair}.md exist.
    Does not overwrite by default.
    Returns the overrides directory path.
    """
    od = _overrides_dir(root)
    od.mkdir(parents=True, exist_ok=True)

    for name in _PROMPT_FILES:
        out = od / name
        if out.exists() and not overwrite:
            continue
        out.write_text(_packaged_text(name), encoding="utf-8")

    return od


def load_prompt(root: Path, name: str) -> str:
    """
    Load prompt text by name (e.g. 'review.md').

    Resolution order:
      1) --root/.noctune_cache/overrides/<name> (user-editable)
      2) packaged prompt in noctune.prompts/<name> (fallback)

    Also ensures override files exist (auto-heal).
    """
    ensure_prompt_overrides(root, overwrite=False)
    override_path = _overrides_dir(root) / name
    if override_path.exists():
        return override_path.read_text(encoding="utf-8")
    return _packaged_text(name)
