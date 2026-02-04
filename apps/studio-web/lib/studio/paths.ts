import path from 'node:path';

export function getDefaultRepoRoot(): string {
  // When running `pnpm dev` from `apps/studio-web`, this resolves to the monorepo root.
  return path.resolve(process.cwd(), '../..');
}

export function getAllowedRepoRoots(): string[] {
  const raw = (process.env.NOCTUNE_STUDIO_ALLOWED_ROOTS ?? '').trim();
  const fromEnv = raw
    ? raw
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .map((p) => path.resolve(p))
    : [];

  const fallback = [getDefaultRepoRoot()];

  const seen = new Set<string>();
  const out: string[] = [];
  for (const root of [...fromEnv, ...fallback]) {
    if (!seen.has(root)) {
      seen.add(root);
      out.push(root);
    }
  }
  return out;
}

export function resolveRepoRootOrThrow(repoRoot: string): string {
  const rr = path.resolve(repoRoot);
  const allowed = getAllowedRepoRoots();

  for (const base of allowed) {
    if (rr === base) return rr;
    if (rr.startsWith(base + path.sep)) return rr;
  }

  throw new Error(
    `repoRoot is not allowed. Set NOCTUNE_STUDIO_ALLOWED_ROOTS to include it. repoRoot=${rr}`,
  );
}

export function resolvePathInRepoOrThrow(repoRoot: string, relOrAbsPath: string): string {
  const rr = path.resolve(repoRoot);
  const p = path.resolve(rr, relOrAbsPath);
  if (p === rr || p.startsWith(rr + path.sep)) return p;
  throw new Error(`path escapes repoRoot: ${relOrAbsPath}`);
}

