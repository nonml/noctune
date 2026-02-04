import { NextResponse } from 'next/server';

import {
  allowSession,
  issueOnceToken,
  newId,
  setAlwaysAllowed,
  STUDIO_BROWSER_ID_COOKIE,
  STUDIO_SESSION_ID_COOKIE,
} from '@/lib/studio/allow';
import { resolveRepoRootOrThrow } from '@/lib/studio/paths';

export const runtime = 'nodejs';

type Mode = 'once' | 'session' | 'always';

function getCookie(header: string | null, name: string): string | null {
  if (!header) return null;
  const parts = header.split(';').map((c) => c.trim());
  for (const p of parts) {
    if (!p.startsWith(name + '=')) continue;
    return decodeURIComponent(p.slice(name.length + 1));
  }
  return null;
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { repoRoot?: string; mode?: Mode }
    | null;
  const repoRoot = body?.repoRoot ? String(body.repoRoot) : '';
  const mode = body?.mode ? String(body.mode) : '';

  if (!repoRoot) {
    return NextResponse.json(
      { ok: false, error: 'repoRoot is required' },
      { status: 400 },
    );
  }
  if (!['once', 'session', 'always'].includes(mode)) {
    return NextResponse.json(
      { ok: false, error: 'mode must be once|session|always' },
      { status: 400 },
    );
  }

  const rr = resolveRepoRootOrThrow(repoRoot);
  const cookieHeader = request.headers.get('cookie');

  let browserId = getCookie(cookieHeader, STUDIO_BROWSER_ID_COOKIE);
  if (!browserId) browserId = newId(16);

  if (mode === 'once') {
    const { token, expiresAtMs } = issueOnceToken();
    const res = NextResponse.json({ ok: true, mode, repoRoot: rr, token, expiresAtMs });
    res.cookies.set(STUDIO_BROWSER_ID_COOKIE, browserId, {
      httpOnly: true,
      sameSite: 'lax',
      path: '/',
      maxAge: 365 * 24 * 60 * 60,
    });
    return res;
  }

  if (mode === 'session') {
    const sessionId = newId(16);
    const { expiresAtMs } = allowSession(sessionId);
    const res = NextResponse.json({ ok: true, mode, repoRoot: rr, expiresAtMs });
    res.cookies.set(STUDIO_SESSION_ID_COOKIE, sessionId, {
      httpOnly: true,
      sameSite: 'lax',
      path: '/',
      maxAge: 12 * 60 * 60,
    });
    res.cookies.set(STUDIO_BROWSER_ID_COOKIE, browserId, {
      httpOnly: true,
      sameSite: 'lax',
      path: '/',
      maxAge: 365 * 24 * 60 * 60,
    });
    return res;
  }

  await setAlwaysAllowed(rr, browserId);
  const res = NextResponse.json({ ok: true, mode, repoRoot: rr });
  res.cookies.set(STUDIO_BROWSER_ID_COOKIE, browserId, {
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge: 365 * 24 * 60 * 60,
  });
  return res;
}

