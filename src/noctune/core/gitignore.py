from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pathspec import PathSpec
from pathspec.patterns.gitwildmatch import GitWildMatchPattern


@dataclass
class GitIgnore:
    spec: PathSpec

    @staticmethod
    def load(repo_root: Path) -> "GitIgnore":
        gi = repo_root / ".gitignore"
        lines: list[str] = []
        if gi.exists():
            lines = [
                ln.rstrip("\n")
                for ln in gi.read_text(encoding="utf-8", errors="ignore").splitlines()
            ]
        spec = PathSpec.from_lines(GitWildMatchPattern, lines)
        return GitIgnore(spec=spec)

    def is_ignored(self, rel_posix: str) -> bool:
        # PathSpec expects posix separators
        return self.spec.match_file(rel_posix)
