from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .gitignore import GitIgnore

HARD_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    ".noctune_cache",
}


@dataclass
class RepoScanner:
    root: Path
    gitignore: GitIgnore

    @staticmethod
    def create(root: Path) -> "RepoScanner":
        return RepoScanner(
            root=root.resolve(), gitignore=GitIgnore.load(root.resolve())
        )

    def iter_python_files(self) -> Iterable[Path]:
        root = self.root

        for p in root.rglob("*.py"):
            rel = p.relative_to(root)

            # hard excludes by top-level folder
            if rel.parts and rel.parts[0] in HARD_EXCLUDES:
                continue

            rel_posix = rel.as_posix()

            # honor .gitignore
            if self.gitignore.is_ignored(rel_posix):
                continue

            yield p

    def from_file_list(self, file_list_path: Path) -> List[Path]:
        root = self.root
        out: List[Path] = []
        txt = file_list_path.read_text(encoding="utf-8", errors="ignore")
        for raw in txt.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            p = (root / s).resolve()
            if not p.exists() or p.suffix != ".py":
                continue
            # apply same excludes/ignore rules
            rel = p.relative_to(root)
            if rel.parts and rel.parts[0] in HARD_EXCLUDES:
                continue
            if self.gitignore.is_ignored(rel.as_posix()):
                continue
            out.append(p)
        return out
