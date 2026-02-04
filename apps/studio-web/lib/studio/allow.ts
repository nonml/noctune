import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import { resolveRepoRootOrThrow } from './paths';

export const STUDIO_BROWSER_ID_COOKIE = 'noctune_studio_browser_id';
export const STUDIO_SESSION_ID_COOKIE = 'noctune_studio_session_id';

type OnceTokenRec = { expiresAtMs: number };

declare global {
  // eslint-disable-next-line no-var
  var __noctuneStudioOnceTokens: Map<string, OnceTokenRec> | undefined;
  // eslint-disable-next-line no-var
  var __noctuneStudioAllowedSessions: Map<string, number> | undefined;
}

function onceTokens(): Map<string, OnceTokenRec> {
  if (!globalThis.__noctuneStudioOnceTokens) {
    globalThis.__noctuneStudioOnceTokens = new Map();
  }
  return globalThis.__noctuneStudioOnceTokens;
}

function allowedSessions(): Map<string, number> {
  if (!globalThis.__noctuneStudioAllowedSessions) {
    globalThis.__noctuneStudioAllowedSessions = new Map();
  }
  return globalThis.__noctuneStudioAllowedSessions;
}

export function newId(bytes: number = 16): string {
  return crypto.randomBytes(bytes).toString('hex');
}

export function issueOnceToken(ttlMs: number = 2 * 60 * 1000): { token: string; expiresAtMs: number } {
  const token = newId(24);
  const expiresAtMs = Date.now() + ttlMs;
  onceTokens().set(token, { expiresAtMs });
  return { token, expiresAtMs };
}

export function consumeOnceToken(token: string): boolean {
  const rec = onceTokens().get(token);
  if (!rec) return false;
  onceTokens().delete(token);
  if (Date.now() > rec.expiresAtMs) return false;
  return true;
}

export function allowSession(sessionId: string, ttlMs: number = 12 * 60 * 60 * 1000): { expiresAtMs: number } {
  const expiresAtMs = Date.now() + ttlMs;
  allowedSessions().set(sessionId, expiresAtMs);
  return { expiresAtMs };
}

export function isSessionAllowed(sessionId: string): boolean {
  const exp = allowedSessions().get(sessionId);
  if (!exp) return false;
  if (Date.now() > exp) {
    allowedSessions().delete(sessionId);
    return false;
  }
  return true;
}

function allowFilePath(repoRoot: string): string {
  return path.join(repoRoot, '.noctune_cache', 'studio_allow.json');
}

export async function setAlwaysAllowed(repoRoot: string, browserId: string): Promise<void> {
  const rr = resolveRepoRootOrThrow(repoRoot);
  const p = allowFilePath(rr);
  await fs.mkdir(path.dirname(p), { recursive: true });

  let obj: any = {};
  try {
    obj = JSON.parse(await fs.readFile(p, 'utf-8'));
  } catch {
    obj = {};
  }

  if (!obj || typeof obj !== 'object') obj = {};
  if (!obj.browsers || typeof obj.browsers !== 'object') obj.browsers = {};
  obj.browsers[browserId] = { allowed: true, updatedAt: new Date().toISOString() };

  await fs.writeFile(p, JSON.stringify(obj, null, 2) + '\n', 'utf-8');
}

export async function isAlwaysAllowed(repoRoot: string, browserId: string): Promise<boolean> {
  const rr = resolveRepoRootOrThrow(repoRoot);
  const p = allowFilePath(rr);
  try {
    const obj = JSON.parse(await fs.readFile(p, 'utf-8'));
    return Boolean(obj?.browsers?.[browserId]?.allowed);
  } catch {
    return false;
  }
}

