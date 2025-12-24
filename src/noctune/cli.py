from __future__ import annotations

import argparse
from pathlib import Path

from .core.config import NoctuneConfig, load_config, write_noctune_toml
from .core.paths import RepoPaths
from .core.prompts import ensure_prompt_overrides
from .core.runner import run_noctune
from .core.scanner import RepoScanner
from .core.tools import which


def _prompt_yes_no(msg: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    resp = input(msg + suffix).strip().lower()
    if not resp:
        return not default_no
    return resp in ("y", "yes")


def _ensure_tooling(cfg: NoctuneConfig) -> None:
    if cfg.ruff_required and not which("ruff"):
        raise SystemExit(
            "noctune: ruff is required but not found on PATH. Install ruff and retry."
        )
    # rg is optional by design; you can add a warning later.


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    rp = RepoPaths.from_root(root)
    rp.ensure()

    # Load existing (if any), then prompt for missing pieces
    cfg, _cfg_path, _raw = load_config(root)

    if args.base_url:
        cfg.llm.base_url = args.base_url

    if not cfg.llm.base_url:
        cfg.llm.base_url = input(
            "LLM base_url (OpenAI-compatible, e.g. http://127.0.0.1:8080): "
        ).strip()

    if args.api_key is not None:
        cfg.llm.api_key = args.api_key
    elif cfg.llm.api_key is None:
        k = input("API key (optional, press enter to skip): ").strip()
        cfg.llm.api_key = k or None

    if args.allow_apply is not None:
        cfg.allow_apply = bool(args.allow_apply)
    else:
        # Ask once here; later commands can also prompt if missing.
        cfg.allow_apply = _prompt_yes_no(
            "Allow Noctune to modify files in this repo?", default_no=True
        )

    path = write_noctune_toml(root, cfg)
    print(f"noctune: wrote config: {path}")

    if args.gitignore_prompt:
        gi = root / ".gitignore"
        line = ".noctune_cache/\n"
        if _prompt_yes_no("Add .noctune_cache/ to .gitignore?", default_no=False):
            existing = (
                gi.read_text(encoding="utf-8", errors="ignore") if gi.exists() else ""
            )
            if ".noctune_cache/" not in existing:
                gi.write_text(
                    existing
                    + ("" if existing.endswith("\n") or existing == "" else "\n")
                    + line,
                    encoding="utf-8",
                )
                print("noctune: updated .gitignore")
            else:
                print("noctune: .gitignore already contains .noctune_cache/")
        else:
            print(
                "noctune: skipped .gitignore update. Add this line if desired: .noctune_cache/"
            )

    ensure_prompt_overrides(root, overwrite=False)
    print(
        f"noctune: prompt overrides created at: {root / '.noctune_cache' / 'overrides'}"
    )

    return 0


def _collect_paths(root: Path, paths: list[str], file_list: str | None) -> list[Path]:
    scanner = RepoScanner.create(root)
    if file_list:
        fl = Path(file_list)
        if not fl.is_absolute():
            fl = (root / fl).resolve()
        return scanner.from_file_list(fl)
    if not paths:
        # default to root scan
        return list(scanner.iter_python_files())

    out: list[Path] = []
    for p in paths:
        pp = (root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if pp.is_dir():
            # scan within that directory
            for f in pp.rglob("*.py"):
                # apply scanner filtering by reusing full scan and filtering membership is expensive;
                # keep simple for v0: only include files under dir, then ignore rules.
                rel = f.relative_to(root)
                if scanner.gitignore.is_ignored(rel.as_posix()):
                    continue
                if rel.parts and rel.parts[0] in (
                    ".noctune_cache",
                    ".git",
                    ".venv",
                    "venv",
                    "__pycache__",
                    "build",
                    "dist",
                ):
                    continue
                out.append(f)
        else:
            if pp.exists() and pp.suffix == ".py":
                rel = pp.relative_to(root)
                if not scanner.gitignore.is_ignored(rel.as_posix()):
                    out.append(pp)
    return out


def _require_apply_permission(cfg: NoctuneConfig, yes: bool) -> None:
    if cfg.allow_apply:
        return
    if yes:
        # In non-interactive mode, require explicit --yes; caller can persist by writing config later.
        raise SystemExit(
            "noctune: refusing to modify files without allow_apply=true in config. Run `noctune init` or re-run without --yes to approve interactively."
        )
    # Interactive prompt
    if not _prompt_yes_no(
        "Noctune needs permission to modify files in this repo. Allow now?",
        default_no=True,
    ):
        raise SystemExit("noctune: permission denied; no changes made.")


def _run_stage(
    stage: str,
    *,
    root: Path,
    cfg: NoctuneConfig,
    files: list[Path],
    run_id: str | None,
    max_files: int | None,
    ruff_fix: str,
    llm: str,
    verbose_stream: bool,
    log_level: str,
) -> int:
    return run_noctune(
        stage,
        root=root,
        cfg=cfg,
        run_id=run_id,
        files=files,
        max_files=max_files,
        ruff_fix=(ruff_fix == "safe"),
        llm_enabled=(llm == "on"),
        log_level=log_level,
        verbose_stream=verbose_stream,
    )


def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    files = _collect_paths(root, args.paths, args.file_list)
    return _run_stage(
        "plan",
        root=root,
        cfg=cfg,
        files=files,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix=args.ruff_fix,
        llm=args.llm,
        verbose_stream=cfg.llm.verbose_stream or bool(args.v),
        log_level=args.log_level,
    )


def cmd_review(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    files = _collect_paths(root, args.paths, args.file_list)
    return _run_stage(
        "review",
        root=root,
        cfg=cfg,
        files=files,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix=args.ruff_fix,
        llm=args.llm,
        verbose_stream=cfg.llm.verbose_stream or bool(args.v),
        log_level=args.log_level,
    )


def cmd_edit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    _ensure_tooling(cfg)
    _require_apply_permission(cfg, yes=bool(args.yes))
    files = _collect_paths(root, args.paths, args.file_list)
    return _run_stage(
        "edit",
        root=root,
        cfg=cfg,
        files=files,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix=args.ruff_fix,
        llm=args.llm,
        verbose_stream=cfg.llm.verbose_stream or bool(args.v),
        log_level=args.log_level,
    )


def cmd_repair(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    _ensure_tooling(cfg)
    _require_apply_permission(cfg, yes=bool(args.yes))
    files = _collect_paths(root, args.paths, args.file_list)
    return _run_stage(
        "repair",
        root=root,
        cfg=cfg,
        files=files,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix=args.ruff_fix,
        llm=args.llm,
        verbose_stream=cfg.llm.verbose_stream or bool(args.v),
        log_level=args.log_level,
    )


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    _ensure_tooling(cfg)
    _require_apply_permission(cfg, yes=bool(args.yes))
    files = _collect_paths(root, args.paths, args.file_list)
    return _run_stage(
        "run",
        root=root,
        cfg=cfg,
        files=files,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix=args.ruff_fix,
        llm=args.llm,
        verbose_stream=cfg.llm.verbose_stream or bool(args.v),
        log_level=args.log_level,
    )


def build_parser() -> argparse.ArgumentParser:
    # Common options should be accepted both BEFORE and AFTER the subcommand.
    common = argparse.ArgumentParser(add_help=False)

    common.add_argument("--root", default=".", help="Repo root (default: .)")
    common.add_argument(
        "--file-list",
        default=None,
        help="Text file with one relative .py path per line",
    )
    common.add_argument("--run-id", default=None, help="Resume/use a specific run id")
    common.add_argument(
        "--max-files", type=int, default=None, help="Process at most N files"
    )
    common.add_argument(
        "--ruff-fix",
        choices=["safe", "off"],
        default="safe",
        help="Whether to run `ruff check --fix` (safe fixes only) during repair",
    )
    common.add_argument(
        "--llm",
        choices=["on", "off"],
        default="on",
        help="Enable/disable LLM calls (off = no-op/stub for debugging)",
    )
    common.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        default="INFO",
        help="Console log level",
    )
    common.add_argument(
        "-v", action="count", default=0, help="Increase verbosity (-v, -vv)"
    )
    common.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive mode (also used for permission gating)",
    )

    # Top-level parser also includes common options so `noctune --root . run` works.
    p = argparse.ArgumentParser(prog="noctune", parents=[common], add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    sp = sub.add_parser(
        "init",
        parents=[common],
        help="First-time setup (config + permissions + optional .gitignore update)",
    )
    sp.add_argument("--base-url", default=None, help="LLM base_url (OpenAI-compatible)")
    sp.add_argument("--api-key", default=None, help="API key (optional)")
    sp.add_argument(
        "--allow-apply",
        choices=["true", "false"],
        default=None,
        help="Set allow_apply explicitly",
    )
    sp.add_argument(
        "--no-gitignore-prompt",
        dest="gitignore_prompt",
        action="store_false",
        help="Do not prompt to update .gitignore",
    )
    sp.set_defaults(func=cmd_init, gitignore_prompt=True)

    # plan/review/edit/repair/run
    for name, fn in [
        ("plan", cmd_plan),
        ("review", cmd_review),
        ("edit", cmd_edit),
        ("repair", cmd_repair),
        ("run", cmd_run),
    ]:
        sc = sub.add_parser(name, parents=[common], help=f"{name} pipeline stage")
        sc.add_argument("paths", nargs="*", help="Paths/files (default: scan repo)")
        sc.set_defaults(func=fn)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # normalize allow_apply flag parsing for init
    if getattr(args, "allow_apply", None) in ("true", "false"):
        args.allow_apply = args.allow_apply == "true"
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
