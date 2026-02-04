import './globals.css';

export const metadata = {
  title: 'Noctune Studio',
  description: 'Local Noctune Studio web UI',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

