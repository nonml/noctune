from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: Optional[str] = None
    api_key: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    stream: bool = True
    verbose_stream: bool = True
    stream_print_reasoning: bool = True


@dataclass
class GitConfig:
    enabled: bool = False
    branch_prefix: str = "noctune"
    base_branch: Optional[str] = None
    auto_stash: bool = True

    # Commit strategy:
    # - each_approval: legacy micro-commits (optional)
    # - patchsets: commit grouped patchsets at end of run (recommended)
    commit_strategy: str = "patchsets"  # each_approval|patchsets

    # Legacy toggle (only used when commit_strategy == "each_approval")
    commit_each_approval: bool = True

    # Patchset grouping (only used when commit_strategy == "patchsets")
    patchset_grouping: str = "module"  # single|module|file|policy_pack
    patchset_module_depth: int = 2
    patchset_max_commits: int = 5

    commit_message: str = "noctune: {rel_path}::{qname}"
    patchset_commit_message: str = "noctune: patchset {group}"



@dataclass
class StudioConfig:
    enabled: bool = False
    # If empty, defaults to <repo_root>/.noctune_cache/noctune_studio.db
    db_path: Optional[str] = None


@dataclass
class ApprovalConfig:
    # none: keep legacy behavior (LLM approval only). prompt: ask on TTY. file: wait for <run>/state/approvals/*.decision.
    mode: str = "none"  # none|prompt|file|auto
    # auto: skip human gate (still uses LLM approve). Useful for safe packs.
    require_for_apply: bool = True


@dataclass
class PolicyConfig:
    # Built-in packs (optional). Examples: ["lint", "format", "docs"]
    packs: list[str] = field(default_factory=list)

    # Auto-approve small diffs (human gate bypass) when approvals.mode is prompt/file.
    auto_approve_max_diff_lines: int = 0

    # Only auto-approve for these file globs (POSIX style), e.g. ["**/*.py"]
    auto_approve_globs: list[str] = field(default_factory=list)



@dataclass
class PolicyPack:
    # Pack name is the key in [tool.noctune.policy_packs.<name>]
    allowed_globs: list[str] = field(default_factory=list)
    max_diff_lines: int = 0
    tools_allowed: list[str] = field(default_factory=list)
    auto_approve_max_diff_lines: int = 0
    auto_approve_globs: list[str] = field(default_factory=list)


@dataclass
class NoctuneConfig:
    allow_apply: bool = False
    llm: LLMConfig = field(default_factory=LLMConfig)
    ruff_required: bool = True
    rg_optional: bool = True
    git: GitConfig = field(default_factory=GitConfig)
    studio: StudioConfig = field(default_factory=StudioConfig)
    approvals: ApprovalConfig = field(default_factory=ApprovalConfig)

    policies: PolicyConfig = field(default_factory=PolicyConfig)

    # Named policy packs from config (and built-ins).
    policy_packs: Dict[str, PolicyPack] = field(default_factory=dict)


def _merge_dict(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k] = _merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def discover_config(root: Path) -> tuple[Path | None, Dict[str, Any]]:
    """
    Discovery order:
      1) noctune.toml at repo root
      2) pyproject.toml [tool.noctune]
    """
    noctune_toml = root / "noctune.toml"
    if noctune_toml.exists():
        data = tomllib.loads(noctune_toml.read_text(encoding="utf-8"))
        return noctune_toml, data

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        tool = data.get("tool", {})
        noctune = tool.get("noctune", {})
        return pyproject, {"tool": {"noctune": noctune}}

    return None, {}


