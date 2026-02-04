import { NextResponse } from 'next/server';

import { stopNoctuneRun } from '@/lib/studio/noctune';
import { computeAllowWrite } from '@/lib/studio/permissions';

export const runtime = 'nodejs';

export async function POST(
  request: Request,
  { params }: { params: { runId: string } },
) {
  const body = (await request.json().catch(() => null)) as
    | { repoRoot?: string; pid?: number | null; allowToken?: string | null }
    | null;

  const repoRoot = body?.repoRoot ? String(body.repoRoot) : '';
  const runId = String(params.runId || '').trim();
  const pid = body?.pid == null ? null : Number(body.pid);

  if (!repoRoot) {
    return NextResponse.json({ ok: false, error: 'repoRoot is required' }, { status: 400 });
  }
  if (!runId) {
    return NextResponse.json({ ok: false, error: 'runId is required' }, { status: 400 });
  }

  const allow = await computeAllowWrite({
    repoRoot,
    allowToken: body?.allowToken ?? null,
    cookieHeader: request.headers.get('cookie'),
  });
  if (!allow.allowed) {
    return NextResponse.json({ ok: false, error: 'run_not_allowed' }, { status: 403 });
  }

  try {
    const res = await stopNoctuneRun({ repoRoot, runId, pid });
    return NextResponse.json({ ok: true, ...res });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message ?? e) },
      { status: 400 },
    );
  }
}

