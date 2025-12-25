from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .applier import apply_replace_symbol
from .config import NoctuneConfig
from .gates import check_parse, check_ruff, ruff_fix_safe
from .impact import build_impact
from .indexer import Symbol, extract_symbols, index_file
from .llm import LLMClient
from .logger import EventLogger
from .prompts import load_prompt
from .repair import heuristic_basic, micro_llm_repair
from .state import (
    detect_newline_style,
    ensure_run_paths,
    load_json,
    read_bytes,
    save_json,
    sha256_bytes,
    sha256_text,
    write_text,
)


def _task_id(rel_path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", rel_path)[:180]


def _best_effort_json(text: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Extract and parse the first JSON object found in text."""
    if not text:
        return False, "empty", None
    # strip code fences
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
    t = re.sub(r"```\s*$", "", t)
    # find first { ... } object
    start = t.find("{")
    end = t.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return False, "no json object", None
    js = t[start : end + 1]
    try:
        return True, "", json.loads(js)
    except Exception as e:
        return False, f"json parse error: {e}", None


def _extract_symbol_source(text: str, sym: Symbol) -> str:
    lines = text.splitlines(keepends=True)
    start = max(sym.lineno - 1, 0)
    end = max(sym.end_lineno, start)
    return "".join(lines[start:end])


def _label_from_review(text: str) -> Optional[str]:
    m = re.search(r"^\s*Label:\s*`?([NPW])`?\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    m2 = re.search(r"\bLabel\s*:\s*([NPW])\b", text)
    return m2.group(1) if m2 else None


def _llm_chat_logged(
    *,
    llm: LLMClient,
    system: str,
    user: str,
    logger: EventLogger,
    stage: str,
    tag: str,
    verbose_llm: bool,
    stream: bool = True,
) -> tuple[bool, str]:
    t0 = time.time()
    ok, out = llm.chat(
        system=system,
        user=user,
        stream=stream,
        verbose=verbose_llm,
        tag=tag,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    try:
        out_hash = sha256_text(out) if out else ""
    except Exception:
        out_hash = ""
    logger.info(
        event="llm_call",
        stage=stage,
        tag=tag,
        ok=ok,
        elapsed_ms=elapsed_ms,
        system_chars=len(system or ""),
        user_chars=len(user or ""),
        output_chars=len(out or ""),
        output_sha256=out_hash,
    )
    return ok, out


def _impact_pack(root: Path, src_text: str, *, max_names: int = 10):
    syms = extract_symbols(src_text)
    names: list[str] = []
    for s in syms:
        # grep the leaf name; keep small
        leaf = s.qname.split(".")[-1]
        if leaf and leaf not in names:
            names.append(leaf)
        if len(names) >= max_names:
            break
    return build_impact(str(root), src_text, names)


def _meaningless_change(before: str, after: str) -> bool:
    if before == after:
        return True

    # ignore whitespace-only changes
    def norm(s: str) -> str:
        return "\n".join(
            [ln.strip() for ln in s.replace("\r\n", "\n").split("\n") if ln.strip()]
        )

    return norm(before) == norm(after)


def _write_full_file_proposal(
    *,
    root: Path,
    rel_path: str,
    work_abs: str,
    task_art: str,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    reason: str,
) -> None:
    """Last-resort: ask LLM for a full-file replacement and write proposed_full_file.py (checkpointed)."""
    proposal_path = os.path.join(task_art, "proposed_full_file.py")
    if os.path.exists(proposal_path):
        return  # checkpoint: keep the first proposal

    # If no LLM, still write something human-friendly.
    if llm is None:
        write_text(
            proposal_path,
            "# proposed_full_file.py was requested but LLM is disabled.\n"
            f"# reason: {reason}\n",
        )
        return

    cur = read_bytes(work_abs).decode("utf-8", errors="replace")
    system = (
        "You are a senior Python engineer.\n"
        "Task: return a FULL corrected replacement for the focus file.\n"
        "Rules:\n"
        "- Output ONLY the full Python file content (no prose).\n"
        "- Avoid sweeping refactors; minimize formatting churn.\n"
        "- Fix syntax errors and obvious Ruff issues if possible.\n"
    )
    user = f"Path: {rel_path}\nReason: {reason}\n\nCurrent content:\n{cur}"

    ok, out = llm.chat(
        system=system,
        user=user,
        stream=True,
        verbose=verbose_llm,
        tag=f"fullfile:{rel_path}",
    )
    if not ok:
        write_text(
            proposal_path,
            f"# LLM full-file proposal failed.\n# reason: {reason}\n",
        )
        write_text(os.path.join(task_art, "full_file_proposal_error.txt"), out + "\n")
        return

    # Strip a single fence wrapper if the model added one.
    proposed = out.strip()
    proposed = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", proposed)
    proposed = re.sub(r"\s*```\s*$", "", proposed)

    write_text(proposal_path, proposed.rstrip() + "\n")


@dataclass
class StageResult:
    ok: bool
    msg: str


def run_stage(
    *,
    stage: str,  # plan|review|edit|repair|run
    root: Path,
    rel_paths: List[str],
    cfg: NoctuneConfig,
    run_id: Optional[str],
    max_files: Optional[int],
    ruff_fix_mode: str,  # safe|off
    llm_enabled: bool,
    log_level: str,
    verbosity: int,
) -> int:
    rp = ensure_run_paths(str(root), run_id)
    logger = EventLogger(
        events_path=os.path.join(rp.logs_dir, "events.jsonl"), level=log_level
    )

    # SQLite symbol index
    db_path = os.path.join(rp.state_dir, "symbols.sqlite")

    # LLM
    llm: Optional[LLMClient] = None
    if llm_enabled:
        llm = LLMClient(
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key or "",
            model=cfg.llm.model or "",
            timeout_s=180,
            extra_headers=cfg.llm.headers or {},
            request_overrides=None,
            mode="openai_chat",
            stream_default=bool(cfg.llm.stream),
            stream_print_reasoning=bool(cfg.llm.stream_print_reasoning),
            stream_print_headers=True,
        )

    verbose_llm = bool(cfg.llm.verbose_stream) or (verbosity > 0)

    interrupt_count = 0
    processed = 0

    for rel_path in rel_paths:
        if max_files is not None and processed >= max_files:
            break

        try:
            processed += 1
            abs_path = (root / rel_path).resolve()
            if not abs_path.exists():
                logger.warn(event="file_missing", rel_path=rel_path)
                continue

            task_id = _task_id(rel_path)
            task_art = os.path.join(rp.artifacts_dir, task_id)
            os.makedirs(task_art, exist_ok=True)

            raw = read_bytes(str(abs_path))
            newline = detect_newline_style(raw)
            file_hash = sha256_bytes(raw)

            task_state_path = os.path.join(rp.state_dir, "tasks", f"{task_id}.json")
            task_state = load_json(task_state_path, default={})
            prev_hash = task_state.get("file_hash")

            # If review says W and hash unchanged -> skip
            review_path = os.path.join(task_art, "review.md")
            if os.path.exists(review_path) and prev_hash == file_hash:
                try:
                    lbl = _label_from_review(
                        Path(review_path).read_text(encoding="utf-8", errors="replace")
                    ) or task_state.get("label")
                except Exception:
                    lbl = task_state.get("label")
                if lbl == "W":
                    logger.info(event="skip_complete", rel_path=rel_path, label="W")
                    continue

            # Always keep backup snapshot
            backup_path = os.path.join(rp.backups_dir, task_id + ".before.py")
            if not os.path.exists(backup_path):
                with open(backup_path, "wb") as f:
                    f.write(raw)

            # Work file path (temp)
            work_abs = os.path.join(rp.work_dir, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(work_abs), exist_ok=True)
            with open(work_abs, "wb") as f:
                f.write(raw)

            # Index symbols for this file
            try:
                index_file(db_path, rel_path, raw.decode("utf-8", errors="replace"))
            except Exception:
                logger.warn(
                    event="index_failed",
                    rel_path=rel_path,
                    error=traceback.format_exc()[:2000],
                )

            # Dispatch by stage
            if stage == "plan":
                _do_plan(root, rel_path, raw, task_art, llm, verbose_llm, logger)
            elif stage == "review":
                _do_review(root, rel_path, raw, task_art, llm, verbose_llm, logger)
            elif stage == "edit":
                _do_edit(
                    root=root,
                    rel_path=rel_path,
                    real_abs=str(abs_path),
                    raw=raw,
                    newline=newline,
                    task_art=task_art,
                    work_abs=work_abs,
                    cfg=cfg,
                    llm=llm,
                    verbose_llm=verbose_llm,
                    ruff_fix_mode=ruff_fix_mode,
                    logger=logger,
                )
            elif stage == "repair":
                _do_repair_only(
                    root=root,
                    rel_path=rel_path,
                    real_abs=str(abs_path),
                    raw=raw,
                    newline=newline,
                    task_art=task_art,
                    work_abs=work_abs,
                    cfg=cfg,
                    llm=llm,
                    verbose_llm=verbose_llm,
                    ruff_fix_mode=ruff_fix_mode,
                    logger=logger,
                )
            elif stage == "run":
                _do_run_full(
                    root=root,
                    rel_path=rel_path,
                    real_abs=str(abs_path),
                    raw=raw,
                    newline=newline,
                    task_art=task_art,
                    work_abs=work_abs,
                    cfg=cfg,
                    llm=llm,
                    verbose_llm=verbose_llm,
                    ruff_fix_mode=ruff_fix_mode,
                    logger=logger,
                )
            else:
                logger.error(event="bad_stage", stage=stage)
                return 2

            # Save state
            # Refresh file after possible apply
            try:
                new_raw = read_bytes(str(abs_path))
            except Exception:
                new_raw = raw
            task_state = {
                "rel_path": rel_path,
                "file_hash": sha256_bytes(new_raw),
                "label": None,
            }
            if os.path.exists(review_path):
                try:
                    lbl = _label_from_review(
                        Path(review_path).read_text(encoding="utf-8", errors="replace")
                    )
                    task_state["label"] = lbl
                except Exception:
                    pass
            save_json(task_state_path, task_state)

        except KeyboardInterrupt:
            interrupt_count += 1
            logger.warn(
                event="keyboard_interrupt", count=interrupt_count, rel_path=rel_path
            )
            if interrupt_count == 1:
                # Human note only on first interrupt (best-effort).
                if os.isatty(0):
                    try:
                        note = input(
                            "\nnoctune: interrupt received. Enter a short note (or empty to continue): "
                        ).strip()
                    except Exception:
                        note = ""
                else:
                    note = ""
                if note:
                    write_text(
                        os.path.join(
                            rp.artifacts_dir, _task_id(rel_path), "human_note.txt"
                        ),
                        note + "\n",
                    )
                continue
            if interrupt_count == 2:
                # Skip current file
                write_text(
                    os.path.join(
                        rp.artifacts_dir, _task_id(rel_path), "skipped_by_interrupt.txt"
                    ),
                    "skipped\n",
                )
                continue
            # Third: terminate
            return 130

    return 0


def _do_plan(
    root: Path,
    rel_path: str,
    raw: bytes,
    task_art: str,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    logger: EventLogger,
) -> None:
    if llm is None:
        write_text(os.path.join(task_art, "plan.md"), "LLM disabled; skipping plan.\n")
        return
    plan_path = os.path.join(task_art, "plan.md")
    if os.path.exists(plan_path):
        return

    src_text = raw.decode("utf-8", errors="replace")
    impact = _impact_pack(root, src_text, max_names=10)

    # Plan (free-form)
    system = load_prompt(root, "plan.md")
    user = (
        f"Path: {rel_path}\n\nImports:\n"
        + "\n".join(impact.imports[:60])
        + "\n\nSource:\n"
        + src_text
    )
    ok, out = _llm_chat_logged(
        llm=llm,
        system=system,
        user=user,
        logger=logger,
        stage="plan",
        tag=f"plan:{rel_path}",
        verbose_llm=verbose_llm,
        stream=True,
    )
    write_text(plan_path, out + "\n")
    logger.info(event="plan_written", rel_path=rel_path, ok=ok)


def _do_draft(
    root: Path,
    rel_path: str,
    raw: bytes,
    task_art: str,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    logger: EventLogger,
) -> None:
    """Draft must be guided by the latest review; keep it as a distinct stage."""
    draft_path = os.path.join(task_art, "draft.json")

    if os.path.exists(draft_path):
        return

    if llm is None:
        obj = {"file": rel_path, "targets": []}
        payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        write_text(draft_path, payload)
        write_text(
            os.path.join(task_art, "draft.raw.txt"),
            "LLM disabled; skipping draft.\n",
        )
        return

    src_text = raw.decode("utf-8", errors="replace")
    impact = _impact_pack(root, src_text, max_names=10)

    review_path = os.path.join(task_art, "review.md")
    review_text = ""
    if os.path.exists(review_path):
        try:
            review_text = Path(review_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            review_text = ""

    system = load_prompt(root, "draft.md")
    callsite_lines: list[str] = []
    for k, hits in (impact.callsites or {}).items():
        callsite_lines.append(f"## {k}")
        callsite_lines.extend(hits[:30])

    user = (
        f"Path: {rel_path}\n\n"
        "You must choose targets based on the REVIEW objectives.\n\n"
        "REVIEW (may be empty):\n"
        + (review_text[:12000] if review_text else "(missing)\n")
        + "\n\n"
        "Evidence: imports and grep callsites (may be incomplete).\n\n"
        "Imports:\n" + "\n".join(impact.imports[:80]) + "\n\n"
        "Callsites:\n" + "\n".join(callsite_lines[:600]) + "\n\n"
        "Source:\n" + src_text
    )

    ok, out = _llm_chat_logged(
        llm=llm,
        system=system,
        user=user,
        logger=logger,
        stage="draft",
        tag=f"draft:{rel_path}",
        verbose_llm=verbose_llm,
        stream=True,
    )

    write_text(os.path.join(task_art, "draft.raw.txt"), out + "\n")
    ok2, err, obj = _best_effort_json(out)
    if not ok2 or not obj:
        obj = {"file": rel_path, "targets": []}
        write_text(os.path.join(task_art, "draft_parse_error.txt"), err + "\n")
        logger.warn(event="draft_parse_failed", rel_path=rel_path, error=err[:4000])

    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    write_text(draft_path, payload)
    logger.info(event="draft_written", rel_path=rel_path, ok=ok)


def _do_review(
    root: Path,
    rel_path: str,
    raw: bytes,
    task_art: str,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    logger: EventLogger,
) -> None:
    review_path = os.path.join(task_art, "review.md")
    if os.path.exists(review_path):
        return
    if llm is None:
        write_text(
            review_path, "Score: 0/100\nLabel: N\n\nLLM disabled; skipping review.\n"
        )
        return
    src_text = raw.decode("utf-8", errors="replace")
    impact = _impact_pack(root, src_text, max_names=10)
    system = load_prompt(root, "review.md")
    callsite_lines = []
    for k, hits in (impact.callsites or {}).items():
        callsite_lines.append(f"## {k}")
        callsite_lines.extend(hits[:30])
    user = (
        f"Path: {rel_path}\n\n"
        "Evidence (imports + grep callsites):\n\n"
        "Imports:\n" + "\n".join(impact.imports[:80]) + "\n\n"
        "Callsites:\n" + "\n".join(callsite_lines[:600]) + "\n\n"
        "Source:\n" + src_text
    )
    ok, out = _llm_chat_logged(
        llm=llm,
        system=system,
        user=user,
        logger=logger,
        stage="review",
        tag=f"review:{rel_path}",
        verbose_llm=verbose_llm,
        stream=True,
    )
    write_text(review_path, out + "\n")
    logger.info(
        event="review_written", rel_path=rel_path, ok=ok, label=_label_from_review(out)
    )


def _do_edit(
    *,
    root: Path,
    rel_path: str,
    real_abs: str,
    raw: bytes,
    newline: str,
    task_art: str,
    work_abs: str,
    cfg: NoctuneConfig,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    ruff_fix_mode: str,
    logger: EventLogger,
) -> None:
    if llm is None:
        write_text(
            os.path.join(task_art, "edit_skipped.txt"), "LLM disabled; skipping edit.\n"
        )
        return

    # Edit is allowed as a standalone task. If prerequisites are missing, create them.
    plan_path = os.path.join(task_art, "plan.md")
    if not os.path.exists(plan_path):
        _do_plan(root, rel_path, raw, task_art, llm, verbose_llm, logger)

    review_path = os.path.join(task_art, "review.md")
    if not os.path.exists(review_path):
        _do_review(root, rel_path, raw, task_art, llm, verbose_llm, logger)

    draft_path = os.path.join(task_art, "draft.json")

    try:
        if os.path.exists(review_path):
            if os.path.exists(draft_path) and os.path.getmtime(
                review_path
            ) > os.path.getmtime(draft_path):
                os.remove(draft_path)
    except Exception:
        pass

    if not os.path.exists(draft_path):
        _do_draft(root, rel_path, raw, task_art, llm, verbose_llm, logger)

    selection = load_json(draft_path, default={})
    targets = selection.get("targets", []) or []
    if not isinstance(targets, list):
        targets = []
    if not targets:
        write_text(
            os.path.join(task_art, "edit_no_targets.txt"), "No targets selected.\n"
        )
        return

    # Real and temp start aligned
    real_bytes = read_bytes(real_abs)
    temp_bytes = read_bytes(work_abs)

    # Ensure we edit on temp first
    if temp_bytes != real_bytes:
        temp_bytes = real_bytes
        with open(work_abs, "wb") as f:
            f.write(temp_bytes)

    src_text = real_bytes.decode("utf-8", errors="replace")
    syms = extract_symbols(src_text)
    sym_map = {s.qname: s for s in syms}

    any_approved = False

    for t in targets[:3]:
        qname = str(t.get("qname", "")).strip()
        if not qname or qname not in sym_map:
            continue
        edit_prompt = str(t.get("edit_prompt", "") or "").strip()
        draft_code = str(t.get("draft_code", "") or "").strip()

        # BEFORE from current real_bytes (may change as we apply)
        cur_real_text = real_bytes.decode("utf-8", errors="replace")
        cur_syms = extract_symbols(cur_real_text)
        cur_map = {s.qname: s for s in cur_syms}
        if qname not in cur_map:
            continue
        before_code = _extract_symbol_source(cur_real_text, cur_map[qname])

        # Edit attempt (single pass + optional repair)
        system = load_prompt(root, "edit.md")
        user = (
            f"Path: {rel_path}\nQname: {qname}\n\n"
            "Current symbol code:\n" + before_code + "\n\n"
            "Edit prompt (plain text; follow deterministically):\n"
            + (edit_prompt + "\n\n" if edit_prompt else "(missing)\n\n")
            + "Draft replacement code (follow as source of truth; keep signature/decorators from current code):\n"
            + (draft_code + "\n\n" if draft_code else "(missing)\n\n")
        )
        ok, out = _llm_chat_logged(
            llm=llm,
            system=system,
            user=user,
            logger=logger,
            stage="edit",
            tag=f"edit:{qname}",
            verbose_llm=verbose_llm,
            stream=True,
        )
        write_text(
            os.path.join(task_art, f"edit_{_task_id(qname)}.raw.txt"), out + "\n"
        )
        ok2, err, obj = _best_effort_json(out)
        if not ok2 or not obj:
            write_text(
                os.path.join(task_art, f"edit_{_task_id(qname)}.parse_error.txt"),
                err + "\n",
            )
            logger.warn(
                event="edit_parse_failed",
                rel_path=rel_path,
                qname=qname,
                error=err[:4000],
            )
            continue
        new_code = str(obj.get("code", ""))
        if not new_code.strip():
            continue

        # Pre-check: meaningless change
        if _meaningless_change(before_code, new_code):
            write_text(
                os.path.join(
                    task_art, f"edit_{_task_id(qname)}.rejected_meaningless.txt"
                ),
                "meaningsless\n",
            )
            continue

        # Apply to temp
        prev_temp = temp_bytes
        ar = apply_replace_symbol(
            rel_path=rel_path,
            original_bytes=temp_bytes,
            op_qname=qname,
            new_code=new_code,
        )
        if not ar.ok:
            write_text(
                os.path.join(task_art, f"apply_{_task_id(qname)}.error.txt"),
                ar.msg + "\n",
            )
            logger.warn(
                event="apply_temp_failed",
                rel_path=rel_path,
                qname=qname,
                msg=ar.msg[:4000],
            )
            continue

        temp_text = ar.updated_source
        temp_bytes = temp_text.encode("utf-8")
        with open(work_abs, "wb") as f:
            f.write(temp_bytes)

        # Heuristic trim + tabs before gates
        temp_text2 = heuristic_basic(temp_text)
        if temp_text2 != temp_text:
            temp_bytes = temp_text2.encode("utf-8")
            with open(work_abs, "wb") as f:
                f.write(temp_bytes)

        # Gates on temp
        parse_ok, parse_err = check_parse(work_abs)
        ruff_ok, ruff_out, ruff_err = check_ruff(work_abs)
        if (not parse_ok) or (not ruff_ok):
            # Optional safe ruff fix
            if ruff_fix_mode == "safe":
                ruff_fix_safe(work_abs)
                parse_ok, parse_err = check_parse(work_abs)
                ruff_ok, ruff_out, ruff_err = check_ruff(work_abs)

        if (not parse_ok) or (not ruff_ok):
            # Micro-LLM repair on symbol only (one attempt)
            diag = ""
            if not parse_ok:
                diag += f"SyntaxError: {parse_err}\n"
            if not ruff_ok:
                diag += (
                    json.dumps(ruff_out, ensure_ascii=False)
                    if not isinstance(ruff_out, str)
                    else ruff_out
                )[:2000]
                diag += "\n"
                diag += (
                    json.dumps(ruff_err, ensure_ascii=False)
                    if not isinstance(ruff_err, str)
                    else ruff_err
                )[:2000]

            # extract current symbol from temp and repair it
            temp_current_text = read_bytes(work_abs).decode("utf-8", errors="replace")
            temp_syms2 = extract_symbols(temp_current_text)
            temp_map2 = {s.qname: s for s in temp_syms2}
            if qname in temp_map2:
                bad_code = _extract_symbol_source(temp_current_text, temp_map2[qname])
                rep_prompt = load_prompt(root, "repair.md")
                okr, fixed = micro_llm_repair(
                    llm=llm,
                    repair_prompt=rep_prompt,
                    diagnostics=diag,
                    symbol_code=bad_code,
                    verbose=verbose_llm,
                    tag=f"repair:{qname}",
                )
                if okr and fixed.strip():
                    ar2 = apply_replace_symbol(
                        rel_path=rel_path,
                        original_bytes=read_bytes(work_abs),
                        op_qname=qname,
                        new_code=fixed,
                    )
                    if ar2.ok:
                        temp_bytes = ar2.updated_source.encode("utf-8")
                        with open(work_abs, "wb") as f:
                            f.write(temp_bytes)
                        # re-run gates
                        parse_ok, parse_err = check_parse(work_abs)
                        ruff_ok, ruff_out, ruff_err = check_ruff(work_abs)

        if (not parse_ok) or (not ruff_ok):
            _write_full_file_proposal(
                root=root,
                rel_path=rel_path,
                work_abs=work_abs,
                task_art=task_art,
                llm=llm,
                verbose_llm=verbose_llm,
                reason=f"edit gate failed for {qname}: parse_ok={parse_ok}, ruff_ok={ruff_ok}",
            )

            # revert temp, record report
            temp_bytes = prev_temp
            with open(work_abs, "wb") as f:
                f.write(temp_bytes)
            write_text(
                os.path.join(task_art, f"gate_fail_{_task_id(qname)}.txt"),
                f"parse_ok={parse_ok} ruff_ok={ruff_ok}\n"
                f"{parse_err or ''}\n"
                f"{ruff_out or ''}\n"
                f"{ruff_err or ''}\n",
            )
            logger.warn(
                event="gate_failed",
                rel_path=rel_path,
                qname=qname,
                parse_ok=parse_ok,
                ruff_ok=ruff_ok,
                parse_err=(parse_err or "")[:2000],
            )
            continue

        # Approver (LLM)
        # AFTER symbol code from temp
        temp_final_text = read_bytes(work_abs).decode("utf-8", errors="replace")
        temp_syms3 = extract_symbols(temp_final_text)
        temp_map3 = {s.qname: s for s in temp_syms3}
        after_code = (
            _extract_symbol_source(
                temp_final_text, temp_map3.get(qname, cur_map[qname])
            )
            if temp_map3
            else new_code
        )

        approve_system = load_prompt(root, "approve.md")
        intent = str(t.get("intent", "")).strip()
        edit_prompt_ap = str(t.get("edit_prompt", "") or "").strip()

        acceptance = t.get("acceptance", []) or []
        if not isinstance(acceptance, list):
            acceptance = [str(acceptance)]
        acceptance_summary = "- " + "\n- ".join(
            [str(x) for x in acceptance if str(x).strip()][:30]
        )
        if acceptance_summary == "- ":
            acceptance_summary = "(none)"

        gate_summary = "parse_ok=True\nruff_ok=True\n"
        user2 = (
            f"Path: {rel_path}\nQname: {qname}\n\n"
            f"Draft intent: {intent}\n\n"
            f"Acceptance criteria:\n{acceptance_summary}\n\n"
            f"Edit prompt (excerpt):\n{(edit_prompt_ap[:2000] + ('…' if len(edit_prompt_ap) > 2000 else ''))}\n\n"
            f"Gate summary:\n{gate_summary}\n"
            "BEFORE:\n" + before_code + "\n\n"
            "AFTER:\n" + after_code + "\n"
        )
        ok_a, out_a = _llm_chat_logged(
            llm=llm,
            system=approve_system,
            user=user2,
            logger=logger,
            stage="approve",
            tag=f"approve:{qname}",
            verbose_llm=verbose_llm,
            stream=True,
        )
        write_text(
            os.path.join(task_art, f"approve_{_task_id(qname)}.txt"), out_a + "\n"
        )
        decision = (out_a.strip().splitlines()[:1] or [""])[0].strip().upper()
        if decision != "APPROVE":
            # revert temp
            temp_bytes = prev_temp
            with open(work_abs, "wb") as f:
                f.write(temp_bytes)
            write_text(
                os.path.join(task_art, f"rejected_{_task_id(qname)}.txt"),
                out_a.strip()[:4000] + "\n",
            )
            continue

        any_approved = True

        # Apply to real only if allowed
        if cfg.allow_apply:
            ar_real = apply_replace_symbol(
                rel_path=rel_path,
                original_bytes=real_bytes,
                op_qname=qname,
                new_code=after_code,
            )
            if ar_real.ok:
                real_bytes = ar_real.updated_source.encode("utf-8")
                with open(real_abs, "wb") as f:
                    f.write(real_bytes)
                logger.info(event="applied", rel_path=rel_path, qname=qname)
            else:
                logger.warn(
                    event="apply_real_failed",
                    rel_path=rel_path,
                    qname=qname,
                    msg=ar_real.msg,
                )

        # Sync temp with real after approval (so later edits stack cleanly)
        temp_bytes = real_bytes
        with open(work_abs, "wb") as f:
            f.write(temp_bytes)

    if not any_approved:
        write_text(
            os.path.join(task_art, "edit_no_approvals.txt"),
            "No symbol changes were approved.\n",
        )


def _do_repair_only(
    *,
    root: Path,
    rel_path: str,
    real_abs: str,
    raw: bytes,
    newline: str,
    task_art: str,
    work_abs: str,
    cfg: NoctuneConfig,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    ruff_fix_mode: str,
    logger: EventLogger,
) -> None:
    # Repair-only: run gates; if failing, apply heuristic + optional ruff --fix; do not use editor/approver.
    with open(work_abs, "wb") as f:
        f.write(raw)
    parse_ok, parse_err = check_parse(work_abs)
    ruff_ok, ruff_out, ruff_err = check_ruff(work_abs)
    if (not parse_ok) or (not ruff_ok):
        _write_full_file_proposal(
            root=root,
            rel_path=rel_path,
            work_abs=work_abs,
            task_art=task_art,
            llm=llm,
            verbose_llm=verbose_llm,
            reason=f"repair-only gate failed: parse_ok={parse_ok}, ruff_ok={ruff_ok}",
        )
        # Do not apply to real; leave artifacts for human/codex.
        return

    write_text(
        os.path.join(task_art, "repair_gates.txt"),
        f"parse_ok={parse_ok}\nruff_ok={ruff_ok}\n{parse_err or ''}\n{ruff_out or ''}\n{ruff_err or ''}\n",
    )

    # Apply repaired temp to real only if allow_apply and now clean
    if cfg.allow_apply and parse_ok and ruff_ok:
        repaired = read_bytes(work_abs)
        with open(real_abs, "wb") as f:
            f.write(repaired)


def _do_run_full(
    *,
    root: Path,
    rel_path: str,
    real_abs: str,
    raw: bytes,
    newline: str,
    task_art: str,
    work_abs: str,
    cfg: NoctuneConfig,
    llm: Optional[LLMClient],
    verbose_llm: bool,
    ruff_fix_mode: str,
    logger: EventLogger,
) -> None:
    # Full loop: plan -> review -> draft -> edit -> approve; then repeat review/draft/edit as needed.
    max_passes = 3
    for p in range(max_passes):
        _do_plan(
            root, rel_path, read_bytes(real_abs), task_art, llm, verbose_llm, logger
        )
        _do_review(
            root, rel_path, read_bytes(real_abs), task_art, llm, verbose_llm, logger
        )
        review_text = Path(os.path.join(task_art, "review.md")).read_text(
            encoding="utf-8", errors="replace"
        )
        lbl = _label_from_review(review_text)
        if lbl == "W":
            return

        pth = os.path.join(task_art, "draft.json")
        try:
            if os.path.exists(pth):
                os.remove(pth)
        except Exception:
            pass
        _do_draft(
            root, rel_path, read_bytes(real_abs), task_art, llm, verbose_llm, logger
        )

        _do_edit(
            root=root,
            rel_path=rel_path,
            real_abs=real_abs,
            raw=read_bytes(real_abs),
            newline=newline,
            task_art=task_art,
            work_abs=work_abs,
            cfg=cfg,
            llm=llm,
            verbose_llm=verbose_llm,
            ruff_fix_mode=ruff_fix_mode,
            logger=logger,
        )
        # Refresh review + draft for next pass.
        for fp in ("review.md", "draft.json", "selection.json"):
            pth = os.path.join(task_art, fp)
            try:
                if os.path.exists(pth):
                    os.remove(pth)
            except Exception:
                pass
    write_text(
        os.path.join(task_art, "run_stopped.txt"),
        "Stopped after max passes without reaching W.\n",
    )
