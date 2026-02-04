import Link from 'next/link';

export default function Home() {
  return (
    <main className="container">
      <div className="panel">
        <h1 style={{ margin: 0 }}>Noctune Studio</h1>
        <p style={{ marginTop: 8, opacity: 0.8 }}>
          Local chat + repo tools.
        </p>
        <div className="row">
          <Link href="/studio">Chat</Link>
          <Link href="/studio/runs">Runs</Link>
        </div>
      </div>
    </main>
  );
}
