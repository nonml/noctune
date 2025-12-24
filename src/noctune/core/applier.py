from __future__ import annotations

import ast
from dataclasses import dataclass

from .indexer import extract_symbols
from .state import detect_newline_style


def _split_lines_keepends(text: str, newline: str) -> list[str]:
    # keepends split but normalized to the file newline style
    # First, normalize incoming to \n, then re-emit with newline
    tmp = text.replace("\r\n", "\n")
    lines = tmp.split("\n")
    if len(lines) == 1:
        return [lines[0]]
    out = []
    for i, line in enumerate(lines):
        if i < len(lines) - 1:
            out.append(line + newline)
        else:
            out.append(line)
    return out


def _get_newline_style(file_bytes: bytes) -> str:
    return detect_newline_style(file_bytes)


def _indent_of_line(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_block(code: str, indent: str, newline: str) -> str:
    # keep relative indentation; set minimal indent to 0 then add target indent
    code = code.replace("\r\n", "\n").rstrip() + "\n"
    lines = code.split("\n")
    # compute min indent across non-empty lines (spaces only)
    non_empty = [line for line in lines if line.strip()]
    min_indent = None
    for line in non_empty:
        leading = len(line) - len(line.lstrip(" "))
        if min_indent is None or leading < min_indent:
            min_indent = leading
    if min_indent is None:
        min_indent = 0
    out_lines = []
    for line in lines:
        if not line.strip():
            out_lines.append("")
            continue
        if line.startswith(" " * min_indent):
            l2 = line[min_indent:]
        else:
            l2 = line.lstrip(" ")
        out_lines.append(indent + l2)
    return newline.join(out_lines).rstrip(newline) + newline


@dataclass
class ApplyResult:
    ok: bool
    msg: str
    updated_source: str
    changed_qnames: list[str]


def apply_replace_symbol(
    rel_path: str,
    original_bytes: bytes,
    op_qname: str,
    new_code: str,
) -> ApplyResult:
    newline = _get_newline_style(original_bytes)
    original = original_bytes.decode("utf-8", errors="replace")
    try:
        ast.parse(original)
    except SyntaxError as e:
        return ApplyResult(False, f"Original file not parseable: {e}", original, [])

    syms = extract_symbols(original)
    target = None
    for s in syms:
        if s.qname == op_qname:
            target = s
            break
    if not target:
        return ApplyResult(False, f"Symbol not found: {op_qname}", original, [])

    # Determine indent from original symbol first line
    lines = original.replace("\r\n", "\n").split("\n")
    # lineno is 1-based
    first_line = lines[target.lineno - 1] if 0 <= target.lineno - 1 < len(lines) else ""
    indent = _indent_of_line(first_line)
    new_block = _reindent_block(new_code, indent, "\n")  # internal uses \n
    # Convert to file newline
    new_block = new_block.replace("\n", newline)

    # Replace lines in a keepends-aware way
    keep = original.splitlines(keepends=True)
    start_i = target.lineno - 1
    end_i = target.end_lineno
    keep2 = [*keep[:start_i], new_block, *keep[end_i:]]
    updated = "".join(keep2)
    return ApplyResult(True, "ok", updated, [op_qname])
