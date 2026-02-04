import fs from 'node:fs/promises';
import path from 'node:path';

import { resolveRepoRootOrThrow } from './paths';

export type RunState = {
  run_id?: string;
  repo_root?: string;
  stage?: string;
  status?: string;
  pid?: number | null;
  pack?: string | null;
  profile?: string | null;
  branch?: string | null;
  head_sha?: string | null;
  error?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  [k: string]: any;
};

function isoToMs(s: any): number {
  if (!s || typeof s !== 'string') return 0;
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? ms : 0;
}

function safeJsonParse(raw: string): any {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function pidExists(pid: number): boolean {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e: any) {
    if (e?.code === 'ESRCH') return false;
    return true;
  }
}

export async function readRunState(opts: {
  repoRoot: string;
  runId: string;
}): Promise<{ ok: boolean; state: RunState | null; path: string; pidAlive?: boolean }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  const p = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'state', 'run.json');
  try {
    const raw = await fs.readFile(p, 'utf-8');
    const obj = safeJsonParse(raw);
    const state: RunState | null = obj && typeof obj === 'object' ? obj : null;
    const pid = typeof state?.pid === 'number' ? state.pid : null;
    return { ok: true, state, path: p, pidAlive: pid ? pidExists(pid) : false };
  } catch {
    return { ok: false, state: null, path: p };
  }
}

export async function listRuns(opts: {
  repoRoot: string;
  limit?: number | null;
}): Promise<{ ok: boolean; runs: Array<{ runId: string; state: RunState | null }>; dir: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const dir = path.join(repoRoot, '.noctune_cache', 'runs');
  let names: string[] = [];
  try {
    names = await fs.readdir(dir);
  } catch {
    return { ok: true, runs: [], dir };
  }

  const recs: Array<{ runId: string; state: RunState | null; sortMs: number }> = [];
  for (const name of names) {
    if (!name || name.startsWith('.')) continue;
    const st = await readRunState({ repoRoot, runId: name });
    const sortMs = isoToMs(st.state?.updated_at) || isoToMs(st.state?.started_at) || 0;
    recs.push({ runId: name, state: st.state, sortMs });
  }

  recs.sort((a, b) => {
    if (a.sortMs !== b.sortMs) return b.sortMs - a.sortMs;
    return b.runId.localeCompare(a.runId);
  });

  const lim = Math.max(1, Math.min(Math.floor(opts.limit ?? 50), 200));
  const out = recs.slice(0, lim).map(({ runId, state }) => ({ runId, state }));
  return { ok: true, runs: out, dir };
}

export type ApprovalWithDecision = {
  approval_id: string;
  approval: any;
  decided: boolean;
  decision: any | null;
  path: string;
};

export async function listApprovalsWithDecisions(opts: {
  repoRoot: string;
  runId: string;
}): Promise<{ ok: boolean; approvals: ApprovalWithDecision[]; dir: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  const dir = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'state', 'approvals');

  let entries: string[] = [];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return { ok: true, approvals: [], dir };
  }

  const approvals: ApprovalWithDecision[] = [];
  for (const name of entries.sort()) {
    if (!name.endsWith('.json')) continue;
    const p = path.join(dir, name);
    const approval_id = name.replace(/\.json$/, '');

    let approval: any = null;
    try {
      approval = safeJsonParse(await fs.readFile(p, 'utf-8'));
    } catch {
      approval = null;
    }

    const decisionPath = p.replace(/\.json$/, '.decision');
    let decision: any = null;
    let decided = false;
    try {
      const rawDecision = await fs.readFile(decisionPath, 'utf-8');
      decision = safeJsonParse(rawDecision) ?? { raw: rawDecision.trim() };
      decided = true;
    } catch {
      decided = false;
      decision = null;
    }

    approvals.push({ approval_id, approval, decided, decision, path: p });
  }

  return { ok: true, approvals, dir };
}

