'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

type AllowMode = 'deny' | 'once' | 'session' | 'always';

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    // @ts-expect-error runtime feature
    return crypto.randomUUID();
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function fmtIso(x: any): string {
  const s = typeof x === 'string' ? x : '';
  if (!s) return '';
  const d = new Date(s);
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : s;
}

export default function RunsPage() {
  const [repoRoot, setRepoRoot] = useState('');

  const [allowMode, setAllowMode] = useState<AllowMode>('deny');
  const [onceToken, setOnceToken] = useState<string | null>(null);
  const [onceExpiresAtMs, setOnceExpiresAtMs] = useState<number | null>(null);

  const [runs, setRuns] = useState<Array<{ runId: string; state: any | null }>>([]);
  const [error, setError] = useState<string>('');

  const chatId = useMemo(() => {
    if (typeof window === 'undefined') return 'studio';
    const existing = window.localStorage.getItem('noctune_studio_chat_id');
    if (existing) return existing;
    const next = newId();
    window.localStorage.setItem('noctune_studio_chat_id', next);
    return next;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('/api/studio/config');
        const j = await r.json();
        if (j?.defaultRepoRoot && !repoRoot) setRepoRoot(String(j.defaultRepoRoot));
      } catch {
        // ignore
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function requestAllow(mode: Exclude<AllowMode, 'deny'>) {
    setAllowMode(mode);
    setOnceToken(null);
    setOnceExpiresAtMs(null);

    const r = await fetch('/api/studio/allow', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ repoRoot, mode }),
    });
    const j = await r.json();
    if (!r.ok || !j?.ok) throw new Error(j?.error || 'Failed to allow');

    if (mode === 'once') {
      setOnceToken(String(j.token));
      setOnceExpiresAtMs(Number(j.expiresAtMs));
    }
  }

  async function refreshRuns() {
    if (!repoRoot) return;
    setError('');
    try {
      const r = await fetch(`/api/studio/runs?repoRoot=${encodeURIComponent(repoRoot)}&limit=50`);
      const j = await r.json();
      if (!r.ok || !j?.ok) throw new Error(j?.error || 'Failed to list runs');
      setRuns(Array.isArray(j.runs) ? j.runs : []);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    refreshRuns().catch(() => {});
    if (!repoRoot) return;
    const t = window.setInterval(() => refreshRuns().catch(() => {}), 2000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoRoot]);

  return (
    <main className="container">
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="col">
            <div style={{ fontWeight: 700, fontSize: 18 }}>Noctune Studio</div>
            <div className="label">Runs</div>
          </div>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <div className="col" style={{ alignItems: 'flex-end' }}>
              <div className="row">
                <button className="button" onClick={() => requestAllow('once')}>
                  Allow once
                </button>
                <button className="button" onClick={() => requestAllow('session')}>
                  Allow this session
                </button>
                <button className="button" onClick={() => requestAllow('always')}>
                  Always allow
                </button>
              </div>
              <div className="label">
                mode: {allowMode}
                {allowMode === 'once' && onceExpiresAtMs ? (
                  <> (expires {new Date(onceExpiresAtMs).toLocaleTimeString()})</>
                ) : null}
              </div>
            </div>
            <div className="col" style={{ alignItems: 'flex-end' }}>
              <Link href="/studio">Chat</Link>
              <div className="label">session: {chatId.slice(0, 8)}…</div>
            </div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <div className="col" style={{ flex: 1, minWidth: 420 }}>
            <div className="label">repoRoot</div>
            <input
              className="input"
              value={repoRoot}
              onChange={(e) => setRepoRoot(e.target.value)}
              placeholder="/absolute/path/to/repo"
            />
          </div>
          <div className="col" style={{ alignItems: 'flex-end' }}>
            <button className="button" onClick={() => refreshRuns()}>
              Refresh
            </button>
          </div>
        </div>

        {error ? (
          <div className="msg" style={{ marginTop: 10 }}>
            <div className="msg-role">error</div>
            <div className="msg-body">{error}</div>
          </div>
        ) : null}

        <div style={{ marginTop: 12 }}>
          {runs.length ? (
            runs.map((r) => (
              <div key={r.runId} className="msg">
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <div className="col" style={{ gap: 2 }}>
                    <div style={{ fontWeight: 600 }}>{r.runId}</div>
                    <div className="label">
                      {String(r.state?.stage || '')} • {String(r.state?.status || '')}
                      {typeof r.state?.pid === 'number' ? ` • pid ${r.state.pid}` : ''}
                    </div>
                    <div className="label">
                      updated: {fmtIso(r.state?.updated_at)}{' '}
                      {r.state?.started_at ? `• started: ${fmtIso(r.state.started_at)}` : ''}
                    </div>
                  </div>
                  <div className="col" style={{ alignItems: 'flex-end' }}>
                    <Link
                      href={`/studio/runs/${encodeURIComponent(r.runId)}?repoRoot=${encodeURIComponent(repoRoot)}`}
                    >
                      Open
                    </Link>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="label" style={{ marginTop: 10 }}>
              No runs yet. Start one from the chat (tool: `noctuneStart`), then come back here.
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

