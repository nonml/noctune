from __future__ import annotations

from importlib import resources
from pathlib import Path

# Shipped prompts live in the packaged module: noctune.prompts/
# Users can override prompts repo-locally at: --root/.noctune_cache/overrides/*.md
_PROMPT_FILES = (
    "review.md",
    "draft.md",
    "edit.md",
    "repair.md",
    "approve.md",
)


def _packaged_text(name: str) -> str:
    return resources.files("noctune.prompts").joinpath(name).read_text(encoding="utf-8")


def overrides_dir(root: Path) -> Path:
    return root.resolve() / ".noctune_cache" / "overrides"


def ensure_prompt_overrides(root: Path, *, overwrite: bool = False) -> Path:
    """
    Ensure --root/.noctune_cache/overrides/{review,draft,edit,repair,approve}.md exist.
    Does not overwrite by default.
    Returns the overrides directory path.
    """
    od = overrides_dir(root)
    od.mkdir(parents=True, exist_ok=True)
    for name in _PROMPT_FILES:
        out = od / name
        if out.exists() and not overwrite:
            continue
        try:
            out.write_text(_packaged_text(name), encoding="utf-8")
        except Exception:
            # Do not crash init/run if packaging is broken; leave a minimal placeholder.
            out.write_text(
                f"Noctune default prompt missing: {name}\n", encoding="utf-8"
            )
    return od


def load_prompt(root: Path, name: str) -> str:
    """Load prompt text by name, using override-first resolution."""
    ensure_prompt_overrides(root, overwrite=False)
    p = overrides_dir(root) / name
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return _packaged_text(name)
