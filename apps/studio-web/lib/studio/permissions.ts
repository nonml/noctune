import path from 'node:path';

import {
  consumeOnceToken,
  isAlwaysAllowed,
  isSessionAllowed,
  STUDIO_BROWSER_ID_COOKIE,
  STUDIO_SESSION_ID_COOKIE,
} from './allow';

export function getCookie(header: string | null, name: string): string | null {
  if (!header) return null;
  const parts = header.split(';').map((c) => c.trim());
  for (const p of parts) {
    if (!p.startsWith(name + '=')) continue;
    return decodeURIComponent(p.slice(name.length + 1));
  }
  return null;
}

export async function computeAllowWrite(opts: {
  repoRoot: string;
  allowToken?: string | null;
  cookieHeader: string | null;
}): Promise<{ allowed: boolean; mode: 'once' | 'session' | 'always' | 'none' }> {
  if (opts.allowToken && consumeOnceToken(String(opts.allowToken))) {
    return { allowed: true, mode: 'once' };
  }

  const sessionId = getCookie(opts.cookieHeader, STUDIO_SESSION_ID_COOKIE);
  if (sessionId && isSessionAllowed(sessionId)) {
    return { allowed: true, mode: 'session' };
  }

  const browserId = getCookie(opts.cookieHeader, STUDIO_BROWSER_ID_COOKIE);
  if (browserId && (await isAlwaysAllowed(opts.repoRoot, browserId))) {
    return { allowed: true, mode: 'always' };
  }

  return { allowed: false, mode: 'none' };
}

export function isSafeCachePath(repoRoot: string, relOrAbsPath: string): boolean {
  const rr = path.resolve(repoRoot);
  const p = path.resolve(rr, relOrAbsPath);
  const cacheDir = path.join(rr, '.noctune_cache');
  return p === cacheDir || p.startsWith(cacheDir + path.sep);
}

