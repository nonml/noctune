from __future__ import annotations

import re

from .llm import LLMClient


def heuristic_trim_trailing_ws(text: str) -> str:
    return (
        "\n".join(
            [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
        ).rstrip()
        + "\n"
    )


def heuristic_fix_tabs(text: str) -> str:
    return text.replace("\t", "    ")


def heuristic_basic(text: str) -> str:
    t = text
    t = heuristic_fix_tabs(t)
    t = heuristic_trim_trailing_ws(t)
    # normalize smart quotes
    t = t.replace("“", '"').replace("”", '"').replace("’", "'")
    return t


def micro_llm_repair(
    llm: LLMClient,
    repair_prompt: str,
    symbol_code: str,
    diagnostics: str,
    timeout_note: str = "",
    *,
    verbose: bool = False,
    tag: str = "",
) -> tuple[bool, str]:
    user = (
        "Diagnostics:\n"
        f"{diagnostics}\n\n"
        "Symbol code:\n"
        f"{symbol_code}\n\n"
        "Return ONLY corrected symbol code."
    )
    ok, out = llm.chat(system=repair_prompt, user=user, verbose=verbose, tag=tag)
    if not ok:
        return False, out
    # strip fences if any
    out = out.strip()
    out = re.sub(r"^```[a-zA-Z0-9]*\s*", "", out)
    out = re.sub(r"```\s*$", "", out)
    return True, out.strip() + "\n"
