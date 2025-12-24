from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:8080"
    model: Optional[str] = None
    api_key: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    stream: bool = True
    verbose_stream: bool = True
    stream_print_reasoning: bool = True


@dataclass
class NoctuneConfig:
    allow_apply: bool = False
    llm: LLMConfig = field(default_factory=LLMConfig)
    ruff_required: bool = True
    rg_optional: bool = True


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

    cfg = NoctuneConfig(
        allow_apply=allow_apply,
        llm=llm,
        ruff_required=bool(node.get("ruff_required", True)),
        rg_optional=bool(node.get("rg_optional", True)),
    )
    return cfg, cfg_path, merged


def write_noctune_toml(root: Path, cfg: NoctuneConfig) -> Path:
    path = root / "noctune.toml"
    api_key_line = (
        f'api_key = "{cfg.llm.api_key}"' if cfg.llm.api_key else 'api_key = ""'
    )
    model_line = f'model = "{cfg.llm.model}"' if cfg.llm.model else 'model = ""'

    # Keep this TOML very simple and stable.
    text = f"""# Noctune configuration (repo-local)
# This file is safe to commit if it contains no secrets.

[tool.noctune]
allow_apply = {str(cfg.allow_apply).lower()}
ruff_required = {str(cfg.ruff_required).lower()}
rg_optional = {str(cfg.rg_optional).lower()}

[tool.noctune.llm]
base_url = "{cfg.llm.base_url}"
{model_line}
{api_key_line}
stream = {str(cfg.llm.stream).lower()}
verbose_stream = {str(cfg.llm.verbose_stream).lower()}
stream_print_reasoning = {str(cfg.llm.stream_print_reasoning).lower()}
"""
    path.write_text(text, encoding="utf-8")
    return path
