# Noctune 🦉

Noctune is a local, agentic code editor for Python monorepos that is designed to work with small or budget-constrained LLMs.

It runs a resumable, per-file pipeline that is intentionally conservative and failure-tolerant: it plans, gathers minimal repo impact evidence, proposes per-symbol edits, applies them (with explicit permission), then “approves” the result using deterministic gates (parse + Ruff). If anything fails, it attempts lightweight repairs and, as a last resort, writes a full-file proposal for a human to apply.

Noctune speaks to any OpenAI-compatible server that implements `POST /v1/chat/completions` (for example, llama.cpp’s OpenAI server).

## Key ideas

- One model, all roles: the same model is used for planning, review, editing, and micro-repairs.
- Small-context friendly: prefers per-symbol edits (`Class.method` is a first-class edit unit).
- No unified diffs: edits are applied via symbol replacement, not patch hunks.
- Failure-tolerant: any LLM stage may fail; the pipeline checkpoints every stage and continues from the last good artifact.
- Deterministic “approver”: after edits, Noctune runs `ast.parse` and `ruff check` (optionally `ruff check --fix` with safe fixes) and only keeps changes that pass.
- No repo execution: Noctune never runs tests or executes your code. It only parses and lints.

## Install

This repository is structured as an installable package, but you can also run it from a checkout.

- Development install:

  - `python -m pip install -e .`

Python 3.11+ is required.

## Quickstart

1) From your repo root, initialize config:

- `noctune init`

This writes `noctune.toml` and (optionally) offers to add `.noctune_cache/` to your `.gitignore`.

2) Run the full pipeline across your repo:

- `noctune run --root .`

Or provide an explicit file list (one relative `.py` path per line):

- `noctune run --root . --file-list path/to/files.txt`

If you pass `--yes`, Noctune will not prompt interactively. In non-interactive mode, you must have granted permission previously by setting `allow_apply = true` in `noctune.toml`.

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

`noctune init` writes a minimal `noctune.toml` like:

```toml
[tool.noctune]
allow_apply = false
ruff_required = true
rg_optional = true

[tool.noctune.llm]
base_url = "http://127.0.0.1:8080"
model = ""
api_key = ""
stream = true
verbose_stream = true
stream_print_reasoning = true
```

Environment overrides (useful for secrets):

- `NOCTUNE_BASE_URL`
- `NOCTUNE_API_KEY`
- `NOCTUNE_HEADERS_JSON` (JSON dict)

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
- Second Ctrl+C: skips the current file.
- Third Ctrl+C: terminates the run.

## License

MIT. See `LICENSE`.
