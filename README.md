# Noctune 🦉

Noctune is a resumable, local‑LLM agentic “code upgrader” for Python monorepos. It is designed for small or budget-constrained LLMs and long overnight runs: it plans, gathers minimal repo evidence (imports + grep callsites), proposes per‑symbol upgrades, applies them on a temporary work copy, runs lightweight gates (parse + Ruff), and then asks an automated approver whether the change is worth patching into the real file.

Key properties:
- CLI-first, ruff-like UX (`noctune ...`)
- One file at a time; per-symbol editing (functions/classes/methods) instead of unified diffs
- Resumable checkpoints (you can stop/restart without losing progress)
- “Temp-first” editing: changes are validated on a work file before touching the real file
- OpenAI-compatible `/v1/chat/completions` only (llama.cpp and similar servers)

## Install

This repo is not published to PyPI yet. Install locally:

```bash
python -m pip install -e .
```

or

```bash
pip install github+https://github.com/nonml/noctune
```

## Quickstart

1) Initialize in your repo root:

```bash
noctune init --root .
```

This creates:
- `./noctune.toml` (repo-local config)
- `./.noctune_cache/overrides/*.md` (editable prompts)

2) Configure your LLM endpoint in `noctune.toml`:

```toml
[tool.noctune]
allow_apply = false
ruff_required = true
rg_optional = true

[tool.noctune.llm]
base_url = "http://127.0.0.1:8080/v1"
# model = "qwen"    # optional; server may default
api_key = "local"   # optional for local servers
stream = true
verbose_stream = true
stream_print_reasoning = true
```

Environment overrides (useful for secrets):

- `NOCTUNE_BASE_URL`
- `NOCTUNE_API_KEY`
- `NOCTUNE_HEADERS_JSON` (JSON dict)

3) Run on the whole repo:

```bash
noctune run --root .
```

Or run on a file list (one relative `.py` per line):

```bash
noctune run --root . --file-list path/to/files.txt
```

Or target specific paths:

```bash
noctune edit --root . src/foo.py
noctune review --root . src/foo.py
```

If you pass `--yes`, Noctune will not prompt interactively. In non-interactive mode, you must have granted permission previously by setting `allow_apply = true` in `noctune.toml`.

Artifacts are written under:

- `./.noctune_cache/runs/<run_id>/artifacts/<task_id>/...`

## How it works

For each file, Noctune executes a resumable flow:

1. Plan
- Writes `plan.md` (or `plan.json`) per file.
- Establishes a small, explicit plan and constraints.

2. Review
- Writes `review.md` and a label (`N`, `P`, `W`).
- Defines what must change to reach `W` (high-leverage checklist).

3. Select
- Writes `selection.json`.
- Uses the review plus minimal evidence to choose 1–3 high-leverage symbols/chunks and produces a concrete “change spec” (free-form bullets/pseudo-code).

4. Edit (temp-first)
- Editor sees only the symbol code + selector change spec (not the full file).
- Applies to work copy, runs gates, repairs if needed.

5. Approve
- Approver compares BEFORE vs AFTER (no unified diff) and approves/rejects patching the real file.

6. Iterate
- Repeat `review → select → edit → approve` until `W` or pass limit.

2. In the “Commands” section, adjust these descriptions to reflect that:

- `noctune plan` does only plan
- `noctune review` does only review
- `noctune edit` should require that `selection.json` already exists (or it can internally run `select` if missing, but the conceptual pipeline is still Review → Select → Edit)
- `noctune run` orchestrates: plan → review → select → edit → approve, then loops review/select/edit/approve until W or max passes.

If you want the tooling to be consistent with the docs, you should also ensure the actual orchestrator calls stages in that order. If the code currently runs select before review, fix order in the orchestrator (typically a single function like `run_file_pipeline(...)`).

If you tell me whether you want `noctune edit` to automatically trigger `select` when missing (convenient) or to refuse and instruct the user to run `noctune select` first (stricter), I’ll give you the exact wording for the README and the cleanest behavior for v1.


Important constraints:
- No git integration.
- No code execution.
- Tests are intentionally skipped in this version.

## Commands

Noctune follows a “ruff-like” CLI shape:

- `noctune plan` — generate `plan.json` per file.
- `noctune review` — generate `review.md` per file.
- `noctune edit` — run one edit pass per file (plan + review + edit + gates).
- `noctune repair` — attempt to repair the current file state using deterministic and micro-LLM repair.
- `noctune run` — do everything, potentially with multiple edit passes until Label `W` or a pass limit.

Common flags:

- `--root <path>`: repo root (default `.`)
- `--file-list <txt>`: one relative file path per line
- `--run-id <id>`: resume/use a specific run ID
- `--max-files N`: process at most N files
- `--ruff-fix safe|off`: run `ruff check --fix` during repair (default `safe`)
- `--llm on|off`: disable LLM calls (deterministic scan/index only)
- `-v`: verbose streaming (prints model output as it is generated)

## Configuration

Noctune discovers configuration in this order:

1) `noctune.toml` at the repo root (recommended)
2) `pyproject.toml` under `[tool.noctune]`

## Editing prompts

Noctune ships default prompts inside the package, but always writes repo-local overrides on `init`:

- `./.noctune_cache/overrides/plan.md`
- `./.noctune_cache/overrides/select.md`
- `./.noctune_cache/overrides/review.md`
- `./.noctune_cache/overrides/edit.md`
- `./.noctune_cache/overrides/repair.md`
- `./.noctune_cache/overrides/approve.md`

Edit those files directly. You do not need to reinstall the package.


## Output layout and checkpoints

All state is stored under your repo root:

- `.noctune_cache/runs/<run_id>/state/` — run/task state, symbol index DB
- `.noctune_cache/runs/<run_id>/artifacts/<task_id>/` — per-file artifacts
- `.noctune_cache/runs/<run_id>/backups/<task_id>/` — pre-apply backups
- `.noctune_cache/runs/<run_id>/logs/events.jsonl` — structured event log

Per-file artifacts include (when applicable):

- `plan.json`
- `impact.json`
- `review.md`
- `edit_ops_p<N>.json` and `edit_raw_p<N>.txt`
- `apply_report_p<N>.json`
- `gate_parse_error.txt` / `gate_ruff_error.json`
- `proposed_full_file.py` (last-resort proposal)

If a stage already has its artifact, Noctune will reuse it and continue from the next stage.

## Approver behavior (what happens after edits)

After applying symbol replacements, Noctune evaluates the new file with deterministic gates:

1) Parse gate: `ast.parse` must succeed.
2) Lint gate: `ruff check` must be clean.

If the gates fail, Noctune will attempt, in order:

- Heuristic cleanup (indent/newline normalization and simple fixes).
- Optional `ruff check --fix` (safe fixes only; never enables unsafe fixes).
- Micro-LLM repair on the changed symbols using `repair_prompt.txt`.

If the file still cannot pass the gates, Noctune restores the pre-apply backup so it does not leave your repo in a broken state, then writes `proposed_full_file.py` for human review.

## Interrupt policy

Noctune is designed for overnight runs. It treats Ctrl+C as a structured “human support” signal:

- First Ctrl+C: prompts you for guidance and then continues.
- Second Ctrl+C: terminates the run.

## License

MIT (see `LICENSE`).
