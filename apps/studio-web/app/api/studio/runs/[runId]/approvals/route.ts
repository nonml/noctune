import { NextResponse } from 'next/server';

import { listApprovalsWithDecisions } from '@/lib/studio/runs';

export const runtime = 'nodejs';

export async function GET(
  request: Request,
  { params }: { params: { runId: string } },
) {
  const u = new URL(request.url);
  const repoRoot = u.searchParams.get('repoRoot') ?? '';
  const runId = String(params.runId || '').trim();

  if (!repoRoot) {
    return NextResponse.json({ ok: false, error: 'repoRoot is required' }, { status: 400 });
  }
  if (!runId) {
    return NextResponse.json({ ok: false, error: 'runId is required' }, { status: 400 });
  }

  try {
    const res = await listApprovalsWithDecisions({ repoRoot, runId });
    return NextResponse.json({ ok: true, ...res });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message ?? e) },
      { status: 400 },
    );
  }
}

