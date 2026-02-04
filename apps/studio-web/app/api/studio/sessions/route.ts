import fs from 'node:fs/promises';
import path from 'node:path';

import { NextResponse } from 'next/server';

import { resolveRepoRootOrThrow } from '@/lib/studio/paths';

export const runtime = 'nodejs';

function sessionsDir(repoRoot: string): string {
  return path.join(repoRoot, '.noctune_cache', 'studio_chat', 'sessions');
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const repoRootRaw = url.searchParams.get('repoRoot') || '';
  if (!repoRootRaw) {
    return NextResponse.json(
      { ok: false, error: 'repoRoot is required' },
      { status: 400 },
    );
  }
  const repoRoot = resolveRepoRootOrThrow(repoRootRaw);
  const dir = sessionsDir(repoRoot);

  let names: string[] = [];
  try {
    names = await fs.readdir(dir);
  } catch {
    return NextResponse.json({ ok: true, sessions: [] });
  }

  const sessions: Array<{ id: string; mtimeMs: number }> = [];
  for (const name of names) {
    if (!name.endsWith('.json')) continue;
    const id = name.slice(0, -'.json'.length);
    try {
      const st = await fs.stat(path.join(dir, name));
      sessions.push({ id, mtimeMs: st.mtimeMs });
    } catch {
      // ignore
    }
  }

  sessions.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return NextResponse.json({ ok: true, sessions });
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { repoRoot?: string; sessionId?: string; messages?: unknown }
    | null;

  const repoRootRaw = body?.repoRoot ? String(body.repoRoot) : '';
  const sessionId = body?.sessionId ? String(body.sessionId) : '';
  if (!repoRootRaw || !sessionId) {
    return NextResponse.json(
      { ok: false, error: 'repoRoot and sessionId are required' },
      { status: 400 },
    );
  }
  const repoRoot = resolveRepoRootOrThrow(repoRootRaw);
  const dir = sessionsDir(repoRoot);
  await fs.mkdir(dir, { recursive: true });

  const p = path.join(dir, `${sessionId}.json`);
  await fs.writeFile(
    p,
    JSON.stringify(
      { savedAt: new Date().toISOString(), sessionId, messages: body?.messages ?? [] },
      null,
      2,
    ) + '\n',
    'utf-8',
  );

  return NextResponse.json({ ok: true, path: p });
}

