'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';

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

export default function RunDetailPage({ params }: { params: { runId: string } }) {
  const runId = String(params.runId || '').trim();
  const sp = useSearchParams();
  const initialRepoRoot = sp.get('repoRoot') ?? '';

  const [repoRoot, setRepoRoot] = useState(initialRepoRoot);

  const [allowMode, setAllowMode] = useState<AllowMode>('deny');
  const [onceToken, setOnceToken] = useState<string | null>(null);
  const [onceExpiresAtMs, setOnceExpiresAtMs] = useState<number | null>(null);

  const [state, setState] = useState<any | null>(null);
  const [pidAlive, setPidAlive] = useState<boolean | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const cursorRef = useRef<number | null>(null);
  const [approvals, setApprovals] = useState<any[]>([]);
  const [error, setError] = useState<string>('');

  const inflight = useRef(false);

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

  useEffect(() => {
    if (!repoRoot) return;
    try {
      window.localStorage.setItem('noctune_studio_repo_root', repoRoot);
    } catch {
      // ignore
    }
  }, [repoRoot]);

  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);

  useEffect(() => {
    if (repoRoot) return;
    try {
      const saved = window.localStorage.getItem('noctune_studio_repo_root');
      if (saved) setRepoRoot(saved);
    } catch {
      // ignore
    }
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

  async function refreshState() {
    if (!repoRoot || !runId) return;
    const r = await fetch(
      `/api/studio/runs/${encodeURIComponent(runId)}/state?repoRoot=${encodeURIComponent(repoRoot)}`,
    );
    const j = await r.json();
    if (!r.ok || !j?.ok) throw new Error(j?.error || 'Failed to load run state');
    setState(j.state ?? null);
    setPidAlive(typeof j.pidAlive === 'boolean' ? j.pidAlive : null);
  }

  async function pollEvents() {
    if (!repoRoot || !runId) return;
    if (inflight.current) return;
    inflight.current = true;
    try {
      const cur = cursorRef.current;
      const qs = new URLSearchParams({
        repoRoot,
        limit: '200',
      });
      if (cur != null) qs.set('cursor', String(cur));
      const r = await fetch(`/api/studio/runs/${encodeURIComponent(runId)}/events?${qs.toString()}`);
      const j = await r.json();
      if (!r.ok || !j?.ok) throw new Error(j?.error || 'Failed to load events');
      const got = Array.isArray(j.events) ? j.events : [];
      const nextCursor = typeof j.nextCursor === 'number' ? j.nextCursor : null;
      if (cur == null) {
        setEvents(got);
      } else if (got.length) {
        setEvents((prev) => [...prev, ...got]);
      }
      if (nextCursor != null) {
        cursorRef.current = nextCursor;
        setCursor(nextCursor);
      }
    } finally {
      inflight.current = false;
    }
  }

  async function refreshApprovals() {
    if (!repoRoot || !runId) return;
    const r = await fetch(
      `/api/studio/runs/${encodeURIComponent(runId)}/approvals?repoRoot=${encodeURIComponent(repoRoot)}`,
    );
    const j = await r.json();
    if (!r.ok || !j?.ok) throw new Error(j?.error || 'Failed to load approvals');
    setApprovals(Array.isArray(j.approvals) ? j.approvals : []);
  }

  useEffect(() => {
    setError('');
    refreshState()
      .then(() => pollEvents())
      .then(() => refreshApprovals())
      .catch((e: any) => setError(String(e?.message ?? e)));
    if (!repoRoot) return;

    const t1 = window.setInterval(() => {
      refreshState().catch(() => {});
    }, 1500);
    const t2 = window.setInterval(() => {
      pollEvents().catch(() => {});
    }, 1000);
    const t3 = window.setInterval(() => {
      refreshApprovals().catch(() => {});
    }, 2000);
    return () => {
      window.clearInterval(t1);
      window.clearInterval(t2);
      window.clearInterval(t3);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoRoot, runId]);

  async function stopRun() {
    if (!repoRoot) return;
    setError('');
    const r = await fetch(`/api/studio/runs/${encodeURIComponent(runId)}/stop`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ repoRoot, pid: state?.pid ?? null, allowToken: onceToken }),
    });
    setOnceToken(null);
    setOnceExpiresAtMs(null);
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j?.error || 'Stop failed');
    }
  }

  async function decide(approvalId: string, approved: boolean) {
    if (!repoRoot) return;
    const reason = window.prompt(approved ? 'Approval note (optional)' : 'Rejection reason (optional)') || '';
    const r = await fetch(
      `/api/studio/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ repoRoot, approved, reason, allowToken: onceToken }),
      },
    );
    setOnceToken(null);
    setOnceExpiresAtMs(null);
    const j = await r.json().catch(() => null);
    if (!r.ok || !j?.ok) throw new Error(j?.error || 'Decision failed');
    await refreshApprovals();
  }

  function resetEventsTail() {
    setEvents([]);
    setCursor(null);
    cursorRef.current = null;
    pollEvents().catch(() => {});
  }

  return (
    <main className="container">
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="col">
            <div style={{ fontWeight: 700, fontSize: 18 }}>Run {runId}</div>
            <div className="label">
              {String(state?.stage || '')} • {String(state?.status || '')}{' '}
              {typeof state?.pid === 'number' ? `• pid ${state.pid}` : ''}
              {pidAlive != null ? ` • pidAlive=${String(pidAlive)}` : ''}
            </div>
            <div className="label">
              started: {fmtIso(state?.started_at)} • updated: {fmtIso(state?.updated_at)}{' '}
              {state?.ended_at ? `• ended: ${fmtIso(state.ended_at)}` : ''}
            </div>
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
              <Link href="/studio/runs">Runs</Link>
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
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <button className="button" onClick={() => resetEventsTail()}>
              Reset tail
            </button>
            <button
              className="button"
              onClick={() => stopRun().catch((e: any) => setError(String(e?.message ?? e)))}
              disabled={!repoRoot}
            >
              Stop
            </button>
          </div>
        </div>

        {error ? (
          <div className="msg" style={{ marginTop: 10 }}>
            <div className="msg-role">error</div>
            <div className="msg-body">{error}</div>
          </div>
        ) : null}

        <div className="row" style={{ marginTop: 10, alignItems: 'stretch' }}>
          <div className="panel" style={{ flex: 2, minWidth: 420 }}>
            <div style={{ fontWeight: 600 }}>Events</div>
            <div className="label">cursor: {cursor == null ? '(tail)' : cursor}</div>
            <div className="messages" style={{ maxHeight: 360 }}>
              {events.map((e, i) => (
                <div key={i} className="msg">
                  <div className="msg-role">{String(e?.type || e?.event || 'event')}</div>
                  <pre className="msg-body" style={{ fontSize: 12, opacity: 0.85 }}>
                    {JSON.stringify(e, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </div>
          <div className="panel" style={{ flex: 1, minWidth: 320 }}>
            <div style={{ fontWeight: 600 }}>Approvals</div>
            <div className="label">pending + decided (file-based)</div>
            <div className="messages" style={{ maxHeight: 360 }}>
              {approvals.length ? (
                approvals.map((a) => (
                  <div key={String(a.approval_id)} className="msg">
                    <div style={{ fontWeight: 600 }}>{String(a.approval_id)}</div>
                    <div className="label">
                      {a.decided
                        ? `decided: ${String(a.decision?.approved ?? a.decision?.decision ?? 'yes/no')}`
                        : 'pending'}
                    </div>
                    {a.approval ? (
                      <details style={{ marginTop: 8 }}>
                        <summary style={{ cursor: 'pointer' }}>details</summary>
                        <pre className="msg-body" style={{ fontSize: 12 }}>
                          {JSON.stringify(a.approval, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                    {!a.decided ? (
                      <div className="row" style={{ marginTop: 8 }}>
                        <button
                          className="button"
                          onClick={() => decide(String(a.approval_id), true).catch((e: any) => setError(String(e?.message ?? e)))}
                        >
                          Approve
                        </button>
                        <button
                          className="button"
                          onClick={() => decide(String(a.approval_id), false).catch((e: any) => setError(String(e?.message ?? e)))}
                        >
                          Reject
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="label" style={{ marginTop: 8 }}>
                  No approvals found.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
