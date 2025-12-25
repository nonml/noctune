from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass


@dataclass
class ImpactPack:
    imports: list[str]
    callsites: dict[str, list[str]]


def extract_imports(source: str) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append(f"import {a.name}" + (f" as {a.asname}" if a.asname else ""))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = ", ".join(
                [n.name + (f" as {n.asname}" if n.asname else "") for n in node.names]
            )
            out.append(f"from {mod} import {names}")
    return out


def ripgrep_callsites(
    repo_root: str, names: list[str], max_hits_per_name: int = 30
) -> dict[str, list[str]]:
    # Minimum requirement: imports + grep call sites
    out: dict[str, list[str]] = {n: [] for n in names}
    rg = "rg"
    for name in names:
        # simple patterns
        pats = [rf"\b{name}\s*\(", rf"\b{name}\b"]
        hits: list[str] = []
        for pat in pats:
            try:
                cp = subprocess.run(
                    [rg, "-n", "--no-heading", "-S", pat, repo_root],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                return {n: ["ripgrep (rg) not found on PATH"] for n in names}
            for line in cp.stdout.splitlines():
                if ".noctune_cache/" in line.replace("\\", "/"):
                    continue
                hits.append(line[:500])
                if len(hits) >= max_hits_per_name:
                    break
            if len(hits) >= max_hits_per_name:
                break
        out[name] = hits
    return out


def build_impact(
    repo_root: str, focus_source: str, symbol_names: list[str]
) -> ImpactPack:
    return ImpactPack(
        imports=extract_imports(focus_source),
        callsites=ripgrep_callsites(repo_root, symbol_names),
    )
