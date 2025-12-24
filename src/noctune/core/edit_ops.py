from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class EditOp:
    op: str
    qname: str
    new_code: str | None = None
    reason: str | None = None

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

def _best_effort_json_extract(text: str) -> str | None:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1)
    # fallback: find first { ... } balanced-ish by braces
    start = text.find("{")
    if start == -1:
        return None
    # naive scan
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return None

def parse_edit_ops(raw: str) -> tuple[bool, str, list[EditOp]]:
    js = _best_effort_json_extract(raw)
    if not js:
        return False, "No JSON object found in LLM output", []
    try:
        obj = json.loads(js)
    except Exception as e:
        return False, f"JSON parse error: {e}", []
    ops = []
    for it in obj.get("ops", []):
        ops.append(EditOp(
            op=str(it.get("op","")).strip(),
            qname=str(it.get("qname","")).strip(),
            new_code=it.get("new_code", None),
            reason=it.get("reason", None),
        ))
    return True, "", ops
