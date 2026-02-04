import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';

import { resolveRepoRootOrThrow } from './paths';

export type NoctuneStage = 'review' | 'edit' | 'repair' | 'run';

export function generateRunId(now: Date = new Date()): string {
  const pad2 = (n: number) => String(n).padStart(2, '0');
  const y = now.getUTCFullYear();
  const mo = pad2(now.getUTCMonth() + 1);
  const d = pad2(now.getUTCDate());
  const h = pad2(now.getUTCHours());
  const mi = pad2(now.getUTCMinutes());
  const s = pad2(now.getUTCSeconds());
  const rand = crypto.randomBytes(4).toString('hex');
  return `${y}${mo}${d}_${h}${mi}${s}_${rand}`;
}

export async function startNoctuneRun(opts: {
  repoRoot: string;
  stage: NoctuneStage;
  relPaths?: string[] | null;
  extraArgs?: string[] | null;
  runId?: string | null;
}): Promise<{ runId: string; pid: number }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const stage = opts.stage;
  const runId = (opts.runId ?? '').trim() || generateRunId();

  const runDir = path.join(repoRoot, '.noctune_cache', 'runs', runId);
  const stateDir = path.join(runDir, 'state');
  await fs.mkdir(stateDir, { recursive: true });

  const cmd = process.env.NOCTUNE_STUDIO_PYTHON ?? 'python3';
  const args = ['-m', 'noctune', stage, '--root', repoRoot, '--run-id', runId];

  if (opts.relPaths && opts.relPaths.length) {
    const fl = path.join(runDir, 'file_list.txt');
    await fs.writeFile(fl, opts.relPaths.join('\n') + '\n', 'utf-8');
    args.push('--file-list', fl);
  }

  if (opts.extraArgs && opts.extraArgs.length) {
    args.push(...opts.extraArgs);
  }

  const child = spawn(cmd, args, {
    cwd: repoRoot,
    detached: true,
    stdio: 'ignore',
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });
  child.unref();

  return { runId, pid: child.pid ?? -1 };
}

export async function stopNoctuneRun(opts: {
  repoRoot: string;
  runId: string;
  pid?: number | null;
}): Promise<{ ok: boolean; stopFlagPath: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  if (!runId) throw new Error('runId is required');

  const stopFlagPath = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'state', 'stop.flag');
  await fs.mkdir(path.dirname(stopFlagPath), { recursive: true });
  await fs.writeFile(stopFlagPath, 'stop\n', 'utf-8');

  if (opts.pid) {
    try {
      process.kill(opts.pid, 'SIGTERM');
    } catch {
      // ignore
    }
  }

  return { ok: true, stopFlagPath };
}

export async function tailNoctuneEvents(opts: {
  repoRoot: string;
  runId: string;
  cursor?: number | null;
  limit?: number | null;
}): Promise<{ events: any[]; cursor: number; nextCursor: number; path: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  const p1 = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'events', 'events.jsonl');
  const p2 = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'logs', 'events.jsonl');

  let ep = p1;
  try {
    await fs.stat(ep);
  } catch {
    ep = p2;
  }

  let raw = '';
  try {
    raw = await fs.readFile(ep, 'utf-8');
  } catch {
    return { events: [], cursor: 0, nextCursor: 0, path: ep };
  }

  const lines = raw.split('\n').filter(Boolean);
  const n = lines.length;
  const lim = Math.max(1, Math.min(Math.floor(opts.limit ?? 200), 500));
  const start =
    opts.cursor == null
      ? Math.max(0, n - lim)
      : Math.max(0, Math.min(Math.floor(opts.cursor), n));
  const end = Math.min(n, start + lim);

  const out: any[] = [];
  for (const ln of lines.slice(start, end)) {
    try {
      out.push(JSON.parse(ln));
    } catch {
      // ignore bad lines
    }
  }
  return { events: out, cursor: start, nextCursor: end, path: ep };
}

export async function listPendingApprovals(opts: {
  repoRoot: string;
  runId: string;
}): Promise<{ approvals: any[]; dir: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  const dir = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'state', 'approvals');

  let entries: string[] = [];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return { approvals: [], dir };
  }

  const approvals: any[] = [];
  for (const name of entries.sort()) {
    if (!name.endsWith('.json')) continue;
    const p = path.join(dir, name);
    const decision = p.replace(/\.json$/, '.decision');
    try {
      await fs.stat(decision);
      continue;
    } catch {
      // ok
    }
    try {
      approvals.push(JSON.parse(await fs.readFile(p, 'utf-8')));
    } catch {
      // ignore
    }
  }

  return { approvals, dir };
}

export async function decideApproval(opts: {
  repoRoot: string;
  runId: string;
  approvalId: string;
  approved: boolean;
  reason?: string | null;
}): Promise<{ ok: boolean; decisionPath: string }> {
  const repoRoot = resolveRepoRootOrThrow(opts.repoRoot);
  const runId = String(opts.runId || '').trim();
  const approvalId = String(opts.approvalId || '').trim();
  if (!approvalId) throw new Error('approvalId is required');

  const dir = path.join(repoRoot, '.noctune_cache', 'runs', runId, 'state', 'approvals');
  await fs.mkdir(dir, { recursive: true });
  const decisionPath = path.join(dir, `${approvalId}.decision`);
  await fs.writeFile(
    decisionPath,
    JSON.stringify({ approved: Boolean(opts.approved), reason: opts.reason ?? '' }),
    'utf-8',
  );
  return { ok: true, decisionPath };
}

