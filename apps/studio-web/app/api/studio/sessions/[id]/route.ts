import fs from 'node:fs/promises';
import path from 'node:path';

import { NextResponse } from 'next/server';

import { resolveRepoRootOrThrow } from '@/lib/studio/paths';

export const runtime = 'nodejs';

export async function GET(request: Request, { params }: { params: { id: string } }) {
  const { id } = params;
  const url = new URL(request.url);
  const repoRootRaw = url.searchParams.get('repoRoot') || '';
  if (!repoRootRaw) {
    return NextResponse.json(
      { ok: false, error: 'repoRoot is required' },
      { status: 400 },
    );
  }
  const repoRoot = resolveRepoRootOrThrow(repoRootRaw);

  const p = path.join(repoRoot, '.noctune_cache', 'studio_chat', 'sessions', `${id}.json`);
  try {
    const text = await fs.readFile(p, 'utf-8');
    return NextResponse.json({ ok: true, session: JSON.parse(text) });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: 'not_found', path: p, detail: String(e?.message ?? e) },
      { status: 404 },
    );
  }
}

