from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .logger import EventLogger


@dataclass
class GitRunContext:
    enabled: bool
    run_branch: Optional[str] = None
    base_branch: Optional[str] = None
    stashed: bool = False


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
    )


def _current_branch(root: Path) -> Optional[str]:
    p = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if p.returncode != 0:
        return None
    b = (p.stdout or "").strip()
    return b or None


def head_sha(root: Path) -> Optional[str]:
    p = _git(root, "rev-parse", "HEAD")
    if p.returncode != 0:
        return None
    s = (p.stdout or "").strip()
    return s or None


def _is_clean(root: Path) -> bool:
    p = _git(root, "status", "--porcelain")
    return p.returncode == 0 and (p.stdout or "").strip() == ""


def ensure_git_run_branch(
    *,
    root: Path,
    run_id: str,
    branch_prefix: str,
    base_branch: Optional[str],
    auto_stash: bool,
    logger: Optional[EventLogger] = None,
) -> GitRunContext:
    # If not a git repo, disable silently.
    if not (root / ".git").exists():
        if logger:
            logger.warn(event="git_disabled", msg="not a git repo")
        return GitRunContext(enabled=False)

    ctx = GitRunContext(enabled=True)

    cur = _current_branch(root)
    ctx.base_branch = base_branch or cur

    if auto_stash and not _is_clean(root):
        p = _git(root, "stash", "push", "-u", "-m", f"noctune:{run_id}")
        if p.returncode == 0:
            ctx.stashed = True
            if logger:
                logger.info(event="git_stash", msg=(p.stdout or "").strip())
        else:
            if logger:
                logger.warn(
                    event="git_stash_failed", msg=(p.stderr or p.stdout or "").strip()
                )

    run_branch = f"{branch_prefix}/{run_id}"
    # Create and checkout run branch
    p = _git(root, "checkout", "-B", run_branch)
    if p.returncode != 0:
        if logger:
            logger.warn(
                event="git_checkout_failed", msg=(p.stderr or p.stdout or "").strip()
            )
        return GitRunContext(enabled=False)

    ctx.run_branch = run_branch
    if logger:
        logger.info(
            event="git_branch", run_branch=run_branch, base_branch=ctx.base_branch or ""
        )

    return ctx


def maybe_git_commit(
    *,
    root: Path,
    rel_path: str,
    qname: str,
    message_template: str,
    logger: Optional[EventLogger] = None,
) -> None:
    # Add only the touched file (cheap, safer)
    add = _git(root, "add", "--", rel_path)
    if add.returncode != 0:
        if logger:
            logger.warn(
                event="git_add_failed",
                rel_path=rel_path,
                msg=(add.stderr or add.stdout or "").strip(),
            )
        return

    # If nothing staged, skip
    diff = _git(root, "diff", "--cached", "--name-only")
    if diff.returncode != 0 or not (diff.stdout or "").strip():
        return

    msg = message_template.format(rel_path=rel_path, qname=qname)
    commit = _git(root, "commit", "-m", msg)
    if commit.returncode != 0:
        if logger:
            logger.warn(
                event="git_commit_failed",
                msg=(commit.stderr or commit.stdout or "").strip(),
            )
        return
    if logger:
        logger.info(event="git_commit", rel_path=rel_path, qname=qname, msg=msg)


def _changed_files_worktree(root: Path) -> List[str]:
    """Return modified/untracked file paths (worktree) relative to repo root."""
    p = _git(root, "status", "--porcelain")
    if p.returncode != 0:
        return []
    out: List[str] = []
    for ln in (p.stdout or "").splitlines():
        if not ln:
            continue
        # Format: XY <path> (or 'R  old -> new')
        s = ln[3:].strip()
        if " -> " in s:
            s = s.split(" -> ", 1)[1].strip()
        if s:
            out.append(s)
    # de-dupe, stable order
    seen = set()
    uniq: List[str] = []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _group_key(rel_path: str, *, strategy: str, module_depth: int = 2) -> str:
    rp = rel_path.replace("\\", "/")
    if strategy == "single":
        return "all"
    if strategy == "file":
        return rp
    if strategy == "module":
        parts = [p for p in rp.split("/") if p]
        if len(parts) <= 1:
            return parts[0] if parts else "root"
        depth = max(1, int(module_depth or 1))
        return "/".join(parts[: min(len(parts), depth)])
    # policy_pack grouping is handled by caller (needs pack metadata); fallback to module
    return _group_key(rel_path, strategy="module", module_depth=module_depth)


def commit_patchsets(
    *,
    root: Path,
    changed_files: List[str] | None,
    strategy: str,
    module_depth: int,
    max_commits: int,
    message_template: str,
    logger: Optional[EventLogger] = None,
) -> int:
    """Commit grouped patchsets (1..N commits) from current worktree changes.

    Returns number of commits created.
    """
    files = list(changed_files or [])
    if not files:
        files = _changed_files_worktree(root)
    if not files:
        return 0

    groups: Dict[str, List[str]] = {}
    for f in files:
        k = _group_key(f, strategy=strategy, module_depth=module_depth)
        groups.setdefault(k, []).append(f)

    # Compress to max_commits by merging smallest groups into 'misc'
    maxc = max(1, int(max_commits or 1))
    if len(groups) > maxc:
        items = sorted(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
        keep = items[-(maxc - 1) :] if maxc > 1 else []
        misc = items[: len(items) - len(keep)]
        newg: Dict[str, List[str]] = {k: v for k, v in keep}
        misc_files: List[str] = []
        for _, v in misc:
            misc_files.extend(v)
        if misc_files:
            newg["misc"] = misc_files
        groups = dict(sorted(newg.items(), key=lambda kv: kv[0]))

    commits = 0
    # Ensure index is clean between patchsets
    _git(root, "reset")
    for group, flist in groups.items():
        _git(root, "reset")
        add = _git(root, "add", "--", *flist)
        if add.returncode != 0:
            if logger:
                logger.warn(
                    event="patchset_add_failed",
                    group=group,
                    msg=(add.stderr or add.stdout or "").strip(),
                )
            continue

        diff = _git(root, "diff", "--cached", "--name-only")
        if diff.returncode != 0 or not (diff.stdout or "").strip():
            continue

        msg = message_template.format(group=group)
        c = _git(root, "commit", "-m", msg)
        if c.returncode != 0:
            if logger:
                logger.warn(
                    event="patchset_commit_failed",
                    group=group,
                    msg=(c.stderr or c.stdout or "").strip(),
                )
            continue

        commits += 1
        if logger:
            logger.info(event="patchset_commit", group=group, files=len(flist), msg=msg)

    _git(root, "reset")
    return commits
