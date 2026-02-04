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
pip install git+https://github.com/nonml/noctune
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

## Noctune Studio (local chat + tools)

Noctune Studio is a local-first “ChatGPT + repo tools + Noctune runs” layer.

- Web app (recommended): `apps/studio-web/README.md`
  - Chat with your OpenAI-compatible LLM (e.g. llama.cpp)
  - Tool-calling: read/search/edit repo files (permission-gated), start/monitor Noctune runs (permission-gated)
  - Saves chat sessions under `<repoRoot>/.noctune_cache/studio_chat/sessions/`
- Python daemon + MCP server (optional): `noctune studio serve` / `noctune studio mcp`

### Run the web app

```bash
cd apps/studio-web
pnpm install
cp .env.example .env.local
# Set at least:
# NOCTUNE_STUDIO_LLM_BASE_URL, NOCTUNE_STUDIO_LLM_MODEL
pnpm dev
```

Open:
- http://localhost:3000/studio
- http://localhost:3000/studio/runs

### Run the Python daemon (optional)

```bash
python -m pip install -e ".[studio]"
noctune studio serve --root . --port 7331
```

Stop the latest run:

```bash
noctune studio stop --root .
```

Run MCP server (stdio):

```bash
noctune studio mcp
```

Notes:
- `noctune-legacy/` is reference-only and intentionally ignored by git.

## Tests

This repo uses `pytest`.

```bash
.venv/bin/python -m pytest -q
```

If you don’t have a venv yet:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

Artifacts are written under:

- `./.noctune_cache/runs/<run_id>/artifacts/<task_id>/...`

## How it works

For each file, Noctune executes a resumable flow:

1. Review
- Writes `review.md` and a label (`N`, `P`, `W`).
- Defines what must change to reach `W` (high-leverage checklist).

2. Draft
- Writes `draft.json`.
- Uses the review plus minimal evidence to choose 1–3 high-leverage symbols/chunks and produces a concrete editor-ready payload: `edit_prompt` (plain text) + `draft_code` (near-final replacement code).

3. Edit (temp-first)
- Editor sees only the symbol code + Draft payload (edit_prompt + draft_code) (not the full file).
- Applies to work copy, runs gates, repairs if needed.

4. Approve
- Approver compares BEFORE vs AFTER (no unified diff) and approves/rejects patching the real file.

5. Iterate
- Repeat `review → draft → edit → approve` until `W` or pass limit.

Notes on orchestration:
- `noctune edit` will create any missing prerequisites for a file (review, draft) before attempting edits.
- `noctune run` orchestrates: review → draft → edit → approve, then loops review/draft/edit/approve until Label `W` or max passes.


Important constraints:
- No git integration.
- No code execution.
- Tests are intentionally skipped in this version.

## Commands

Noctune follows a “ruff-like” CLI shape:

- `noctune review` — generate `review.md` per file.
- `noctune edit` — run one edit pass per file (review + draft + edit + approve + gates).
- `noctune repair` — attempt to repair the current file state using deterministic and micro-LLM repair.
- `noctune run` — full loop (review + draft + edit + approve), potentially with multiple passes until Label `W` or a pass limit.

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

- `./.noctune_cache/overrides/draft.md`
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
