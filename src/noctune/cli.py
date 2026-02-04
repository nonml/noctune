from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core.config import NoctuneConfig, load_config, write_noctune_toml
from .core.prompts import ensure_prompt_overrides
from .core.runner import run_stage
from .core.scanner import RepoScanner
from .core.state import find_latest_run_id, ensure_run_paths
from .core.tools import which


def _prompt_yes_no(msg: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    resp = input(msg + suffix).strip().lower()
    if not resp:
        return not default_no
    return resp in ("y", "yes")


def _ensure_tooling(cfg: NoctuneConfig) -> None:
    if cfg.ruff_required and not which("ruff"):
        raise SystemExit("noctune: ruff is required but not found on PATH")
    if not cfg.rg_optional and not which("rg"):
        raise SystemExit("noctune: ripgrep (rg) is required but not found on PATH")


def _collect_rel_paths(
    root: Path, paths: list[str], file_list: str | None
) -> list[str]:
    scanner = RepoScanner.create(root)
    if file_list:
        items = scanner.from_file_list(Path(file_list))
        return [p.relative_to(root).as_posix() for p in items]

    if paths:
        out: list[str] = []
        for s in paths:
            p = (root / s).resolve()
            if p.is_dir():
                for f in scanner.iter_python_files(p):
                    out.append(f.relative_to(root).as_posix())
            else:
                if p.exists() and p.suffix == ".py":
                    out.append(p.relative_to(root).as_posix())
        return out

    # default: entire repo
    return [p.relative_to(root).as_posix() for p in scanner.iter_python_files()]


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    cfg, cfg_path, _ = load_config(root)

    # First-time config file
    out_cfg = root / "noctune.toml"
    if not out_cfg.exists():
        allow_apply = False
        if os.isatty(0) and not args.yes:
            allow_apply = _prompt_yes_no(
                "Allow Noctune to patch files in this repo?", default_no=True
            )
        write_noctune_toml(
            out_cfg,
            cfg,
            allow_apply=allow_apply,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
        )
        print(f"wrote {out_cfg}")

    # Ensure prompt overrides exist for easy user editing
    od = ensure_prompt_overrides(root, overwrite=bool(args.overwrite_prompts))
    print(f"prompt overrides: {od}")

    # Optional .gitignore update (best-effort)
    gi = root / ".gitignore"
    if gi.exists():
        text = gi.read_text(encoding="utf-8", errors="ignore")
    else:
        text = ""
    if ".noctune_cache/" not in text and ".noctune_cache" not in text:
        if os.isatty(0) and not args.yes:
            if _prompt_yes_no("Add .noctune_cache/ to .gitignore?", default_no=False):
                gi.write_text(
                    text
                    + ("\n" if text and not text.endswith("\n") else "")
                    + ".noctune_cache/\n",
                    encoding="utf-8",
                )
                print("updated .gitignore")

    return 0


def _cmd_stage(stage: str, args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg, _, _ = load_config(root)
    _ensure_tooling(cfg)
    if getattr(args, 'approval_mode', None):
        cfg.approvals.mode = str(args.approval_mode)
    if getattr(args, "pack", None):
        cfg.policies.packs = [str(args.pack)]

    rel_paths = _collect_rel_paths(
        root, getattr(args, "paths", []) or [], args.file_list
    )
    return run_stage(
        stage=stage,
        root=root,
        rel_paths=rel_paths,
        cfg=cfg,
        run_id=args.run_id,
        max_files=args.max_files,
        ruff_fix_mode=args.ruff_fix,
        llm_enabled=(args.llm == "on"),
        log_level=args.log_level,
        verbosity=args.v,
    )


def cmd_review(args: argparse.Namespace) -> int:
    return _cmd_stage("review", args)


def cmd_edit(args: argparse.Namespace) -> int:
    return _cmd_stage("edit", args)


def cmd_repair(args: argparse.Namespace) -> int:
    return _cmd_stage("repair", args)


def cmd_run(args: argparse.Namespace) -> int:
    return _cmd_stage("run", args)



def cmd_studio(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()

    if args.action == "stop":
        run_id = args.run_id or find_latest_run_id(str(root))
        if not run_id:
            raise SystemExit("noctune studio stop: no run found")
        rp = ensure_run_paths(str(root), run_id)
        Path(rp.state_dir).mkdir(parents=True, exist_ok=True)
        (Path(rp.state_dir) / "stop.flag").write_text("stop\n", encoding="utf-8")
        print(f"noctune studio: stop.flag written for run {run_id}")
        return 0

    if args.action == "mcp":
        from .studio.mcp_server import main as mcp_main
        import asyncio
        asyncio.run(mcp_main())
        return 0

    # serve
    try:
        import uvicorn
    except Exception as e:
        raise SystemExit('noctune studio serve requires extras: pip install -e ".[studio]"') from e
    from .studio.daemon import create_app
    app = create_app()
    uvicorn.run(app, host=args.host, port=int(args.port))
    return 0

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="Repo root (default: .)")
    common.add_argument(
        "--file-list",
        default=None,
        help="Text file with one relative .py path per line",
    )
    common.add_argument("--run-id", default=None, help="Reuse an existing run-id (resume)")
    common.add_argument("--max-files", type=int, default=None, help="Stop after N files")
    common.add_argument(
        "--ruff-fix",
        choices=["safe", "off"],
        default="safe",
        help="Apply ruff --fix on temp/work file",
    )
    common.add_argument("--llm", choices=["on", "off"], default="on", help="Enable/disable LLM calls")
    common.add_argument(
        "--approval-mode",
        choices=["none", "prompt", "file", "auto"],
        default=None,
        help="Override [tool.noctune.approvals].mode for this run",
    )
    common.add_argument(
        "--pack",
        default=None,
        help="Policy pack name (overrides [tool.noctune.policies].packs[0])",
    )
    common.add_argument("--log-level", choices=["DEBUG", "INFO", "WARN", "ERROR"], default="INFO")
    common.add_argument("-v", action="count", default=0, help="Increase verbosity (-v, -vv)")
    common.add_argument("--yes", action="store_true", help="Non-interactive mode (init prompts)")
    common.add_argument(
        "--continue",
        dest="continue_last",
        action="store_true",
        help="Resume the most recent run id under --root/.noctune_cache/runs/ (ignored if --run-id is set)",
    )

    p = argparse.ArgumentParser(prog="noctune")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    ip = sub.add_parser("init", help="Create noctune.toml + prompt overrides", parents=[common])
    ip.add_argument("--base-url", default=None)
    ip.add_argument("--api-key", default=None)
    ip.add_argument("--model", default=None)
    ip.add_argument("--overwrite-prompts", action="store_true")
    ip.set_defaults(func=cmd_init)

    # stages
    for name, fn in [("review", cmd_review), ("edit", cmd_edit), ("repair", cmd_repair), ("run", cmd_run)]:
        sp2 = sub.add_parser(name, help=f"Run stage: {name}", parents=[common])
        sp2.add_argument("paths", nargs="*", help="Files/dirs under --root (default: whole repo)")
        sp2.set_defaults(func=fn)

    # studio
    sp = sub.add_parser("studio", help="Noctune Studio: run daemon/MCP, or stop an active run")
    sp.add_argument("--root", default=".", help="Repo root (default: .)")
    sp.add_argument(
        "action",
        choices=["serve", "mcp", "stop"],
        help="serve: HTTP daemon, mcp: MCP stdio server, stop: create stop.flag for a run",
    )
    sp.add_argument("--host", default="127.0.0.1", help="Daemon host (serve)")
    sp.add_argument("--port", type=int, default=7331, help="Daemon port (serve)")
    sp.add_argument("--run-id", default=None, help="Run id to stop (stop). If omitted, stops latest run.")
    sp.set_defaults(func=cmd_studio)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(getattr(args, "root", ".")).resolve()

    if getattr(args, "continue_last", False) and not getattr(args, "run_id", None):
        rid = find_latest_run_id(root)
        if not rid:
            raise SystemExit(
                "noctune: --continue was set but no prior runs were found under "
                f"{root / '.noctune_cache' / 'runs'}"
            )
        args.run_id = rid
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
