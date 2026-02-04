import { NextResponse } from 'next/server';

import { listRuns } from '@/lib/studio/runs';

export const runtime = 'nodejs';

export async function GET(request: Request) {
  const u = new URL(request.url);
  const repoRoot = u.searchParams.get('repoRoot') ?? '';
  const limit = u.searchParams.get('limit');

  if (!repoRoot) {
    return NextResponse.json({ ok: false, error: 'repoRoot is required' }, { status: 400 });
  }

  try {
    const res = await listRuns({ repoRoot, limit: limit ? Number(limit) : null });
    return NextResponse.json({ ok: true, ...res });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message ?? e) },
      { status: 400 },
    );
  }
}

