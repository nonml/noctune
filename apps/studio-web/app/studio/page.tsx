'use client';

import { DefaultChatTransport } from 'ai';
import { useChat } from '@ai-sdk/react';
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';

type AnyUIMessage = any;

type AllowMode = 'deny' | 'once' | 'session' | 'always';

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    // @ts-expect-error runtime feature
    return crypto.randomUUID();
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function Part({ part }: { part: any }) {
  if (!part || typeof part !== 'object') return null;
  if (part.type === 'text' && typeof part.text === 'string') {
    return <div className="msg-body">{part.text}</div>;
  }
  if (part.type === 'reasoning' && typeof part.text === 'string') {
    return (
      <details style={{ marginTop: 8 }}>
        <summary style={{ cursor: 'pointer', fontSize: 12, opacity: 0.7 }}>
          reasoning
        </summary>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: 0.7 }}>
          {part.text}
        </pre>
      </details>
    );
  }
  return (
    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: 0.7 }}>
      {JSON.stringify(part, null, 2)}
    </pre>
  );
}

export default function StudioPage() {
  const [repoRoot, setRepoRoot] = useState('');
  const [model, setModel] = useState('');
  const [system, setSystem] = useState('');

  const [allowMode, setAllowMode] = useState<AllowMode>('deny');
  const [onceToken, setOnceToken] = useState<string | null>(null);
  const [onceExpiresAtMs, setOnceExpiresAtMs] = useState<number | null>(null);

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
        if (j?.llm?.model && !model) setModel(String(j.llm.model));
      } catch {
        // ignore
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: '/api/studio/chat',
        prepareSendMessagesRequest({ messages }) {
          const token = allowMode === 'once' ? onceToken : null;
          return {
            body: {
              repoRoot,
              model,
              system: system || undefined,
              allowToken: token,
              messages,
            },
          };
        },
      }),
    [repoRoot, model, system, allowMode, onceToken],
  );

  const { messages, setMessages, sendMessage, status } = useChat<AnyUIMessage>({
    id: chatId,
    transport,
    generateId: newId,
    onFinish: () => {
      setOnceToken(null);
      setOnceExpiresAtMs(null);

      fetch('/api/studio/sessions', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ repoRoot, sessionId: chatId, messages }),
      }).catch(() => {});
    },
  });

  const [input, setInput] = useState('');
  const [savedSessions, setSavedSessions] = useState<Array<{ id: string; mtimeMs: number }>>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string>('');

  async function refreshSessions() {
    if (!repoRoot) return;
    const r = await fetch(`/api/studio/sessions?repoRoot=${encodeURIComponent(repoRoot)}`);
    const j = await r.json();
    if (j?.ok && Array.isArray(j.sessions)) {
      setSavedSessions(j.sessions);
      if (!selectedSessionId && j.sessions[0]?.id) setSelectedSessionId(j.sessions[0].id);
    }
  }

  useEffect(() => {
    refreshSessions().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoRoot]);

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

  function send(text: string) {
    sendMessage({ role: 'user', parts: [{ type: 'text', text }] });
  }

  return (
    <main className="container">
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="col">
            <div style={{ fontWeight: 700, fontSize: 18 }}>Noctune Studio</div>
            <div className="label">Local chat + tools (gated writes/runs)</div>
          </div>
          <div className="col" style={{ alignItems: 'flex-end' }}>
            <div className="label">status: {status}</div>
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
              <Link className="button" href="/studio/runs">
                Runs
              </Link>
            </div>
            <div className="label">
              mode: {allowMode}
              {allowMode === 'once' && onceExpiresAtMs ? (
                <> (expires {new Date(onceExpiresAtMs).toLocaleTimeString()})</>
              ) : null}
            </div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <div className="col" style={{ flex: 2, minWidth: 320 }}>
            <div className="label">repoRoot</div>
            <input
              className="input"
              value={repoRoot}
              onChange={(e) => setRepoRoot(e.target.value)}
              placeholder="/absolute/path/to/repo"
            />
          </div>
          <div className="col" style={{ flex: 1, minWidth: 240 }}>
            <div className="label">model</div>
            <input
              className="input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. qwen2.5-coder"
            />
          </div>
        </div>

        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer' }}>system prompt (optional)</summary>
          <textarea
            className="textarea"
            style={{ width: '100%', height: 110, marginTop: 8 }}
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            placeholder="Extra instructions for this chat…"
          />
        </details>

        <div className="row" style={{ marginTop: 10 }}>
          <button className="button" onClick={() => send('Search for "TODO" in this repo. Use search tool.')}>
            Search TODOs
          </button>
          <button className="button" onClick={() => send('Start a Noctune run (stage=run). Use noctuneStart.')}>
            Start Noctune run
          </button>
          <button className="button" onClick={() => refreshSessions()}>
            Refresh sessions
          </button>
          <button
            className="button"
            onClick={() => {
              const blob = new Blob([JSON.stringify(messages, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `noctune-studio-${chatId}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            Export JSON
          </button>
        </div>
      </div>

      <div className="panel row">
        <div className="col" style={{ minWidth: 280 }}>
          <div className="label">saved sessions</div>
          <select
            className="select"
            value={selectedSessionId}
            onChange={(e) => setSelectedSessionId(e.target.value)}
          >
            <option value="">(none)</option>
            {savedSessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}
              </option>
            ))}
          </select>
        </div>
        <button
          className="button"
          onClick={async () => {
            if (!selectedSessionId) return;
            const r = await fetch(
              `/api/studio/sessions/${encodeURIComponent(selectedSessionId)}?repoRoot=${encodeURIComponent(repoRoot)}`,
            );
            const j = await r.json();
            if (j?.ok && j.session?.messages && Array.isArray(j.session.messages)) {
              setMessages(j.session.messages);
            }
          }}
        >
          Load
        </button>
        <button
          className="button"
          onClick={() =>
            fetch('/api/studio/sessions', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ repoRoot, sessionId: chatId, messages }),
            })
          }
        >
          Save
        </button>
      </div>

      <div className="panel messages">
        {messages.length === 0 ? (
          <div className="label">Type a message. Example: “Read src/noctune/core/runner.py and explain the lifecycle.”</div>
        ) : null}
        {messages.map((m: any) => (
          <div key={m.id} className="msg">
            <div className="msg-role">{m.role}</div>
            {Array.isArray(m.parts) ? (
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {m.parts.map((p: any, i: number) => (
                  <Part key={i} part={p} />
                ))}
              </div>
            ) : (
              <div className="msg-body">{String(m.content ?? '')}</div>
            )}
          </div>
        ))}
      </div>

      <form
        className="panel footer"
        onSubmit={(e) => {
          e.preventDefault();
          const text = input.trim();
          if (!text) return;
          setInput('');
          send(text);
        }}
      >
        <input
          className="input"
          style={{ flex: 1 }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask, search, edit files, start Noctune runs…"
        />
        <button className="button" type="submit" disabled={status === 'streaming' || status === 'submitted'}>
          Send
        </button>
      </form>
    </main>
  );
}
