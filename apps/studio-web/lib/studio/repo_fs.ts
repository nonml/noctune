import fs from 'node:fs/promises';
import path from 'node:path';

import { resolvePathInRepoOrThrow, resolveRepoRootOrThrow } from './paths';

function isWithinNoctuneCache(repoRoot: string, absPath: string): boolean {
  const cacheDir = path.join(repoRoot, '.noctune_cache');
  return absPath === cacheDir || absPath.startsWith(cacheDir + path.sep);
}

export async function readTextFile(
  repoRoot: string,
  relOrAbsPath: string,
  opts?: { maxBytes?: number },
): Promise<{ path: string; content: string; truncated: boolean }> {
  const rr = resolveRepoRootOrThrow(repoRoot);
  const p = resolvePathInRepoOrThrow(rr, relOrAbsPath);
  const maxBytes = Math.max(1, Math.min(opts?.maxBytes ?? 256_000, 2_000_000));

  const buf = await fs.readFile(p);
  if (buf.length <= maxBytes) {
    return { path: p, content: buf.toString('utf-8'), truncated: false };
  }
  return { path: p, content: buf.subarray(0, maxBytes).toString('utf-8'), truncated: true };
}

async function backupFile(repoRoot: string, absPath: string): Promise<void> {
  if (isWithinNoctuneCache(repoRoot, absPath)) return;

  const rel = path.relative(repoRoot, absPath);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupRoot = path.join(repoRoot, '.noctune_cache', 'studio_edits', 'backups', stamp);
  const dst = path.join(backupRoot, rel);

  await fs.mkdir(path.dirname(dst), { recursive: true });
  try {
    await fs.copyFile(absPath, dst);
  } catch {
    // If the file doesn't exist yet, no backup needed.
  }
}

export async function writeTextFile(
  repoRoot: string,
  relOrAbsPath: string,
  content: string,
): Promise<{ path: string; bytes: number }> {
  const rr = resolveRepoRootOrThrow(repoRoot);
  const p = resolvePathInRepoOrThrow(rr, relOrAbsPath);

  await fs.mkdir(path.dirname(p), { recursive: true });
  await backupFile(rr, p);
  await fs.writeFile(p, content, 'utf-8');
  return { path: p, bytes: Buffer.byteLength(content, 'utf-8') };
}

export async function replaceLines(
  repoRoot: string,
  relOrAbsPath: string,
  startLine: number,
  endLine: number,
  replacement: string,
): Promise<{ path: string; applied: boolean; newLineCount: number }> {
  const rr = resolveRepoRootOrThrow(repoRoot);
  const p = resolvePathInRepoOrThrow(rr, relOrAbsPath);

  const raw = await fs.readFile(p, 'utf-8');
  const lines = raw.split(/\r?\n/);

  const s = Math.max(1, Math.floor(startLine));
  const e = Math.max(s, Math.floor(endLine));
  if (s > lines.length + 1) {
    return { path: p, applied: false, newLineCount: lines.length };
  }

  const before = lines.slice(0, s - 1);
  const after = lines.slice(e);
  const repLines = replacement.split(/\r?\n/);
  const nextLines = [...before, ...repLines, ...after];
  const next = nextLines.join('\n');

  await backupFile(rr, p);
  await fs.writeFile(p, next, 'utf-8');
  return { path: p, applied: true, newLineCount: nextLines.length };
}

