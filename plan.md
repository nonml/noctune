# Noctune Studio — Trackable Plan

Goal: a **local multipurpose chat platform** (“ChatGPT + Codex/Cline + Noctune”) that can:
- chat with an OpenAI-compatible local LLM (llama.cpp)
- answer questions about a repo
- do **chat-driven file edits** (permission-gated)
- start/monitor Noctune runs and handle approvals (permission-gated)
- expose/consume MCP tools (both directions)

---

## Milestone M0 — Repo hygiene (reference vs product)

- [x] Keep `noctune-legacy/` as reference-only (git-ignored)
- [x] Create first-class web app at `apps/studio-web/`
- [x] Remove duplicate Studio code that was temporarily added under legacy

**DoD / Acceptance**
- [x] `apps/studio-web/` is the only Studio web app we actively build on.
- [x] No Studio-specific code remains under `noctune-legacy/apps/web/` (kept as reference).

---

## Milestone M1 — Studio Web MVP (chat + tools)

**Scope (must-have)**
- [x] Web UI at `/studio` (`apps/studio-web/app/studio/page.tsx`)
- [x] Connect to OpenAI-compatible endpoint (`NOCTUNE_STUDIO_LLM_BASE_URL`, `NOCTUNE_STUDIO_LLM_MODEL`)
- [x] Permission model: Allow once / Allow this session / Always allow
- [x] Repo sandboxing: `NOCTUNE_STUDIO_ALLOWED_ROOTS` + path traversal protection
- [x] Tools (chat can call):
  - [x] `readFile`, `search` (rg), `writeFile`, `replaceLines` (write/edit gated; `.noctune_cache/` exempt)
  - [x] `noctuneStart`, `noctuneStop`, `noctuneEvents`, `noctuneApprovals`, `noctuneDecide` (run/decide gated)
- [x] Local persistence:
  - [x] Save/load sessions to `<repoRoot>/.noctune_cache/studio_chat/sessions/`
  - [x] Export transcript JSON from the browser

**DoD / Acceptance**
- [ ] `pnpm dev` in `apps/studio-web` and open `http://localhost:3000/studio`.
- [ ] Without allowing, a write/run tool returns `*_not_allowed` and the assistant asks for permission.
- [ ] With “Allow once”, you can edit a file and a backup is created under `<repoRoot>/.noctune_cache/studio_edits/backups/`.
- [ ] With “Allow this session”, you can start a Noctune run and tail events from `events.jsonl`.
- [ ] With “Always allow”, the permission persists across browser restarts via `<repoRoot>/.noctune_cache/studio_allow.json`.

**Nice-to-have (not required for DoD)**
- [ ] Render tool calls/results in a readable “panel” UI (not raw JSON)
- [ ] “Model profiles” dropdown (inspired by legacy `model_profiles.json`)

---

## Milestone M2 — Canonical run state + approvals invariants (engine + Studio)

- [x] Define `state/run.json` schema as single source of truth:
  - [x] `status`, `started_at`, `updated_at`, `ended_at`
  - [x] `repo_root`, `pack`, `profile`, `run_id`
  - [x] `branch`, `head_sha`, `error`
  - [ ] `resume_policy`
- [x] Ensure daemon/worker/MCP read/write via one helper module
- [ ] Approval invariants:
  - [x] If approval required and undecided → apply/commit must raise (provably enforced)
  - [ ] Decision schema: `approval_id`, `decision`, `decided_at`, `decided_by`, `reason`
- [ ] Stop semantics:
  - [x] Stop emits `stopped` event and sets status `stopped` (not `failed` unless real error)

**DoD / Acceptance**
- [ ] Starting a run always creates `state/run.json` immediately.
- [ ] UI/API/MCP status endpoints agree with `state/run.json`.
- [ ] Killing the worker process results in status `failed` with `error`, not silent “done”.
- [ ] “Stop” reliably results in `stopped` status and event.

---

## Milestone M3 — Patchsets + Git output (production-ready changes)

