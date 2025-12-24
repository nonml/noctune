from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoPaths:
    root: Path
    cache_dir: Path
    runs_dir: Path

    @staticmethod
    def from_root(root: Path) -> "RepoPaths":
        root = root.resolve()
        cache_dir = root / ".noctune_cache"
        runs_dir = cache_dir / "runs"
        return RepoPaths(root=root, cache_dir=cache_dir, runs_dir=runs_dir)

    def ensure(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
