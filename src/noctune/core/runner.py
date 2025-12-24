from __future__ import annotations

import json
import os
import re
import traceback
from pathlib import Path
from typing import Any, Iterable

from .applier import apply_replace_symbol
from .config import NoctuneConfig
from .edit_ops import parse_edit_ops
from .gates import check_parse, check_ruff, ruff_fix_safe
from .impact import build_impact
from .indexer import extract_symbols, index_file
from .llm import LLMClient
from .logger import EventLogger
from .prompts import load_prompt
from .repair import heuristic_basic, micro_llm_repair
from .scanner import RepoScanner
from .state import (
    detect_newline_style,
    ensure_run_paths,
    load_json,
    now_iso,
    read_bytes,
    save_json,
    sha256_bytes,
    write_text,
)

MAX_PASSES_PER_FILE = 5


def _task_id_from_path(rel_path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", rel_path)[:180]


def _artifact_dir(paths, task_id: str) -> str:
    d = os.path.join(paths.artifacts_dir, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def _label_from_review(text: str) -> str | None:
    m = re.search(r"^\s*Label:\s*`?([NPW])`?\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    m2 = re.search(r"\bLabel\s*:\s*([NPW])\b", text)
    return m2.group(1) if m2 else None


def _strip_fences(text: str) -> str:
    t = text.strip()
    # remove single fence wrapper if present
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _normalize_newlines(text: str, newline: str) -> str:
    t = text.replace("\r\n", "\n")
    if newline == "\n":
        return t
    return t.replace("\n", newline)


def _write_bytes_preserve_newline(abs_path: str, text: str, newline: str) -> None:
    data = _normalize_newlines(text, newline).encode("utf-8")
    with open(abs_path, "wb") as f:
        f.write(data)


def _read_text_utf8(abs_path: str) -> tuple[str, str]:
    b = read_bytes(abs_path)
    newline = detect_newline_style(b)
    return b.decode("utf-8", errors="replace"), newline


def _extract_symbol_code(source: str, qname: str) -> str | None:
    try:
        syms = extract_symbols(source)
    except Exception:
        return None
    target = None
    for s in syms:
        if s.qname == qname:
            target = s
            break
    if not target:
        return None

    # Use \n for slicing; callers can normalize later.
    lines = source.replace("\r\n", "\n").split("\n")
    start = max(0, target.lineno - 1)
    end = max(start, target.end_lineno)
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _build_plan_user(rel_path: str, source: str) -> str:
    names = []
    try:
        names = [s.qname for s in extract_symbols(source)]
    except Exception:
        names = []
    return (
        f"Focus file: {rel_path}\n"
        f"Top-level symbols (including methods):\n{json.dumps(names, ensure_ascii=False)}\n\n"
        "Source:\n```python\n" + source + "\n```\n"
    )


def _build_review_user(
    rel_path: str, source: str, impact: dict[str, Any] | None
) -> str:
    parts: list[str] = [f"Path: {rel_path}"]
    if impact:
        parts.append("\nEvidence (deterministic, best-effort):")
        if impact.get("imports"):
            parts.append("\nImports:\n" + "\n".join(impact["imports"][:200]))
        if impact.get("callsites"):
            parts.append("\nCalls (sample):")
            # keep small
            for k, v in list((impact.get("callsites") or {}).items())[:30]:
                if not v:
                    continue
                parts.append(f"\n- {k}:")
                parts.extend(["  " + ln for ln in v[:5]])
    parts.append("\nSource:\n```python\n" + source + "\n```\n")
    return "\n".join(parts)


def _build_edit_user(
    rel_path: str,
    source: str,
    plan_obj: dict[str, Any] | None,
    review_text: str | None,
    impact: dict[str, Any] | None,
    milestone: dict[str, Any] | None,
) -> str:
    parts: list[str] = [f"Focus file: {rel_path}"]
    if milestone:
        parts.append("\nApply ONLY this milestone (one pass):")
        parts.append(json.dumps(milestone, ensure_ascii=False, indent=2))
    elif plan_obj:
        parts.append("\nPlan (first milestone preferred):")
        parts.append(json.dumps(plan_obj, ensure_ascii=False, indent=2)[:8000])
    if review_text:
        # keep review small; take the top sections
        parts.append("\nLatest review (excerpt):")
        parts.append(review_text[:8000])
    if impact:
        parts.append("\nImpact evidence:")
        parts.append(json.dumps(impact, ensure_ascii=False, indent=2)[:8000])
    parts.append("\nSource:\n```python\n" + source + "\n```\n")
    return "\n".join(parts)


def _choose_milestone(
    plan_obj: dict[str, Any] | None, done_ids: set[str]
) -> dict[str, Any] | None:
    if not plan_obj:
        return None
    ms = plan_obj.get("milestones")
    if not isinstance(ms, list):
        return None
    for it in ms:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id", "")).strip()
        if mid and mid not in done_ids:
            return it
    return None


def _safe_load_json_file(path: str) -> Any | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_skip_report(paths, task_id: str, rel_path: str, reason: str) -> None:
    d = _artifact_dir(paths, task_id)
    p = os.path.join(d, "final_report.md")
    write_text(p, f"Path: {rel_path}\n\nStatus: SKIPPED\n\nReason: {reason}\n")


def run_noctune(
    mode: str,
    *,
    root: Path,
    cfg: NoctuneConfig,
    run_id: str | None = None,
    files: Iterable[Path] | None = None,
    file_list: Path | None = None,
    max_files: int | None = None,
    ruff_fix: bool = True,
    llm_enabled: bool = True,
    log_level: str = "INFO",
    verbose_stream: bool | None = None,
) -> int:
    """Run Noctune in the given mode.

    Modes:
      - plan: generate plan artifacts only
      - review: plan + impact + review
      - edit: plan + impact + review + one edit/repair pass
      - repair: attempt deterministic repair on current file state
      - run: full flow; repeat edit+review passes until Label W or pass limit
    """

    root = root.resolve()
    paths = ensure_run_paths(str(root), run_id)
    log = EventLogger(os.path.join(paths.logs_dir, "events.jsonl"), level=log_level)

    run_state_path = os.path.join(paths.state_dir, "run.json")
    run_state = load_json(run_state_path, {})
    if not run_state:
        run_state = {
            "schema_version": 1,
            "run_id": paths.run_id,
            "created_at": now_iso(),
            "repo_root": str(root),
            "interrupt_count": 0,
            "current_file": None,
        }
        save_json(run_state_path, run_state)

    scanner = RepoScanner.create(root)
    if files is None:
        if file_list is not None:
            files = scanner.from_file_list(file_list)
        else:
            files = list(scanner.iter_python_files())
    file_list2 = list(files)
    if max_files:
        file_list2 = file_list2[:max_files]

    log.info(
        event="run_start",
        run_id=paths.run_id,
        mode=mode,
        file_count=len(file_list2),
        ruff_fix=ruff_fix,
        llm_enabled=llm_enabled,
    )

    # LLM
    llm: LLMClient | None = None
    if llm_enabled and cfg.llm.base_url:
        llm = LLMClient(
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key or "",
            model=cfg.llm.model or "",
            timeout_s=120,
            extra_headers=cfg.llm.headers or {},
            stream_default=bool(cfg.llm.stream),
            stream_print_reasoning=bool(cfg.llm.stream_print_reasoning),
            stream_print_headers=True,
        )

    # Prompts are packaged resources.
    plan_prompt = load_prompt(root, "plan.md")
    review_prompt = load_prompt(root, "review.md")
    edit_prompt = load_prompt(root, "edit.md")
    repair_prompt = load_prompt(root, "repair.md")

    db_path = os.path.join(paths.state_dir, "symbols.sqlite")

    # streaming verbosity: config default, optionally overridden
    if verbose_stream is None:
        verbose_stream = bool(cfg.llm.verbose_stream)

    for abs_file in file_list2:
        try:
            rel_path = abs_file.resolve().relative_to(root).as_posix()
        except Exception:
            # skip files not under root
            continue

        task_id = _task_id_from_path(rel_path)
        task_state_path = os.path.join(paths.state_dir, "tasks", task_id + ".json")
        tstate = load_json(task_state_path, {})
        if not tstate:
            tstate = {
                "schema_version": 1,
                "task_id": task_id,
                "path": rel_path,
                "status": "pending",
                "file_hash": None,
                "label": None,
                "pass_count": 0,
                "milestones_done": [],
                "human_notes": [],
                "last_error": None,
            }
            save_json(task_state_path, tstate)

        # Skip completed (W) if hash unchanged
        b = read_bytes(str(abs_file))
        fh = sha256_bytes(b)
        if (
            tstate.get("status") == "complete"
            and tstate.get("label") == "W"
            and tstate.get("file_hash") == fh
        ):
            log.info(event="skip_complete", task_id=task_id, path=rel_path)
            continue

        # Update run_state pointer
        run_state["current_file"] = rel_path
        save_json(run_state_path, run_state)

        try:
            _process_one(
                mode=mode,
                root=root,
                abs_file=abs_file,
                rel_path=rel_path,
                task_id=task_id,
                task_state_path=task_state_path,
                tstate=tstate,
                paths=paths,
                db_path=db_path,
                log=log,
                cfg=cfg,
                llm=llm,
                plan_prompt=plan_prompt,
                review_prompt=review_prompt,
                edit_prompt=edit_prompt,
                repair_prompt=repair_prompt,
                ruff_fix=ruff_fix,
                verbose_stream=bool(verbose_stream),
            )
        except KeyboardInterrupt:
            # Ctrl+C policy: 1 -> human note; 2 -> skip file; 3 -> terminate
            run_state = load_json(run_state_path, {})
            run_state["interrupt_count"] = int(run_state.get("interrupt_count", 0)) + 1
            save_json(run_state_path, run_state)
            ic = int(run_state["interrupt_count"])
            log.warn(event="keyboard_interrupt", count=ic, current_file=rel_path)

            if ic == 1:
                note = input(
                    "\n[Human support] Enter guidance for this file (empty to continue):\n> "
                ).strip()
                tstate = load_json(task_state_path, {})
                if note:
                    tstate.setdefault("human_notes", []).append(
                        {"ts": now_iso(), "note": note}
                    )
                    save_json(task_state_path, tstate)
                continue
            if ic == 2:
                _write_skip_report(
                    paths,
                    task_id,
                    rel_path,
                    reason="Second KeyboardInterrupt; skipping file.",
                )
                tstate = load_json(task_state_path, {})
                tstate["status"] = "skipped"
                save_json(task_state_path, tstate)
                continue
            # ic >= 3
            log.error(event="terminate_on_interrupt", count=ic)
            return 130
        except Exception as e:
            log.error(
                event="file_crash",
                task_id=task_id,
                path=rel_path,
                err=str(e),
                tb=traceback.format_exc()[:4000],
            )
            tstate = load_json(task_state_path, {})
            tstate["last_error"] = str(e)
            tstate["status"] = "error"
            save_json(task_state_path, tstate)
            continue

    log.info(event="run_end", run_id=paths.run_id)
    return 0


def _process_one(
    *,
    mode: str,
    root: Path,
    abs_file: Path,
    rel_path: str,
    task_id: str,
    task_state_path: str,
    tstate: dict[str, Any],
    paths,
    db_path: str,
    log: EventLogger,
    cfg: NoctuneConfig,
    llm: LLMClient | None,
    plan_prompt: str,
    review_prompt: str,
    edit_prompt: str,
    repair_prompt: str,
    ruff_fix: bool,
    verbose_stream: bool,
) -> None:
    d = _artifact_dir(paths, task_id)
    abs_path = str(abs_file)

    source, newline = _read_text_utf8(abs_path)
    file_hash = sha256_bytes(read_bytes(abs_path))

    tstate["status"] = "in_progress"
    tstate["file_hash"] = file_hash
    save_json(task_state_path, tstate)

    # Index symbols (deterministic). Do not block on failures.
    try:
        syms = index_file(db_path, rel_path, source)
        sym_names = [s.qname for s in syms]
    except Exception:
        sym_names = []

    # PLAN
    plan_path = os.path.join(d, "plan.json")
    plan_obj: dict[str, Any] | None = None
    if os.path.exists(plan_path):
        plan_obj = _safe_load_json_file(plan_path)
    if plan_obj is None and mode in ("plan", "review", "edit", "repair", "run"):
        if not llm:
            write_text(
                os.path.join(d, "plan_error.txt"),
                "LLM disabled or missing base_url; cannot generate plan.\n",
            )
        else:
            ok, out = llm.chat(
                system=plan_prompt,
                user=_build_plan_user(rel_path, source),
                verbose=verbose_stream,
                tag=f"plan:{rel_path}",
            )
            if ok:
                raw = _strip_fences(out)
                try:
                    plan_obj = json.loads(raw)
                    write_text(
                        plan_path,
                        json.dumps(plan_obj, indent=2, ensure_ascii=False) + "\n",
                    )
                except Exception as e:
                    write_text(os.path.join(d, "plan_raw.txt"), out)
                    write_text(
                        os.path.join(d, "plan_error.txt"),
                        f"Plan JSON parse failed: {e}\n",
                    )
            else:
                write_text(os.path.join(d, "plan_error.txt"), out + "\n")

    if mode == "plan":
        tstate["status"] = "planned"
        save_json(task_state_path, tstate)
        return

    # IMPACT (deterministic)
    impact_path = os.path.join(d, "impact.json")
    impact_obj: dict[str, Any] | None = None
    if os.path.exists(impact_path):
        impact_obj = _safe_load_json_file(impact_path)
    if impact_obj is None:
        try:
            impact = build_impact(str(root), source, sym_names[:200])
            impact_obj = {
                "imports": impact.imports,
                "callsites": impact.callsites,
            }
            write_text(
                impact_path, json.dumps(impact_obj, indent=2, ensure_ascii=False) + "\n"
            )
        except Exception as e:
            impact_obj = {"error": str(e)}
            write_text(
                impact_path, json.dumps(impact_obj, indent=2, ensure_ascii=False) + "\n"
            )

    # REVIEW
    review_path = os.path.join(d, "review.md")
    review_text: str | None = None
    if os.path.exists(review_path):
        try:
            review_text = Path(review_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            review_text = None
    if review_text is None:
        if not llm:
            review_text = "Label: N\n\n(no LLM configured; review skipped)\n"
            write_text(review_path, review_text)
        else:
            ok, out = llm.chat(
                system=review_prompt,
                user=_build_review_user(rel_path, source, impact_obj),
                verbose=verbose_stream,
                tag=f"review:{rel_path}",
            )
            review_text = out if ok else ("Label: N\n\n" + out)
            write_text(review_path, review_text)

    label = _label_from_review(review_text or "")
    if label:
        tstate["label"] = label
        save_json(task_state_path, tstate)

    if label == "W":
        tstate["status"] = "complete"
        tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
        save_json(task_state_path, tstate)
        log.info(event="file_complete", task_id=task_id, path=rel_path)
        return

    if mode == "review":
        tstate["status"] = "reviewed"
        save_json(task_state_path, tstate)
        return

    # REPAIR-only mode: do not generate edits; attempt to clean the current file.
    if mode == "repair":
        _repair_current_file(
            abs_path=abs_path,
            rel_path=rel_path,
            task_id=task_id,
            paths=paths,
            newline=newline,
            llm=llm,
            repair_prompt=repair_prompt,
            verbose_stream=verbose_stream,
            ruff_fix=ruff_fix,
            log=log,
            d=d,
        )
        tstate["status"] = "repaired"
        tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
        save_json(task_state_path, tstate)
        return

    # EDIT / RUN
    if not llm:
        write_text(
            os.path.join(d, "final_report.md"),
            f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nReason: LLM disabled or missing base_url; cannot edit.\n",
        )
        tstate["status"] = "needs_human"
        save_json(task_state_path, tstate)
        return

    done_ids = set([str(x) for x in (tstate.get("milestones_done") or [])])
    passes = int(tstate.get("pass_count") or 0)

    # In edit mode, do exactly one pass. In run mode, loop with a ceiling.
    pass_budget = 1 if mode == "edit" else max(1, MAX_PASSES_PER_FILE - passes)
    while pass_budget > 0:
        pass_budget -= 1
        passes = int(tstate.get("pass_count") or 0) + 1
        tstate["pass_count"] = passes
        save_json(task_state_path, tstate)

        # Refresh from disk each pass
        source, newline = _read_text_utf8(abs_path)
        file_hash = sha256_bytes(read_bytes(abs_path))
        tstate["file_hash"] = file_hash
        save_json(task_state_path, tstate)

        milestone = _choose_milestone(plan_obj, done_ids)
        if milestone and milestone.get("id"):
            cur_ms_id = str(milestone.get("id"))
        else:
            cur_ms_id = ""

        user = _build_edit_user(
            rel_path=rel_path,
            source=source,
            plan_obj=plan_obj,
            review_text=review_text,
            impact=impact_obj,
            milestone=milestone,
        )
        ok, out = llm.chat(
            system=edit_prompt,
            user=user,
            verbose=verbose_stream,
            tag=f"edit:{rel_path}:p{passes}",
        )
        write_text(os.path.join(d, f"edit_raw_p{passes}.txt"), out)
        if not ok:
            write_text(
                os.path.join(d, "final_report.md"),
                f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nLLM edit failed:\n{out}\n",
            )
            tstate["status"] = "needs_human"
            tstate["last_error"] = "llm_edit_failed"
            save_json(task_state_path, tstate)
            return

        ok_ops, err, ops = parse_edit_ops(out)
        if not ok_ops:
            write_text(
                os.path.join(d, "final_report.md"),
                f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nCould not parse edit ops JSON: {err}\n",
            )
            tstate["status"] = "needs_human"
            tstate["last_error"] = f"edit_ops_parse_failed: {err}"
            save_json(task_state_path, tstate)
            return
        write_text(
            os.path.join(d, f"edit_ops_p{passes}.json"),
            json.dumps([op.__dict__ for op in ops], indent=2, ensure_ascii=False)
            + "\n",
        )

        # Backup current file bytes before applying
        backup_name = f"p{passes}__{_task_id_from_path(rel_path)}.before.py"
        backup_path = os.path.join(paths.backups_dir, task_id, backup_name)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "wb") as f:
            f.write(read_bytes(abs_path))

        changed: list[str] = []
        updated_source = source
        # Apply replace_symbol ops only (v0). Others are recorded as skipped.
        skipped_ops: list[dict[str, Any]] = []
        for op in ops:
            if op.op != "replace_symbol":
                skipped_ops.append(
                    {"op": op.op, "qname": op.qname, "reason": "unsupported_in_v0"}
                )
                continue
            if not op.qname or not op.new_code:
                skipped_ops.append(
                    {
                        "op": op.op,
                        "qname": op.qname,
                        "reason": "missing_qname_or_new_code",
                    }
                )
                continue
            res = apply_replace_symbol(
                rel_path, updated_source.encode("utf-8"), op.qname, op.new_code
            )
            if not res.ok:
                skipped_ops.append({"op": op.op, "qname": op.qname, "reason": res.msg})
                continue
            updated_source = res.updated_source
            changed.extend(res.changed_qnames)

        # Write apply report now (even if nothing applied)
        apply_report = {
            "path": rel_path,
            "pass": passes,
            "changed_qnames": changed,
            "skipped_ops": skipped_ops,
        }
        write_text(
            os.path.join(d, f"apply_report_p{passes}.json"),
            json.dumps(apply_report, indent=2, ensure_ascii=False) + "\n",
        )

        if not changed:
            # Nothing applied; mark milestone as done to avoid infinite loops if it keeps asking for unsupported ops.
            if cur_ms_id:
                done_ids.add(cur_ms_id)
                tstate.setdefault("milestones_done", []).append(cur_ms_id)
                save_json(task_state_path, tstate)
            if mode == "edit":
                tstate["status"] = "no_changes"
                save_json(task_state_path, tstate)
                return
            # In run mode, re-review and continue (maybe plan was weak)
            review_text = None
            if os.path.exists(review_path):
                os.remove(review_path)
            continue

        # Apply to disk (caller has already gated permission in CLI).
        _write_bytes_preserve_newline(abs_path, updated_source, newline)

        # Gates + repair loop
        gate_ok = _gates_and_repair(
            abs_path=abs_path,
            rel_path=rel_path,
            newline=newline,
            changed_qnames=changed,
            d=d,
            llm=llm,
            repair_prompt=repair_prompt,
            verbose_stream=verbose_stream,
            ruff_fix=ruff_fix,
        )
        if not gate_ok:
            # Last-resort: write a full-file proposal artifact.
            # IMPORTANT: do not leave a broken file in the repo; restore the backup.
            _write_full_file_proposal(
                abs_path=abs_path,
                rel_path=rel_path,
                d=d,
                llm=llm,
                verbose_stream=verbose_stream,
            )
            try:
                with open(backup_path, "rb") as f:
                    orig = f.read()
                with open(abs_path, "wb") as f:
                    f.write(orig)
                write_text(
                    os.path.join(d, "restore_note.txt"),
                    "Gate(s) failed after apply; restored the pre-apply backup.\n",
                )
            except Exception:
                # If restore fails, keep going but surface it in logs.
                log.warn(event="restore_failed", task_id=task_id, path=rel_path)
            tstate["status"] = "needs_human"
            tstate["last_error"] = "gates_failed"
            tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
            save_json(task_state_path, tstate)
            return

        # Mark milestone done if we had one
        if cur_ms_id:
            done_ids.add(cur_ms_id)
            tstate.setdefault("milestones_done", []).append(cur_ms_id)
            save_json(task_state_path, tstate)

        # Re-review after successful pass
        review_text = None
        if os.path.exists(review_path):
            os.remove(review_path)
        # loop back to top of while; next iteration will regenerate review

        # Force review regeneration now
        source, _ = _read_text_utf8(abs_path)
        ok_r, out_r = llm.chat(
            system=review_prompt,
            user=_build_review_user(rel_path, source, impact_obj),
            verbose=verbose_stream,
            tag=f"review:{rel_path}:p{passes}",
        )
        review_text = out_r if ok_r else ("Label: N\n\n" + out_r)
        write_text(review_path, review_text)
        label = _label_from_review(review_text)
        if label:
            tstate["label"] = label
            save_json(task_state_path, tstate)
        if label == "W":
            tstate["status"] = "complete"
            tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
            save_json(task_state_path, tstate)
            log.info(event="file_complete", task_id=task_id, path=rel_path)
            return

        if mode == "edit":
            tstate["status"] = "edited"
            tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
            save_json(task_state_path, tstate)
            return

    # If we ran out of pass budget
    tstate["status"] = "incomplete"
    tstate["file_hash"] = sha256_bytes(read_bytes(abs_path))
    save_json(task_state_path, tstate)


def _gates_and_repair(
    *,
    abs_path: str,
    rel_path: str,
    newline: str,
    changed_qnames: list[str],
    d: str,
    llm: LLMClient,
    repair_prompt: str,
    verbose_stream: bool,
    ruff_fix: bool,
) -> bool:
    # Parse gate
    source, _nl = _read_text_utf8(abs_path)
    ok_parse, parse_err = check_parse(source)
    if not ok_parse:
        # Heuristic cleanup on whole file
        cleaned = heuristic_basic(source)
        ok_parse2, parse_err2 = check_parse(cleaned)
        if ok_parse2:
            _write_bytes_preserve_newline(abs_path, cleaned, newline)
            source = cleaned
            ok_parse, parse_err = True, None
        else:
            write_text(
                os.path.join(d, "gate_parse_error.txt"),
                f"{parse_err}\n{parse_err2 or ''}\n",
            )
            # Try micro-LLM repairs on changed symbols (best-effort)
            if changed_qnames:
                ok_repair = _micro_repair_symbols(
                    abs_path=abs_path,
                    rel_path=rel_path,
                    newline=newline,
                    qnames=changed_qnames,
                    d=d,
                    llm=llm,
                    repair_prompt=repair_prompt,
                    diagnostics=parse_err2 or parse_err or "parse failed",
                    verbose_stream=verbose_stream,
                )
                if ok_repair:
                    source, _ = _read_text_utf8(abs_path)
                    ok_parse, parse_err = check_parse(source)
            if not ok_parse:
                write_text(
                    os.path.join(d, "final_report.md"),
                    f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nParse gate failed:\n{parse_err}\n",
                )
                return False

    # Ruff gate
    ok_ruff, ruff_json, ruff_stderr = check_ruff(abs_path)
    if not ok_ruff and ruff_fix:
        ruff_fix_safe(abs_path)
        # re-check after fix
        ok_ruff, ruff_json, ruff_stderr = check_ruff(abs_path)

    if not ok_ruff:
        # Try micro-LLM repair on changed symbols
        diag = (
            json.dumps(ruff_json, ensure_ascii=False)[:4000]
            if ruff_json is not None
            else (ruff_stderr or "ruff failed")
        )
        ok_repair = _micro_repair_symbols(
            abs_path=abs_path,
            rel_path=rel_path,
            newline=newline,
            qnames=changed_qnames,
            d=d,
            llm=llm,
            repair_prompt=repair_prompt,
            diagnostics=diag,
            verbose_stream=verbose_stream,
        )
        if ok_repair:
            ok_ruff, ruff_json, ruff_stderr = check_ruff(abs_path)

    if not ok_ruff:
        write_text(
            os.path.join(d, "gate_ruff_error.json"),
            json.dumps(
                {"ruff": ruff_json, "stderr": ruff_stderr}, indent=2, ensure_ascii=False
            )
            + "\n",
        )
        write_text(
            os.path.join(d, "final_report.md"),
            f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nRuff gate failed.\n",
        )
        return False

    return True


def _micro_repair_symbols(
    *,
    abs_path: str,
    rel_path: str,
    newline: str,
    qnames: list[str],
    d: str,
    llm: LLMClient,
    repair_prompt: str,
    diagnostics: str,
    verbose_stream: bool,
) -> bool:
    # Two rounds max.
    for round_i in range(2):
        made_change = False
        source, _ = _read_text_utf8(abs_path)
        for qn in qnames:
            sym_code = _extract_symbol_code(source, qn)
            if not sym_code:
                continue
            ok, fixed = micro_llm_repair(
                llm,
                repair_prompt,
                symbol_code=sym_code,
                diagnostics=diagnostics,
                verbose=verbose_stream,
                tag=f"repair:{rel_path}:{qn}:r{round_i + 1}",
            )
            if not ok:
                continue
            # Apply repaired symbol
            res = apply_replace_symbol(rel_path, source.encode("utf-8"), qn, fixed)
            if res.ok:
                _write_bytes_preserve_newline(abs_path, res.updated_source, newline)
                made_change = True
                source = res.updated_source
        if not made_change:
            return False
        # Re-check parse after each round
        source2, _ = _read_text_utf8(abs_path)
        ok_parse, _err = check_parse(source2)
        if not ok_parse:
            continue
        return True
    return False


def _write_full_file_proposal(
    *,
    abs_path: str,
    rel_path: str,
    d: str,
    llm: LLMClient,
    verbose_stream: bool,
) -> None:
    # Always produce a human-friendly proposal file on last-resort failure.
    source, _nl = _read_text_utf8(abs_path)
    proposal_path = os.path.join(d, "proposed_full_file.py")
    # If we already have a proposal, keep it (checkpoint).
    if os.path.exists(proposal_path):
        return

    sys_prompt = (
        "You are a senior Python engineer.\n\n"
        "Task: return a FULL corrected replacement for the focus file.\n"
        "Rules:\n"
        "- Output ONLY the full Python file content (no prose, no fences).\n"
        "- Minimize formatting churn where possible.\n"
        "- Fix syntax errors and obvious Ruff issues if you can.\n"
    )
    user = f"Focus file: {rel_path}\n\nCurrent content:\n" + source
    ok, out = llm.chat(
        system=sys_prompt,
        user=user,
        verbose=verbose_stream,
        tag=f"fullfile:{rel_path}",
    )
    if not ok:
        write_text(proposal_path, "# LLM full-file proposal failed\n")
        write_text(os.path.join(d, "full_file_proposal_error.txt"), out + "\n")
        return
    proposed = _strip_fences(out)
    write_text(proposal_path, proposed.rstrip() + "\n")


def _repair_current_file(
    *,
    abs_path: str,
    rel_path: str,
    task_id: str,
    paths,
    newline: str,
    llm: LLMClient | None,
    repair_prompt: str,
    verbose_stream: bool,
    ruff_fix: bool,
    log: EventLogger,
    d: str,
) -> None:
    source, _ = _read_text_utf8(abs_path)
    ok_parse, perr = check_parse(source)
    if not ok_parse:
        cleaned = heuristic_basic(source)
        ok2, perr2 = check_parse(cleaned)
        if ok2:
            _write_bytes_preserve_newline(abs_path, cleaned, newline)
            source = cleaned
        else:
            write_text(
                os.path.join(d, "gate_parse_error.txt"), f"{perr}\n{perr2 or ''}\n"
            )

    ok_ruff, ruff_json, ruff_stderr = check_ruff(abs_path)
    if not ok_ruff and ruff_fix:
        ruff_fix_safe(abs_path)
        ok_ruff, ruff_json, ruff_stderr = check_ruff(abs_path)

    if ok_ruff:
        return

    # If still failing, try micro repair based on last edit ops if available.
    if not llm:
        write_text(
            os.path.join(d, "final_report.md"),
            f"Path: {rel_path}\n\nStatus: NEEDS_HUMAN\n\nRepair requested, but LLM is not configured.\n",
        )
        return

    # Find most recent ops file in artifacts
    qnames: list[str] = []
    for p in sorted(Path(d).glob("edit_ops_p*.json"), reverse=True):
        obj = _safe_load_json_file(str(p))
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict) and it.get("qname"):
                    qnames.append(str(it["qname"]))
        if qnames:
            break
    qnames = list(dict.fromkeys(qnames))[:30]
    diag = (
        json.dumps(ruff_json, ensure_ascii=False)[:4000]
        if ruff_json is not None
        else (ruff_stderr or "ruff failed")
    )

    if not qnames:
        # No symbol hints; write proposal and return.
        _write_full_file_proposal(
            abs_path=abs_path,
            rel_path=rel_path,
            d=d,
            llm=llm,
            verbose_stream=verbose_stream,
        )
        return

    ok_repair = _micro_repair_symbols(
        abs_path=abs_path,
        rel_path=rel_path,
        newline=newline,
        qnames=qnames,
        d=d,
        llm=llm,
        repair_prompt=repair_prompt,
        diagnostics=diag,
        verbose_stream=verbose_stream,
    )
    if not ok_repair:
        _write_full_file_proposal(
            abs_path=abs_path,
            rel_path=rel_path,
            d=d,
            llm=llm,
            verbose_stream=verbose_stream,
        )
        return