def load_config(root: Path) -> tuple[NoctuneConfig, Path | None, Dict[str, Any]]:
    cfg_path, raw = discover_config(root)

    base: Dict[str, Any] = {"tool": {"noctune": {}}}
    merged = _merge_dict(base, raw)
    node = merged.get("tool", {}).get("noctune", {})

    # Env overrides (useful for CI/secrets)
    base_url = os.environ.get("NOCTUNE_BASE_URL")
    api_key = os.environ.get("NOCTUNE_API_KEY")
    headers_json = os.environ.get("NOCTUNE_HEADERS_JSON")

    llm_node = dict(node.get("llm", {}) or {})
    if base_url:
        llm_node["base_url"] = base_url
    if api_key:
        llm_node["api_key"] = api_key
    if headers_json:
        try:
            llm_node["headers"] = json.loads(headers_json)
        except Exception:
            pass

    allow_apply = bool(node.get("allow_apply", False))

    llm = LLMConfig(
        base_url=str(llm_node.get("base_url", LLMConfig.base_url)),
        model=llm_node.get("model"),
        api_key=llm_node.get("api_key"),
        headers=dict(llm_node.get("headers", {}) or {}),
        stream=bool(llm_node.get("stream", True)),
        verbose_stream=bool(llm_node.get("verbose_stream", True)),
        stream_print_reasoning=bool(llm_node.get("stream_print_reasoning", True)),
    )

    git_node = dict(node.get("git", {}) or {})
    studio_node = dict(node.get("studio", {}) or {})
    approvals_node = dict(node.get("approvals", {}) or {})
    policies_node = dict(node.get("policies", {}) or {})

    git_cfg = GitConfig(
        enabled=bool(git_node.get("enabled", False)),
        branch_prefix=str(git_node.get("branch_prefix", "noctune")),
        base_branch=git_node.get("base_branch"),
        auto_stash=bool(git_node.get("auto_stash", True)),
        commit_strategy=str(git_node.get("commit_strategy", "patchsets")),
        commit_each_approval=bool(git_node.get("commit_each_approval", True)),
        patchset_grouping=str(git_node.get("patchset_grouping", "module")),
        patchset_module_depth=int(git_node.get("patchset_module_depth", 2) or 2),
        patchset_max_commits=int(git_node.get("patchset_max_commits", 5) or 5),
        commit_message=str(git_node.get("commit_message", "noctune: {rel_path}::{qname}")),
        patchset_commit_message=str(git_node.get("patchset_commit_message", "noctune: patchset {group}")),
    )

    studio_cfg = StudioConfig(
        enabled=bool(studio_node.get("enabled", False)),
        db_path=studio_node.get("db_path"),
    )

    approvals_cfg = ApprovalConfig(
        mode=str(approvals_node.get("mode", "none")),
        require_for_apply=bool(approvals_node.get("require_for_apply", True)),
    )

    policies_cfg = PolicyConfig(
        packs=list(policies_node.get("packs", []) or []),
        auto_approve_max_diff_lines=int(
            policies_node.get("auto_approve_max_diff_lines", 0) or 0
        ),
        auto_approve_globs=list(policies_node.get("auto_approve_globs", []) or []),
    )

    # Named policy packs: [tool.noctune.policy_packs.<name>]
    packs_node = dict(node.get("policy_packs", {}) or {})
    policy_packs: Dict[str, PolicyPack] = {}
    for name, pn in packs_node.items():
        if not isinstance(pn, dict):
            continue
        policy_packs[str(name)] = PolicyPack(
            allowed_globs=list(pn.get("allowed_globs", []) or []),
            max_diff_lines=int(pn.get("max_diff_lines", 0) or 0),
            tools_allowed=list(pn.get("tools_allowed", []) or []),
            auto_approve_max_diff_lines=int(pn.get("auto_approve_max_diff_lines", 0) or 0),
            auto_approve_globs=list(pn.get("auto_approve_globs", []) or []),
        )



    cfg = NoctuneConfig(
        allow_apply=allow_apply,
        llm=llm,
        ruff_required=bool(node.get("ruff_required", True)),
        rg_optional=bool(node.get("rg_optional", True)),
        git=git_cfg,
        studio=studio_cfg,
        approvals=approvals_cfg,
        policies=policies_cfg,
        policy_packs=policy_packs,
    )
    return cfg, cfg_path, merged


def write_noctune_toml(
    path: Path,
    cfg: NoctuneConfig,
    *,
    allow_apply: bool | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Path:
    """Write a repo-local noctune.toml.

    This file is safe to commit if it contains no secrets. Prefer env vars for secrets:
      NOCTUNE_BASE_URL, NOCTUNE_API_KEY, NOCTUNE_HEADERS_JSON
    """
    allow_apply_val = cfg.allow_apply if allow_apply is None else bool(allow_apply)

    llm = cfg.llm
    base_url_val = base_url if base_url is not None else llm.base_url
    api_key_val = api_key if api_key is not None else (llm.api_key or "")
    model_val = model if model is not None else (llm.model or "")

    model_line = f'model = "{model_val}"' if model_val else "# model = "
    api_key_line = f'api_key = "{api_key_val}"' if api_key_val else 'api_key = ""'

    text = f"""# Noctune configuration (repo-local)
# This file is safe to commit if it contains no secrets.

[tool.noctune]
allow_apply = {str(allow_apply_val).lower()}
ruff_required = {str(cfg.ruff_required).lower()}
rg_optional = {str(cfg.rg_optional).lower()}

[tool.noctune.llm]
base_url = "{base_url_val}"
{model_line}
{api_key_line}
stream = {str(cfg.llm.stream).lower()}
verbose_stream = {str(cfg.llm.verbose_stream).lower()}
stream_print_reasoning = {str(cfg.llm.stream_print_reasoning).lower()}

# Optional: Git-native output (branch + commits). Requires allow_apply = true.
[tool.noctune.git]
enabled = false
branch_prefix = "noctune"
# base_branch = ""          # if empty, uses current branch
auto_stash = true

# Recommended: grouped patchsets (1-5 commits per run)
commit_strategy = "patchsets"          # patchsets|each_approval
patchset_grouping = "module"          # single|module|file|policy_pack
patchset_module_depth = 2
patchset_max_commits = 5
patchset_commit_message = "noctune: patchset {group}"

# Legacy: micro-commits (one commit per approval)
commit_each_approval = true
commit_message = "noctune: {rel_path}::{qname}"

# Optional: Studio services (daemon/MCP). Install: pip install -e ".[studio]"
[tool.noctune.studio]
enabled = false
# db_path = ""

# Optional: Human approval gate (Cline-like). Default is 'none' (LLM-only).

# Optional: Policy packs + auto-approve rules.
[tool.noctune.policies]
# packs = ["lint"]
# auto_approve_max_diff_lines = 0
# auto_approve_globs = ["**/*.py"]
# Named policy packs (recommended)
# [tool.noctune.policy_packs.lint_fix]
# allowed_globs = ["**/*.py"]
# max_diff_lines = 200
# tools_allowed = ["ruff"]
# auto_approve_max_diff_lines = 40
# auto_approve_globs = ["**/*.py"]

[tool.noctune.approvals]
# mode = "none"   # none|prompt|file|auto
# require_for_apply = true
"""
    path.write_text(text, encoding="utf-8")
    return path