- [x] Patchset builder (group proposed edits before git writes)
- [ ] Grouping strategies:
  - [x] per-file
  - [x] per-module (path depth / configurable)
  - [ ] per-policy-pack
  - [x] single patchset
- [ ] Commit production:
  - [x] Apply patchset → commit (tools/tests gating TBD)
  - [ ] Run allowed tools/tests before commit
  - [ ] Commit message template: `Noctune Studio: <pack> (#<run_id>) [patchset N/M]`
  - [x] Enforce `patchset_max_commits=5`
- [x] Keep `commit_each_approval=true` as a debug option

**DoD / Acceptance**
- [ ] One run can produce a small number of clean commits with stable grouping.
- [ ] If tools/tests fail, patchset is not committed and a clear reason is logged.

---

## Milestone M4 — Policy Packs (guardrails + automation)

- [x] Config format:
  - [x] `[tool.noctune.policy_packs.<name>]`
  - [x] `allowed_globs`, `max_diff_lines`
  - [x] `tools_allowed = ["ruff", "format", "pytest", ...]`
  - [ ] `auto_approve = { enabled, max_risk, max_lines, require_tests }`
- [x] Enforcement:
  - [x] Refuse modifications outside `allowed_globs` (emit `policy_violation`)
  - [ ] Skip/log tools not in `tools_allowed`
  - [x] Auto-approve only if all pack rules pass; otherwise request approval
- [x] Built-in packs:
  - [x] `lint_fix`
  - [x] `typing_pass`
  - [x] `py_upgrade`
  - [x] `deps_bump`

**DoD / Acceptance**
- [ ] Running with a pack cannot write outside its allowed globs.
- [ ] Auto-approve is impossible when pack constraints are violated.

---

## Milestone M5 — Audit DB (SQLite, v1 requirement)

- [x] Schema:
  - [x] `runs(run_id, repo_root, pack, profile, status, started_at, ended_at, head_sha, branch, error, ...)`
  - [x] `events(run_id, idx, ts, type, payload_json)`
  - [x] `approvals(run_id, approval_id, created_at, file_path, symbol, risk_score, reason, diff, payload_json)`
  - [x] `decisions(run_id, approval_id, decision, decided_at, decided_by, reason, payload_json)`
- [x] Ingest path:
  - [x] End-of-run ingest at minimum (plus best-effort sync on status/decision)
  - [x] Cursor-based tailing from DB (`/runs/{run_id}/events_db`)

**DoD / Acceptance**
- [x] You can recover run status + approvals + decisions from SQLite even if `events.jsonl` is missing.

---

## Milestone M6 — UI v1 (workflow-first)

- [x] Runs list (status + started/updated) (`/studio/runs`)
- [x] Run detail: status + stop + events tail + approvals queue (`/studio/runs/<run_id>`)
- [x] Approval UI: approve/reject + reason (details JSON for now)
- [x] Transport: polling (cursor-based tail for events)

**DoD / Acceptance**
- [ ] A full “risky edit → blocked → approve → continues” flow is doable from the browser.

---

## Milestone M7 — MCP completeness + stability

- [ ] Tools:
  - [ ] `enqueue_run(repo_root, pack, profile)`
  - [ ] `get_status(run_id)`
  - [ ] `tail_events(run_id, cursor)`
  - [ ] `list_approvals(run_id)`
  - [ ] `decide_approval(run_id, approval_id, approve|reject, reason)`
  - [ ] `stop_run(run_id)`
- [ ] Contract tests validate request/response JSON schema

**DoD / Acceptance**
- [ ] An external agent can run the entire “start → tail → approve → stop” flow via MCP.

---

## Hardening + QA (before calling it v1)

- [ ] Integration tests on a small fixture repo:
  - [ ] Safe edit auto-applies; risky edit blocks; approval unblocks; patchset commits produced
- [ ] Failure modes:
  - [ ] Worker crash mid-run → daemon marks `failed`, retains logs
  - [ ] Stop mid-apply → consistent state; resume continues correctly
- [ ] Determinism:
  - [ ] Given same inputs, approval IDs and patchset grouping are stable
