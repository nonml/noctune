import { NextResponse } from 'next/server';

import { decideApproval } from '@/lib/studio/noctune';
import { computeAllowWrite } from '@/lib/studio/permissions';

export const runtime = 'nodejs';

export async function POST(
  request: Request,
  { params }: { params: { runId: string; approvalId: string } },
) {
  const body = (await request.json().catch(() => null)) as
    | {
        repoRoot?: string;
        approved?: boolean;
        reason?: string;
        allowToken?: string | null;
      }
    | null;

  const repoRoot = body?.repoRoot ? String(body.repoRoot) : '';
  const runId = String(params.runId || '').trim();
  const approvalId = String(params.approvalId || '').trim();
  const approved = Boolean(body?.approved);
  const reason = body?.reason ? String(body.reason) : '';

  if (!repoRoot) {
    return NextResponse.json({ ok: false, error: 'repoRoot is required' }, { status: 400 });
  }
  if (!runId) {
    return NextResponse.json({ ok: false, error: 'runId is required' }, { status: 400 });
  }
  if (!approvalId) {
    return NextResponse.json({ ok: false, error: 'approvalId is required' }, { status: 400 });
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
    const res = await decideApproval({ repoRoot, runId, approvalId, approved, reason });
    return NextResponse.json({ ok: true, ...res });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message ?? e) },
      { status: 400 },
    );
  }
}

